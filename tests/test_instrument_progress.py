import unittest
from semi_auto_probe.instrument_control import ManualSession, ManualSweep, ManualSettings
import test_instrument_window as base


class ProgressTests(unittest.TestCase):
    def session(self, **sweep):
        s = ManualSession()
        s.shutdown()
        s.thread.join(1)
        s.sweep_values = ManualSweep(**sweep).values(ManualSettings())
        s._show_sweep_progress = True
        s.sweeping = True
        return s

    def test_short_first_leg_and_turning_points(self):
        s = self.session(start=0, stop=1, low=-1, step=.5, segments=3)
        # 0,.5,1 / .5,0 / -.5,-1: 7 actual reads.
        for index, segment in ((0,1), (2,1), (3,2), (4,2), (5,3), (6,3), (7,3)):
            s.sweep_index = index
            progress = s.sweep_progress()
            self.assertEqual(progress['sweep_segment_current'], segment)
            self.assertEqual(progress['sweep_segments_total'], 3)
            self.assertEqual(progress['sweep_points_total'], 7)
            self.assertEqual(progress['sweep_points_done'], index)
        s.sweeping = False
        s.sweep_index = 3  # Stopped immediately after reading the first turn.
        self.assertEqual(s.sweep_progress()['sweep_segment_current'], 1)

    def test_downward_uneven_steps_and_single_segment(self):
        s = self.session(start=.2, stop=1, low=-1, step=.7, direction='down', segments=2)
        s.sweep_index = 3
        self.assertEqual(s.sweep_progress()['sweep_segment_current'], 2)
        s = self.session(segments=1)
        s.sweep_index = len(s.sweep_values)
        s.sweeping = False
        self.assertEqual(s.sweep_progress()['sweep_segment_current'], 1)
        self.assertEqual(s.sweep_progress()['sweep_points_done'], s.sweep_progress()['sweep_points_total'])
        s._show_sweep_progress = False
        self.assertEqual(s.sweep_progress()['sweep_points_total'], 0)


class ProgressWindowTests(unittest.TestCase):
    setUp = base.InstrumentWindowTests.setUp
    tearDown = base.InstrumentWindowTests.tearDown
    pump = base.InstrumentWindowTests.pump
    open = base.InstrumentWindowTests.open

    def test_progress_uses_existing_gap_and_survives_compact(self):
        self.open()
        w = self.window
        before = w.canvas.get_tk_widget().winfo_height()
        payload = dict(sweep_points_total=100, sweep_points_done=37,
                       sweep_segment_current=3, sweep_segments_total=20)
        self.assertEqual(w._show_scan_progress(payload), ' · Segment 3/20')
        self.root.update()
        self.assertTrue(w.sweep_progress_track.winfo_ismapped())
        self.assertEqual(w.sweep_progress_track.winfo_height(), 2)
        self.assertEqual(w.canvas.get_tk_widget().winfo_height(), before)
        self.assertAlmostEqual(float(w.sweep_progress_fill.place_info()['relwidth']), .37)
        w.compact.set(True)
        w.set_compact()
        w.output_text.set('OUTPUT ON' + w._show_scan_progress(payload))
        self.root.update()
        self.assertTrue(w.sweep_progress_track.winfo_ismapped())
        self.assertTrue(w.monitor_off.winfo_ismapped())
        self.assertLessEqual(w.iv_x_box.winfo_rootx()+w.iv_x_box.winfo_width(), w.display.winfo_rootx()+w.display.winfo_width())
        w._show_scan_progress({})
        self.root.update()
        self.assertFalse(w.sweep_progress_track.winfo_ismapped())
