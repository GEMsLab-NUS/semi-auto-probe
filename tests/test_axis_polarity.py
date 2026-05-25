from __future__ import annotations

import queue
import unittest

from semi_auto_probe.af_plane import SamplePlaneModel, clear_sample_plane_model, set_sample_plane_model
from semi_auto_probe.app import ProbeApp
from semi_auto_probe.config import ProbeConfig
from semi_auto_probe.protocol import Axis, AxisPosition


class DummyVar:
    def __init__(self, value: object = "") -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class FakeSerialClient:
    def __init__(self, positions: dict[str, int]) -> None:
        self.positions = dict(positions)
        self.moves: list[tuple[Axis, bool, int, int]] = []

    def read_stable_xyz_positions(self):
        return [
            (b"", b"", AxisPosition(Axis.X, False, self.positions["X"], b"")),
            (b"", b"", AxisPosition(Axis.Y, False, self.positions["Y"], b"")),
            (b"", b"", AxisPosition(Axis.Z, False, self.positions["Z"], b"")),
        ]

    def move_relative(self, *, axis: Axis, reverse: bool, pulses: int, speed_percent: int):
        self.moves.append((axis, reverse, pulses, speed_percent))
        self.positions[axis.name] += -pulses if reverse else pulses
        return b""

    def wait_axis_reached(self, axis: Axis, timeout: float):
        return b""


class AxisPolarityTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_sample_plane_model()

    def test_config_toggles_build_xy_polarity_and_preserve_z(self) -> None:
        app = ProbeApp.__new__(ProbeApp)
        app.probe_config = ProbeConfig(motor_axis_polarity={"X": 1, "Y": 1, "Z": -1})
        app.x_axis_reversed_var = DummyVar(True)
        app.y_axis_reversed_var = DummyVar(False)

        self.assertEqual(
            ProbeApp._motor_axis_polarity_from_config_vars(app),
            {"X": -1, "Y": 1, "Z": -1},
        )

    def test_polarity_change_remaps_current_logical_positions(self) -> None:
        app = ProbeApp.__new__(ProbeApp)
        app.probe_config = ProbeConfig(motor_axis_polarity={"X": -1, "Y": -1, "Z": 1})
        app.current_position_values = {"X": 12, "Y": -34, "Z": 56}
        app.position_vars = {"X": DummyVar(), "Y": DummyVar(), "Z": DummyVar()}
        app.modified_position_axes = {"X", "Y"}
        app.position_edit_modes = {"X": "Relative", "Y": "Absolute", "Z": None}
        app.autofocus_z_var = DummyVar()

        ProbeApp._remap_current_positions_for_polarity_change(app, {"X": 1, "Y": 1, "Z": 1})

        self.assertEqual(app.current_position_values, {"X": -12, "Y": 34, "Z": 56})
        self.assertEqual(app.position_vars["X"].get(), "-12")
        self.assertEqual(app.position_vars["Y"].get(), "34")
        self.assertEqual(app.position_vars["Z"].get(), "56")
        self.assertEqual(app.modified_position_axes, set())
        self.assertEqual(app.position_edit_modes, {"X": None, "Y": None, "Z": None})

    def test_common_absolute_stage_move_applies_reversed_xy_controller_directions(self) -> None:
        app = ProbeApp.__new__(ProbeApp)
        app.probe_config = ProbeConfig(motor_axis_polarity={"X": -1, "Y": -1, "Z": 1})
        app.current_position_values = {"X": 0, "Y": 0, "Z": 100}
        app.result_queue = queue.Queue()
        app.serial_client = FakeSerialClient({"X": 0, "Y": 0, "Z": 100})

        entries = ProbeApp._move_absolute_stage_raw(
            app,
            10,
            20,
            100,
            source="imgstitch",
            expected_targets={"X": 10, "Y": 20, "Z": 100},
        )

        self.assertEqual(
            app.serial_client.moves,
            [
                (Axis.X, True, 10, 100),
                (Axis.Y, True, 20, 100),
            ],
        )
        self.assertEqual(app.serial_client.positions, {"X": -10, "Y": -20, "Z": 100})
        self.assertEqual(ProbeApp._axis_from_position_entries(app, entries, Axis.X), 10)
        self.assertEqual(ProbeApp._axis_from_position_entries(app, entries, Axis.Y), 20)

    def test_logical_z_relative_helper_applies_reversed_controller_direction(self) -> None:
        app = ProbeApp.__new__(ProbeApp)
        app.probe_config = ProbeConfig(motor_axis_polarity={"X": 1, "Y": 1, "Z": -1})
        app.serial_client = FakeSerialClient({"X": 0, "Y": 0, "Z": -95})
        app.status_var = DummyVar()

        _command, pulses = ProbeApp._move_relative_logical_axis(app, Axis.Z, 5, 12)

        self.assertEqual(pulses, 5)
        self.assertEqual(app.serial_client.moves, [(Axis.Z, True, 5, 12)])
        self.assertEqual(app.serial_client.positions["Z"], -100)

    def test_focusmap_z_sync_uses_logical_axis_polarity(self) -> None:
        app = ProbeApp.__new__(ProbeApp)
        app.probe_config = ProbeConfig(motor_axis_polarity={"X": 1, "Y": 1, "Z": -1}, cc_speed_percent=12)
        app.current_position_values = {"X": 0, "Y": 0, "Z": 95}
        app.result_queue = queue.Queue()
        app.serial_client = FakeSerialClient({"X": 0, "Y": 0, "Z": -95})
        set_sample_plane_model(
            SamplePlaneModel(
                enabled=True,
                type="plane",
                a=0.0,
                b=0.0,
                c=100.0,
                rms_residual=0.0,
                pv_residual=0.0,
                max_abs_residual=0.0,
                tilt_x_deg=0.0,
                tilt_y_deg=0.0,
                valid_points=3,
                failed_points=0,
            )
        )

        ProbeApp._focusmap_z_sync_worker(app)

        self.assertEqual(app.serial_client.moves, [(Axis.Z, True, 5, 12)])
        self.assertEqual(app.serial_client.positions["Z"], -100)
        events = list(app.result_queue.queue)
        self.assertTrue(any(event[0] == "focusmap_z_synced" and event[1] == 100 for event in events))


if __name__ == "__main__":
    unittest.main()
