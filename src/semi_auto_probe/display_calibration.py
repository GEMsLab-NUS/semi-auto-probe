"""Session-scoped display calibration side channel; never sends UVF samples."""
from __future__ import annotations

from dataclasses import dataclass

import hashlib
import json
import math
import shutil
import subprocess
import threading
import time

import numpy as np


def coordinate_basis(config):
    scales = tuple(float(config.um_per_pulse(axis)) for axis in "XYZ")
    polarities = tuple(-1 if int(config.motor_axis_polarity.get(axis, 1)) < 0 else 1 for axis in "XYZ")
    if not all(math.isfinite(value) and value > 0 for value in scales):
        raise ValueError("Invalid pulse scale")
    return scales, polarities


@dataclass(frozen=True)
class DisplayFocusReference:
    """Single successful AF zero, used only for display (never stage motion)."""
    c: float
    enabled: bool = True
    a: float = 0.0
    b: float = 0.0


def calibration_payload(mapper, plane, config):
    xy_valid = mapper is not None
    f_valid = plane is not None and plane.enabled
    if not xy_valid and not f_valid:
        return None
    scales, polarities = coordinate_basis(config)
    matrix = np.zeros((3, 4))
    if xy_valid:
        forward = np.asarray(mapper.matrix, dtype=float)
        if forward.shape != (2, 3) or not np.isfinite(forward).all():
            raise ValueError("Invalid LayoutMap")
        try:
            inverse = np.linalg.inv(forward[:, 1:3])
        except np.linalg.LinAlgError as exc:
            raise ValueError("LayoutMap is not invertible") from exc
        matrix[:2, :2] = inverse @ np.diag(np.asarray(scales[:2]) * polarities[:2])
        matrix[:2, 3] = -inverse @ forward[:, 0]
    if f_valid:
        a, b, c = float(plane.a), float(plane.b), float(plane.c)
        px, py, pz = polarities
        sz = scales[2]
        matrix[2] = (-sz*a*px, -sz*b*py, sz*pz, -sz*c)
    if not np.isfinite(matrix).all() or np.any(np.abs(matrix) > 1e12):
        raise ValueError("Invalid display calibration coefficients")
    coefficients = matrix.tolist()
    valid_axes = {"U": xy_valid, "V": xy_valid, "F": bool(f_valid)}
    revision = hashlib.sha256(json.dumps(
        [coefficients, valid_axes], separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    # Preserve the original wire protocol when all three outputs are available.
    payload = {"schema_version": 1 if xy_valid and f_valid else 2,
               "calibration_id": revision, "z_calibrated": bool(f_valid),
               "input_units": "pulse", "output_units": {"U": "layout", "V": "layout", "F": "um"},
               "matrix": coefficients}
    if payload["schema_version"] == 2:
        payload["valid_axes"] = valid_axes
    return payload


class CalibrationGate:
    """A reset invalidates each source until that source has been replaced."""
    def __init__(self):
        self.lock = threading.RLock()
        self.sources = (None, None)
        self.blocked_sources = None
        self.basis = None

    def invalidate(self):
        with self.lock:
            self.blocked_sources = self.sources

    def observe(self, mapper, plane, config):
        with self.lock:
            basis = coordinate_basis(config)
            changed = self.basis is not None and basis != self.basis
            if changed:
                self.invalidate()
            self.basis = basis
            self.sources = mapper, plane
            if self.blocked_sources is not None:
                old_mapper, old_plane = self.blocked_sources
                mapper = None if mapper is old_mapper else mapper
                plane = None if plane is old_plane else plane
                if mapper is not None and plane is not None:
                    self.blocked_sources = None
            return calibration_payload(mapper, plane, config), changed


class SshCalibrationHTTP:
    """Use the serial tunnel's SSH target/config; JSON travels on stdin only."""
    def __init__(self, ssh_target):
        if not ssh_target or ssh_target.startswith("-") or any(c.isspace() for c in ssh_target):
            raise ValueError("Invalid SSH target")
        self.ssh_target = ssh_target

    def __call__(self, method, path, payload=None):
        if (method, path) not in {("GET", "/api/bridge/status"), ("PUT", "/api/bridge/calibration"), ("DELETE", "/api/bridge/calibration")}:
            raise ValueError("Unsupported calibration request")
        ssh = shutil.which("ssh")
        if not ssh:
            raise RuntimeError("Display calibration: OpenSSH unavailable")
        command = f"curl -sS --max-time 2 -X {method} -H 'Content-Type: application/json'"
        if payload is not None:
            command += " --data-binary @-"
        command += f" -w '\\n%{{http_code}}' http://127.0.0.1:5000{path}"
        result = subprocess.run([ssh, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2",
            "-o", "StrictHostKeyChecking=accept-new", self.ssh_target, command],
            input=json.dumps(payload, allow_nan=False) if payload is not None else "",
            text=True, capture_output=True, timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            # Do not expose stderr, command lines, host credentials or response bodies.
            raise RuntimeError("Display calibration SSH/HTTP request failed")
        body, _, status = result.stdout.strip().rpartition("\n")
        if status != "200":
            raise RuntimeError("Display calibration HTTP request was not accepted")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("Invalid display calibration response")
        return value


class CalibrationPublisher:
    """One serialized publisher, debounced updates, bounded retry, no Tk calls."""
    def __init__(self, request, report=lambda message: None, *, autostart=True):
        self.request, self.report = request, report
        self.condition = threading.Condition()
        self.desired = None
        self.revision = 0
        self.attempted = -1
        self.dirty = True
        self.urgent_delete = False
        self.closed = False
        self.session = None
        self.bound_session = None
        self.sent = object()
        self.last_error = False
        self.thread = threading.Thread(target=self._run, name="display-calibration", daemon=True)
        if autostart:
            self.thread.start()

    def submit(self, payload):
        with self.condition:
            if self.desired == payload or self.closed:
                return
            self.desired = payload
            self.revision += 1
            self.dirty = True
            self.condition.notify_all()

    def invalidate(self, *, wait=False):
        with self.condition:
            self.desired = None
            self.revision += 1
            revision = self.revision
            self.urgent_delete = True
            self.dirty = True
            self.condition.notify_all()
            if wait and self.thread.is_alive():
                self.condition.wait_for(lambda: self.attempted >= revision or self.closed, timeout=8)

    def close(self):
        # Detaching the raw bridge clears the receiver session. Never block Tk
        # waiting for a network request during disconnect/shutdown.
        with self.condition:
            self.closed = True
            self.condition.notify_all()

    def step(self):
        """One reconciliation attempt, also usable with a deterministic fake HTTP API."""
        with self.condition:
            if self.closed or not self.dirty:
                return True
            revision, desired, urgent = self.revision, self.desired, self.urgent_delete
        try:
            # An urgent revocation uses the already confirmed attached session
            # first; do not spend its deadline on an extra discovery request.
            if urgent and self.session:
                response = self.request("DELETE", "/api/bridge/calibration", {"session_id": self.session})
                if not response.get("ok"):
                    raise ValueError("Display revocation rejected")
                self.sent = None
            else:
                status = self.request("GET", "/api/bridge/status")
                session = status.get("session_id")
                if not session or not status.get("bridge_active") or not status.get("serial_connected"):
                    raise ValueError("Waiting for attached bridge session")
                if self.bound_session is not None and session != self.bound_session:
                    raise ValueError("Bridge owner changed; reconnect the serial transport")
                self.bound_session = session
                if session != self.session:
                    self.session, self.sent = session, object()
            with self.condition:
                if self.closed or revision != self.revision:
                    return False
            if self.sent != desired:
                response = self.request("DELETE", "/api/bridge/calibration", {"session_id": self.session})
                if not response.get("ok"):
                    raise ValueError("Display revocation rejected")
                self.sent = None
                with self.condition:
                    if self.closed or revision != self.revision:
                        return False
                if desired is not None:
                    response = self.request("PUT", "/api/bridge/calibration", {**desired, "session_id": self.session})
                    if not response.get("ok"):
                        raise ValueError("Display publication rejected")
                    self.sent = desired
            with self.condition:
                if revision == self.revision:
                    self.dirty = False
                    self.urgent_delete = False
            if self.last_error:
                self.report("Display calibration synchronization recovered.")
            self.last_error = False
            return True
        except Exception:
            self.session = None  # 409/reconnect/timeout: discover the current session on retry.
            if not self.last_error:
                self.report("Display calibration synchronization pending; serial control remains available.")
            self.last_error = True
            return False
        finally:
            with self.condition:
                self.attempted = max(self.attempted, revision)
                self.condition.notify_all()

    def _run(self):
        backoff = 1.0
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.dirty or self.closed)
                if self.closed:
                    return
                if not self.urgent_delete:
                    self.condition.wait(timeout=.2)
            success = self.step()
            with self.condition:
                if self.closed:
                    return
                if not success:
                    self.condition.wait(timeout=backoff)
                    backoff = min(10., backoff * 2)
                else:
                    backoff = 1.
