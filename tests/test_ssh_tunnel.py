import queue
import subprocess
import unittest
from unittest.mock import patch

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.serial_client import CommunicationTestResult
from semi_auto_probe.ssh_tunnel import RemoteBridgeStatus, SshSerialTunnel, SshTunnelError, build_ssh_tunnel_command, probe_remote_bridge_status


class FakeProcess:
    def poll(self):
        return None


class FlakyClient:
    def __init__(self) -> None:
        self.attempts = 0

    def open(self) -> None:
        self.attempts += 1
        if self.attempts < 3:
            raise OSError("listener not ready")


class RejectedRemoteSerialClient:
    port = "socket://127.0.0.1:49152"

    def communication_test(self) -> CommunicationTestResult:
        return CommunicationTestResult(False, "3A AA", "", "Timeout")


class SshTunnelTest(unittest.TestCase):
    def test_command_is_noninteractive_loopback_only_forward(self) -> None:
        command = build_ssh_tunnel_command(
            "ssh",
            "icalculate@100.77.247.59",
            49152,
            "127.0.0.1",
            9500,
            6.0,
        )

        self.assertEqual(command[0:3], ["ssh", "-N", "-T"])
        self.assertIn("BatchMode=yes", command)
        self.assertIn("ExitOnForwardFailure=yes", command)
        self.assertIn("127.0.0.1:49152:127.0.0.1:9500", command)
        self.assertEqual(command[-1], "icalculate@100.77.247.59")

    def test_wait_for_serial_open_retries_until_ssh_listener_is_ready(self) -> None:
        tunnel = SshSerialTunnel("user@example", connect_timeout=1.0)
        tunnel._process = FakeProcess()
        client = FlakyClient()

        tunnel.wait_for_serial_open(client)

        self.assertEqual(client.attempts, 3)

    def test_serial_url_is_bound_to_loopback(self) -> None:
        tunnel = SshSerialTunnel("user@example")
        tunnel.local_port = 49152

        self.assertEqual(tunnel.serial_url, "socket://127.0.0.1:49152")

    def test_remote_test_failure_preserves_actual_response(self) -> None:
        app = ProbeApp.__new__(ProbeApp)
        app.serial_client = RejectedRemoteSerialClient()
        app.result_queue = queue.Queue()

        app._comm_test_worker()

        result = app.result_queue.get_nowait()
        self.assertFalse(result.ok)
        self.assertIn("Timeout", result.message)
        self.assertIn("no response", result.message)

    @patch("semi_auto_probe.ssh_tunnel.subprocess.run")
    @patch("semi_auto_probe.ssh_tunnel.shutil.which", return_value="ssh")
    def test_status_probe_reads_exclusive_bridge_contract(self, _which, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                '{"service_ready":true,"serial_connected":true,"serial_port":"/dev/ttyUSB0",'
                '"bridge_active":false,"available":true,"control_owner":"local"}'
            ),
            stderr="",
        )

        status = probe_remote_bridge_status("user@example")

        self.assertEqual(
            status,
            RemoteBridgeStatus(
                controller_connected=True,
                bridge_active=False,
                available=True,
                serial_port="/dev/ttyUSB0",
            ),
        )
        remote_command = run.call_args.args[0][-1]
        self.assertIn("/api/bridge/status", remote_command)
        self.assertIn("/api/status", remote_command)

    @patch("semi_auto_probe.ssh_tunnel.subprocess.run")
    @patch("semi_auto_probe.ssh_tunnel.shutil.which", return_value="ssh")
    def test_status_probe_supports_existing_api_during_pi_upgrade(self, _which, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"connected":true,"port":"/dev/ttyUSB0"}',
            stderr="",
        )

        status = probe_remote_bridge_status("user@example")

        self.assertTrue(status.available)
        self.assertTrue(status.controller_connected)
        self.assertFalse(status.bridge_active)

    @patch("semi_auto_probe.ssh_tunnel.subprocess.run")
    @patch("semi_auto_probe.ssh_tunnel.shutil.which", return_value="ssh")
    def test_status_probe_reports_bounded_timeout(self, _which, run) -> None:
        run.side_effect = subprocess.TimeoutExpired(cmd=["ssh"], timeout=10)

        with self.assertRaisesRegex(SshTunnelError, "timed out"):
            probe_remote_bridge_status("user@example")

    def test_remote_status_message_distinguishes_pi_serial_and_session_state(self) -> None:
        disconnected = RemoteBridgeStatus(False, False, False)
        occupied = RemoteBridgeStatus(True, True, False, "/dev/ttyUSB0")

        self.assertIn("motor serial device is not connected", ProbeApp._remote_bridge_status_message(disconnected))
        self.assertIn("already in use", ProbeApp._remote_bridge_status_message(occupied))


if __name__ == "__main__":
    unittest.main()
