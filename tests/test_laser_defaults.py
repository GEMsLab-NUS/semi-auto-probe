import queue
import threading
import unittest
from unittest.mock import Mock

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.auto_test import AutoTestFlowStep, create_autotest_flow_card
from semi_auto_probe.dp100 import DP100Driver, DP100Reading, _silence_dotnet_console
from semi_auto_probe.measure_debug import MeasureDebugApp
from semi_auto_probe.waveform import parse_waveform_points, run_dp100_waveform
from semi_auto_probe.workflow_library import builtin_templates, cards_from_template


class LaserDefaultsTests(unittest.TestCase):
    def test_vendor_dotnet_hex_console_is_redirected(self):
        console = Mock()
        text_writer = Mock()
        text_writer.Null = object()

        _silence_dotnet_console(console, text_writer)

        console.SetOut.assert_called_once_with(text_writer.Null)
        console.SetError.assert_called_once_with(text_writer.Null)

    def test_dp100_read_output_uses_measured_callback_units(self):
        class API:
            ReceBasicInfoEvent = None
            def GetBasicInfo(self):
                self.ReceBasicInfoEvent(2475, 15)
                return True
        driver = DP100Driver.__new__(DP100Driver)
        driver._api = API()
        driver._reading_callback_type = lambda callback: callback
        self.assertEqual(driver.read_output(), DP100Reading(2.475, .015))

    def test_every_builtin_light_waveform_stays_within_module_default_profile(self):
        cards = [create_autotest_flow_card(kind, kind) for kind in ("laser_output", "laser_pulse")]
        for name in builtin_templates():
            cards.extend(cards_from_template(name))
        for card in cards:
            if card.type_id not in {"laser_output", "laser_pulse"}:
                continue
            with self.subTest(card=card.card_id):
                calibrated = card.params.get("program") == "calibrated_response_suite"
                self.assertLessEqual(float(card.params["peak_voltage_v"]), 3.3 if calibrated else 3.0)
                self.assertLessEqual(float(card.params["peak_current_a"]), .025 if calibrated else .010)
                self.assertEqual(float(card.params["ovp_v"]), 3.3)
                self.assertLessEqual(float(card.params["ocp_a"]), .028)
                if card.type_id == "laser_pulse":
                    for point in parse_waveform_points(card.params):
                        self.assertLessEqual(point.voltage_v, 3.3 if calibrated else 3.0)
                        self.assertLessEqual(point.current_a, .025 if calibrated else .010)

    def test_app_and_bench_apply_same_protection_before_output_with_missing_params(self):
        for kind in ("app", "bench"):
            with self.subTest(kind=kind):
                driver = Mock()
                step = AutoTestFlowStep("laser_output", {})
                if kind == "app":
                    app = ProbeApp.__new__(ProbeApp)
                    app._get_dp100_driver = lambda: driver
                    app.result_queue = queue.Queue()
                    app._execute_autotest_flow_step(None, None, step, None, None)
                else:
                    app = MeasureDebugApp.__new__(MeasureDebugApp)
                    app.driver = driver
                    app._run_light_step(step)
                calls = driver.mock_calls
                self.assertEqual([call[0] for call in calls], ["configure_limits", "set_output"])
                value = calls[1].args[0]
                self.assertEqual((value.voltage_mv, value.current_ma), (3000, 10))
                self.assertEqual(calls[0].kwargs, {"ovp_v": 3.3, "ocp_a": .025})

    def test_empty_waveform_uses_low_drive_and_turns_off_at_end(self):
        driver = Mock()
        points = parse_waveform_points({})
        self.assertEqual(max(point.voltage_v for point in points), 3.0)
        self.assertEqual(max(point.current_a for point in points), .010)
        wait = Mock(spec=threading.Event)
        wait.wait.return_value = False
        wait.is_set.return_value = False
        run_dp100_waveform(driver, {}, stop_event=wait)
        self.assertEqual(driver.configure_limits.call_args.kwargs, {"ovp_v": 3.3, "ocp_a": .025})
        self.assertFalse(driver.set_output.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
