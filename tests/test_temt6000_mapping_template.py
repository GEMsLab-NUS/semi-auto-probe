"""TEMT6000 spatial-response template, tested with simulated hardware only."""
import csv
import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.auto_test import AutoTestFlowStep, measurement_flow_steps_from_cards, create_autotest_flow_card
from semi_auto_probe.config import ProbeConfig
from semi_auto_probe.dp100 import SimulatedDP100Driver
from semi_auto_probe.flow_graph import validate_flow_graph
from semi_auto_probe.flow_editor import MeasurementFlowEditor
from semi_auto_probe.keithley2450 import Keithley2450IVRunner
from semi_auto_probe.mapping import MappingSession, generate_mapping_plan, load_mapping, read_mapping_artifact
from semi_auto_probe.workflow_library import (
    TEMT6000_MAPPING_TEMPLATE, builtin_templates, cards_from_template,
    cards_from_workflow_payload, workflow_payload,
)
from test_mapping import FakeSerial, FakeInstrument, settings


class DetectorInstrument(FakeInstrument):
    def __init__(self, events, **kwargs):
        super().__init__(events, **kwargs)
        self.output_enabled = False
        self.bias_v = 5.0

    def write(self, command):
        super().write(command)
        if command == ":OUTP ON":
            self.output_enabled = True
        elif command == ":OUTP OFF":
            self.output_enabled = False

    def query(self, command):
        self.assert_read_output()
        super().query(command)
        return f"{self.bias_v},{self.reads * 1e-6},500000"

    def assert_read_output(self):
        if not self.output_enabled:
            raise AssertionError("Reading with detector bias disabled")


class TEMT6000MappingTemplateTests(unittest.TestCase):
    def flow(self, *, fast=False):
        cards = cards_from_template(TEMT6000_MAPPING_TEMPLATE)
        if fast:
            for card in cards:
                card.params["settle_s"] = "0"
        return measurement_flow_steps_from_cards(cards)

    def app(self):
        app = ProbeApp.__new__(ProbeApp)
        app.serial_client = FakeSerial()
        app.probe_config = ProbeConfig(wire_bond_enabled=True)
        app.autotest_stop_event = threading.Event()
        app.result_queue = queue.Queue()
        app.autotest_grid_rows = app.autotest_grid_cols = 3
        app._axis_move_timeout = lambda pulses, speed: 5
        app._dp100_driver = SimulatedDP100Driver()
        app._dp100_driver.connect()
        return app

    def run_map(self, folder, app, instrument):
        editor = object.__new__(MeasurementFlowEditor)
        editor.cards = cards_from_template(TEMT6000_MAPPING_TEMPLATE)
        for card in editor.cards:
            card.params["settle_s"] = "0"
        editor._ensure_boundary_nodes()
        plan = generate_mapping_plan(settings(measurement_flow=measurement_flow_steps_from_cards(editor.cards)), (1, 1, 1))
        ProbeApp._validate_mapping_workflow(plan.settings)
        with patch("semi_auto_probe.app.Keithley2450IVRunner", lambda: Keithley2450IVRunner(instrument)):
            app._mapping_worker(plan, folder, 10, dict.fromkeys("XYZ", 1))
        return load_mapping(folder)

    def test_template_defaults_connections_and_save_load(self):
        self.assertIn(TEMT6000_MAPPING_TEMPLATE, builtin_templates())
        flow = self.flow()
        self.assertEqual(validate_flow_graph(flow), ())
        self.assertEqual([step.type_id for step in flow], ["workflow_start", "laser_output", "constant_current", "workflow_end", "plot_panel"])
        laser, detector = flow[1:3]
        self.assertEqual(laser.params["_next"], detector.params["_node_id"])
        self.assertEqual((laser.params["peak_voltage_v"], laser.params["peak_current_a"]), ("3.0", "0.010"))
        self.assertEqual(laser.params["mapping_once"], "true")
        self.assertEqual(laser.params["settle_s"], "2")
        self.assertEqual(detector.params["output_terminal"], "front")
        self.assertEqual(detector.params["voltage_v"], "5.0")
        self.assertEqual(detector.params["current_limit_a"], "0.001")
        self.assertEqual(detector.params["sample_count"], "1")
        self.assertEqual(detector.params["nplc"], "0.1")
        self.assertEqual(detector.params["hold_during_mapping"], "true")
        restored = cards_from_workflow_payload(workflow_payload(cards_from_template(TEMT6000_MAPPING_TEMPLATE)))
        self.assertEqual(measurement_flow_steps_from_cards(restored), flow)

    def test_scan_holds_bias_and_laser_between_points_and_saves_current_map(self):
        app = self.app()
        laser = app._dp100_driver
        instrument = DetectorInstrument([])
        original_move = app.serial_client.move_relative
        move_checks = []
        def move(**kwargs):
            self.assertTrue(instrument.output_enabled)
            self.assertTrue(laser.commands[-1][1])
            read_count = instrument.reads
            original_move(**kwargs)
            self.assertEqual(instrument.reads, read_count)
            move_checks.append(kwargs["axis"].name)
        app.serial_client.move_relative = move
        with tempfile.TemporaryDirectory() as tmp:
            _, metadata, results = self.run_map(Path(tmp), app, instrument)
            self.assertEqual(metadata["status"], "completed")
            self.assertEqual(metadata["completed_points"], 9)
            self.assertEqual(instrument.events.count(":OUTP ON"), 1)
            self.assertEqual(instrument.events.count(":OUTP OFF"), 1)
            self.assertEqual(instrument.events.count("*RST"), 1)
            self.assertIn(":ROUT:TERM FRON", instrument.events)
            self.assertIn(":SOUR:VOLT:LEV 5", instrument.events)
            self.assertEqual(instrument.reads, 9)
            self.assertEqual([enabled for _, enabled in laser.commands], [True, False])
            self.assertFalse(laser.connected)
            self.assertFalse(instrument.output_enabled)
            self.assertTrue(move_checks)
            self.assertNotIn("Z", move_checks)
            metric = next(key for key in results[0, 0].workflow_data["metrics"] if key.endswith("mean_current_a"))
            self.assertAlmostEqual(results[0, 0].workflow_data["metrics"][metric], 1e-6, delta=1e-12)
            self.assertAlmostEqual(results[1, 2].workflow_data["metrics"][metric], 4e-6, delta=1e-12)
            self.assertAlmostEqual(results[2, 2].workflow_data["metrics"][metric], 9e-6, delta=1e-12)
            with (Path(tmp) / "samples.csv").open(newline="", encoding="utf-8") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 9)
            point_path = next(path for path in results[0, 0].workflow_data["artifacts"]
                              if "current" in Path(path).parts and path.endswith(".json"))
            payload = json.loads(read_mapping_artifact(Path(tmp), point_path))
            self.assertEqual(payload["bias_lifetime"], "mapping")
            self.assertEqual(payload["optical_response"]["wavelength_nm"], 650)
            from semi_auto_probe.temt6000 import TEMT6000Response
            power_key = metric.replace("mean_current_a", "estimated_irradiance_w_m2")
            self.assertAlmostEqual(results[1, 2].workflow_data["metrics"][power_key], TEMT6000Response().irradiance(4e-6))

    def test_dual_plot_load_and_inspect_same_node(self):
        import tkinter as tk
        from types import SimpleNamespace
        from semi_auto_probe.mapping_panel import MappingPanel
        from semi_auto_probe.temt6000 import TEMT6000Response
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.run_map(directory, self.app(), DetectorInstrument([]))
            root = tk.Tk()
            root.withdraw()
            try:
                panel = MappingPanel(root, get_scales=lambda: (1, 1, 1), record_corner=Mock(), start_run=Mock(), stop_run=Mock())
                with patch("semi_auto_probe.mapping_panel.filedialog.askopenfilename", return_value=str(directory / "mapping.json")):
                    panel.load()
                # An unrelated node with a much larger current must not contaminate the map.
                panel.results[1, 2].workflow_data["metrics"]["nodes/other/current/point: statistics.mean_current_a"] = 1
                panel._draw()
                self.assertEqual(len(panel.mapping_axes), 2)
                self.assertAlmostEqual(panel.values[1, 2], 4e-6)
                self.assertAlmostEqual(panel.irradiance_values[1, 2], TEMT6000Response().irradiance(4e-6))
                panel._inspect_point(SimpleNamespace(inaxes=panel.mapping_axes[1], xdata=2, ydata=1))
                self.assertIn("estimated irradiance", panel.detail.get())
                self.assertEqual(panel.finish("completed", "", directory), ("completed", ""))
                self.assertTrue((directory / "mapping.png").exists())
                with patch("semi_auto_probe.mapping_panel.filedialog.askopenfilename", return_value=str(directory / "mapping.json")):
                    panel.load()
                panel._draw()
                self.assertEqual(len(panel.mapping_axes), 2)
                self.assertAlmostEqual(panel.values[2, 2], 9e-6)
            finally:
                root.destroy()

    def test_stop_timeout_failed_bias_and_initialization_close_both_outputs(self):
        for failure in ("stop", "timeout", "bias", "initialization", "cleanup", "disk"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                app = self.app()
                laser = app._dp100_driver
                instrument = DetectorInstrument([], read_fail=2 if failure == "timeout" else None,
                    stop=app.autotest_stop_event if failure == "stop" else None,
                    write_fail=":OUTP ON" if failure == "initialization" else ":OUTP OFF" if failure == "cleanup" else None)
                if failure == "bias":
                    instrument.bias_v = .4
                if failure == "disk":
                    with patch.object(MappingSession, "sample", side_effect=OSError("disk full")):
                        _, metadata, _ = self.run_map(Path(tmp), app, instrument)
                else:
                    _, metadata, _ = self.run_map(Path(tmp), app, instrument)
                self.assertEqual(metadata["status"], "stopped" if failure == "stop" else "failed")
                self.assertEqual(instrument.events.count(":OUTP ON"), 1)
                self.assertIn(":OUTP OFF", instrument.events)
                self.assertEqual([enabled for _, enabled in laser.commands], [True, False])
                self.assertFalse(laser.connected)
                if failure == "bias":
                    self.assertIn("Fixed bias not maintained", metadata["error"])
                if failure == "cleanup":
                    self.assertIn("Output cleanup failed", metadata["error"])

    def test_conflicting_nodes_rejected_before_running_held_session(self):
        for conflict in (AutoTestFlowStep("iv", {}), AutoTestFlowStep("laser_pulse", {})):
            flow = self.flow() + (conflict,)
            # Keep graph connected so the check exercises the hardware conflict.
            flow[2].params["_next"] = "conflict"
            conflict.params.update(_node_id="conflict", _next="end")
            with self.assertRaisesRegex(ValueError, "exclusive|single constant laser"):
                ProbeApp._validate_mapping_workflow(settings(measurement_flow=flow))

    def test_editor_connects_dc_laser_in_flow_and_retains_light_input_support(self):
        editor = object.__new__(MeasurementFlowEditor)
        editor.cards = cards_from_template(TEMT6000_MAPPING_TEMPLATE)
        editor._ensure_boundary_nodes()
        editor.status_var = Mock()
        editor._changed = Mock()
        editor._connect("start", "next", "constant_laser")
        editor._connect("constant_laser", "next", "temt6000_current")
        self.assertEqual(editor._card("start").params["_next"], "constant_laser")
        self.assertEqual(validate_flow_graph(measurement_flow_steps_from_cards(editor.cards)), ())
        source = create_autotest_flow_card("laser_output", "light")
        target = create_autotest_flow_card("photo_iv", "photo")
        source.x, target.x = -320, 20
        target.params["_light_source"] = source.card_id
        editor.cards = [source, target]
        editor._ensure_boundary_nodes()
        self.assertEqual(editor._card("workflow_start").params["_next"], "photo")
        self.assertNotIn("_next", source.params)
        self.assertEqual(validate_flow_graph(measurement_flow_steps_from_cards(editor.cards)), ())
        route = editor._orthogonal_route(source, target, "waveform@signal", target_kind="waveform@light")
        self.assertEqual(route[0][1], source.y + editor._node_height(source) / 2)


if __name__ == "__main__":
    unittest.main()
