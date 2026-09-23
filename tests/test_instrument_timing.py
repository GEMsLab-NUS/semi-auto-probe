"""Timing regressions: deterministic clock plus real worker with a latency transport."""
import queue
import statistics
import time
import unittest
from unittest.mock import patch
from test_instrument_control import FakeVisa
from semi_auto_probe.instrument_control import ManualSession, ManualSettings, Keithley2450Manual, ManualSweep


class TimingTests(unittest.TestCase):
    def test_sample_period_is_start_to_start_not_post_read_sleep(self):
        s = ManualSession()
        s.shutdown(); s.thread.join(1)
        class Adapter:
            settings = ManualSettings()
            def measure(self): return (1., .001)
            def output_state(self): return True
        s.adapter = Adapter(); s.interval = .05; s.next_sample = 10.; s.segment_start = 10.
        s.acquiring = True
        with patch('semi_auto_probe.instrument_control.time.monotonic', side_effect=[10., 10.02]):
            s._sample()
        self.assertAlmostEqual(s.next_sample, 10.05)

    def test_continuous_and_sweep_have_no_50ms_poll_floor(self):
        for sweep in (False, True):
            class TimedVisa(FakeVisa):
                def __init__(self): super().__init__(); self.starts=[]
                def query(self, command):
                    if command.startswith(':READ?'):
                        self.starts.append(time.monotonic()); time.sleep(.004)
                    return super().query(command)
            visa = TimedVisa(); session = ManualSession(lambda _: Keithley2450Manual(visa))
            def wait(predicate):
                end = time.monotonic() + 3
                while not predicate():
                    if time.monotonic() >= end: self.fail('worker timed out')
                    time.sleep(.001)
            try:
                session.claim('fake'); wait(lambda: session.connected)
                session.submit('apply', ManualSettings()); wait(lambda: session.configured)
                if sweep: session.submit('sweep', ManualSweep(0,.2,.05,.005))
                else: session.submit('start', .05)
                wait(lambda: len(visa.starts) >= 5)
                gaps = [b-a for a,b in zip(visa.starts, visa.starts[1:])]
                self.assertLess(statistics.median(gaps), .07, gaps)
            finally:
                session.submit('release'); wait(lambda: not session.claimed)
                session.shutdown(); session.thread.join(1)

    def test_overrun_skips_slots_without_catchup_burst(self):
        session = ManualSession(); session.shutdown(); session.thread.join(1)
        class Adapter:
            def measure(self): return (1, .01)
            def output_state(self): return True
        session.adapter = Adapter(); session.acquiring = True
        session.interval = .05; session.next_sample = 10.; session.segment_start = 10.
        with patch('semi_auto_probe.instrument_control.time.monotonic', side_effect=[10., 10.121]):
            session._sample()
        self.assertAlmostEqual(session.next_sample, 10.15)
        self.assertEqual(session.missed_periods, 2)

    def test_one_query_preserves_status_errors_and_device_timestamp(self):
        class Visa:
            def __init__(self): self.queries=[]; self.response='1.2,0.002,123.45;1;0,"No error"'
            def query(self, command): self.queries.append(command); return self.response
        visa = Visa(); adapter = Keithley2450Manual(visa); adapter.settings = ManualSettings()
        self.assertEqual(adapter.measure_frame(), (1.2, .002, True, 123.45))
        self.assertEqual(len(visa.queries), 1)
        visa.response = '1.2,0.002,123.45;1;1131,"bad value;device timestamp"'
        with self.assertRaisesRegex(RuntimeError, 'bad value;device timestamp'):
            adapter.measure_frame()
        visa.response = '1.2,0.002,123.45;UNKNOWN;0,"No error"'
        with self.assertRaisesRegex(RuntimeError, 'Unknown output'):
            adapter.measure_frame()

    def test_device_clock_drives_elapsed_not_host_reply_time(self):
        session = ManualSession(); session.shutdown(); session.thread.join(1)
        class Adapter(Keithley2450Manual):
            def __init__(self): self.times=iter([100.,100.02]); self.settings=ManualSettings()
            def measure_frame(self): return 1., .001, True, next(self.times)
        session.adapter = Adapter(); session.segment_start=10.
        for host_time in (10.5, 10.9):
            with patch('semi_auto_probe.instrument_control.time.monotonic', return_value=host_time): session._sample()
        samples = [session.events.get()[1] for _ in range(2)]
        self.assertAlmostEqual(samples[1].elapsed_s, .02)
        self.assertAlmostEqual(samples[1].host_elapsed_s, .9)
        self.assertAlmostEqual(samples[1].sample_interval_s, .02)

    def test_iv_source_delay_is_not_double_counted_and_is_restored(self):
        visa = FakeVisa(); adapter = Keithley2450Manual(visa)
        adapter.apply(ManualSettings())
        visa.parameters[':SOUR:VOLT:DEL'] = '.123'
        visa.parameters[':SOUR:VOLT:DEL:AUTO'] = '1'
        adapter.begin_host_sweep()
        self.assertEqual(visa.parameters[':SOUR:VOLT:DEL'], '0')
        adapter.output(False)
        self.assertEqual(visa.parameters[':SOUR:VOLT:DEL'], '0.123')
        self.assertEqual(visa.parameters[':SOUR:VOLT:DEL:AUTO'], '1')

    def test_timer_resolution_is_balanced_and_only_requested_while_active(self):
        from semi_auto_probe.acquisition_timing import AcquisitionTimer
        from unittest.mock import Mock
        library = Mock()
        library.timeBeginPeriod.return_value = 0
        with patch('semi_auto_probe.acquisition_timing.os.name', 'nt'), patch('ctypes.WinDLL', return_value=library, create=True):
            timer = AcquisitionTimer()
            timer.set_active(False)
            library.timeBeginPeriod.assert_not_called()
            timer.set_active(True); timer.set_active(True)
            library.timeBeginPeriod.assert_called_once_with(1)
            timer.set_active(False); timer.set_active(False)
            library.timeEndPeriod.assert_called_once_with(1)
