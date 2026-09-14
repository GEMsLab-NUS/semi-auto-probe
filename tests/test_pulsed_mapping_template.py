import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np
from matplotlib.figure import Figure
from types import SimpleNamespace
from unittest.mock import patch

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.auto_test import measurement_flow_steps_from_cards
from semi_auto_probe.auto_test import AutoTestFlowStep
from semi_auto_probe.config import ProbeConfig
from semi_auto_probe.dp100 import SimulatedDP100Driver
from semi_auto_probe.keithley2450 import Keithley2450IVRunner
from semi_auto_probe.mapping import generate_mapping_plan, load_mapping, read_mapping_artifact
from semi_auto_probe.mapping_panel import MappingPanel
from semi_auto_probe.mapping_plot import draw_laser_calibration_figure, draw_workflow_heatmap_figure
from semi_auto_probe.waveform import calibrated_response_program
from semi_auto_probe.workflow_library import TEMT6000_PULSED_MAPPING_TEMPLATE, builtin_templates, cards_from_template
from test_mapping import FakeSerial, settings
from test_temt6000_mapping_template import DetectorInstrument


class PulsedMappingTemplateTests(unittest.TestCase):
    def test_default_program_has_requested_power_and_width_sequences(self):
        calibration, power, width, heatmap = [c for c in cards_from_template(TEMT6000_PULSED_MAPPING_TEMPLATE) if c.type_id not in {"workflow_start", "workflow_end"}]
        params = {**power.params, **width.params, "response_phase": "both"}
        points, events = calibrated_response_program(params, [(.001, 2.02), (.025, 2.5)])
        power_events = [event for event in events if event["phase"] == "power"]
        widths = [event for event in events if event["phase"] == "width"]
        expected_up = np.linspace(2.02, 62.5, 25)
        self.assertEqual(len(power_events), 49)
        np.testing.assert_allclose([event["target_power_mw"] for event in power_events[:25]], expected_up)
        np.testing.assert_allclose([event["target_power_mw"] for event in power_events[25:]], expected_up[-2::-1])
        self.assertEqual([event["width_ms"] for event in widths], [100, 200, 300, 400, 500])
        self.assertTrue(all(abs(event["target_power_mw"] - (2.02 + 62.5) / 2) < 1e-12 for event in widths))
        self.assertEqual(points[-1].time_ms, 65400)
        self.assertEqual({point.current_a for point in points if point.voltage_v > 0}, {.025})
        self.assertEqual({event["drive_current_limit_a"] for event in events}, {.025})
        self.assertEqual(
            [card.type_id for card in (calibration, power, width, heatmap)],
            ["laser_iv_calibration", "photo_power_steps", "photo_width_steps", "plot_panel"],
        )
        self.assertEqual(power.params["_next"], width.card_id)
        self.assertEqual(power.params["_input_calibration_source"], calibration.card_id)
        self.assertEqual(width.params["_input_calibration_source"], calibration.card_id)
        self.assertEqual(heatmap.params["_input_slot3_source"], power.card_id)
        self.assertEqual(heatmap.params["_input_slot4_source"], width.card_id)

    def test_template_calibrates_once_runs_two_serial_tests_and_publishes_heatmaps(self):
        cards = cards_from_template(TEMT6000_PULSED_MAPPING_TEMPLATE)
        calibration, power, width, heatmap = [c for c in cards if c.type_id not in {"workflow_start", "workflow_end"}]
        calibration.params.update(
            calibration_current_min_a=".001", calibration_current_max_a=".003",
            calibration_current_step_a=".001", calibration_settle_ms="0",
            readback_timeout_s=".2", readback_interval_ms="1",
        )
        power.params.update(
            power_min_mw="auto", power_max_mw="auto", power_levels="3",
            power_width_ms="20", pulse_gap_ms="20", program_lead_ms="20",
            sample_interval_ms="2", nplc=".01",
        )
        width.params.update(
            width_power_mw="auto", width_min_ms="20", width_max_ms="40", width_step_ms="20",
            pulse_gap_ms="20", program_lead_ms="20", sample_interval_ms="2", nplc=".01",
        )
        flow = measurement_flow_steps_from_cards(cards)
        plan = generate_mapping_plan(settings(x_b_um=1, y_b_um=0, measurement_flow=flow), (1, 1, 1))
        ProbeApp._validate_mapping_workflow(plan.settings)
        app = ProbeApp.__new__(ProbeApp)
        app.serial_client = FakeSerial()
        app.probe_config = ProbeConfig(wire_bond_enabled=True)
        app.autotest_stop_event = threading.Event()
        app.result_queue = queue.Queue()
        app.autotest_grid_rows, app.autotest_grid_cols = 1, 2
        app._axis_move_timeout = lambda *_: 5
        timeline = []
        app._dp100_driver = SimulatedDP100Driver(
            on_change=lambda setpoint, enabled: timeline.append(("laser", setpoint.voltage_v, setpoint.current_a, enabled)))
        app._dp100_driver.connect()
        laser_driver = app._dp100_driver
        app.mapping_running = True
        detector = DetectorInstrument(timeline)
        with tempfile.TemporaryDirectory() as tmp, patch(
                "semi_auto_probe.app.Keithley2450IVRunner", lambda: Keithley2450IVRunner(detector)):
            directory = Path(tmp)
            app._mapping_worker(plan, directory, 100, dict.fromkeys("XYZ", 1))
            _, metadata, results = load_mapping(directory)
            self.assertEqual(metadata["status"], "completed", metadata.get("error"))
            self.assertEqual(len(results), 2)
            self.assertTrue((directory / "laser_iv_calibration.csv").is_file())
            self.assertTrue((directory / "laser_iv_pv_calibration.png").is_file())
            self.assertTrue((directory / "laser_response_program_power.json").is_file())
            self.assertTrue((directory / "laser_response_program_width.json").is_file())
            curve_paths = [
                path for result in results.values() for path in result.workflow_data["artifacts"]
                if "photo_transient" in Path(path).parts and path.endswith(".csv")
            ]
            self.assertEqual(len(curve_paths), 4)
            self.assertTrue(all(len(read_mapping_artifact(directory, path)) > 100 for path in curve_paths))
            metrics = results[0, 0].workflow_data["metrics"]
            self.assertTrue(any(key.endswith("power_sensitivity_a_per_mw") for key in metrics))
            self.assertTrue(any(key.endswith("width_sensitivity_a_per_s") for key in metrics))
            saved = json.loads((directory / "workflow_plots" / "0_0.json").read_text())
            self.assertIsInstance(saved["values"]["3"], float)
            self.assertIsInstance(saved["values"]["4"], float)
            panel = SimpleNamespace(directory=directory, _it_curve_paths=lambda _result: [])
            linked_curves = MappingPanel._it_curve_series(panel, results[0, 0])
            self.assertEqual(len(linked_curves), 2)
            enabled = [setpoint for setpoint, state in laser_driver.commands if state]
            self.assertEqual(sum(setpoint.current_ma in {1, 2, 3} and setpoint.voltage_mv == 3300 for setpoint in enabled), 3)
            bias_on = timeline.index(":OUTP ON")
            first_calibrated_pulse = next(index for index, event in enumerate(timeline)
                                          if isinstance(event, tuple) and event[0] == "laser" and event[3]
                                          and event[1] < 3.3)
            self.assertGreater(first_calibrated_pulse, bias_on)
            self.assertLess(timeline.index(":READ?"), first_calibrated_pulse)

    def test_paired_heatmaps_and_calibration_curves_render_as_two_axes(self):
        plan = generate_mapping_plan(settings(x_b_um=1, y_b_um=1), (1, 1, 1))
        states = np.ones((2, 2), dtype=int)
        primary = np.array([[1, 2], [3, 4]], dtype=float) * 1e-6
        secondary = np.array([[4, 3], [2, 1]], dtype=float) * 1e-6

        heatmap_axes = draw_workflow_heatmap_figure(
            Figure(figsize=(9, 4)), plan, primary, secondary, states,
            primary_label="Power response (A)", secondary_label="Width response (A)",
        )
        calibration_axes = draw_laser_calibration_figure(
            Figure(figsize=(9, 4)),
            [
                {"effective_current_a": .001, "measured_voltage_v": 1.9, "regulation_mode": "current_limit"},
                {"effective_current_a": .002, "measured_voltage_v": 2.1, "regulation_mode": "current_limit"},
            ],
            [(.001, 1.9), (.002, 2.1)],
        )

        self.assertEqual(len(heatmap_axes), 2)
        self.assertEqual([axis.get_aspect() for axis in heatmap_axes], [1, 1])
        self.assertEqual([axis.get_title() for axis in heatmap_axes], ["Power response (A)", "Width response (A)"])
        self.assertEqual(len(calibration_axes), 2)
        self.assertEqual([axis.get_title() for axis in calibration_axes], ["Laser I-V calibration", "Laser P-V calibration"])

    def test_calibration_retries_stale_zero_readback_before_failing(self):
        class StaleDriver(SimulatedDP100Driver):
            def __init__(self):
                super().__init__()
                self.read_count = 0
            def read_output(self, timeout=1.0):
                self.read_count += 1
                if self.read_count <= 2:
                    from semi_auto_probe.dp100 import DP100Reading
                    return DP100Reading(0, 0)
                return super().read_output(timeout)

        calibration = next(c for c in cards_from_template(TEMT6000_PULSED_MAPPING_TEMPLATE) if c.type_id == "laser_iv_calibration")
        calibration.params.update(
            calibration_current_max_a=".001", calibration_settle_ms="0",
            readback_timeout_s=".2", readback_interval_ms="1",
        )
        app = ProbeApp.__new__(ProbeApp)
        app.autotest_stop_event = threading.Event()
        app._mapping_laser_calibrations = {}
        app._dp100_driver = StaleDriver()
        app._dp100_driver.connect()
        app.mapping_running = True
        with tempfile.TemporaryDirectory() as tmp:
            values, records = app._get_or_run_laser_calibration(calibration.params, Path(tmp))
        self.assertEqual(len(values), 1)
        self.assertEqual(records[0]["readback_attempts"], 3)

    def test_current_limit_mode_uses_target_current_despite_quantized_readback(self):
        class QuantizedDriver(SimulatedDP100Driver):
            def read_output(self, timeout=1.0):
                from semi_auto_probe.dp100 import DP100Reading
                target_ma = self.last_setpoint.current_ma
                return DP100Reading(1.897 + .03 * target_ma, max(0, target_ma - 1) / 1000)

        calibration = next(c for c in cards_from_template(TEMT6000_PULSED_MAPPING_TEMPLATE) if c.type_id == "laser_iv_calibration")
        calibration.params.update(
            calibration_current_min_a=".009", calibration_current_max_a=".010",
            calibration_current_step_a=".001", calibration_settle_ms="0",
            readback_timeout_s=".02", readback_interval_ms="1",
        )
        app = ProbeApp.__new__(ProbeApp)
        app.autotest_stop_event = threading.Event()
        app._mapping_laser_calibrations = {}
        app._dp100_driver = QuantizedDriver()
        app._dp100_driver.connect()
        app.mapping_running = True
        with tempfile.TemporaryDirectory() as tmp:
            values, records = app._get_or_run_laser_calibration(calibration.params, Path(tmp))

        self.assertEqual(values[-1][0], .010)
        self.assertEqual(records[-1]["measured_current_a"], .009)
        self.assertAlmostEqual(records[-1]["effective_current_a"], .010)
        self.assertEqual(records[-1]["regulation_mode"], "current_limit")

    def test_voltage_limit_mode_uses_measured_current_and_curve_is_monotonic(self):
        class CeilingDriver(SimulatedDP100Driver):
            def read_output(self, timeout=1.0):
                from semi_auto_probe.dp100 import DP100Reading
                target_ma = self.last_setpoint.current_ma
                if target_ma <= 2:
                    return DP100Reading(2.2 - .1 * target_ma, target_ma / 1000)
                return DP100Reading(3.3, .002)

        calibration = next(c for c in cards_from_template(TEMT6000_PULSED_MAPPING_TEMPLATE) if c.type_id == "laser_iv_calibration")
        calibration.params.update(
            calibration_current_min_a=".001", calibration_current_max_a=".004",
            calibration_current_step_a=".001", calibration_settle_ms="0",
            readback_timeout_s=".02", readback_interval_ms="1",
        )
        app = ProbeApp.__new__(ProbeApp)
        app.autotest_stop_event = threading.Event()
        app._mapping_laser_calibrations = {}
        app._dp100_driver = CeilingDriver()
        app._dp100_driver.connect()
        app.mapping_running = True
        with tempfile.TemporaryDirectory() as tmp:
            values, records = app._get_or_run_laser_calibration(calibration.params, Path(tmp))

        self.assertEqual(records[-1]["regulation_mode"], "voltage_limit")
        self.assertEqual(records[-1]["effective_current_a"], .002)
        self.assertEqual([current for current, _voltage in values], [.001, .002])
        self.assertEqual([voltage for _current, voltage in values], sorted(voltage for _current, voltage in values))

    def test_one_ma_calibration_accepts_zero_ma_quantized_readback_with_live_voltage(self):
        class QuantizedDriver(SimulatedDP100Driver):
            def read_output(self, timeout=1.0):
                from semi_auto_probe.dp100 import DP100Reading
                if self.last_setpoint.current_ma == 1:
                    return DP100Reading(1.896, 0)
                return super().read_output(timeout)

        calibration = next(c for c in cards_from_template(TEMT6000_PULSED_MAPPING_TEMPLATE) if c.type_id == "laser_iv_calibration")
        calibration.params.update(
            calibration_current_max_a=".002", calibration_settle_ms="0",
            readback_timeout_s=".02", readback_interval_ms="1",
        )
        app = ProbeApp.__new__(ProbeApp)
        app.autotest_stop_event = threading.Event()
        app._mapping_laser_calibrations = {}
        app._dp100_driver = QuantizedDriver()
        app._dp100_driver.connect()
        app.mapping_running = True
        with tempfile.TemporaryDirectory() as tmp:
            values, records = app._get_or_run_laser_calibration(calibration.params, Path(tmp))

        self.assertEqual(values[0], (.001, 1.896))
        self.assertEqual(records[0]["target_current_a"], .001)
        self.assertEqual(records[0]["measured_current_a"], 0)
        self.assertEqual(values[1][0], .002)

    def test_persistent_zero_readback_reports_output_or_callback_hint(self):
        class ZeroDriver(SimulatedDP100Driver):
            def read_output(self, timeout=1.0):
                from semi_auto_probe.dp100 import DP100Reading
                return DP100Reading(0, 0)

        calibration = next(c for c in cards_from_template(TEMT6000_PULSED_MAPPING_TEMPLATE) if c.type_id == "laser_iv_calibration")
        calibration.params.update(
            calibration_current_max_a=".001", calibration_settle_ms="0",
            readback_timeout_s=".01", readback_interval_ms="1",
        )
        app = ProbeApp.__new__(ProbeApp)
        app.autotest_stop_event = threading.Event()
        app._mapping_laser_calibrations = {}
        app._dp100_driver = ZeroDriver()
        app._dp100_driver.connect()
        app.mapping_running = True
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(
                RuntimeError, "continued to report output OFF.*load connection.*DLL callback"):
            app._get_or_run_laser_calibration(calibration.params, Path(tmp))

    def test_template_is_registered(self):
        self.assertIn(TEMT6000_PULSED_MAPPING_TEMPLATE, builtin_templates())

    def test_held_transient_reserves_its_2450_resource(self):
        cards = cards_from_template(TEMT6000_PULSED_MAPPING_TEMPLATE)
        flow = list(measurement_flow_steps_from_cards(cards))
        next(s for s in flow if s.type_id == "photo_width_steps").params["_next"] = "other_current"
        flow.append(AutoTestFlowStep("constant_current", {
            "_node_id": "other_current", "_next": "end", "resource_name": "GPIB0::18::INSTR"
        }))
        with self.assertRaisesRegex(ValueError, "exclusive"):
            ProbeApp._validate_mapping_workflow(settings(measurement_flow=tuple(flow)))


if __name__ == "__main__":
    unittest.main()
