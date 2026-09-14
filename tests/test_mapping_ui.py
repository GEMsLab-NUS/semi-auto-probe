"""Exercise the real Tk mode integration using simulated motion and VISA."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.auto_test import create_autotest_flow_card
from semi_auto_probe.config import load_probe_config
from semi_auto_probe.keithley2450 import Keithley2450IVRunner
from semi_auto_probe.mapping import read_mapping_artifact
from test_mapping import FakeInstrument, FakeSerial, settings


class MappingUITests(unittest.TestCase):
    def test_mapping_mode_completes_without_camera_layout_or_focusmap(self):
        previous = Path.cwd()
        events = []
        app = None
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                with patch.object(ProbeApp, "start_camera"), patch.object(ProbeApp, "connect_and_test_serial"), \
                     patch.object(ProbeApp, "_maximize_default_window", lambda instance: instance.withdraw()), \
                     patch("semi_auto_probe.app.Keithley2450IVRunner", lambda: Keithley2450IVRunner(FakeInstrument(events))):
                    app = ProbeApp()
                    self.assertNotIn("autotest_modes", app.__dict__)
                    self.assertFalse(app.probe_config.wire_bond_enabled)
                    app.wire_bond_enabled_var.set(True)
                    app._change_wire_bond_mode()
                    self.assertFalse(app.probe_config.wire_bond_enabled)
                    self.assertFalse(app.wire_bond_enabled_var.get())
                    card = create_autotest_flow_card("constant_current", "current_node")
                    card.params.update(sample_count="2", settle_s="0")
                    app.autotest_panel.measurement_flow_cards[:] = [card]
                    app.autotest_panel._update_measurement_summary()
                    app.admin_mode_enabled = True
                    app.wire_bond_enabled_var.set(True)
                    app._change_wire_bond_mode()
                    self.assertTrue(app.probe_config.wire_bond_enabled)
                    self.assertTrue(json.loads(app.config_path.read_text())["wire_bond_enabled"])
                    self.assertTrue(load_probe_config(app.config_path).wire_bond_enabled)
                    with patch("semi_auto_probe.app.save_probe_config", side_effect=OSError("disk full")):
                        app.wire_bond_enabled_var.set(False)
                        app._change_wire_bond_mode()
                    self.assertTrue(app.probe_config.wire_bond_enabled)
                    self.assertTrue(app.wire_bond_enabled_var.get())
                    self.assertEqual(app.mapping_panel.settings().measurement_flow, app.autotest_panel.measurement_flow())
                    # An enabled mode remains usable by non-admin operators.
                    app.admin_mode_enabled = False
                    app.serial_client = FakeSerial()
                    app.serial_client.set_admin_mode_enabled = Mock()
                    app._axis_move_timeout = lambda pulses, speed: 5
                    app.start_autotest(settings())
                    self.assertTrue(app.mapping_running)
                    self.assertEqual(str(app.workflow_button.cget("state")), "disabled")
                    app.admin_mode_enabled = True
                    app.wire_bond_enabled_var.set(False)
                    app._change_wire_bond_mode()
                    self.assertTrue(app.probe_config.wire_bond_enabled)
                    app.admin_mode_enabled = False
                    deadline = time.monotonic() + 15
                    while app.mapping_running and time.monotonic() < deadline:
                        app.update()
                        time.sleep(.01)
                    self.assertFalse(app.mapping_running)
                    self.assertFalse(app.motion_busy)
                    self.assertEqual(str(app.workflow_button.cget("state")), "normal")
                    self.assertEqual(str(app.wire_bond_mode_switch.cget("state")), "disabled")
                    self.assertIn("completed", app.mapping_panel.status.get())
                    directory = app.mapping_panel.directory
                    self.assertTrue((directory / "mapping.png").is_file())
                    self.assertEqual(json.loads((directory / "mapping.json").read_text())["completed_points"], 9)
                    self.assertFalse(any(move[0].name == "Z" for move in app.serial_client.moves))
                    self.assertEqual(events.count(":OUTP ON"), 9)
                    self.assertEqual(events[-1], ":OUTP OFF")
                    self.assertTrue(any(key.endswith("mean_current_a") for key in app.mapping_panel.metric_picker.cget("values")))
                    self.assertTrue((directory / "mapping_artifacts.sqlite3").is_file())
                    self.assertFalse((directory / "points").exists())
                    current_csvs = [
                        path for result in app.mapping_panel.results.values()
                        for path in result.workflow_data["artifacts"]
                        if "current" in Path(path).parts and path.endswith(".csv")
                    ]
                    self.assertEqual(len(current_csvs), 9)
                    self.assertTrue(read_mapping_artifact(directory, current_csvs[0]))
                    self.assertEqual(app.autotest_panel.measurement_flow_cards, [card])
                    with patch("semi_auto_probe.mapping_panel.filedialog.askopenfilename", return_value=str(directory / "mapping.json")):
                        app.mapping_panel.load()
                    self.assertEqual(app.mapping_panel._completed, 9)
                    self.assertAlmostEqual(app.mapping_panel.values[1, 2], -1.5e-9)
                    app.admin_mode_enabled = True
                    app.wire_bond_enabled_var.set(False)
                    app._change_wire_bond_mode()
                    self.assertFalse(app.probe_config.wire_bond_enabled)
                    self.assertEqual(app.autotest_panel.measurement_flow_cards, [card])
                    app.serial_client = None
            finally:
                if app is not None:
                    app.serial_client = None
                    app.destroy()
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
