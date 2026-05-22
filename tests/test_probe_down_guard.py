from __future__ import annotations

import queue
import unittest

from semi_auto_probe.af_plane import clear_sample_plane_model
from semi_auto_probe.app import ProbeApp
from semi_auto_probe.config import ProbeConfig
from semi_auto_probe.protocol import Axis, AxisPosition


class DummyBool:
    def __init__(self, value: bool) -> None:
        self.value = value

    def get(self) -> bool:
        return self.value


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


class ProbeDownGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_sample_plane_model()

    def tearDown(self) -> None:
        clear_sample_plane_model()

    def make_app_shell(self) -> ProbeApp:
        app = ProbeApp.__new__(ProbeApp)
        app.probe_config = ProbeConfig(probe_safe_z_margin_um=100.0)
        app.current_position_values = {"X": 0, "Y": 0, "Z": 1000}
        app.probe_down_var = DummyBool(True)
        app.result_queue = queue.Queue()
        app.serial_client = FakeSerialClient(app.current_position_values)
        return app

    def test_xy_move_retracts_to_safe_z_then_returns_to_contact_z(self) -> None:
        app = self.make_app_shell()

        ProbeApp._move_absolute_stage(app, 10, 20, 1000, source="button", expected_targets={"X": 10, "Y": 20})

        fake = app.serial_client
        self.assertIsInstance(fake, FakeSerialClient)
        self.assertEqual(fake.positions, {"X": 10, "Y": 20, "Z": 1000})
        self.assertEqual(
            fake.moves,
            [
                (Axis.Z, True, 200, 100),
                (Axis.X, False, 10, 100),
                (Axis.Y, False, 20, 100),
                (Axis.Z, False, 200, 100),
            ],
        )


if __name__ == "__main__":
    unittest.main()
