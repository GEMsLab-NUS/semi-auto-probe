"""Manual device groups. No Tk or new device connections outside ManualSession."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import csv
import io
import json
import math
import queue
import threading
import time
import uuid
import zipfile

from .instrument_control import ManualSession, ManualSample, ManualSettings
from .power_supply_control import supply_kind


class BusArbiter:
    """Non-preemptive, priority/FIFO scheduling of complete device transactions."""
    def __init__(self):
        self.condition = threading.Condition()
        self.waiters = {}
        self.active = set()
        self.serial = 0

    @staticmethod
    def key(address):
        # GPIB instruments share an interface; USB/TCP instruments do not.
        upper = address.upper()
        return upper.split('::')[0] if upper.startswith('GPIB') else upper

    @contextmanager
    def transaction(self, address, priority=1):
        key = self.key(address)
        with self.condition:
            self.serial += 1
            ticket = (priority, self.serial)
            waiting = self.waiters.setdefault(key, [])
            waiting.append(ticket)
            self.condition.wait_for(lambda: key not in self.active and ticket == min(waiting))
            waiting.remove(ticket)
            self.active.add(key)
        try:
            yield
        finally:
            with self.condition:
                self.active.remove(key)
                self.condition.notify_all()


@dataclass(frozen=True)
class SystemConfiguration:
    scanner: str
    settings: dict
    sweep: object
    auxiliary_order: tuple
    settle: dict
    auxiliary_interval: float = 1.

    def validate(self, sessions):
        if self.scanner not in sessions or not isinstance(self.settings.get(self.scanner), ManualSettings):
            raise ValueError('Select a Keithley 2450 as the scan device.')
        others = set(sessions) - {self.scanner}
        if set(self.auxiliary_order) != others or len(self.auxiliary_order) != len(others):
            raise ValueError('Each auxiliary must occur once in the startup order.')
        if set(self.settings) != set(sessions):
            raise ValueError('Apply parameters for every member.')
        if not math.isfinite(self.auxiliary_interval) or not .2 <= self.auxiliary_interval <= 60:
            raise ValueError('Auxiliary interval must be 0.2–60 s.')
        for address in others:
            delay = self.settle.get(address, .2)
            if not math.isfinite(delay) or delay < 0:
                raise ValueError('Settling times must be finite and nonnegative.')
        for settings in self.settings.values():
            settings.validate()
        self.sweep.values(self.settings[self.scanner])

    def snapshot(self, sessions):
        return dict(
            device_bindings=[dict(device_id=a, identity=s.identity,
                role='scan' if a == self.scanner else 'constant', channel=s.channel)
                for a, s in sessions.items()],
            constant_settings={a: asdict(self.settings[a]) for a in self.auxiliary_order},
            scan_axes=[dict(device_id=self.scanner, settings=asdict(self.settings[self.scanner]),
                            sweep=asdict(self.sweep))],
            startup_policy=dict(auxiliary_order=self.auxiliary_order, settle_s=self.settle,
                                auxiliary_interval_s=self.auxiliary_interval),
            end_policy='scanner_off_then_auxiliaries_reverse',
            clock='system_elapsed_s is host monotonic time; instrument clocks are independent')


class SystemStopped(Exception):
    pass


class InstrumentSystem:
    """Own all member claims synchronously; coordinate their independent workers."""
    def __init__(self, adapter_factory, *, command_timeout=30.):
        self.factory = adapter_factory
        self.bus = BusArbiter()
        self.sessions = {}
        self.events = queue.Queue()
        self.commands = queue.Queue()
        self.stop_event = threading.Event()
        self.halt = threading.Event()
        self.state = 'ready'
        self.fault = ''
        self.releasing = False
        self.command_timeout = command_timeout
        self.run_id = ''
        self.run_start = None
        self.configuration = None
        self.runs = []
        self.samples = []
        self._acks = {}
        self._errors = {}
        self._operation_fault = ''
        self._active_auxiliaries = set()
        self.thread = threading.Thread(target=self._run, daemon=True, name='instrument-system')
        self.thread.start()

    @property
    def fault(self):
        return self._fault or '; '.join(f'{a}: {s.fault}' for a, s in self.sessions.items() if s.claimed and s.fault)

    @fault.setter
    def fault(self, value):
        self._fault = value

    @property
    def claimed(self):
        return self.releasing or any(s.claimed for s in self.sessions.values())

    @property
    def acquiring(self):
        return self.state in {'starting', 'running', 'stopping'}

    @property
    def locked(self):
        return self.state in {'opening', 'applying', 'starting', 'running', 'stopping'} or self.releasing

    def open(self, rows):
        if self.claimed:
            raise ValueError('Release the current system before changing members.')
        if not 1 <= len(rows) <= 3 or len({r['address'] for r in rows}) != len(rows):
            raise ValueError('Select one to three distinct devices.')
        if sum(supply_kind(r['device']) == 'dp100' for r in rows) > 1:
            raise ValueError('Only one shared DP100 USB device is supported.')
        previous = self.sessions
        selected = {r['address'] for r in rows}
        for address, session in previous.items():
            if address not in selected:
                session.shutdown()
        self.sessions = {r['address']: previous[r['address']] if r['address'] in previous else
            ManualSession(self.factory, bus=self.bus, passive_open=True) for r in rows}
        # Reserve every member before a single open reaches a worker.
        for address, session in self.sessions.items():
            session.claimed = True
            session.address = address
        self.configuration = None
        self.run_start = None
        self._acks.clear()
        self._errors.clear()
        self._operation_fault = ''
        self._active_auxiliaries.clear()
        self.state = 'opening'
        self.commands.put(('open', None))

    def submit(self, action, value=None):
        if action in {'stop', 'off', 'release'}:
            self.stop_event.set()
            if action == 'release':
                self.releasing = True
        elif self.locked:
            raise ValueError('System is busy.')
        else:
            self.stop_event.clear()
            self.state = 'applying' if action in {'apply', 'apply_all'} else 'starting'
        self.commands.put((action, value))

    def _send(self, address, action, value=None):
        self._acks.pop((address, action), None)
        self._errors.pop(address, None)
        self.sessions[address].submit(action, value)

    def _drain(self):
        for address, session in self.sessions.items():
            for _ in range(500):
                try:
                    kind, payload = session.events.get_nowait()
                except queue.Empty:
                    break
                if kind in {'done', 'failed', 'cancelled'}:
                    self._acks[address, payload] = kind
                if kind == 'error':
                    self._errors[address] = str(payload)
                    self._operation_fault = f'{address}: {payload}'
                if kind == 'state' and address in self._active_auxiliaries and self.state in {'starting', 'running'} and payload['output'] is not True:
                    self._operation_fault = f'{address}: auxiliary output stopped or became unknown'
                if kind == 'sample' and self.run_start is not None:
                    payload = replace(payload, system_run_id=self.run_id, device_id=address,
                        device_role='scan' if address == self.configuration.scanner else 'constant',
                        system_elapsed_s=payload.host_monotonic_s - self.run_start, path_index=())
                    self.samples.append(payload)
                self.events.put(('member', (address, kind, payload)))

    def _wait(self, address, action, *, interruptible=True):
        deadline = time.monotonic() + self.command_timeout
        while True:
            self._drain()
            ack = self._acks.pop((address, action), None)
            if ack:
                if ack != 'done':
                    raise RuntimeError(self._errors.get(address, f'{address}: {action} failed'))
                return
            if interruptible and self.stop_event.is_set():
                raise SystemStopped()
            if interruptible and self._operation_fault:
                raise RuntimeError(self._operation_fault)
            if time.monotonic() >= deadline:
                raise TimeoutError(f'{address}: {action} timed out; ownership retained')
            self.halt.wait(.005)

    def _command(self, address, action, value=None, **options):
        if options.get('interruptible', True) and self.stop_event.is_set():
            raise SystemStopped()
        self._send(address, action, value)
        self._wait(address, action, **options)

    def _open(self):
        self.fault = ''
        try:
            for address in self.sessions:
                self._send(address, 'open', address)
            for address in self.sessions:
                self._wait(address, 'open', interruptible=False)
            for session in self.sessions.values():
                session.next_status = time.monotonic() + 1.
        except Exception:
            # Detach is queued behind open, never OFF. Existing outputs are untouched.
            for address in self.sessions:
                self._send(address, 'detach')
            for address in self.sessions:
                self._wait(address, 'detach', interruptible=False)
            raise

    def _apply(self, settings):
        for address, value in settings.items():
            if address not in self.sessions:
                raise ValueError('Unknown device.')
            value.validate()
        for address, value in settings.items():
            self._command(address, 'apply', value)

    def _start(self, config):
        config.validate(self.sessions)
        for address, session in self.sessions.items():
            if not session.connected or not session.configured or session.acquiring or session.adapter.settings != config.settings[address]:
                raise ValueError(f'{address}: apply current parameters before Start system.')
        # No output changes until all fresh output readbacks confirm OFF.
        for address in self.sessions:
            self._command(address, 'check')
        if any(s.output is not False for s in self.sessions.values()):
            raise ValueError('Every output must be confirmed OFF. Use All OFF before Start system.')
        self.configuration = config
        self.run_id = str(uuid.uuid4())
        self.run_start = time.monotonic()
        self.runs.append(dict(run_id=self.run_id, started_at=datetime.now(timezone.utc).isoformat(),
                              configuration=config.snapshot(self.sessions)))
        for address, session in self.sessions.items():
            session.io_priority = 0 if address == config.scanner else 1
        try:
            for address in config.auxiliary_order:
                self._command(address, 'on')
                self._active_auxiliaries.add(address)
                deadline = time.monotonic() + config.settle.get(address, .2)
                while time.monotonic() < deadline:
                    self._drain()
                    if self.stop_event.wait(min(.01, max(0., deadline - time.monotonic()))):
                        raise SystemStopped()
                    if self._operation_fault:
                        raise RuntimeError(self._operation_fault)
                self._command(address, 'start', config.auxiliary_interval)
            self._command(config.scanner, 'sweep', config.sweep)
            self.state = 'running'
            while self.sessions[config.scanner].acquiring:
                self._drain()
                if self._operation_fault:
                    raise RuntimeError(self._operation_fault)
                if self.stop_event.wait(.005):
                    raise SystemStopped()
            self._drain()
            if self._operation_fault:
                raise RuntimeError(self._operation_fault)
        finally:
            try:
                self._all_off(emergency=bool(self._operation_fault) or self.stop_event.is_set())
            finally:
                self.run_start = None

    def _all_off(self, *, release=False, emergency=False):
        self.state = 'stopping'
        self._active_auxiliaries.clear()
        config = self.configuration
        order = ([config.scanner] + list(reversed(config.auxiliary_order))) if config else list(reversed(self.sessions))
        errors = []
        action = 'release' if release else 'off'
        targets = [a for a in order if self.sessions[a].claimed]
        if emergency:
            for address in targets:
                self._send(address, action)
        for index, address in enumerate(targets):
            if not emergency:
                self._send(address, action)
            try:
                self._wait(address, action, interruptible=False)
            except Exception as exc:
                errors.append(f'{address}: {exc}')
                if isinstance(exc, TimeoutError):
                    self.sessions[address].output = None
                    self.sessions[address].fault = str(exc)
                if not emergency:
                    # Once shutdown fails, dispatch remaining requests immediately.
                    for remaining in targets[index + 1:]:
                        self._send(remaining, action)
                    emergency = True
        self.releasing = False
        if errors:
            raise RuntimeError('; '.join(errors))

    def _run(self):
        while not self.halt.is_set():
            self._drain()
            if self.state == 'ready' and self._operation_fault and self.claimed:
                original = self._operation_fault
                try:
                    self._all_off(emergency=True)
                except Exception as exc:
                    original += '; ' + str(exc)
                self.state = 'fault'
                self.fault = original
                self.events.put(('error', original))
            try:
                action, value = self.commands.get(timeout=.02)
            except queue.Empty:
                continue
            self._operation_fault = ''
            try:
                if action == 'open':
                    self._open()
                elif action in {'apply', 'apply_all'}:
                    self._apply(value)
                elif action == 'start':
                    self._start(value)
                elif action in {'stop', 'off', 'release'}:
                    if action == 'release':
                        self.releasing = True
                    self._all_off(release=action == 'release', emergency=True)
                elif action == 'retry':
                    self._command(value, 'release', interruptible=False)
                self.state = 'ready'
                self.fault = ''
                self._operation_fault = ''
                self.events.put(('done', action))
            except SystemStopped:
                self.state = 'ready'
                self.events.put(('done', 'stop'))
            except Exception as exc:
                message = str(exc)
                if action in {'apply', 'apply_all'}:
                    try:
                        self._all_off(emergency=True)
                    except Exception as cleanup:
                        message += '; ' + str(cleanup)
                self.state = 'fault'
                self.fault = message
                self.releasing = False
                self.events.put(('error', self.fault))
            self._drain()

    def export(self, path):
        samples, runs = tuple(self.samples), tuple(self.runs)
        export_system(path, samples, runs)

    def shutdown(self):
        if self.claimed:
            raise RuntimeError('Confirm every output OFF and release before shutdown.')
        self.halt.set()
        for session in self.sessions.values():
            session.shutdown()


def export_system(path, samples, runs):
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('configuration.json', json.dumps(dict(schema_version=1, runs=runs), indent=2))
        addresses = dict.fromkeys(p.device_id or p.address for p in samples)
        for run in runs:
            for binding in run['configuration']['device_bindings']:
                addresses.setdefault(binding['device_id'], None)
        for index, address in enumerate(addresses, 1):
            buffer = io.StringIO(newline='')
            writer = csv.DictWriter(buffer, fieldnames=list(ManualSample.__dataclass_fields__))
            writer.writeheader()
            writer.writerows(asdict(p) for p in samples if (p.device_id or p.address) == address)
            archive.writestr(f'device_{index}.csv', '\ufeff' + buffer.getvalue())
        archive.writestr('devices.json', json.dumps({f'device_{i}.csv': a for i, a in enumerate(addresses, 1)}, indent=2))
