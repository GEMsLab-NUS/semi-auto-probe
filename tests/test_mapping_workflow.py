"""Shared workflow execution, result provenance and failure handling (no hardware)."""
import csv
import json
import queue
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.auto_test import AutoTestFlowStep, create_autotest_flow_card
from semi_auto_probe.config import ProbeConfig
from semi_auto_probe.keithley2450 import Keithley2450IVRunner, IVSweepConfig
from semi_auto_probe.mapping import generate_mapping_plan, load_mapping, MappingSession, read_mapping_artifact
from test_mapping import FakeSerial, FakeInstrument, settings


def fixed_current(node="current", **params):
    card = create_autotest_flow_card("constant_current", node)
    card.params.update(sample_count="2", settle_s="0", _node_id=node, **params)
    return AutoTestFlowStep(card.type_id, dict(card.params))


class MappingWorkflowTests(unittest.TestCase):
    def app(self):
        app = ProbeApp.__new__(ProbeApp)
        app.serial_client = FakeSerial()
        app.probe_config = ProbeConfig(wire_bond_enabled=True)
        app.autotest_stop_event = threading.Event()
        app.result_queue = queue.Queue()
        app.autotest_grid_rows = app.autotest_grid_cols = 3
        app._axis_move_timeout = lambda pulses, speed: 5
        return app

    def run_map(self, folder, flow, instrument_factory=None, app=None, **geometry):
        app = app or self.app()
        plan = generate_mapping_plan(settings(measurement_flow=flow, **geometry), (1, 1, 1))
        events = []
        factory = instrument_factory or (lambda: FakeInstrument(events))
        with patch("semi_auto_probe.app.Keithley2450IVRunner", lambda: Keithley2450IVRunner(factory())), \
             patch("semi_auto_probe.mapping.Keithley2450IVRunner", side_effect=AssertionError("Independent mapping instrument must not be used")):
            app._mapping_worker(plan, folder, 10, dict.fromkeys("XYZ", 1))
        return app, events, load_mapping(folder)

    def test_shared_graph_conditions_and_separate_node_artifacts(self):
        flow = (
            fixed_current("read", _next="branch"),
            AutoTestFlowStep("condition_metric", {"_node_id": "branch", "source_node": "read",
                "metric": "statistics.mean_current_a", "operator": "<", "threshold": "0",
                "_true": "accepted", "_false": "rejected"}),
            fixed_current("accepted"),
            fixed_current("rejected"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            app, events, (plan, metadata, results) = self.run_map(Path(tmp), flow)
            self.assertEqual(metadata["status"], "completed")
            self.assertEqual(metadata["completed_points"], 9)
            self.assertNotIn("measurement", metadata["settings"])
            self.assertEqual(plan.settings.measurement_flow, flow)
            self.assertEqual(events.count(":OUTP ON"), 18)
            self.assertTrue(all(move[0].name != "Z" for move in app.serial_client.moves))
            first = results[0, 0]
            self.assertEqual(set(first.workflow_data["metrics"]), set(results[1, 2].workflow_data["metrics"]))
            self.assertIsNone(first.mean_current_a)  # Never mix nodes/biases.
            self.assertTrue(any("accepted" in key for key in first.workflow_data["metrics"]))
            self.assertFalse(any("rejected" in path for path in first.workflow_data["artifacts"]))
            self.assertTrue((Path(tmp) / "mapping_artifacts.sqlite3").is_file())
            self.assertFalse((Path(tmp) / "points").exists())
            first_artifact = first.workflow_data["artifacts"][0]
            self.assertTrue(read_mapping_artifact(Path(tmp), first_artifact))
            with (Path(tmp) / "samples.csv").open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 36)
            self.assertEqual({row["workflow_node"] for row in rows}, {"read", "accepted"})

    def test_wait_workflow_does_not_connect_any_instrument(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, events, (_, metadata, results) = self.run_map(Path(tmp), (AutoTestFlowStep("wait", {"duration_s": "0"}),),
                instrument_factory=Mock(side_effect=AssertionError("No measurement node")))
            self.assertEqual(metadata["status"], "completed")
            self.assertEqual(events, [])
            self.assertTrue(all(result.workflow_data["metrics"] == {} for result in results.values()))
            for name in ("images", "iv", "wobb", "hp6614c", "b1500", "current", "it"):
                self.assertFalse((Path(tmp) / name).exists(), name)

    def test_read_error_and_stop_preserve_partial_point_and_close_output(self):
        for failure in ("timeout", "stop", "initialization", "cleanup"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                app = self.app()
                events = []
                instrument = FakeInstrument(events, read_fail=2 if failure == "timeout" else None,
                    write_fail=":OUTP ON" if failure == "initialization" else ":OUTP OFF" if failure == "cleanup" else None,
                    stop=app.autotest_stop_event if failure == "stop" else None)
                _, _, (_, metadata, results) = self.run_map(Path(tmp), (fixed_current(),), lambda: instrument, app)
                self.assertEqual(metadata["status"], "stopped" if failure == "stop" else "failed")
                self.assertEqual(len(results), 1)
                self.assertIn(":OUTP OFF", events)
                self.assertEqual(results[0, 0].status, "incomplete" if failure == "stop" else "failed")
                self.assertTrue(results[0, 0].workflow_data["artifacts"])
                self.assertTrue((Path(tmp) / results[0, 0].workflow_data["directory"]).is_dir())
                if failure in {"timeout", "stop"}:
                    with (Path(tmp) / "samples.csv").open(newline="", encoding="utf-8") as stream:
                        self.assertEqual(len(list(csv.DictReader(stream))), 1)
                if failure == "cleanup":
                    self.assertIn("cleanup", metadata["error"])

    def test_write_failure_closes_node_and_finishes_failed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(MappingSession, "sample", side_effect=OSError("disk full")):
            _, events, (_, metadata, results) = self.run_map(Path(tmp), (fixed_current(),))
            self.assertEqual(metadata["status"], "failed")
            self.assertIn("disk full", metadata["error"])
            self.assertEqual(events.count(":READ?"), 1)
            self.assertEqual(events[-1], ":OUTP OFF")
            self.assertEqual(len(results), 1)

    def test_iv_uses_common_executor_retains_coordinates_and_closes_held_bias(self):
        card = create_autotest_flow_card("iv", "sweep")
        card.params.update(start="0", stop="0.1", step="0.1", source_delay_s="0", output_off_after="false", _node_id="sweep")
        flow = (AutoTestFlowStep("iv", card.params),)
        from test_instrument_control import FakeVisa
        instrument = FakeVisa()  # New cards confirm output/ranges and query paired V/I.
        with tempfile.TemporaryDirectory() as tmp:
            _, _, (_, metadata, results) = self.run_map(Path(tmp), flow, instrument_factory=lambda: instrument, x_b_um=0, y_b_um=0)
            events = instrument.commands
            self.assertEqual(metadata["status"], "completed", metadata.get("error"))
            self.assertEqual(events[-1], ":OUTP OFF")
            artifacts = results[0, 0].workflow_data["artifacts"]
            result_path = next(path for path in artifacts if "iv" in Path(path).parts and path.endswith(".json"))
            payload = json.loads(read_mapping_artifact(Path(tmp), result_path))
            self.assertIsNone(payload["coordinates"]["gds"])
            self.assertFalse(payload["measurement"]["output_off_after"])
            self.assertEqual(payload["measurement"]["segments"], 1)
            self.assertEqual(payload["measurement"]["initial_direction"], "up")
            self.assertTrue(any(key.endswith("resistance_ohm") for key in results[0, 0].workflow_data["metrics"]))

    def test_invalid_fixed_z_flow_rejected_before_motion(self):
        with self.assertRaisesRegex(ValueError, "WobbTest"):
            settings(measurement_flow=(AutoTestFlowStep("wobb_test", {}),)).normalized()
        ProbeApp._validate_mapping_workflow(settings(measurement_flow=(AutoTestFlowStep("it", {}),)))
        with self.assertRaisesRegex(ValueError, "not executable"):
            ProbeApp._validate_mapping_workflow(settings(measurement_flow=(AutoTestFlowStep("light_control", {}),)))
        with self.assertRaisesRegex(ValueError, "Configure"):
            ProbeApp._validate_mapping_workflow(settings(measurement_flow=()))

    def test_strict_sweep_initialization_and_exit_errors(self):
        for command in (":OUTP ON", ":OUTP OFF"):
            with self.subTest(command=command):
                instrument = FakeInstrument([], write_fail=command)
                runner = Keithley2450IVRunner(instrument)
                with self.assertRaises((OSError, RuntimeError)):
                    runner.run_sweep(IVSweepConfig(start=0, stop=.1, step=.1, source_delay_s=0), strict_cleanup=True)
                self.assertEqual(instrument.events[-1], ":OUTP OFF")


if __name__ == "__main__":
    unittest.main()
