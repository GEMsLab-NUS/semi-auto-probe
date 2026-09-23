import csv
import json
import queue
import tempfile
import time
import unittest
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from semi_auto_probe.instrument_control import ManualSample, ManualSettings, ManualSweep
from semi_auto_probe.manual_autosave import ManualAutoSave, default_save_root
import test_instrument_window as single
import test_instrument_system_window as group


def metadata(run='test-run-123', timestamp='2026-09-22T10:00:00+00:00'):
    return dict(run_id=run, started_at=timestamp, kind='iv', settings=asdict(ManualSettings()),
                sweep=asdict(ManualSweep(segments=3)))


def sample(index, segment=1):
    return ManualSample('2026-09-22T10:00:00+00:00', segment, index*.1,
        'KEITHLEY,MODEL 2450', 'GPIB0::18::INSTR', '1', index*.01, 1e-6)


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


class AutoSaveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / 'manualtest_session'
        self.writer = ManualAutoSave(self.root, flush_interval=.02)

    def tearDown(self):
        self.writer.shutdown()
        self.writer.thread.join(3)
        self.assertFalse(self.writer.thread.is_alive())
        self.directory.cleanup()

    def finish(self):
        self.writer.shutdown()
        self.writer.thread.join(3)
        errors = []
        while not self.writer.events.empty():
            kind, payload = self.writer.events.get_nowait()
            if kind == 'error': errors.append(payload)
        self.assertFalse(errors)

    def test_incremental_backfill_no_duplicates_and_full_config(self):
        points = tuple(sample(i) for i in range(10))
        meta = metadata()
        self.writer.append(0, points[0], meta)
        self.writer.backfill(0, points, {1: meta})
        self.writer.backfill(0, points, {1: meta})
        self.finish()
        files = list(self.root.rglob('*.csv'))
        self.assertEqual(len(files), 1)
        self.assertEqual(len(read_csv(files[0])), 10)
        self.assertIn('IV-V_start0V_low-1V_top1V_up_seg3_NPLC1', files[0].name)
        self.assertIn('2450_GPIB0-18_CH1', files[0].name)
        config = json.loads(next(self.root.rglob('*_config.json')).read_text(encoding='utf-8'))
        self.assertEqual(config['acquisition']['settings'], meta['settings'])
        self.assertEqual(config['acquisition']['sweep']['segments'], 3)

    def test_acquisition_date_and_same_time_distinct_runs(self):
        for i, timestamp in enumerate(('2026-09-20T12:00:00+00:00','2026-09-22T12:00:00+00:00','2026-09-22T12:00:00+00:00')):
            self.writer.append(i, sample(i, i+1), metadata(f'{i}-unique-run', timestamp))
        self.finish()
        files = list(self.root.rglob('*.csv'))
        self.assertEqual(len(files), 3)
        expected = {datetime.fromisoformat(t).astimezone().strftime('%Y-%m-%d') for t in
            ('2026-09-20T12:00:00+00:00','2026-09-22T12:00:00+00:00')}
        self.assertEqual({p.parent.name for p in files}, expected)

    def test_write_error_is_reported_and_reenable_replays_retained_samples(self):
        self.root.write_text('not a directory')
        self.writer.append(0, sample(0), metadata())
        kind, _payload = self.writer.events.get(timeout=2)
        self.assertEqual(kind, 'error')
        self.root.unlink()
        self.writer.resume()
        self.writer.backfill(0, (sample(0), sample(1)), {1: metadata()})
        self.finish()
        files = list(self.root.rglob('*.csv'))
        self.assertEqual(len(read_csv(files[0])), 2)
        self.assertIn('_retry', files[0].name)

    def test_periodic_flush_before_shutdown(self):
        self.writer.append(0, sample(0), metadata())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            files = list(self.root.rglob('*.csv'))
            if files and len(read_csv(files[0])) == 1:
                return
            time.sleep(.01)
        self.fail('Live sample was not flushed')

    def test_many_runs_reopen_evicted_files_without_duplicate_headers(self):
        for i in range(20):
            self.writer.append(i, sample(i, i+1), metadata(f'run-{i:04d}'))
        self.writer.append(20, sample(20, 1), metadata('run-0000'))
        self.finish()
        paths = list(self.root.rglob('*.csv'))
        self.assertEqual(len(paths), 20)
        self.assertEqual(sum(len(read_csv(p)) for p in paths), 21)

    def test_default_root_is_repository_not_current_directory(self):
        self.assertEqual(default_save_root(), Path(__file__).resolve().parents[1] / 'manualtest_session')


class AutoSaveWindowTests(unittest.TestCase):
    pump = single.InstrumentWindowTests.pump
    open = single.InstrumentWindowTests.open
    apply = single.InstrumentWindowTests.apply

    def setUp(self):
        single.InstrumentWindowTests.setUp(self)
        self.directory = tempfile.TemporaryDirectory()
        self.writer = self.window._autosaver = ManualAutoSave(self.directory.name, flush_interval=.02)

    def tearDown(self):
        single.InstrumentWindowTests.tearDown(self)
        self.writer.thread.join(3)
        self.directory.cleanup()

    def test_default_enabled_saves_without_checkbox_interaction(self):
        self.assertTrue(self.window.auto_save.get())
        self.assertTrue(self.window._autosave_enabled)
        self.open(); self.apply()
        self.window._send('once')
        self.pump(lambda: len(self.window.samples) == 1 and not self.window.pending)
        self.writer.shutdown(); self.writer.thread.join(3)
        paths = list(Path(self.directory.name).rglob('*.csv'))
        self.assertEqual(len(paths), 1)
        self.assertEqual(len(read_csv(paths[0])), 1)

    def test_enable_after_iv_and_replot_saves_actual_config_and_all_rows(self):
        self.window.auto_save.set(False)
        self.window._toggle_autosave()
        self.open()
        w = self.window
        w.function.set(single.IV_FUNCTION_LABELS['voltage'])
        w._mode_changed()
        w.sweep_stop.set('.2'); w.sweep_step.set('.1')
        self.apply()
        w.start_iv()
        self.pump(lambda: len(w.samples) == 3 and not w.session.acquiring)
        self.assertFalse(list(Path(self.directory.name).rglob('*.csv')))
        w.replot()
        w.nplc.set('10'); w.sweep_stop.set('7')
        w.auto_save.set(True); w._toggle_autosave()
        self.writer.shutdown(); self.writer.thread.join(3)
        paths = list(Path(self.directory.name).rglob('*.csv'))
        self.assertEqual(len(read_csv(paths[0])), 3)
        config = json.loads(next(Path(self.directory.name).rglob('*_config.json')).read_text(encoding='utf-8'))
        self.assertEqual(config['acquisition']['settings']['nplc'], 1)
        self.assertEqual(config['acquisition']['sweep']['stop'], .2)
        w.compact.set(True); w.set_compact(); self.root.update()
        self.assertTrue(w.autosave_check.winfo_ismapped())
        self.assertLessEqual(w.autosave_check.winfo_rootx()+w.autosave_check.winfo_width(), w.display.winfo_rootx()+w.display.winfo_width())

    def test_pending_tail_is_saved_without_another_tk_poll(self):
        self.open(); self.apply()
        w = self.window
        w.auto_save.set(True); w._toggle_autosave()
        self.session.submit('once')
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with self.session.events.mutex:
                acquired = any(kind == 'sample' for kind, payload in self.session.events.queue)
            if acquired:
                break
            time.sleep(.01)
        self.assertTrue(acquired)
        self.assertFalse(w.samples)
        self.session.submit('release')
        while self.session.claimed and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.session.claimed)
        w._drain_autosave_tail()
        self.writer.shutdown(); self.writer.thread.join(3)
        paths = list(Path(self.directory.name).rglob('*.csv'))
        self.assertEqual(len(paths), 1)
        self.assertEqual(len(read_csv(paths[0])), 1)



class AutoSaveGroupWindowTests(unittest.TestCase):
    pump = group.SystemWindowTests.pump
    open = group.SystemWindowTests.open

    def setUp(self):
        group.SystemWindowTests.setUp(self)
        self.directory = tempfile.TemporaryDirectory()
        self.writer = self.window._autosaver = ManualAutoSave(self.directory.name)

    def tearDown(self):
        group.SystemWindowTests.tearDown(self)
        self.writer.thread.join(3)
        self.directory.cleanup()

    def test_group_saves_all_devices_with_one_shared_configuration(self):
        self.open()
        w = self.window
        w.auto_save.set(True); w._toggle_autosave()
        w.sweep_stop.set('.1'); w.sweep_step.set('.1')
        w._system_apply(True)
        self.pump(lambda: not w.pending)
        w._start_system()
        self.pump(lambda: bool(w.system.runs) and not w.pending)
        self.writer.shutdown(); self.writer.thread.join(3)
        files = list(Path(self.directory.name).rglob('*.csv'))
        self.assertEqual(len(files), 3)
        self.assertEqual(sum(len(read_csv(p)) for p in files), len(w.system.samples))
        configs = list(Path(self.directory.name).rglob('*_config.json'))
        self.assertEqual(len(configs), 1)
        snapshot = json.loads(configs[0].read_text(encoding='utf-8'))['acquisition']['configuration']
        self.assertEqual(len(snapshot['device_bindings']), 3)
        self.assertEqual(snapshot['scan_axes'][0]['sweep']['stop'], .1)
