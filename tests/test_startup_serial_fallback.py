import unittest
from unittest.mock import Mock, patch

from semi_auto_probe.app import ProbeApp, SERIAL_MODE_LOCAL, SERIAL_MODE_REMOTE_SSH
from semi_auto_probe.ssh_tunnel import RemoteBridgeStatus


class DummyVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class StartupSerialFallbackTests(unittest.TestCase):
    def make_app(self):
        app = ProbeApp.__new__(ProbeApp)
        app.connection_mode_var = DummyVar(SERIAL_MODE_LOCAL)
        app.port_var = DummyVar("COM7")
        app.status_var = DummyVar()
        app._local_port_selection = "COM7"
        app._local_serial_ports = ["COM7"]
        app._startup_connection_pending = False
        app._startup_remote_generation = None
        app._startup_connection_errors = []
        app._startup_local_candidates = []
        app.remote_bridge_check_generation = 0
        app.remote_bridge_check_pending = False
        app.remote_bridge_status = None
        app.remote_bridge_status_checked_at = 0.0
        app.serial_client = None
        app.ssh_serial_tunnel = None
        app._apply_connection_mode_ui = Mock()
        app._apply_serial_ports = Mock()
        app._update_serial_port_style = Mock()
        app._close_serial_transport = Mock()
        return app

    @patch("semi_auto_probe.app.list_serial_ports", return_value=[])
    def test_no_com_ports_switches_to_ssh_preflight(self, _list_ports):
        app = self.make_app()

        def start_remote_check():
            app.remote_bridge_check_generation += 1

        app._start_remote_bridge_status_check = start_remote_check
        app._start_startup_connection()

        self.assertEqual(app.connection_mode_var.get(), SERIAL_MODE_REMOTE_SSH)
        self.assertTrue(app._startup_connection_pending)
        self.assertEqual(app._startup_remote_generation, 1)
        self.assertIn("no working COM controller", app.status_var.get())

    @patch("semi_auto_probe.app.list_serial_ports", return_value=["COM4", "COM9"])
    def test_discovered_com_port_is_used_without_ssh_fallback(self, _list_ports):
        app = self.make_app()
        app._start_remote_bridge_status_check = Mock()
        app.connect_serial = Mock(return_value=True)
        app.run_comm_test = Mock()

        app._start_startup_connection()

        self.assertEqual(app.connection_mode_var.get(), SERIAL_MODE_LOCAL)
        self.assertTrue(app._startup_connection_pending)
        app._apply_serial_ports.assert_called_once_with(["COM4", "COM9"])
        app._start_remote_bridge_status_check.assert_not_called()
        app.connect_serial.assert_called_once_with()
        app.run_comm_test.assert_called_once_with()

    def test_local_write_timeout_advances_to_ssh_instead_of_sticking_on_com(self):
        app = self.make_app()
        app.port_var.set("COM3")
        app._startup_connection_pending = True
        app._startup_local_candidates = []
        app._begin_startup_remote_fallback = Mock()

        app._handle_startup_local_failure("communication failed: Write timeout")

        app._close_serial_transport.assert_called_once_with()
        app._begin_startup_remote_fallback.assert_called_once_with()
        self.assertIn("COM3", app._startup_connection_errors[0])
        self.assertIn("Write timeout", app._startup_connection_errors[0])

    def test_failed_ssh_returns_to_local_with_error(self):
        app = self.make_app()
        app.connection_mode_var.set(SERIAL_MODE_REMOTE_SSH)
        app._startup_connection_pending = True
        app._startup_connection_errors = ["no local COM ports detected"]

        app._finish_startup_connection_error("SSH check failed: unreachable")

        self.assertEqual(app.connection_mode_var.get(), SERIAL_MODE_LOCAL)
        self.assertEqual(app.port_var.get(), "")
        self.assertFalse(app._startup_connection_pending)
        self.assertIn("ERROR", app.status_var.get())
        self.assertIn("unreachable", app.status_var.get())

    def test_ready_ssh_status_connects_and_runs_communication_test(self):
        app = self.make_app()
        app.connection_mode_var.set(SERIAL_MODE_REMOTE_SSH)
        app._startup_connection_pending = True
        app._startup_remote_generation = 4
        app.remote_bridge_check_generation = 4
        app.connect_serial = Mock(return_value=True)
        app.run_comm_test = Mock()

        status = RemoteBridgeStatus(
            controller_connected=True,
            bridge_active=False,
            available=True,
            serial_port="/dev/ttyUSB0",
        )
        app._handle_worker_event(("remote_bridge_status", 4, status, None))

        app.connect_serial.assert_called_once_with()
        app.run_comm_test.assert_called_once_with()
        self.assertTrue(app._startup_connection_pending)


if __name__ == "__main__":
    unittest.main()
