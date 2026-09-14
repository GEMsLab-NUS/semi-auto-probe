import json
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from semi_auto_probe.display_calibration import CalibrationGate, CalibrationPublisher, SshCalibrationHTTP, calibration_payload
from semi_auto_probe.gds_stage_mapper import AffineCoordinateMapper
from semi_auto_probe.serial_client import ControllerSerialClient
from semi_auto_probe.protocol import Axis, build_clear_position_command, build_go_home_command, build_read_position_command, build_stop_command


def models():
    mapper = AffineCoordinateMapper(np.array([[10., 2., .4], [-5., .3, 3.]]))
    plane = SimpleNamespace(enabled=True, a=.2, b=-.3, c=20.)
    config = SimpleNamespace(um_per_pulse=lambda axis: {"X": .1, "Y": .2, "Z": .05}[axis], motor_axis_polarity={"X": -1, "Y": 1, "Z": -1})
    return mapper, plane, config


class FakeHTTP:
    def __init__(self):
        self.session = "session1"
        self.events = []
        self.fail = None

    def __call__(self, method, path, payload=None):
        self.events.append((method, path, payload))
        if method == self.fail:
            self.fail = None
            raise OSError("synthetic HTTP failure")
        if method == "GET":
            return {"bridge_active": self.session is not None, "serial_connected": True, "session_id": self.session}
        if payload["session_id"] != self.session:
            raise OSError("409")
        return {"ok": True}


class DisplayCalibrationTests(unittest.TestCase):
    def test_blocked_http_never_holds_serial_reset_or_stop(self):
        from semi_auto_probe.app import ProbeApp
        entered, release = threading.Event(), threading.Event()
        http = FakeHTTP()
        def slow_http(method, path, payload=None):
            entered.set()
            release.wait(3)
            return http(method, path, payload)
        pub = CalibrationPublisher(slow_http)
        app = ProbeApp.__new__(ProbeApp)
        app.result_queue = queue.Queue()
        app._display_calibration_gate = CalibrationGate()
        app._display_calibration_publisher = pub
        client = ControllerSerialClient("fake")
        client.admin_mode_enabled = True
        client.before_coordinate_reset = app._before_display_coordinate_reset
        client._serial = Mock(is_open=True)
        reset, stop = build_clear_position_command(Axis.Z), build_stop_command(Axis.ALL, emergency=True)
        try:
            self.assertTrue(entered.wait(2))
            with patch("semi_auto_probe.app.clear_sample_plane_model"):
                reset_thread = threading.Thread(target=lambda: client.write_command(reset))
                reset_thread.start()
                reset_thread.join(timeout=.5)
                stop_thread = threading.Thread(target=lambda: client.write_command(stop))
                stop_thread.start()
                stop_thread.join(timeout=.5)
                self.assertFalse(reset_thread.is_alive())
                self.assertFalse(stop_thread.is_alive())
                self.assertFalse(release.is_set())
                self.assertEqual([call.args[0] for call in client._serial.write.call_args_list], [reset, stop])
        finally:
            release.set()
            pub.close()
            pub.thread.join(timeout=2)

    def test_http_work_runs_off_main_thread_and_submit_does_not_wait(self):
        entered, release = threading.Event(), threading.Event()
        threads = []
        http = FakeHTTP()
        def request(method, path, payload=None):
            threads.append(threading.current_thread())
            entered.set()
            release.wait(2)
            return http(method, path, payload)
        pub = CalibrationPublisher(request)
        try:
            pub.submit(calibration_payload(*models()))
            self.assertTrue(entered.wait(2))
            pub.invalidate()  # Must return while the HTTP worker is blocked.
            self.assertFalse(release.is_set())
            release.set()
            with pub.condition:
                self.assertTrue(pub.condition.wait_for(lambda: pub.attempted >= pub.revision, timeout=3))
            self.assertTrue(all(t is not threading.main_thread() for t in threads))
            self.assertNotIn("PUT", [e[0] for e in http.events])
        finally:
            release.set()
            pub.close()
            pub.thread.join(timeout=2)
        self.assertFalse(pub.thread.is_alive())

    def test_worker_reset_waits_for_revocation_attempt(self):
        http = FakeHTTP()
        pub = CalibrationPublisher(http)
        pub.submit(calibration_payload(*models()))
        with pub.condition:
            self.assertTrue(pub.condition.wait_for(lambda: pub.attempted >= pub.revision, timeout=2))
        before = len(http.events)
        thread = threading.Thread(target=lambda: pub.invalidate(wait=True))
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertIn("DELETE", [e[0] for e in http.events[before:]])
        pub.close()
        pub.thread.join(timeout=2)

    def test_app_observation_and_basis_event_clear_models(self):
        from semi_auto_probe.app import ProbeApp
        app = ProbeApp.__new__(ProbeApp)
        mapper, plane, config = models()
        app.probe_config = config
        app.gds_stage_mapper_panel = SimpleNamespace(mapper=mapper)
        app.sample_plane_model = plane
        app.status_var = Mock()
        app.after = Mock(return_value="scheduled")
        app._display_calibration_gate = CalibrationGate()
        app._display_calibration_publisher = Mock()
        with patch("semi_auto_probe.app.get_sample_plane_model", return_value=plane), patch("semi_auto_probe.app.clear_sample_plane_model") as clear:
            app._poll_display_calibration()
            sent = app._display_calibration_publisher.submit.call_args.args[0]
            self.assertEqual(sent, calibration_payload(mapper, plane, config))
            app._invalidate_display_basis("test reset")
            clear.assert_called()
            self.assertIsNone(app.gds_stage_mapper_panel.mapper)
            self.assertIsNone(app.sample_plane_model)
            app._display_calibration_publisher.invalidate.assert_called_once_with(wait=False)
            self.assertIsNone(app._display_calibration_gate.observe(mapper, plane, config)[0])

    def test_matrix_matches_existing_transforms_and_has_no_uvf_samples(self):
        mapper, plane, config = models()
        payload = calibration_payload(mapper, plane, config)
        raw = np.array([123., -57., 800., 1.])
        logical = raw[:3] * [-1, 1, -1]
        expected_uv = mapper.stage_to_gds(logical[0]*.1, logical[1]*.2)
        expected_f = .05 * (logical[2] - (plane.a*logical[0] + plane.b*logical[1] + plane.c))
        np.testing.assert_allclose(np.asarray(payload["matrix"]) @ raw, (*expected_uv, expected_f))
        self.assertEqual(payload["output_units"], {"U": "layout", "V": "layout", "F": "um"})
        self.assertNotIn("U", payload)
        self.assertEqual(calibration_payload(None, plane, config)["valid_axes"], {"U": False, "V": False, "F": True})
        plane.enabled = False
        self.assertEqual(calibration_payload(mapper, plane, config)["valid_axes"], {"U": True, "V": True, "F": False})

    def test_reset_blocks_old_models_even_across_disable_reenable(self):
        mapper, plane, config = models()
        gate = CalibrationGate()
        self.assertIsNotNone(gate.observe(mapper, plane, config)[0])
        gate.invalidate()
        self.assertIsNone(gate.observe(mapper, plane, config)[0])
        plane.enabled = False
        gate.observe(mapper, plane, config)
        plane.enabled = True
        self.assertIsNone(gate.observe(mapper, plane, config)[0])
        new_mapper, new_plane, _ = models()
        self.assertEqual(gate.observe(new_mapper, plane, config)[0]["valid_axes"], {"U": True, "V": True, "F": False})
        self.assertIsNotNone(gate.observe(new_mapper, new_plane, config)[0])

    def test_scale_and_polarity_changes_invalidate_both_models(self):
        for kind in ("scale", "polarity"):
            mapper, plane, config = models()
            gate = CalibrationGate()
            gate.observe(mapper, plane, config)
            if kind == "scale":
                config.um_per_pulse = lambda axis: .25
            else:
                config.motor_axis_polarity["X"] = 1
            payload, changed = gate.observe(mapper, plane, config)
            self.assertTrue(changed)
            self.assertIsNone(payload)
            self.assertIsNone(gate.observe(mapper, plane, config)[0])

    def test_publish_once_then_change_and_disable(self):
        http = FakeHTTP()
        pub = CalibrationPublisher(http, autostart=False)
        payload = calibration_payload(*models())
        pub.submit(payload)
        self.assertTrue(pub.step())
        count = len(http.events)
        for _ in range(100):
            pub.submit(payload)  # local observation after any XYZ update
            pub.step()
        self.assertEqual(len(http.events), count)
        mapper, plane, config = models()
        plane.c += 3
        pub.submit(calibration_payload(mapper, plane, config))
        self.assertTrue(pub.step())
        self.assertEqual([e[0] for e in http.events[-3:]], ["GET", "DELETE", "PUT"])
        pub.submit(None)
        pub.step()
        self.assertEqual(http.events[-1][0], "DELETE")

    def test_attach_retry_reconnect_and_failure_recovery(self):
        http = FakeHTTP()
        http.session = None
        pub = CalibrationPublisher(http, autostart=False)
        pub.submit(calibration_payload(*models()))
        self.assertFalse(pub.step())
        http.session = "new-session"
        http.fail = "PUT"
        self.assertFalse(pub.step())
        self.assertTrue(pub.step())
        self.assertEqual(http.events[-1][2]["session_id"], "new-session")
        pub.close()
        old_count = len(http.events)
        pub.step()
        self.assertEqual(len(http.events), old_count)
        reconnected = CalibrationPublisher(http, autostart=False)
        http.session = "reconnected"
        reconnected.submit(calibration_payload(*models()))
        reconnected.step()
        self.assertEqual(http.events[-1][2]["session_id"], "reconnected")

    def test_publisher_cannot_adopt_another_bridge_owner(self):
        http = FakeHTTP()
        pub = CalibrationPublisher(http, autostart=False)
        pub.submit(calibration_payload(*models()))
        self.assertTrue(pub.step())
        http.session = "another-controller"
        pub.invalidate()
        self.assertFalse(pub.step())
        count = len(http.events)
        pub.submit(calibration_payload(*models()))
        self.assertFalse(pub.step())
        self.assertEqual([e[0] for e in http.events[count:]], ["GET"])

    def test_invalidated_update_cannot_send_stale_put(self):
        http = FakeHTTP()
        pub = CalibrationPublisher(http, autostart=False)
        pub.submit(calibration_payload(*models()))
        def invalidate_during_delete(method, path, payload=None):
            value = http(method, path, payload)
            if method == "DELETE":
                pub.invalidate()
                pub.request = http
            return value
        pub.request = invalidate_during_delete
        self.assertFalse(pub.step())
        self.assertNotIn("PUT", [event[0] for event in http.events])
        self.assertTrue(pub.step())
        self.assertNotIn("PUT", [event[0] for event in http.events])

    def test_reset_delete_precedes_forwarding_and_http_failure_does_not_drop_bytes(self):
        for reset in (build_clear_position_command(Axis.Z), build_go_home_command()):
            http = FakeHTTP()
            pub = CalibrationPublisher(http, autostart=False)
            pub.submit(calibration_payload(*models()))
            pub.step()
            events = []
            client = ControllerSerialClient("fake")
            client.admin_mode_enabled = True
            client._serial = Mock(is_open=True)
            client._serial.write.side_effect = lambda data: events.append(("bytes", data))
            def invalidate():
                pub.invalidate()
                pub.step()
                events.append(("delete-attempt", None))
            client.before_coordinate_reset = invalidate
            client.send_raw(reset[:5], read_length=0)
            client.send_raw(reset[5:], read_length=0)
            self.assertEqual([e[0] for e in events], ["bytes", "delete-attempt", "bytes"])
            self.assertEqual(b"".join(e[1] for e in events if e[0] == "bytes"), reset)
            events.clear()
            http.fail = "DELETE"
            client.send_raw(reset, read_length=0)
            self.assertEqual(events[-1], ("bytes", reset))
            count = len(http.events)
            client.write_command(build_read_position_command(Axis.X))
            self.assertEqual(len(http.events), count)

    def test_ssh_json_is_stdin_and_errors_do_not_expose_remote_output(self):
        completed = SimpleNamespace(returncode=0, stdout='{"bridge_active":false}\n200', stderr="")
        with patch("semi_auto_probe.display_calibration.shutil.which", return_value="ssh"), patch("semi_auto_probe.display_calibration.subprocess.run", return_value=completed) as run:
            transport = SshCalibrationHTTP("user@host")
            transport("PUT", "/api/bridge/calibration", {"session_id": "abc", "matrix": [[1, 0, 0, 0]]*3})
            args, kwargs = run.call_args
            self.assertNotIn("abc", " ".join(args[0]))
            self.assertEqual(json.loads(kwargs["input"])["session_id"], "abc")
            completed.returncode = 1
            completed.stderr = "private authentication diagnostic"
            with self.assertRaisesRegex(RuntimeError, "SSH/HTTP") as error:
                transport("GET", "/api/bridge/status")
            self.assertNotIn("private", str(error.exception))


if __name__ == "__main__":
    unittest.main()
