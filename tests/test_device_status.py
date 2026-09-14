import unittest
from unittest.mock import Mock

from semi_auto_probe.device_status import scan_visa


class DeviceStatusTests(unittest.TestCase):
    def manager(self):
        manager = Mock()
        manager.list_resources.return_value = ("GPIB0::18::INSTR", "ASRL1::INSTR")
        manager.open_bare_resource.return_value = (123, 0)
        manager.visalib.read.return_value = (b"KEITHLEY,2450,123,1.0\n", 0)
        return manager

    def test_identifies_device_without_reset_or_output_commands(self):
        manager = self.manager()
        rows = scan_visa(["GPIB0::18::INSTR"], manager_factory=lambda: manager)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "Connected")
        self.assertIn("2450", rows[0]["device"])
        manager.visalib.write.assert_called_once_with(123, b"*IDN?\n")
        manager.visalib.close.assert_called_once_with(123)
        manager.close.assert_called_once()

    def test_allocation_failure_retains_address_and_error(self):
        manager = self.manager()
        manager.open_bare_resource.side_effect = RuntimeError("VI_ERROR_ALLOC (-1073807300)")
        rows = scan_visa(manager_factory=lambda: manager)
        self.assertEqual(rows[0]["address"], "GPIB0::18::INSTR")
        self.assertEqual(rows[0]["status"], "Connection failed")
        self.assertIn("VI_ERROR_ALLOC", rows[0]["detail"])
        manager.visalib.write.assert_not_called()
        manager.close.assert_called_once()

    def test_query_failure_still_releases_session(self):
        manager = self.manager()
        manager.visalib.read.side_effect = RuntimeError("timeout")
        rows = scan_visa(manager_factory=lambda: manager)
        self.assertEqual(rows[0]["status"], "Session open; identity unverified")
        manager.visalib.close.assert_called_once_with(123)
        manager.close.assert_called_once()

    def test_discovery_failure_still_checks_configured_address(self):
        manager = self.manager()
        manager.list_resources.side_effect = RuntimeError("discovery failed")
        rows = scan_visa(["GPIB0::18::INSTR"], manager_factory=lambda: manager)
        self.assertEqual(rows[0]["status"], "Error")
        self.assertEqual(rows[1]["status"], "Connected")

    def test_missing_backend_is_reported(self):
        rows = scan_visa(manager_factory=Mock(side_effect=RuntimeError("no backend")))
        self.assertEqual(rows[0]["status"], "Unavailable")


if __name__ == "__main__":
    unittest.main()
