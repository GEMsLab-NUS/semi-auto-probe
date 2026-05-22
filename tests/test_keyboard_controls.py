from __future__ import annotations

import unittest

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.config import KEYBOARD_MOTION_SCHEME_WASD_QE, MOTOR_SPEED_PROFILE_FINE, MOTOR_SPEED_PROFILE_SAFE, ProbeConfig
from semi_auto_probe.protocol import Axis, AxisPosition


class DummyVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class KeyboardControlsTests(unittest.TestCase):
    def make_app_shell(self, scheme: str | None = None) -> ProbeApp:
        app = ProbeApp.__new__(ProbeApp)
        app.probe_config = ProbeConfig()
        if scheme is not None:
            app.probe_config.keyboard_motion_scheme = scheme
        return app

    def test_default_keyboard_scheme_uses_arrows_and_page_keys(self) -> None:
        app = self.make_app_shell()

        bindings = ProbeApp._keyboard_bindings_for_configured_scheme(app)

        self.assertEqual(bindings["Right"], ("X", False))
        self.assertEqual(bindings["Left"], ("X", True))
        self.assertEqual(bindings["Prior"], ("Z", False))
        self.assertEqual(bindings["Next"], ("Z", True))

    def test_wasd_keyboard_scheme_uses_wasd_and_qe(self) -> None:
        app = self.make_app_shell(KEYBOARD_MOTION_SCHEME_WASD_QE)

        bindings = ProbeApp._keyboard_bindings_for_configured_scheme(app)

        self.assertEqual(bindings["d"], ("X", False))
        self.assertEqual(bindings["a"], ("X", True))
        self.assertEqual(bindings["w"], ("Y", False))
        self.assertEqual(bindings["s"], ("Y", True))
        self.assertEqual(bindings["q"], ("Z", False))
        self.assertEqual(bindings["e"], ("Z", True))

    def test_x_axis_polarity_reverses_controller_direction_without_changing_key_bindings(self) -> None:
        app = self.make_app_shell(KEYBOARD_MOTION_SCHEME_WASD_QE)
        app.probe_config.motor_axis_polarity = {"X": -1, "Y": 1, "Z": 1}

        bindings = ProbeApp._keyboard_bindings_for_configured_scheme(app)
        reverse, pulses = ProbeApp._relative_move_args_for_logical_step(app, *bindings["d"], pulses=10)

        self.assertEqual(bindings["d"], ("X", False))
        self.assertEqual((reverse, pulses), (True, 10))

    def test_x_axis_polarity_converts_controller_position_to_logical_position(self) -> None:
        app = self.make_app_shell()
        app.probe_config.motor_axis_polarity = {"X": -1, "Y": 1, "Z": 1}
        app.position_vars = {"X": DummyVar(), "Y": DummyVar(), "Z": DummyVar()}
        app.current_position_values = {"X": 0, "Y": 0, "Z": 0}
        app.modified_position_axes = set()
        app.position_edit_modes = {"X": None, "Y": None, "Z": None}
        app.position_inputs = {}
        app.autofocus_z_var = DummyVar()
        app.main_focusmap_plane_var = DummyVar(False)

        ProbeApp._update_axis_position(app, AxisPosition(Axis.X, False, -12, b""))

        self.assertEqual(app.current_position_values["X"], 12)
        self.assertEqual(app.position_vars["X"].get(), "12")

    def test_cycle_jog_step_uses_configured_levels_for_axis(self) -> None:
        app = self.make_app_shell()
        app.jog_step_levels = {"X": (2, 4), "Y": (1,), "Z": (1,)}
        app.step_vars = {"X": DummyVar("2")}
        app.status_var = DummyVar()

        ProbeApp.cycle_jog_step(app, "X")

        self.assertEqual(app.step_vars["X"].get(), "4")
        ProbeApp.cycle_jog_step(app, "X")
        self.assertEqual(app.step_vars["X"].get(), "2")

    def test_numeric_input_validators_reject_wrong_type(self) -> None:
        self.assertTrue(ProbeApp._integer_text_allowed("123", minimum=0, maximum=1000))
        self.assertFalse(ProbeApp._integer_text_allowed("12.3", minimum=0, maximum=1000))
        self.assertFalse(ProbeApp._integer_text_allowed("-1", minimum=0, maximum=1000))
        self.assertTrue(ProbeApp._float_text_allowed("12.3", minimum=0, maximum=1000))
        self.assertFalse(ProbeApp._float_text_allowed("12.3.4", minimum=0, maximum=1000))
        self.assertFalse(ProbeApp._float_text_allowed("-1", minimum=0, maximum=1000))
        self.assertTrue(ProbeApp._jog_step_text_allowed("1, 10; 1000"))
        self.assertFalse(ProbeApp._jog_step_text_allowed("1, ten"))

    def test_motion_speed_uses_active_profile(self) -> None:
        app = self.make_app_shell()
        app.probe_config = ProbeConfig(
            cc_speed_percent=80,
            fine_speed_percent=30,
            safe_speed_percent=10,
            active_motor_speed_profile=MOTOR_SPEED_PROFILE_FINE,
        )

        self.assertEqual(ProbeApp._motion_speed_percent(app), 30)
        self.assertEqual(ProbeApp._motion_speed_percent(app, MOTOR_SPEED_PROFILE_SAFE), 10)
        self.assertAlmostEqual(ProbeApp._axis_move_timeout(300, 30), 10.0)


if __name__ == "__main__":
    unittest.main()
