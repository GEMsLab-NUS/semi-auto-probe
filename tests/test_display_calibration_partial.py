import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from semi_auto_probe.display_calibration import calibration_payload, CalibrationGate
from semi_auto_probe.gds_stage_mapper import AffineCoordinateMapper
from semi_auto_probe.app import ProbeApp

class PartialDisplayCalibrationTests(unittest.TestCase):
    def models(self):
        return (AffineCoordinateMapper(np.array([[10.,2.,0.],[-5.,0.,3.]])),
                SimpleNamespace(enabled=True,a=0.,b=0.,c=100.),
                SimpleNamespace(um_per_pulse=lambda axis: .5,motor_axis_polarity={a:1 for a in 'XYZ'}))

    def test_layout_bonding_publishes_uv_without_focus(self):
        mapper,_,config=self.models()
        payload=calibration_payload(mapper,None,config)
        self.assertIsNotNone(payload)
        self.assertEqual(payload['valid_axes'],{'U':True,'V':True,'F':False})
        self.assertFalse(payload['z_calibrated'])
        np.testing.assert_allclose(np.asarray(payload['matrix'])@np.array([40.,50.,100.,1.]),(5.,10.,0.))

    def test_focus_publishes_f_without_layout(self):
        _,plane,config=self.models()
        payload=calibration_payload(None,plane,config)
        self.assertIsNotNone(payload)
        self.assertEqual(payload['valid_axes'],{'U':False,'V':False,'F':True})
        self.assertAlmostEqual((np.asarray(payload['matrix'])@np.array([40.,50.,110.,1.]))[2],5.)

    def test_reset_allows_independent_recalibration_but_not_stale_sources(self):
        mapper,plane,config=self.models()
        gate=CalibrationGate(); gate.observe(mapper,plane,config); gate.invalidate()
        self.assertIsNone(gate.observe(mapper,plane,config)[0])
        new_mapper,_,_=self.models()
        payload,_=gate.observe(new_mapper,plane,config)
        self.assertIsNotNone(payload)
        self.assertEqual(payload['valid_axes'],{'U':True,'V':True,'F':False})

    def test_autofocus_success_publishes_zero_without_creating_motion_plane(self):
        import queue
        from unittest.mock import patch
        from semi_auto_probe.display_calibration import coordinate_basis
        _,_,config=self.models()
        app=ProbeApp.__new__(ProbeApp)
        app.probe_config=config
        app.serial_client=Mock()
        app.result_queue=queue.Queue()
        app._write_autofocus_history_file=Mock()
        app._run_autofocus_sequence=Mock(return_value={'usable':True,'stopped':False,'result_z':125.})
        app._autofocus_worker('Laplacian',10,1,100)
        event=app.result_queue.get_nowait()
        self.assertEqual(event[0],'display_focus_reference')
        app._accept_display_focus_reference(*event[1:])
        self.assertNotIn('sample_plane_model',app.__dict__)
        app.gds_stage_mapper_panel=None
        app._display_calibration_gate=CalibrationGate()
        app._display_calibration_publisher=Mock()
        app.after=Mock()
        with patch('semi_auto_probe.app.get_sample_plane_model',return_value=None):
            app._poll_display_calibration()
        payload=app._display_calibration_publisher.submit.call_args.args[0]
        self.assertEqual(payload['valid_axes'],{'U':False,'V':False,'F':True})
        self.assertAlmostEqual((np.asarray(payload['matrix'])@np.array([0.,0.,125.,1.]))[2],0.)

    def test_autofocus_failure_or_stop_does_not_set_display_zero(self):
        import queue
        for result in ({'usable':False,'stopped':False}, {'usable':True,'stopped':True}):
            app=ProbeApp.__new__(ProbeApp)
            app.probe_config=self.models()[2]
            app.serial_client=Mock()
            app.result_queue=queue.Queue()
            app._write_autofocus_history_file=Mock()
            app._run_autofocus_sequence=Mock(return_value=result)
            app._autofocus_worker('Laplacian',10,1,100)
            self.assertEqual(list(app.result_queue.queue),[('autofocus_done',)])

    def test_delayed_focus_result_is_rejected_after_coordinate_reset(self):
        from semi_auto_probe.display_calibration import coordinate_basis
        app=ProbeApp.__new__(ProbeApp)
        app.probe_config=self.models()[2]
        basis=coordinate_basis(app.probe_config)
        app._display_basis_generation=1
        app._accept_display_focus_reference(100.,basis,0)
        self.assertNotIn('_display_focus_reference',app.__dict__)
        app._accept_display_focus_reference(100.,basis,1)
        self.assertEqual(app._display_focus_reference.c,100.)

    def test_startup_sends_uncalibrated_once_and_does_not_stream_samples(self):
        from semi_auto_probe.display_calibration import CalibrationPublisher
        events=[]
        def request(method,path,payload=None):
            events.append((method,path,payload))
            if method=='GET':
                return {'session_id':'new-start','bridge_active':True,'serial_connected':True}
            return {'ok':True}
        publisher=CalibrationPublisher(request,autostart=False)
        publisher.submit(None)
        self.assertTrue(publisher.step())
        self.assertEqual([e[0] for e in events],['GET','DELETE'])
        mapper,_,config=self.models()
        calibration=calibration_payload(mapper,None,config)
        publisher.submit(calibration); publisher.step()
        count=len(events)
        for _ in range(100):
            publisher.submit(calibration); publisher.step()
        self.assertEqual(len(events),count)
        restarted=CalibrationPublisher(request,autostart=False)
        restarted.step()
        self.assertEqual(events[-1][0],'DELETE')

    def test_startup_restored_files_do_not_reenable_slave_until_new_calibration(self):
        from unittest.mock import patch
        from semi_auto_probe.display_calibration import DisplayFocusReference
        mapper,plane,config=self.models()
        app=ProbeApp.__new__(ProbeApp)
        app.probe_config=config
        app.gds_stage_mapper_panel=SimpleNamespace(mapper=mapper)
        app.restore_last_focusmap_layoutmap=Mock()
        app._display_calibration_gate=CalibrationGate()
        app._display_calibration_publisher=Mock()
        app.after=Mock()
        with patch('semi_auto_probe.app.get_sample_plane_model',return_value=plane):
            app._restore_startup_context_without_display_calibration()
            app._poll_display_calibration()
            app._display_calibration_publisher.submit.assert_called_with(None)
            app._display_focus_reference=DisplayFocusReference(c=125.)
            app._poll_display_calibration()
            payload=app._display_calibration_publisher.submit.call_args.args[0]
            self.assertEqual(payload['valid_axes'],dict(U=False,V=False,F=True))
            app.gds_stage_mapper_panel.mapper=self.models()[0]
            app._poll_display_calibration()
            payload=app._display_calibration_publisher.submit.call_args.args[0]
            self.assertEqual(payload['schema_version'],1)
