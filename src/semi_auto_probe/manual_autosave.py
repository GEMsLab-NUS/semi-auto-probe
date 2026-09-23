"""Incremental manual-test persistence. Disk I/O never runs on Tk or VISA workers."""
from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import csv
import json
import logging
import os
import queue
import re
import threading
import time
import uuid

from .instrument_control import ManualSample
from .smu_settings import format_si_value


def default_save_root():
    return Path(__file__).resolve().parents[2] / 'manualtest_session'


def safe_name(value, limit=48):
    return re.sub(r'[^A-Za-z0-9_.+-]+', '-', str(value)).strip(' .-')[:limit] or 'device'


def run_summary(metadata):
    axes = metadata.get('configuration', {}).get('scan_axes', [])
    settings = axes[0]['settings'] if axes else metadata.get('settings', {})
    sweep = axes[0]['sweep'] if axes else metadata.get('sweep')
    unit = 'A' if settings.get('mode') == 'current' else 'V'
    value = lambda number: format_si_value(number, unit).replace(' ', '')
    if sweep:
        summary = f'IV-{unit}_start{value(sweep["start"])}_low{value(sweep["low"])}_top{value(sweep["stop"])}_{sweep["direction"]}_seg{sweep["segments"]}'
    else:
        summary = f'{metadata.get("kind", "manual")}_{value(settings.get("value", 0))}'
    if 'nplc' in settings:
        summary += f'_NPLC{settings["nplc"]:g}'
    return safe_name(('system_' if axes else '') + summary, 90)


def device_name(sample):
    model = '2450' if '2450' in sample.device else 'HP6614C' if '6614' in sample.device else 'DP100' if 'DP100' in sample.device.upper() else safe_name(sample.device, 16)
    address = sample.address.replace('::INSTR', '').replace('::', '-')
    return f'{model}_{safe_name(address, 28)}_CH{safe_name(sample.channel, 6)}'


class ManualAutoSave:
    def __init__(self, root=None, *, flush_interval=1.):
        self.root = Path(root) if root is not None else default_save_root()
        self.commands = queue.Queue()
        self.events = queue.Queue()
        self.flush_interval = flush_interval
        self._files = OrderedDict()
        self._paths = {}
        self._runs = {}
        self._last_record = -1
        self._failed = False
        self._recovery = ''
        self._closed = False
        # Non-daemon: normal application exit drains queued data before ending.
        self.thread = threading.Thread(target=self._run, name='manual-autosave', daemon=False)
        self.thread.start()

    def resume(self):
        self.commands.put(('resume', None))

    def append(self, index, sample, metadata):
        self.commands.put(('sample', (index, sample, metadata)))

    def backfill(self, offset, samples, metadata):
        self.commands.put(('batch', (offset, samples, metadata)))

    def flush(self):
        self.commands.put(('flush', None))

    def shutdown(self):
        if not self._closed:
            self._closed = True
            self.commands.put(('close', None))

    def _write(self, index, sample, metadata):
        if self._failed or index <= self._last_record:
            return
        if not metadata or not metadata.get('run_id'):
            raise ValueError('Acquisition configuration unavailable; data remain in application memory.')
        run_id = metadata['run_id']
        if run_id not in self._runs:
            started = datetime.fromisoformat(metadata.get('started_at', sample.timestamp)).astimezone()
            directory = self.root / started.strftime('%Y-%m-%d')
            directory.mkdir(parents=True, exist_ok=True)
            stamp = started.strftime('%H%M%S_') + f'{started.microsecond // 1000:03d}'
            base = directory / f'{stamp}_{run_summary(metadata)}_{safe_name(run_id, 8)}{self._recovery}'
            # Exclusive creation prevents accidental overwriting across launches.
            path = base.with_name(base.name + '_config.json')
            with path.open('x', encoding='utf-8') as handle:
                json.dump(dict(schema_version=1, run_id=run_id, acquisition=metadata,
                    date_basis='local time at acquisition start', data='raw samples while enabled plus retained backfill; no plot decimation'),
                    handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            self._runs[run_id] = base
            self.events.put(('saved', directory))
        key = (run_id, sample.address, sample.channel)
        if key not in self._files:
            base = self._runs[run_id]
            path = base.with_name(base.name + '__' + device_name(sample) + '.csv')
            if len(self._files) >= 16:
                old_key, (old_handle, _writer) = next(iter(self._files.items()))
                old_handle.flush()
                os.fsync(old_handle.fileno())
                old_handle.close()
                del self._files[old_key]
            existing = key in self._paths
            handle = path.open('a' if existing else 'x', newline='', encoding='utf-8' if existing else 'utf-8-sig')
            self._paths[key] = path
            writer = csv.DictWriter(handle, fieldnames=list(ManualSample.__dataclass_fields__))
            self._files[key] = (handle, writer)
            if not existing:
                writer.writeheader()
        self._files.move_to_end(key)
        self._files[key][1].writerow(asdict(sample))
        self._last_record = index

    def _flush(self):
        for handle, _writer in self._files.values():
            handle.flush()
            os.fsync(handle.fileno())

    def _close_files(self):
        for handle, _writer in self._files.values():
            try:
                handle.close()
            except OSError:
                pass
        self._files.clear()

    def _failure(self, exc):
        logging.getLogger(__name__).error('Manual AutoSave failed: %s', exc)
        self._failed = True
        self._close_files()
        self.events.put(('error', str(exc)))

    def _run(self):
        deadline = time.monotonic() + self.flush_interval
        while True:
            try:
                action, value = self.commands.get(timeout=max(0., deadline - time.monotonic()))
            except queue.Empty:
                action, value = 'flush', None
            try:
                if action == 'resume' and self._failed:
                    # Recovery gets new files and replays retained raw records,
                    # avoiding reliance on partially written/buffered CSV rows.
                    self._failed = False
                    self._last_record = -1
                    self._runs.clear()
                    self._paths.clear()
                    self._recovery = '_retry' + uuid.uuid4().hex[:6]
                elif action == 'sample':
                    self._write(*value)
                elif action == 'batch':
                    offset, samples, metadata = value
                    for index, sample in enumerate(samples, offset):
                        self._write(index, sample, metadata.get(sample.segment))
                        if time.monotonic() >= deadline:
                            self._flush()
                            deadline = time.monotonic() + self.flush_interval
                if action in {'flush', 'close'} or time.monotonic() >= deadline:
                    self._flush()
                    deadline = time.monotonic() + self.flush_interval
            except Exception as exc:
                self._failure(exc)
                deadline = time.monotonic() + self.flush_interval
            if action == 'close':
                self._close_files()
                return
