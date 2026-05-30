from __future__ import annotations

import os
import threading
import unittest
from unittest.mock import patch
from types import MethodType

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.config import (
    AUTOFOCUS_PEAK_MODEL_GAUSSIAN,
    CAMERA_CONTROL_MODE_AUTO,
    CAMERA_RESOLUTION_HALF,
    DEFAULT_AGENT_BASE_URL,
    DEFAULT_AGENT_MODEL,
    DEFAULT_AGENT_TIMEOUT_SECONDS,
    JOG_STEP_AXES,
    KEYBOARD_MOTION_SCHEME_ARROW_PAGE,
    MOTOR_SPEED_PROFILE_FAST,
    ProbeConfig,
)
from semi_auto_probe.protocol import Axis, ControllerMotionParameters


class DummyVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class DummyEntry:
    def __init__(self) -> None:
        self.state = "normal"

    def configure(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]


class FailingVisionPanel:
    def draw_overlay(self) -> None:
        raise RuntimeError("overlay redraw failed")


class ExclusiveSerialWorkflowTests(unittest.TestCase):
    def make_app_shell(self) -> ProbeApp:
        app = ProbeApp.__new__(ProbeApp)
        app.serial_client = object()
        app.home_signal_enabled = True
        app.home_signal_stop_event = threading.Event()
        app.home_signal_thread = None
        app.home_signal_button_var = DummyVar("Stop Home Signals")
        app.status_var = DummyVar()
        app.autofocus_status_var = DummyVar("Running")
        app.imgstitch_status_var = DummyVar("Running")
        app.autofocus_restore_home_signal = False
        app.autofocus_restore_realtime = False
        app.autofocus_running = False
        app.motion_busy = False
        app.focus_lock = threading.Lock()
        app.autofocus_run_end_time = None
        app.imgstitch_restore_home_signal = False
        app.imgstitch_restore_realtime = False
        app.imgstitch_running = False
        app.imgstitch_focus_sampling_required = False
        app._home_signal_worker = lambda: None
        return app

    def test_autofocus_pauses_home_polling_and_remembers_restore(self) -> None:
        app = self.make_app_shell()

        app.disable_home_signal_polling()
        app.autofocus_restore_home_signal = True

        self.assertTrue(app.home_signal_stop_event.is_set())
        self.assertFalse(app.home_signal_enabled)
        self.assertEqual(app.home_signal_button_var.get(), "Home Signals")
        self.assertTrue(app.autofocus_restore_home_signal)

    def test_home_polling_can_be_restored_after_exclusive_workflow(self) -> None:
        app = self.make_app_shell()
        app.home_signal_enabled = False
        app.autofocus_restore_home_signal = True

        app.toggle_home_signal_polling()

        self.assertTrue(app.home_signal_enabled)
        self.assertFalse(app.home_signal_stop_event.is_set())
        self.assertEqual(app.home_signal_button_var.get(), "Stop Home Signals")
        app.disable_home_signal_polling()

    def test_autofocus_done_restores_home_polling(self) -> None:
        app = self.make_app_shell()
        app.home_signal_enabled = False
        app.autofocus_restore_home_signal = True
        restored = []

        def restore_home(self) -> None:
            restored.append(True)
            self.home_signal_enabled = True

        app.toggle_home_signal_polling = MethodType(restore_home, app)

        ProbeApp._handle_worker_event(app, ("autofocus_done",))

        self.assertTrue(restored)
        self.assertTrue(app.home_signal_enabled)
        self.assertFalse(app.autofocus_restore_home_signal)

    def test_imgstitch_finished_restores_home_polling(self) -> None:
        app = self.make_app_shell()
        app.home_signal_enabled = False
        app.imgstitch_restore_home_signal = True
        restored = []

        def restore_home(self) -> None:
            restored.append(True)
            self.home_signal_enabled = True

        app.toggle_home_signal_polling = MethodType(restore_home, app)

        ProbeApp._handle_worker_event(app, ("imgstitch_finished",))

        self.assertTrue(restored)
        self.assertTrue(app.home_signal_enabled)
        self.assertFalse(app.imgstitch_restore_home_signal)

    def test_admin_mode_requires_config_token(self) -> None:
        app = ProbeApp.__new__(ProbeApp)
        app.admin_mode_enabled = False
        app.admin_token_var = DummyVar("wrong")
        app.admin_mode_status_var = DummyVar("Admin mode locked")
        app.status_var = DummyVar()
        app.serial_client = None
        app.set_xyz_zero_button = None
        app.set_autofocus_z_zero_button = None

        with patch.dict(os.environ, {"SEMI_AUTO_PROBE_ADMIN_TOKEN": "secret-token"}, clear=False):
            ProbeApp.enable_admin_mode(app)
            self.assertFalse(app.admin_mode_enabled)
            self.assertIn("invalid token", app.admin_mode_status_var.get())

            app.admin_token_var.set("secret-token")
            ProbeApp.enable_admin_mode(app)

        self.assertTrue(app.admin_mode_enabled)
        self.assertEqual(app.admin_token_var.get(), "")

    def test_admin_mode_does_not_lock_cc_accel_entry(self) -> None:
        app = ProbeApp.__new__(ProbeApp)
        app.admin_mode_enabled = False
        app.admin_mode_status_var = DummyVar("Admin mode locked")
        app.serial_client = None
        app.set_xyz_zero_button = None
        app.set_autofocus_z_zero_button = None
        app.cc_accel_time_entry = DummyEntry()

        ProbeApp._update_admin_mode_controls(app)

        self.assertEqual(app.cc_accel_time_entry.state, "normal")
        self.assertNotIn("CC accel/decel", app.admin_mode_status_var.get())

        app.admin_mode_enabled = True
        ProbeApp._update_admin_mode_controls(app)

        self.assertEqual(app.cc_accel_time_entry.state, "normal")

    def test_apply_config_writes_cc_accel_without_admin(self) -> None:
        app = ProbeApp.__new__(ProbeApp)
        app.admin_mode_enabled = False
        app.probe_config = ProbeConfig(cc_accel_time_s=0.3)
        app.probe_config.controller_motion_parameters["X"]["work_speed"] = 30
        app.status_var = DummyVar()
        app.config_status_var = DummyVar()
        app.admin_mode_status_var = DummyVar("Admin mode locked")
        app.objective_var = DummyVar("10")
        app.eyepiece_var = DummyVar("2")
        app.microstep_var = DummyVar("2")
        app.lead_xy_var = DummyVar("1")
        app.lead_z_var = DummyVar("0.5")
        app.base_angle_var = DummyVar("0.72")
        app.cc_speed_percent_var = DummyVar("100")
        app.fine_speed_percent_var = DummyVar("40")
        app.safe_speed_percent_var = DummyVar("15")
        app.probe_safe_z_margin_um_var = DummyVar("100")
        app.motor_speed_profile_var = DummyVar(MOTOR_SPEED_PROFILE_FAST)
        app.controller_motion_parameter_vars = {
            axis: {
                "minimum_speed": DummyVar("0"),
                "work_speed": DummyVar("10"),
                "acceleration": DummyVar("0"),
            }
            for axis in JOG_STEP_AXES
        }
        app.x_axis_reversed_var = DummyVar(False)
        app.y_axis_reversed_var = DummyVar(False)
        app.camera_fov_rotation_var = DummyVar("0")
        app.camera_exposure_mode_var = DummyVar(CAMERA_CONTROL_MODE_AUTO)
        app.camera_exposure_var = DummyVar("0")
        app.camera_gain_mode_var = DummyVar(CAMERA_CONTROL_MODE_AUTO)
        app.camera_gain_var = DummyVar("0")
        app.camera_white_balance_mode_var = DummyVar(CAMERA_CONTROL_MODE_AUTO)
        app.camera_white_balance_temperature_var = DummyVar("6500")
        app.camera_color_saturation_var = DummyVar("128")
        app.camera_color_brightness_var = DummyVar("0")
        app.camera_color_contrast_var = DummyVar("0")
        app.camera_color_gamma_var = DummyVar("100")
        app.camera_index_var = DummyVar("auto")
        app.camera_resolution_var = DummyVar(CAMERA_RESOLUTION_HALF)
        app.camera_target_fps_var = DummyVar("30")
        app.cc_accel_time_var = DummyVar("0.4")
        app.autofocus_settle_ms_var = DummyVar("100")
        app.autofocus_sample_count_var = DummyVar("5")
        app.autofocus_peak_model_var = DummyVar(AUTOFOCUS_PEAK_MODEL_GAUSSIAN)
        app.imgstitch_settle_ms_var = DummyVar("100")
        app.layoutbond_fov_width_var = DummyVar("540")
        app.layoutbond_fov_height_var = DummyVar("450")
        app.keyboard_motion_scheme_var = DummyVar(KEYBOARD_MOTION_SCHEME_ARROW_PAGE)
        app.jog_step_level_vars = {axis: DummyVar("1, 10, 100, 1000") for axis in JOG_STEP_AXES}
        app.focus_threshold_yellow_vars = {
            "Laplacian": DummyVar("1000"),
            "Tenengrad": DummyVar("20000"),
            "Brenner": DummyVar("1000"),
        }
        app.focus_threshold_green_vars = {
            "Laplacian": DummyVar("2000"),
            "Tenengrad": DummyVar("40000"),
            "Brenner": DummyVar("2000"),
        }
        app.agent_api_key_var = DummyVar("")
        app.agent_base_url_var = DummyVar(DEFAULT_AGENT_BASE_URL)
        app.agent_model_var = DummyVar(DEFAULT_AGENT_MODEL)
        app.agent_timeout_var = DummyVar(str(DEFAULT_AGENT_TIMEOUT_SECONDS))
        app._remap_current_positions_for_polarity_change = MethodType(lambda self, previous: None, app)
        app._sync_config_vars_from_config = MethodType(lambda self: None, app)
        app._build_agent_planner = MethodType(lambda self: None, app)
        app._update_config_display = MethodType(lambda self: None, app)
        app._refresh_keyboard_bindings = MethodType(lambda self: None, app)
        app.vision_panel = FailingVisionPanel()

        self.assertTrue(ProbeApp.apply_config(app, save=False))
        self.assertAlmostEqual(app.probe_config.cc_accel_time_s, 0.4)
        self.assertEqual(app.probe_config.cc_acceleration_units(), 40)
        self.assertAlmostEqual(app.probe_config.layoutbond_fov_width_um, 540.0)
        self.assertAlmostEqual(app.probe_config.layoutbond_fov_height_um, 450.0)
        self.assertEqual(app.probe_config.controller_motion_parameters["X"]["work_speed"], 30)

    def test_d5_readback_updates_display_without_overwriting_config(self) -> None:
        app = ProbeApp.__new__(ProbeApp)
        app.probe_config = ProbeConfig(
            controller_motion_parameters={
                "X": {"minimum_speed": 1, "work_speed": 30, "acceleration": 30},
                "Y": {"minimum_speed": 1, "work_speed": 30, "acceleration": 30},
                "Z": {"minimum_speed": 1, "work_speed": 30, "acceleration": 30},
            }
        )
        app.controller_motion_parameter_vars = {
            axis: {
                "minimum_speed": DummyVar("1"),
                "work_speed": DummyVar("30"),
                "acceleration": DummyVar("30"),
            }
            for axis in JOG_STEP_AXES
        }
        app.tx_var = DummyVar()
        app.rx_var = DummyVar()
        app.controller_motion_status_var = DummyVar()
        app.status_var = DummyVar()
        app._append_hex_history = MethodType(lambda self, direction, payload: None, app)

        ProbeApp._handle_worker_event(
            app,
            (
                "controller_motion_parameters",
                [
                    (
                        b"\x3a\xd5",
                        b"\xa3\xb4",
                        ControllerMotionParameters(
                            axis=Axis.X,
                            minimum_speed=0,
                            work_speed=10,
                            acceleration=30,
                            raw=b"",
                        ),
                    )
                ],
                "manual",
            ),
        )

        self.assertEqual(app.controller_motion_parameter_vars["X"]["work_speed"].get(), "10")
        self.assertEqual(app.probe_config.controller_motion_parameters["X"]["work_speed"], 30)
        self.assertIn("work 10", app.controller_motion_status_var.get())


if __name__ == "__main__":
    unittest.main()
