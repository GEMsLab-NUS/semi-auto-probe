from __future__ import annotations

import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.auto_test import FLOW_CATEGORIES, AutoTestPoint, autotest_flow_definitions_by_type, create_autotest_flow_card
from semi_auto_probe.flow_graph import validate_flow_graph
from semi_auto_probe.flow_editor import MeasurementFlowEditor
from semi_auto_probe.keithley2450 import Keithley2450IVRunner
from semi_auto_probe.keithley_timing import interpolate_drive_profile, parse_drive_profile, parse_drive_profile_document, repeat_drive_profile
from semi_auto_probe.measurement_workflow import AutoTestFlowStep
from semi_auto_probe.smu_settings import reduce_workflow_limit
from semi_auto_probe.workflow_library import (
    KEITHLEY_IT_CONSTANT_TEMPLATE,
    KEITHLEY_IT_WAVEFORM_TEMPLATE,
    builtin_templates,
    cards_from_template,
)


class FakeInstrument:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.reads = 0

    def write(self, command: str) -> None:
        self.events.append(command)

    def query(self, command: str) -> str:
        self.events.append(command)
        self.reads += 1
        return f"0.1,{-self.reads * 1e-9},100000"

    def close(self) -> None:
        self.events.append("close")


class KeithleyTimeTests(unittest.TestCase):
    def test_two_column_profile_parses_si_units_and_requires_order(self):
        points = parse_drive_profile("0,0;500m,100m;2,0", source_mode="voltage")
        self.assertEqual([(p.time_s, p.value) for p in points], [(0.0, 0.0), (0.5, 0.1), (2.0, 0.0)])
        current = parse_drive_profile('[[0, 0], [1, "10u"]]', source_mode="current")
        self.assertAlmostEqual(current[-1].value, 10e-6)
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            parse_drive_profile("0,0;1,1;1,0")

    def test_profile_values_are_linearly_interpolated_between_list_rows(self):
        points = parse_drive_profile("0,0;1,100m;5,100m;6,0;10,0", source_mode="voltage")
        self.assertAlmostEqual(interpolate_drive_profile(points, 0.5), 0.05)
        self.assertAlmostEqual(interpolate_drive_profile(points, 3), 0.1)
        self.assertAlmostEqual(interpolate_drive_profile(points, 5.5), 0.05)
        self.assertAlmostEqual(interpolate_drive_profile(points, 20), 0.0)

    def test_waveform_import_accepts_headers_csv_tsv_and_json_objects(self):
        csv_points = parse_drive_profile_document("Time (s),Value\n0,0\n1m,100m\n")
        tsv_points = parse_drive_profile_document("time\tvalue\n0\t0\n2\t1\n")
        json_points = parse_drive_profile_document('[{"time_s":0,"value":0},{"time_s":3,"value":0.5}]')
        self.assertEqual((csv_points[-1].time_s, csv_points[-1].value), (0.001, 0.1))
        self.assertEqual((tsv_points[-1].time_s, tsv_points[-1].value), (2.0, 1.0))
        self.assertEqual((json_points[-1].time_s, json_points[-1].value), (3.0, 0.5))

    def test_wfgmu_tabular_clipboard_profile_preserves_negative_values_and_edges(self):
        text = """0\t0
1.5\t0
2.0000001\t5
5.0000001\t5
5.5\t0
9.5\t0
10.0000003\t-5
13\t-5
13.0000001\t0
20.0000004\t0"""
        points = parse_drive_profile_document(text)
        self.assertEqual(len(points), 10)
        self.assertEqual(points[2], type(points[2])(2.0000001, 5.0))
        self.assertEqual(points[6], type(points[6])(10.0000003, -5.0))
        self.assertEqual(points[-1].time_s, 20.0000004)

    def test_waveform_cycles_repeat_profile_with_reset_edges(self):
        base = parse_drive_profile("0,0;1,1;2,0.5")
        points = repeat_drive_profile(base, 3)
        self.assertEqual(len(points), 9)
        self.assertEqual(points[-1].time_s, 6)
        self.assertEqual([point.value for point in points if point.time_s == 2], [0.5, 0.0])
        self.assertEqual(interpolate_drive_profile(points, 2), 0.0)
        self.assertAlmostEqual(interpolate_drive_profile(points, 2.5), 0.5)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            repeat_drive_profile(base, 0)

    def test_constant_and_waveform_templates_are_valid(self):
        self.assertIn(KEITHLEY_IT_CONSTANT_TEMPLATE, builtin_templates())
        self.assertIn(KEITHLEY_IT_WAVEFORM_TEMPLATE, builtin_templates())
        for name in (KEITHLEY_IT_CONSTANT_TEMPLATE, KEITHLEY_IT_WAVEFORM_TEMPLATE):
            cards = cards_from_template(name)
            flow = tuple(AutoTestFlowStep(card.type_id, {**card.params, "_node_id": card.card_id}) for card in cards)
            self.assertEqual(validate_flow_graph(flow), (), name)
        waveform_cards = cards_from_template(KEITHLEY_IT_WAVEFORM_TEMPLATE)
        measure = next(card for card in waveform_cards if card.type_id == "it")
        source = next(card for card in waveform_cards if card.type_id == "waveform")
        self.assertEqual(measure.params["_input_profile_source"], source.card_id)
        offered = {type_id for _key, _label, type_ids in FLOW_CATEGORIES for type_id in type_ids}
        self.assertNotIn("drive_waveform", offered)
        self.assertIn("waveform", offered)
        categories = {key: type_ids for key, _label, type_ids in FLOW_CATEGORIES}
        self.assertIn("waveform", categories["basic"])
        self.assertNotIn("waveform", categories["light"])

    def test_current_source_mode_builds_v_t_configuration(self):
        config, profile, duration, interval = ProbeApp._keithley_time_config({
            "source_mode": "current",
            "drive_mode": "constant",
            "source_value": "10u",
            "duration_s": "2",
            "sample_interval_s": "0.02",
            "voltage_limit_v": "5",
            "current_limit_a": "1m",
        })
        self.assertEqual(config.sweep_mode, "current")
        self.assertAlmostEqual(profile[0].value, 10e-6)
        self.assertEqual(duration, 2)
        self.assertEqual(interval, 0.02)
        self.assertEqual(config.voltage_limit_v, 5)

    def test_time_config_accepts_si_values_ranges_limits_and_time(self):
        config, profile, duration, interval = ProbeApp._keithley_time_config({
            "source_mode": "voltage",
            "drive_mode": "constant",
            "source_value": "100m",
            "duration_s": "2m",
            "sample_interval_s": "100u",
            "voltage_limit_v": "20",
            "current_limit_a": "10u",
            "source_range": "200m",
            "measure_range": "10u",
            "nplc": "1",
            "autozero": "false",
        })
        self.assertAlmostEqual(profile[0].value, 0.1)
        self.assertAlmostEqual(duration, 2e-3)
        self.assertAlmostEqual(interval, 100e-6)
        self.assertAlmostEqual(config.source_range or 0, 0.2)
        self.assertAlmostEqual(config.measure_range or 0, 10e-6)
        self.assertAlmostEqual(config.current_limit_a, 10e-6)
        self.assertFalse(config.autozero)

    def test_time_range_choices_are_editable_and_reduce_matching_limit(self):
        self.assertEqual(MeasurementFlowEditor._choices("it", "source_range"), ())
        self.assertEqual(MeasurementFlowEditor._choices("it", "measure_range"), ())
        adjusted = reduce_workflow_limit({
            "source_mode": "current",
            "measure_range": "2V",
            "voltage_limit_v": "20V",
        })
        self.assertEqual(adjusted, ("voltage_limit_v", "2"))

    def test_time_editor_normalizes_n_u_m_values_for_both_source_modes(self):
        editor = MeasurementFlowEditor.__new__(MeasurementFlowEditor)
        editor.definitions = autotest_flow_definitions_by_type()
        card = create_autotest_flow_card("it", "it_units")
        for key, raw, expected in (
            ("source_value", "100m", 0.1),
            ("current_limit_a", "10u", 10e-6),
            ("measure_range", "500n", 500e-9),
        ):
            canonical, error = editor._validate_parameter(card, key, raw)
            self.assertFalse(error, (key, error))
            self.assertAlmostEqual(float(canonical), expected)
        card.params["source_mode"] = "current"
        for key, raw, expected in (
            ("source_value", "500n", 500e-9),
            ("source_range", "1m", 1e-3),
            ("measure_range", "200m", 0.2),
        ):
            canonical, error = editor._validate_parameter(card, key, raw)
            self.assertFalse(error, (key, error))
            self.assertAlmostEqual(float(canonical), expected)

    def test_time_profile_rejects_value_outside_fixed_source_range(self):
        with self.assertRaisesRegex(ValueError, "exceeds the selected source range"):
            ProbeApp._keithley_time_config({
                "source_mode": "current",
                "drive_mode": "profile",
                "profile_points": "0,0;1m,2m",
                "duration_s": "2m",
                "sample_interval_s": "100u",
                "voltage_limit_v": "2V",
                "current_limit_a": "10mA",
                "source_range": "1mA",
                "measure_range": "2V",
            })

    def test_connected_waveform_replaces_unused_local_profile(self):
        cards = cards_from_template(KEITHLEY_IT_WAVEFORM_TEMPLATE)
        measure = next(card for card in cards if card.type_id == "it")
        measure.params["profile_points"] = ""
        flow = tuple(AutoTestFlowStep(card.type_id, {**card.params, "_node_id": card.card_id}) for card in cards)
        self.assertEqual(validate_flow_graph(flow), ())
        resolved = ProbeApp._resolve_keithley_time_profile(
            type("Settings", (), {"measurement_flow": flow})(),
            dict(measure.params),
        )
        self.assertTrue(resolved["profile_points"])
        self.assertIn("0.1", resolved["profile_points"])
        self.assertEqual(float(resolved["duration_s"]), 10.0)
        ProbeApp._keithley_time_config(resolved)

    def test_shared_waveform_maps_unitless_values_to_laser_levels(self):
        source = AutoTestFlowStep("waveform", {
            "_node_id": "shape",
            "profile_points": "0,0;1,0.5;2,1",
        })
        settings = type("Settings", (), {"measurement_flow": (source,)})()
        resolved = ProbeApp._resolve_shared_waveform(settings, {
            "_input_profile_source": "shape",
            "baseline_voltage_v": "1",
            "baseline_current_a": "0.01",
            "peak_voltage_v": "3",
            "peak_current_a": "0.03",
        })
        from semi_auto_probe.waveform import parse_waveform_points
        points = parse_waveform_points(resolved)
        self.assertEqual([point.time_ms for point in points], [0, 1000, 2000])
        self.assertEqual([point.voltage_v for point in points], [1, 2, 3])
        for actual, expected in zip((point.current_a for point in points), (0.01, 0.02, 0.03)):
            self.assertAlmostEqual(actual, expected)

    def test_profile_drive_changes_are_independent_from_sampling(self):
        instrument = FakeInstrument()
        app = ProbeApp.__new__(ProbeApp)
        app.autotest_stop_event = threading.Event()
        app.result_queue = queue.Queue()
        point = AutoTestPoint(0, 0, 1, "P1", 0, 0, 0, 0, ())
        params = {
            "resource": "GPIB0::18::INSTR",
            "output_terminal": "rear",
            "source_mode": "voltage",
            "drive_mode": "profile",
            "profile_points": "0,0;0.0015,0.2",
            "duration_s": "0.003",
            "sample_interval_s": "0.001",
            "voltage_limit_v": "20",
            "current_limit_a": "1e-3",
            "source_range": "auto",
            "measure_range": "auto",
            "nplc": "1",
            "autozero": "true",
            "output_off_after": "true",
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "semi_auto_probe.app.Keithley2450IVRunner",
            lambda: Keithley2450IVRunner(instrument),
        ):
            result = app._run_autotest_keithley_time(point, params, Path(tmp))
            payload = json.loads(next((Path(tmp) / "it").glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "done")
        self.assertGreaterEqual(instrument.events.count(":READ?"), 3)
        self.assertIn(":SOUR:VOLT:LEV 0.133333333333", instrument.events)
        self.assertIn(":SOUR:VOLT:LEV 0.2", instrument.events)
        self.assertEqual(payload["measurement"]["sample_interval_s"], 0.001)
        self.assertEqual(payload["measurement"]["profile_points"][1]["time_s"], 0.0015)
        self.assertEqual(instrument.events[-1], ":OUTP OFF")

    def test_new_probe_session_has_no_empty_result_subdirectories(self):
        app = ProbeApp.__new__(ProbeApp)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            app.autotest_session_root = Path(tmp)
            session = app._prepare_autotest_session_dir()
            self.assertTrue(session.is_dir())
            self.assertEqual(list(session.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
