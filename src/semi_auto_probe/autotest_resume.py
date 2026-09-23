"""Crash-safe AutoTest checkpoints and conservative legacy session recovery."""
from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict
from pathlib import Path

from .auto_test import AutoTestFlowStep, AutoTestPointSpec, AutoTestSettings
from .keithley2450 import iv_sweep_configs_from_params

MANIFEST_NAME = "autotest_resume.json"
_FORMAT = "semi_auto_probe.autotest_resume"


def _identity(point):
    return {"order": int(point.order), "row": int(point.row), "col": int(point.col),
            "name": str(point.name), "u": float(point.u), "v": float(point.v)}


def _payload(settings, points, completed):
    return {"format": _FORMAT, "version": 1, "settings": json.loads(json.dumps(asdict(settings.normalized()))),
            "points": [_identity(point) for point in points],
            "completed": sorted(set(int(order) for order in completed))}


def _write(path, payload):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def create_manifest(session_dir: Path, settings: AutoTestSettings, points, completed=()):
    path = session_dir / MANIFEST_NAME
    if path.exists():
        raise FileExistsError(path)
    _write(path, _payload(settings, points, completed))
    return path


def read_manifest(session_dir: Path):
    path = session_dir / MANIFEST_NAME
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != _FORMAT or data.get("version") != 1:
        raise ValueError("Unsupported AutoTest resume manifest")
    if not isinstance(data.get("points"), list) or not isinstance(data.get("completed"), list):
        raise ValueError("Invalid AutoTest resume manifest")
    return data


def settings_from_manifest(data):
    values = dict(data["settings"])
    values["measurement_steps"] = tuple(values.get("measurement_steps", ()))
    values["measurement_flow"] = tuple(
        AutoTestFlowStep(item["type_id"], item["params"])
        for item in values.get("measurement_flow", ())
    )
    return AutoTestSettings(**values).normalized()


def point_specs_from_manifest(data):
    return tuple(AutoTestPointSpec(item["name"], item["u"], item["v"], item["row"], item["col"])
                 for item in data["points"])


def validate_manifest(data, settings, points):
    expected = _payload(settings, points, ())
    if data["settings"] != expected["settings"] or data["points"] != expected["points"]:
        raise ValueError("Current AutoTest settings or point list differ from the saved session")


def _history_done(session_dir, point):
    path = session_dir / "workflow_plots" / f"{point.row}_{point.col}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    recorded = data.get("point", {}) if isinstance(data, dict) else {}
    if not isinstance(recorded, dict):
        return False
    return (isinstance(data, dict) and data.get("status") == "done" and recorded.get("row") == point.row
            and recorded.get("col") == point.col)


def _matches_iv_config(measurement, config):
    if not isinstance(measurement, dict):
        return False
    for key in ("output_terminal", "sweep_mode", "start", "stop", "step", "low",
                "segments", "source_range", "measure_range", "voltage_limit_v", "current_limit_a"):
        expected = getattr(config, key)
        actual = measurement.get(key, object())
        if expected is None:
            if actual is not None:
                return False
        elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
            try:
                if not math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-12):
                    return False
            except (TypeError, ValueError):
                return False
        elif actual != expected:
            return False
    return measurement.get("resource") == config.resource_name


def _validate_legacy_iv_identity(session_dir, completed, settings):
    configs = tuple(config for step in settings.measurement_flow if step.type_id == "iv"
                    for config in iv_sweep_configs_from_params(step.params))
    metadata = []
    for path in (session_dir / "iv").glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if item.get("result_type") == "iv_sweep":
                metadata.append(item)
        except (OSError, ValueError, AttributeError):
            continue
    for point in completed:
        def matches(item):
            device = item.get("device", {})
            gds = item.get("coordinates", {}).get("gds") or {}
            return (device.get("name") == point.name and device.get("order") == point.order
                    and device.get("row") == point.row and device.get("col") == point.col
                    and math.isclose(float(gds.get("u", math.nan)), point.u, abs_tol=1e-8)
                    and math.isclose(float(gds.get("v", math.nan)), point.v, abs_tol=1e-8))
        if not any(matches(item) and any(_matches_iv_config(item.get("measurement"), config) for config in configs)
                   for item in metadata):
            raise ValueError(f"Cannot verify completed IV result or sweep settings for {point.name} in this older session")


def completed_prefix(session_dir: Path, settings, points, manifest=None):
    """Only a continuous prefix with a durable completed result may be skipped."""
    has_plot = any(step.type_id == "plot_panel" for step in settings.measurement_flow)
    if has_plot and len({(point.row, point.col) for point in points}) != len(points):
        raise ValueError("Cannot resume Plot Panel history with duplicate row/column positions")
    if manifest is None and not has_plot:
        raise ValueError("This older session has no resume manifest or Plot Panel history")
    if manifest is not None:
        validate_manifest(manifest, settings, points)
    marked = set(manifest["completed"]) if manifest else set()
    completed = []
    for point in points:
        done = _history_done(session_dir, point) if has_plot else point.order in marked
        if not done:
            break
        completed.append(point)
    if manifest is None and not completed:
        raise ValueError("Older session has no completed Plot Panel points to verify")
    if manifest is None and any(step.type_id == "iv" for step in settings.measurement_flow):
        _validate_legacy_iv_identity(session_dir, completed, settings)
    return tuple(completed)


def mark_completed(session_dir: Path, point):
    data = read_manifest(session_dir)
    if data is None:
        raise FileNotFoundError(session_dir / MANIFEST_NAME)
    if _identity(point) not in data["points"]:
        raise ValueError("Completed point is not in the saved AutoTest plan")
    data["completed"] = sorted(set(data["completed"]) | {int(point.order)})
    _write(session_dir / MANIFEST_NAME, data)
