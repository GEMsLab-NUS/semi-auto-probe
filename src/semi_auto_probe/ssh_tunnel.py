from __future__ import annotations

import collections
import json
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass


class SshTunnelError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteBridgeStatus:
    controller_connected: bool
    bridge_active: bool
    available: bool
    serial_port: str | None = None


def probe_remote_bridge_status(
    ssh_target: str,
    *,
    connect_timeout: float = 6.0,
) -> RemoteBridgeStatus:
    """Query bridge readiness through SSH without opening a bridge session."""

    ssh_executable = shutil.which("ssh")
    if not ssh_executable:
        raise SshTunnelError("OpenSSH client 'ssh' was not found in PATH.")

    timeout_seconds = max(1, int(round(connect_timeout)))
    remote_command = (
        "curl -fsS --max-time 3 http://127.0.0.1:5000/api/bridge/status "
        "|| curl -fsS --max-time 3 http://127.0.0.1:5000/api/status"
    )
    command = [
        ssh_executable,
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={timeout_seconds}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        ssh_target,
        remote_command,
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=connect_timeout + 4.0,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as exc:
        raise SshTunnelError("Raspberry Pi status check timed out.") from exc
    except OSError as exc:
        raise SshTunnelError(f"Unable to run OpenSSH status check: {exc}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"SSH exited with status {completed.returncode}"
        raise SshTunnelError(f"Raspberry Pi status check failed: {detail}")

    try:
        payload = json.loads(completed.stdout.strip())
    except (json.JSONDecodeError, TypeError) as exc:
        raise SshTunnelError("Raspberry Pi status API returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise SshTunnelError("Raspberry Pi status API returned an invalid payload.")

    controller_connected = bool(payload.get("serial_connected", payload.get("connected", False)))
    bridge_active = bool(payload.get("bridge_active", False))
    available = bool(payload.get("available", controller_connected and not bridge_active))
    serial_port_value = payload.get("serial_port", payload.get("port"))
    serial_port = str(serial_port_value) if serial_port_value else None
    return RemoteBridgeStatus(
        controller_connected=controller_connected,
        bridge_active=bridge_active,
        available=available,
        serial_port=serial_port,
    )


class SshSerialTunnel:
    """Own an OpenSSH local forward to a loopback-only serial bridge."""

    def __init__(
        self,
        ssh_target: str,
        *,
        remote_host: str = "127.0.0.1",
        remote_port: int = 9500,
        connect_timeout: float = 6.0,
    ) -> None:
        self.ssh_target = ssh_target
        self.remote_host = remote_host
        self.remote_port = int(remote_port)
        self.connect_timeout = float(connect_timeout)
        self.local_port: int | None = None
        self._process: subprocess.Popen[str] | None = None
        self._stderr_lines: collections.deque[str] = collections.deque(maxlen=20)
        self._stderr_thread: threading.Thread | None = None

    @property
    def serial_url(self) -> str:
        if self.local_port is None:
            raise SshTunnelError("SSH tunnel has not been started.")
        return f"socket://127.0.0.1:{self.local_port}"

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return

        ssh_executable = shutil.which("ssh")
        if not ssh_executable:
            raise SshTunnelError("OpenSSH client 'ssh' was not found in PATH.")

        self.local_port = _find_available_loopback_port()
        command = build_ssh_tunnel_command(
            ssh_executable,
            self.ssh_target,
            self.local_port,
            self.remote_host,
            self.remote_port,
            self.connect_timeout,
        )
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags,
            )
        except OSError as exc:
            self._process = None
            raise SshTunnelError(f"Unable to start OpenSSH: {exc}") from exc

        self._stderr_lines.clear()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def ensure_running(self) -> None:
        process = self._process
        if process is None:
            raise SshTunnelError("SSH tunnel has not been started.")
        return_code = process.poll()
        if return_code is not None:
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=0.2)
            detail = self.error_detail
            suffix = f": {detail}" if detail else ""
            raise SshTunnelError(f"SSH tunnel exited with status {return_code}{suffix}")

    @property
    def error_detail(self) -> str:
        return " ".join(self._stderr_lines).strip()

    def wait_for_serial_open(self, client) -> None:
        """Open a socket-backed serial client once the SSH listener is ready."""

        deadline = time.monotonic() + self.connect_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            self.ensure_running()
            try:
                client.open()
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.1)

        self.ensure_running()
        raise SshTunnelError(
            f"SSH local forward did not become ready within {self.connect_timeout:g}s"
            + (f": {last_error}" if last_error else ".")
        )

    def close(self) -> None:
        process = self._process
        self._process = None
        self.local_port = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        if process.stderr is not None:
            process.stderr.close()

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            stripped = line.strip()
            if stripped:
                self._stderr_lines.append(stripped)


def build_ssh_tunnel_command(
    ssh_executable: str,
    ssh_target: str,
    local_port: int,
    remote_host: str,
    remote_port: int,
    connect_timeout: float,
) -> list[str]:
    timeout_seconds = max(1, int(round(connect_timeout)))
    forwarding = f"127.0.0.1:{int(local_port)}:{remote_host}:{int(remote_port)}"
    return [
        ssh_executable,
        "-N",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        f"ConnectTimeout={timeout_seconds}",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=2",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-L",
        forwarding,
        ssh_target,
    ]


def _find_available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
