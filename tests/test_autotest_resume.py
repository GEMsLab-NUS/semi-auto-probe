import json
import queue
import tempfile
import threading
from dataclasses import replace
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from semi_auto_probe.auto_test import AutoTestFlowStep, AutoTestPanel, AutoTestPoint, AutoTestSettings, measurement_flow_steps_from_cards
from semi_auto_probe.app import ProbeApp
from semi_auto_probe.keithley2450 import iv_sweep_config_from_params
from semi_auto_probe.autotest_resume import (
    completed_prefix, create_manifest, mark_completed, point_specs_from_manifest,
    read_manifest, settings_from_manifest, validate_manifest,
)


class AutoTestResumeTests(unittest.TestCase):
    def settings(self):
        return AutoTestSettings(0, 0, 10, 0, 0, 10, 1, 3, 100, 100, 500, 50, 50, 20,
                                'Dev{i}{j}', measurement_flow=(
                                    AutoTestFlowStep('workflow_start', {'_next': 'iv'}),
                                    AutoTestFlowStep('iv', {'_next': 'end'}),
                                    AutoTestFlowStep('workflow_end', {}),
                                    AutoTestFlowStep('plot_panel', {'_input_slot1_source': 'iv'}),
                                ))

    def points(self):
        return tuple(AutoTestPoint(0, i, i + 1, f'Dev{i}', i * 10., 0., i * 100., 0., ())
                     for i in range(3))

    def write_history(self, directory, point, status='done'):
        folder = directory / 'workflow_plots'
        folder.mkdir(exist_ok=True)
        (folder / f'{point.row}_{point.col}.json').write_text(json.dumps({
            'point': {'row': point.row, 'col': point.col}, 'status': status,
            'curves': {}, 'panels': {},
        }), encoding='utf-8')
        if status == 'done':
            iv = directory / 'iv'
            iv.mkdir(exist_ok=True)
            (iv / f'{point.name}_iv.json').write_text(json.dumps({
                'result_type': 'iv_sweep',
                'device': {'name': point.name, 'order': point.order, 'row': point.row, 'col': point.col},
                'coordinates': {'gds': {'u': point.u, 'v': point.v}},
                'measurement': dict(
                    resource=iv_sweep_config_from_params(self.settings().measurement_flow[1].params).resource_name,
                    **{key: getattr(iv_sweep_config_from_params(self.settings().measurement_flow[1].params), key)
                       for key in ('output_terminal', 'sweep_mode', 'start', 'stop', 'step', 'low',
                                   'segments', 'source_range', 'measure_range', 'voltage_limit_v', 'current_limit_a')}),
            }), encoding='utf-8')

    def test_legacy_session_skips_only_contiguous_done_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            points = self.points()
            self.write_history(directory, points[0])
            self.write_history(directory, points[1])
            self.write_history(directory, points[2], 'failed')
            self.assertEqual(completed_prefix(directory, self.settings(), points), points[:2])
            (directory / 'workflow_plots' / '0_1.json').unlink()
            self.assertEqual(completed_prefix(directory, self.settings(), points), points[:1])
            sidecar = directory / "iv" / "Dev0_iv.json"
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            payload["device"]["name"] = "different device"
            sidecar.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Cannot verify completed IV result"):
                completed_prefix(directory, self.settings(), points)

    def test_legacy_mismatched_iv_scan_cannot_be_resumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            points = self.points()
            self.write_history(directory, points[0])
            sidecar = directory / "iv" / "Dev0_iv.json"
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            payload["measurement"]["stop"] = 20.0
            sidecar.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "sweep settings"):
                completed_prefix(directory, self.settings(), points)

    def test_manifest_keeps_settings_and_requires_plot_result_before_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            points = self.points()
            settings = self.settings()
            create_manifest(directory, settings, points)
            self.write_history(directory, points[0])
            mark_completed(directory, points[0])
            mark_completed(directory, points[1])
            data = read_manifest(directory)
            self.assertEqual(data['completed'], [1, 2])
            self.assertEqual(settings_from_manifest(data), settings.normalized())
            self.assertEqual([item.name for item in point_specs_from_manifest(data)], ['Dev0', 'Dev1', 'Dev2'])
            self.assertEqual(completed_prefix(directory, settings, points, data), points[:1])
            recalibrated = tuple(AutoTestPoint(p.row, p.col, p.order, p.name, p.u, p.v,
                                               p.stage_x_um + 5, p.stage_y_um + 5, ()) for p in points)
            validate_manifest(data, settings, recalibrated)
            changed = list(recalibrated)
            changed[0] = AutoTestPoint(0, 0, 1, 'wrong', 0., 0., 0., 0., ())
            with self.assertRaises(ValueError):
                validate_manifest(data, settings, changed)

    def test_resume_button_passes_selected_session_directory(self):
        panel = AutoTestPanel.__new__(AutoTestPanel)
        panel._prerequisites_ready = lambda: True
        selected = []
        panel.resume_run = selected.append
        with patch("semi_auto_probe.auto_test.filedialog.askdirectory", return_value="C:/runs/autotest_session/old"):
            panel._resume_run()
        self.assertEqual(selected, [Path("C:/runs/autotest_session/old")])

    def test_loaded_resume_populates_test_point_separation_and_flow(self):
        class FakeVar:
            def __init__(self, value=None):
                self.value = value
            def set(self, value):
                self.value = value
            def get(self):
                return self.value
        panel = AutoTestPanel.__new__(AutoTestPanel)
        for name in (
            "origin_u_var", "origin_v_var", "u_vector_u_var", "u_vector_v_var",
            "v_vector_u_var", "v_vector_v_var", "rows_var", "cols_var",
            "z_down_margin_var", "z_up_fast_percent_var", "z_fast_speed_percent_var",
            "z_slow_speed_percent_var", "z_wobble_um_var", "z_wobble_cycles_var",
            "z_offset_um_var", "fov_width_var", "fov_height_var", "name_pattern_var",
            "point_source_var", "execution_progress_text_var", "execution_progress_var",
            "measure_pause_var", "measure_photo_var", "measurement_var",
            "z_up_fast_percent_text_var",
        ):
            setattr(panel, name, FakeVar())
        panel.measurement_flow_cards = []
        panel.start_resume_button = SimpleNamespace(configure=lambda **kwargs: None)
        panel.redraw_preview = lambda: None
        panel._draw_z_wobble_curve = lambda: None
        flow = tuple(AutoTestFlowStep(step.type_id, {
            **step.params, '_node_id': f'flow_{index}', '_x': str(index * 100), '_y': '0',
        }) for index, step in enumerate(self.settings().measurement_flow, start=1))
        settings = replace(self.settings(), origin_u=1160., origin_v=960.,
                           rows=18, cols=12, z_offset_um=20.,
                           measurement_flow=flow).normalized()
        specs = tuple(point_specs_from_manifest({
            'points': [{'name': p.name, 'u': p.u, 'v': p.v, 'row': p.row, 'col': p.col}
                       for p in self.points()]
        }))
        directory = Path("C:/runs/20260922_212103")
        panel.load_resume_plan(directory, settings, specs, 174, 216)
        self.assertEqual(panel.origin_u_var.get(), '1160')
        self.assertEqual(panel.rows_var.get(), '18')
        self.assertEqual(panel.cols_var.get(), '12')
        self.assertEqual(panel.z_offset_um_var.get(), '20')
        self.assertEqual(panel.custom_point_specs, specs)
        loaded_flow = measurement_flow_steps_from_cards(panel.measurement_flow_cards)
        self.assertEqual([step.type_id for step in loaded_flow],
                         [step.type_id for step in settings.measurement_flow])
        for loaded_step, saved_step in zip(loaded_flow, settings.measurement_flow):
            self.assertTrue(saved_step.params.items() <= loaded_step.params.items())
        self.assertEqual(panel.loaded_resume_dir, directory)
        self.assertEqual(panel.settings_from_ui(), settings)
        self.assertIn('174/216', panel.execution_progress_text_var.get())

    def test_start_and_resume_share_one_session_with_original_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            settings = replace(self.settings(), measurement_flow=())
            points = self.points()
            app = ProbeApp.__new__(ProbeApp)
            app._manual_instrument_blocks_measurement = lambda: False
            app._device_scan_in_progress = lambda: False
            app.probe_config = SimpleNamespace(wire_bond_enabled=False)
            app.autotest_running = False
            app.motion_busy = False
            app.gds_stage_mapper_panel = SimpleNamespace(mapper=object())
            app.serial_client = object()
            app.realtime_enabled = False
            app.home_signal_enabled = False
            app.autotest_stop_event = threading.Event()
            app.workflow_buttons = []
            app._update_admin_mode_controls = lambda: None
            app._prepare_autotest_session_dir = lambda: directory
            app.status_var = SimpleNamespace(set=lambda message: None)
            statuses = []
            app.autotest_panel = SimpleNamespace(set_running=lambda state: None,
                set_status=statuses.append, set_progress=lambda *args, **kwargs: None,
                _schedule_preview_redraw=lambda: None, point_overlay_states={})
            threads = []
            class FakeThread:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs
                    threads.append(self)
                def start(self):
                    pass
            with patch("semi_auto_probe.app.get_sample_plane_model", return_value=object()), patch(
                "semi_auto_probe.app.threading.Thread", FakeThread):
                app.start_autotest(settings, points)
                self.assertIsNotNone(read_manifest(directory))
                mark_completed(directory, points[0])
                mark_completed(directory, points[1])
                app.autotest_running = False
                app.motion_busy = False
                app.start_autotest(settings, points, resume_session_dir=directory)
            self.assertEqual(len(threads), 2)
            self.assertEqual(threads[1].kwargs["args"][2], directory.resolve())
            self.assertEqual(app._autotest_resume_completed, 2)
            self.assertEqual(app.autotest_progress_current, 2)
            self.assertIn("next Dev2", statuses[-1])

    def test_resume_loads_plan_before_start_and_uses_current_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            create_manifest(directory, self.settings(), self.points())
            mapper = SimpleNamespace(gds_to_stage=lambda u, v: (u + 100., v + 200.),
                                     stage_to_gds=lambda x, y: (x - 100., y - 200.))
            app = ProbeApp.__new__(ProbeApp)
            app.gds_stage_mapper_panel = SimpleNamespace(mapper=mapper)
            app._camera_fov_rotation_deg = lambda: 0.
            loaded = []
            panel = SimpleNamespace(
                loaded_resume_dir=None, custom_point_specs=None,
                clear_resume_plan=lambda: None, set_status=lambda message: None,
                open_measurement_flow=lambda: None,
                settings_from_ui=lambda: self.settings().normalized(),
            )
            def load_plan(session_dir, settings, specs, completed, total):
                loaded.append((session_dir, settings, specs, completed, total))
                panel.loaded_resume_dir = session_dir
                panel.custom_point_specs = specs
            panel.load_resume_plan = load_plan
            app.autotest_panel = panel
            calls = []
            app.start_autotest = lambda settings, points, **kwargs: calls.append((settings, points, kwargs))
            app.resume_autotest(directory)
            self.assertFalse(calls)
            self.assertEqual((loaded[0][3], loaded[0][4]), (0, 3))
            self.assertEqual(loaded[0][2][1].name, 'Dev1')
            app.start_loaded_autotest_resume()
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], self.settings().normalized())
            self.assertEqual([point.stage_x_um for point in calls[0][1]], [100., 110., 120.])
            self.assertEqual(calls[0][2]["resume_session_dir"], directory.resolve())

    def test_recovered_session_requires_motion_settings_review_before_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            create_manifest(directory, self.settings(), self.points())
            path = directory / 'autotest_resume.json'
            data = json.loads(path.read_text(encoding='utf-8'))
            data['recovery'] = {'source': 'legacy_iv_sidecars', 'motion_settings_inferred': True}
            path.write_text(json.dumps(data), encoding='utf-8')
            mapper = SimpleNamespace(gds_to_stage=lambda u, v: (u, v),
                                     stage_to_gds=lambda x, y: (x, y))
            app = ProbeApp.__new__(ProbeApp)
            app.gds_stage_mapper_panel = SimpleNamespace(mapper=mapper)
            app._camera_fov_rotation_deg = lambda: 0.
            statuses = []
            panel = SimpleNamespace(
                loaded_resume_dir=None, custom_point_specs=None,
                clear_resume_plan=lambda: None, set_status=statuses.append,
                open_measurement_flow=lambda: None,
                settings_from_ui=lambda: self.settings().normalized(),
            )
            def load_plan(session_dir, settings, specs, completed, total):
                panel.loaded_resume_dir = session_dir
                panel.custom_point_specs = specs
            panel.load_resume_plan = load_plan
            app.autotest_panel = panel
            calls = []
            app.start_autotest = lambda *args, **kwargs: calls.append((args, kwargs))
            app.resume_autotest(directory)
            self.assertFalse(calls)
            with patch('semi_auto_probe.app.messagebox.askyesno', return_value=False) as prompt:
                app.start_loaded_autotest_resume()
            self.assertFalse(calls)
            self.assertIn('Z down', prompt.call_args.args[1])
            with patch('semi_auto_probe.app.messagebox.askyesno', return_value=True):
                app.start_loaded_autotest_resume()
            self.assertEqual(len(calls), 1)

    def test_loaded_resume_rejects_changed_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            create_manifest(directory, self.settings(), self.points())
            mapper = SimpleNamespace(gds_to_stage=lambda u, v: (u, v),
                                     stage_to_gds=lambda x, y: (x, y))
            app = ProbeApp.__new__(ProbeApp)
            app.gds_stage_mapper_panel = SimpleNamespace(mapper=mapper)
            app._camera_fov_rotation_deg = lambda: 0.
            statuses = []
            panel = SimpleNamespace(
                loaded_resume_dir=None, custom_point_specs=None,
                clear_resume_plan=lambda: None, set_status=statuses.append,
                open_measurement_flow=lambda: None,
                settings_from_ui=lambda: replace(self.settings().normalized(), z_offset_um=99.),
            )
            def load_plan(session_dir, settings, specs, completed, total):
                panel.loaded_resume_dir = session_dir
                panel.custom_point_specs = specs
            panel.load_resume_plan = load_plan
            app.autotest_panel = panel
            app.start_autotest = lambda *args, **kwargs: self.fail("must not start")
            app.resume_autotest(directory)
            app.start_loaded_autotest_resume()
            self.assertIn("Displayed AutoTest plan changed", statuses[-1])

    def test_worker_resumes_at_first_unfinished_point_and_checkpoints_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            points = self.points()
            settings = self.settings()
            create_manifest(directory, settings, points, (1, 2))
            self.write_history(directory, points[0])
            self.write_history(directory, points[1])
            app = ProbeApp.__new__(ProbeApp)
            app.serial_client = SimpleNamespace(read_stable_xyz_positions=lambda: {})
            app.probe_config = SimpleNamespace(um_per_pulse=lambda axis: 1.)
            app.result_queue = queue.Queue()
            app.autotest_stop_event = threading.Event()
            app._autotest_resume_completed = len(completed_prefix(directory, settings, points, read_manifest(directory)))
            app._axis_from_position_entries = lambda entries, axis: 0
            app._focusmap_z_target_at_xy = lambda x, y: -50
            app._move_absolute_stage = lambda *args, **kwargs: {}
            app._autotest_raise_z_split = lambda *args: {}
            app._autotest_apply_contact_wobble_and_offset = lambda *args: {}
            app._autotest_measurement_summary = lambda settings: "Measured"
            visited = []
            def measure(point, settings, session_dir, cv2_module):
                visited.append((point.name, session_dir))
                self.write_history(session_dir, point)
            app._execute_autotest_measurement_flow = measure
            app._autotest_worker(settings, points, directory)
            self.assertEqual(visited, [("Dev2", directory)])
            self.assertEqual(read_manifest(directory)["completed"], [1, 2, 3])
            events = list(app.result_queue.queue)
            self.assertIn(("autotest_done", 3), events)
            self.assertFalse(any(event[0] == "autotest_error" for event in events))

    def test_remote_stage_disconnect_pauses_without_repeating_point(self):
        import serial
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            points = self.points()
            settings = self.settings()
            create_manifest(directory, settings, points)
            app = ProbeApp.__new__(ProbeApp)
            app.serial_client = SimpleNamespace(
                port="socket://127.0.0.1:1234",
                read_stable_xyz_positions=lambda: {},
            )
            app.probe_config = SimpleNamespace(um_per_pulse=lambda axis: 1.)
            app.result_queue = queue.Queue()
            app.autotest_stop_event = threading.Event()
            app._axis_from_position_entries = lambda entries, axis: 0
            app._focusmap_z_target_at_xy = lambda x, y: -50
            motion_calls = []
            def disconnected_motion(*args, **kwargs):
                motion_calls.append(args)
                raise serial.SerialException(
                    "read failed: [WinError 10054] An existing connection was forcibly closed"
                )
            app._move_absolute_stage = disconnected_motion
            app._autotest_raise_z_split = lambda *args: {}
            app._autotest_apply_contact_wobble_and_offset = lambda *args: {}
            app._execute_autotest_measurement_flow = lambda *args: self.fail("measurement must not start")
            recovered = []
            app._reconnect_autotest_stage_transport_without_motion = lambda: recovered.append(True)
            app._autotest_worker(settings, points, directory)
            events = list(app.result_queue.queue)
            self.assertEqual(len(motion_calls), 1)
            self.assertEqual(recovered, [True])
            self.assertEqual(read_manifest(directory)["completed"], [])
            lost = [event for event in events if event[0] == "autotest_transport_lost"]
            self.assertEqual(len(lost), 1)
            self.assertEqual(lost[0][3], directory)
            self.assertFalse(any(event[0] == "autotest_done" for event in events))

    def test_remote_stage_reconnect_checks_positions_without_motion(self):
        old_client = SimpleNamespace(port="socket://127.0.0.1:1000", close=lambda: None)
        old_tunnel = SimpleNamespace(ssh_target="probe@test", close=lambda: None)
        calls = []
        class FakeTunnel:
            def __init__(self, target):
                calls.append(("tunnel", target))
                self.serial_url = "socket://127.0.0.1:2000"
            def start(self):
                calls.append(("start",))
            def wait_for_serial_open(self, client):
                calls.append(("open", client.port))
            def close(self):
                calls.append(("close",))
        class FakeClient:
            def __init__(self, port):
                self.port = port
                self.before_coordinate_reset = None
                calls.append(("client", port))
            def set_admin_mode_enabled(self, enabled):
                calls.append(("admin", enabled))
            def communication_test(self):
                calls.append(("test",))
                return SimpleNamespace(ok=True, message="ok")
            def read_stable_xyz_positions(self):
                calls.append(("read_positions",))
                return []
            def close(self):
                calls.append(("client_close",))
        app = ProbeApp.__new__(ProbeApp)
        app.serial_client = old_client
        app.ssh_serial_tunnel = old_tunnel
        app.autotest_stop_event = threading.Event()
        app.result_queue = queue.Queue()
        app.admin_mode_enabled = False
        app._before_display_coordinate_reset = lambda: None
        with patch("semi_auto_probe.app.SshSerialTunnel", FakeTunnel), patch(
                "semi_auto_probe.app.ControllerSerialClient", FakeClient):
            app._reconnect_autotest_stage_transport_without_motion()
        self.assertIsInstance(app.serial_client, FakeClient)
        self.assertIsInstance(app.ssh_serial_tunnel, FakeTunnel)
        self.assertIn(("test",), calls)
        self.assertIn(("read_positions",), calls)
        self.assertFalse(any(call[0] in {"move", "move_relative", "move_absolute"} for call in calls))

    def test_disconnect_classifier_only_accepts_remote_serial_errors(self):
        import serial
        remote = SimpleNamespace(port="socket://127.0.0.1:9500")
        local = SimpleNamespace(port="COM7")
        error = serial.SerialException("read failed: [WinError 10054] connection reset")
        self.assertTrue(ProbeApp._is_remote_stage_disconnect(error, remote))
        self.assertFalse(ProbeApp._is_remote_stage_disconnect(error, local))
        self.assertFalse(ProbeApp._is_remote_stage_disconnect(RuntimeError(str(error)), remote))

    def test_invalid_new_plan_cannot_replace_existing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            create_manifest(directory, self.settings(), self.points())
            before = (directory / 'autotest_resume.json').read_bytes()
            with self.assertRaises(FileExistsError):
                create_manifest(directory, self.settings(), self.points())
            self.assertEqual((directory / 'autotest_resume.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
