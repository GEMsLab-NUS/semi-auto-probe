from __future__ import annotations

import csv
import json
import queue
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from matplotlib.figure import Figure

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.config import ProbeConfig
from semi_auto_probe.keithley2450 import ConstantVoltageCurrentConfig, Keithley2450IVRunner
from semi_auto_probe.mapping import MappingSession, MappingSettings, generate_mapping_plan, load_mapping, load_point_samples, run_mapping
from semi_auto_probe.mapping_plot import draw_mapping_figure
from semi_auto_probe.protocol import Axis, AxisPosition


def settings(**kwargs):
    return replace(MappingSettings(x_b_um=2, y_b_um=2, step_x_um=1, step_y_um=1,
                                  settle_s=0, bias_settle_s=0,
                                  measurement=ConstantVoltageCurrentConfig(sample_count=2, nplc=1)), **kwargs)


class FakeInstrument:
    def __init__(self, events, *, read_fail=None, write_fail=None, stop=None):
        self.events = events
        self.read_fail, self.write_fail, self.stop = read_fail, write_fail, stop
        self.reads = 0

    def write(self, command):
        self.events.append(command)
        if command == self.write_fail:
            raise OSError("transport failure")

    def query(self, command):
        self.events.append(command)
        self.reads += 1
        if self.reads == self.read_fail:
            raise TimeoutError("instrument timeout")
        if self.stop:
            self.stop.set()
        return f"0.1,{-self.reads * 1e-9},100000"

    def close(self):
        self.events.append("close")


class FakeSerial:
    def __init__(self):
        self.positions = {"X": 0, "Y": 0, "Z": 123}
        self.moves = []
        self.stop_after_axis = None

    def read_stable_xyz_positions(self):
        return [(b"", b"", AxisPosition(Axis[name], False, value, b"")) for name, value in self.positions.items()]

    def read_xyz_positions(self):
        return self.read_stable_xyz_positions()

    def move_relative(self, *, axis, reverse, pulses, speed_percent):
        self.moves.append((axis, reverse, pulses, speed_percent))
        self.positions[axis.name] += (-1 if reverse else 1) * pulses

    def wait_axis_reached(self, axis, timeout):
        if self.stop_after_axis:
            self.stop_after_axis.set()


class MappingTests(unittest.TestCase):
    def test_serpentine_and_raster_have_same_spatial_indices(self):
        snake = generate_mapping_plan(settings(), (1, 1, 1))
        raster = generate_mapping_plan(settings(pattern="raster"), (1, 1, 1))
        self.assertEqual([(p.row, p.col) for p in snake.points], [(0, 0), (0, 1), (0, 2), (1, 2), (1, 1), (1, 0), (2, 0), (2, 1), (2, 2)])
        self.assertEqual([(p.row, p.col) for p in raster.points], [(r, c) for r in range(3) for c in range(3)])
        self.assertEqual({(p.row, p.col, p.x_pulses, p.y_pulses) for p in snake.points},
                         {(p.row, p.col, p.x_pulses, p.y_pulses) for p in raster.points})

    def test_reversed_corners_exact_decimal_boundary_and_remainder(self):
        plan = generate_mapping_plan(settings(x_a_um=.3, x_b_um=0, step_x_um=.1, y_a_um=2.5, y_b_um=0), (.01, .01, 1))
        self.assertEqual(plan.xs, (0, .1, .2, .3))
        self.assertEqual(plan.ys, (0, 1, 2))
        self.assertEqual(generate_mapping_plan(settings(x_b_um=0), (1, 1, 1)).xs, (0,))
        self.assertEqual(generate_mapping_plan(settings(y_b_um=0), (1, 1, 1)).ys, (0,))

    def test_invalid_geometry_and_duplicate_quantized_positions(self):
        for bad in (settings(step_x_um=0), settings(x_a_um=float("nan")), settings(step_y_um=-1),
                    settings(x_b_um=500), settings(settle_s=-1), settings(pattern="unknown")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                generate_mapping_plan(bad, (1, 1, 1))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            generate_mapping_plan(settings(step_x_um=.1), (1, 1, 1))
        with self.assertRaises(ValueError):
            generate_mapping_plan(settings(), (float("nan"), 1, 1))
        self.assertEqual(len(generate_mapping_plan(settings(x_b_um=499, y_b_um=0), (1, 1, 1)).points), 500)

    def execute(self, directory, *, config=None, instrument=None, stop=None, session_patch=None, move_override=None):
        events = []
        stop = stop or threading.Event()
        instrument = instrument or FakeInstrument(events)
        events = instrument.events
        plan = generate_mapping_plan(config or settings(), (1, 1, 1))
        session = MappingSession(directory, plan, 123)
        if session_patch:
            session_patch(session)
        emitted = []
        def move(point):
            events.append(("move", point.row, point.col))
            return (point.x_pulses, point.y_pulses, 123)
        result = run_mapping(plan, session, move_xy=move_override or move, stop_event=stop,
                             emit=lambda *event: emitted.append(event), runner=Keithley2450IVRunner(instrument))
        return result, plan, events, emitted

    def test_bias_once_read_only_after_move_and_round_trip_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (status, error), plan, events, emitted = self.execute(directory)
            self.assertEqual((status, error), ("completed", ""))
            self.assertEqual(events.count("*RST"), 1)
            self.assertEqual(events.count(":OUTP ON"), 1)
            self.assertEqual(events[-1], ":OUTP OFF")
            self.assertEqual(events.count(":READ?"), 18)
            motion_and_reads = [e for e in events if isinstance(e, tuple) or e == ":READ?"]
            for index in range(0, 27, 3):
                self.assertEqual(motion_and_reads[index][0], "move")
                self.assertEqual(motion_and_reads[index + 1:index + 3], [":READ?", ":READ?"])
            loaded, metadata, results = load_mapping(directory)
            self.assertEqual(loaded, plan)
            self.assertEqual(metadata["completed_points"], 9)
            self.assertEqual(results[(1, 2)].point.order, 4)
            self.assertLess(results[(0, 0)].mean_current_a, 0)
            self.assertEqual(len(load_point_samples(directory, plan.points[0])), 2)

    def test_timeout_saves_partial_samples_and_marks_point_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            instrument = FakeInstrument([], read_fail=2)
            (status, error), _, events, _ = self.execute(directory, instrument=instrument)
            _, metadata, results = load_mapping(directory)
            self.assertEqual(status, "failed")
            self.assertIn("timeout", error)
            self.assertEqual(results[(0, 0)].status, "failed")
            self.assertEqual(metadata["completed_points"], 0)
            self.assertEqual(len(load_point_samples(directory, results[(0, 0)].point)), 1)
            self.assertEqual(events[-1], ":OUTP OFF")
            self.assertEqual(len(results), 1)

    def test_stop_during_sample_keeps_incomplete_point_and_closes_bias(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop = threading.Event()
            instrument = FakeInstrument([], stop=stop)
            (status, _), _, events, _ = self.execute(Path(tmp), instrument=instrument, stop=stop)
            _, _, results = load_mapping(Path(tmp))
            self.assertEqual(status, "stopped")
            self.assertEqual(results[(0, 0)].status, "incomplete")
            self.assertEqual(len(results), 1)
            self.assertEqual(events[-1], ":OUTP OFF")

    def test_stop_during_move_never_reads_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop = threading.Event()
            def move(point):
                stop.set()
                return (0, 0, 123)
            (status, _), _, events, _ = self.execute(Path(tmp), stop=stop, move_override=move)
            self.assertEqual(status, "stopped")
            self.assertNotIn(":READ?", events)

    def test_setup_and_output_off_failures_are_visible(self):
        for command in (":OUTP ON", ":OUTP OFF"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                instrument = FakeInstrument([], write_fail=command)
                (status, error), _, events, _ = self.execute(Path(tmp), instrument=instrument)
                self.assertEqual(status, "failed")
                self.assertIn("transport failure", error)
                self.assertIn(":OUTP OFF", events)
                self.assertEqual(json.loads((Path(tmp) / "mapping.json").read_text())["status"], "failed")

    def test_owned_instrument_closed_and_multiple_cleanup_errors_preserved(self):
        instrument = FakeInstrument([], write_fail=":OUTP ON")
        runner = Keithley2450IVRunner()
        runner._open_resource = lambda _name: instrument
        with self.assertRaises(OSError):
            runner.begin_constant_voltage_current(settings().measurement)
        self.assertEqual(instrument.events[-2:], [":OUTP OFF", "close"])
        self.assertIsNone(runner.instrument)

        runner = Keithley2450IVRunner()
        instrument = FakeInstrument([])
        instrument.write = Mock(side_effect=OSError("write failure"))
        instrument.close = Mock(side_effect=OSError("close failure"))
        runner._open_resource = lambda _name: instrument
        with self.assertRaisesRegex(RuntimeError, "output OFF failed.*close failed"):
            runner.begin_constant_voltage_current(settings().measurement)
        self.assertIsNone(runner.instrument)

    def test_polling_must_stop_before_acquisition(self):
        app = self.make_app()
        app.realtime_thread = Mock()
        app.realtime_thread.is_alive.return_value = True
        with self.assertRaisesRegex(RuntimeError, "polling has not stopped"):
            app._wait_mapping_pollers()
        app.realtime_thread.join.assert_called_once_with(timeout=2.0)

    def test_write_failure_stops_acquisition_and_closes_bias(self):
        with tempfile.TemporaryDirectory() as tmp:
            def break_writer(session):
                session.sample = Mock(side_effect=OSError("disk full"))
            (status, error), _, events, emitted = self.execute(Path(tmp), session_patch=break_writer)
            self.assertEqual(status, "failed")
            self.assertIn("disk full", error)
            self.assertEqual(events[-1], ":OUTP OFF")
            self.assertEqual(events.count(":READ?"), 1)
            self.assertEqual(len([e for e in emitted if e[0] == "mapping_result"][0][1].samples), 1)

    def make_app(self):
        app = ProbeApp.__new__(ProbeApp)
        app.serial_client = FakeSerial()
        app.probe_config = ProbeConfig()
        app.autotest_stop_event = threading.Event()
        app.result_queue = queue.Queue()
        app._axis_move_timeout = lambda pulses, speed: 5
        return app

    def test_motion_uses_xy_only_and_respects_axis_polarity(self):
        app = self.make_app()
        point = generate_mapping_plan(settings(), (1, 1, 1)).points[5]
        self.assertEqual(app._mapping_move_xy(point, 123, 10, {"X": -1, "Y": 1, "Z": 1}), (0, 1, 123))
        point = generate_mapping_plan(settings(), (1, 1, 1)).points[8]
        self.assertEqual(app._mapping_move_xy(point, 123, 10, {"X": -1, "Y": 1, "Z": 1}), (2, 2, 123))
        self.assertEqual(app.serial_client.positions["X"], -2)
        self.assertTrue(all(move[0] != Axis.Z for move in app.serial_client.moves))

    def test_zero_settle_fast_motion_uses_reached_event_without_stable_debounce(self):
        app = self.make_app()
        app._read_positions_event = Mock()
        serial = app.serial_client
        original_stable = serial.read_stable_xyz_positions
        serial.read_stable_xyz_positions = Mock(side_effect=original_stable)
        serial.read_xyz_positions = Mock(side_effect=original_stable)
        cache = {"X": 0, "Y": 0, "Z": 123}
        points = generate_mapping_plan(settings(), (1, 1, 1)).points[:4]
        for point in points:
            self.assertEqual(app._mapping_move_xy(point, 123, 100, dict.fromkeys("XYZ", 1),
                                                  fast=True, position_cache=cache),
                             (point.x_pulses, point.y_pulses, 123))
        self.assertEqual(serial.read_stable_xyz_positions.call_count, 0)
        self.assertEqual(serial.read_xyz_positions.call_count, len(points))
        self.assertEqual(cache, {"X": points[-1].x_pulses, "Y": points[-1].y_pulses, "Z": 123})

    def test_z_drift_and_stop_between_axes_never_cause_z_motion(self):
        app = self.make_app()
        point = generate_mapping_plan(settings(), (1, 1, 1)).points[-1]
        with self.assertRaisesRegex(RuntimeError, "Z changed"):
            app._mapping_move_xy(point, 124, 10, dict.fromkeys("XYZ", 1))
        self.assertEqual(app.serial_client.moves, [])
        app.serial_client.stop_after_axis = app.autotest_stop_event
        app._mapping_move_xy(point, 123, 10, dict.fromkeys("XYZ", 1))
        self.assertEqual([move[0] for move in app.serial_client.moves], [Axis.X])

    def test_dispatch_does_not_touch_gds_focusmap_or_camera(self):
        app = self.make_app()
        app._start_mapping = Mock()
        config = settings()
        app.start_autotest(config)
        app._start_mapping.assert_called_once_with(config)

    def test_emergency_feedback_keeps_motion_locked_until_mapping_cleanup(self):
        app = self.make_app()
        app.mapping_running = True
        app.motion_busy = True
        app._handle_worker_event(("motor_done",))
        self.assertTrue(app.motion_busy)

    def test_mapping_start_position_read_reconnects_once_after_write_timeout(self):
        app = self.make_app()
        original_read = app.serial_client.read_stable_xyz_positions
        app.serial_client.port = "COM_TEST"
        app.serial_client.close = Mock()
        app.serial_client.open = Mock()
        app.serial_client.read_stable_xyz_positions = Mock(
            side_effect=[TimeoutError("Write timeout"), original_read()]
        )

        entries = app._read_mapping_start_positions()

        self.assertEqual([entry[2].position for entry in entries], [0, 0, 123])
        app.serial_client.close.assert_called_once_with()
        app.serial_client.open.assert_called_once_with()
        self.assertEqual(app.serial_client.read_stable_xyz_positions.call_count, 2)

    def test_mapping_start_position_read_reports_stage_and_both_failures(self):
        app = self.make_app()
        app.serial_client.port = "COM_TEST"
        app.serial_client.close = Mock()
        app.serial_client.open = Mock()
        app.serial_client.read_stable_xyz_positions = Mock(
            side_effect=[TimeoutError("Write timeout"), OSError("controller unavailable")]
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Stage controller startup read failed on COM_TEST after one reconnect.*controller unavailable.*Write timeout",
        ):
            app._read_mapping_start_positions()

    def test_worker_with_no_layout_focus_or_camera_and_position_failure(self):
        app = self.make_app()
        app.serial_client.read_stable_xyz_positions = Mock(side_effect=OSError("disconnected"))
        with tempfile.TemporaryDirectory() as tmp:
            app._mapping_worker(generate_mapping_plan(settings(), (1, 1, 1)), Path(tmp), 10, dict.fromkeys("XYZ", 1))
            metadata = json.loads((Path(tmp) / "mapping.json").read_text())
            self.assertEqual(metadata["status"], "failed")
            self.assertIn("disconnected", metadata["error"])
            self.assertEqual(app.result_queue.get()[0], "mapping_finished")

    def test_png_and_missing_cells_are_not_zero(self):
        plan = generate_mapping_plan(settings(), (1, 1, 1))
        values = np.full((3, 3), np.nan)
        values[1, 2] = -2e-9
        states = np.zeros((3, 3), dtype=np.uint8)
        states[1, 2], states[2, 1], states[0, 0] = 1, 2, 3
        figure = Figure()
        axes = draw_mapping_figure(figure, plan, values, states)
        self.assertEqual(axes.images[0].origin, "lower")
        self.assertEqual(float(axes.images[0].get_array()[1, 2]), -2)
        self.assertTrue(axes.images[0].get_array().mask[0, 0])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapping.png"
            figure.savefig(path)
            self.assertGreater(path.stat().st_size, 1000)

    def test_web_repository_lists_mapping_artifacts(self):
        from semi_auto_probe.web_app import AutoTestSessionRepository
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "20260903_120000"
            directory.mkdir()
            self.execute(directory)
            detail = AutoTestSessionRepository(Path(tmp)).session_detail(directory.name)
            self.assertTrue({"mapping.json", "mapping.csv", "samples.csv"}.issubset({entry["path"] for entry in detail["files"]}))


if __name__ == "__main__":
    unittest.main()
