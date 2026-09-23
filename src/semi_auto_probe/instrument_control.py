"""Serialized manual instrument sessions for the desktop Instrument window."""
from __future__ import annotations

import csv
import math
import logging
import queue
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from collections.abc import Sequence
from datetime import datetime, timezone

from .keithley2450 import Keithley2450IVRunner
from .acquisition_timing import AcquisitionTimer


logger = logging.getLogger(__name__)


class ExistingInstrumentErrors(RuntimeError):
    """Errors present before this operation; no configuration has been sent."""


def is_keithley2450(identity: str) -> bool:
    parts = identity.upper().split(',')
    return len(parts) >= 2 and 'KEITHLEY' in parts[0] and parts[1].strip() in {'2450', 'MODEL 2450'}


from .smu_settings import (
    VOLTAGE_RANGES, CURRENT_RANGES, FUNCTION_LABELS, SI_FACTORS,
    parse_si_value, format_si_value, StableReading, SweepPoints, ManualSweep,
    ManualSettings, supported_ranges, range_options, MAX_SWEEP_POINTS, MAX_SWEEP_SEGMENTS,
)


class Keithley2450Manual:
    """Native SCPI adapter. Connection never resets or enables output.

    Source readback ON makes SOUR measured, not the programmed setpoint.
    See 2450 reference manual 2450-901-01, READ and SOUR:READ:BACK.
    """
    channels = ('1',)

    def __init__(self, instrument):
        self.instrument = instrument
        self.settings = None
        self._saved_source_delay = None

    @classmethod
    def open(cls, address):
        return cls(Keithley2450IVRunner()._open_resource(address))

    def identify(self):
        identity = self.instrument.query('*IDN?').strip()
        if not is_keithley2450(identity):
            raise ValueError('Manual control requires a verified Keithley 2450.')
        return identity

    def output_state(self):
        raw = self.instrument.query(':OUTP?').strip().upper()
        if raw not in {'0', '1', 'OFF', 'ON'}:
            raise RuntimeError(f'Unknown output response: {raw}')
        return raw in {'1', 'ON'}

    def _read_error(self):
        response = self.instrument.query(':SYST:ERR?').strip()
        try:
            code = int(response.split(',', 1)[0])
        except ValueError as exc:
            raise RuntimeError(f'Invalid error response: {response}') from exc
        return code, response

    def check_error(self, context=''):
        code, response = self._read_error()
        if code != 0:
            message = f'Instrument error after {context}: {response}' if context else f'Instrument error: {response}'
            logger.error(message)
            raise RuntimeError(message)

    def _check_existing_errors(self):
        # SYST:ERR? removes the oldest queued error. Read and retain the full
        # diagnostic before sending any settings; never erase it with *CLS.
        errors = []
        for _ in range(64):
            code, response = self._read_error()
            if code == 0:
                break
            errors.append(response)
            logger.warning('Existing instrument error before Apply: %s', response)
        else:
            raise ExistingInstrumentErrors(
                'Existing instrument error queue still contains errors after 64 reads. '
                'No parameters sent. Review the instrument event log, then retry Apply. '
                f'First error: {errors[0]}'
            )
        return tuple(errors)

    def apply(self, settings):
        settings.validate()
        on = self.output_state()
        if on and (self.settings is None or (settings.mode, settings.terminal) !=
                   (self.settings.mode, self.settings.terminal) or
                   (settings.source_range, settings.measure_range) !=
                   (self.settings.source_range, self.settings.measure_range)):
            raise ValueError('Turn output OFF before changing function, terminal or ranges.')
        history = self._check_existing_errors()
        self.settings = None
        source, sense, protection = (('VOLT', 'CURR', 'ILIM') if settings.mode == 'voltage'
                                     else ('CURR', 'VOLT', 'VLIM'))
        commands = []
        if not on:
            commands += [f":ROUT:TERM {'FRON' if settings.terminal == 'front' else 'REAR'}",
                         f':SOUR:FUNC:MODE {source}']
        if not on:
            # Range reductions must not inherit a larger previous setpoint.
            commands += [f':SOUR:{source}:RANG:AUTO ON', f':SOUR:{source}:LEV 0']
        commands += [f':SENS:FUNC "{sense}"']
        if settings.measure_range is None:
            commands += [f':SENS:{sense}:RANG:AUTO ON']
        else:
            commands += [f':SENS:{sense}:RANG:AUTO OFF', f':SENS:{sense}:RANG {settings.measure_range:.12g}']
        if settings.source_range is None:
            commands += [f':SOUR:{source}:RANG:AUTO ON']
        else:
            commands += [f':SOUR:{source}:RANG {settings.source_range:.12g}']
        # Range changes can alter compliance: always set the user's limit last.
        commands += [f':SOUR:{source}:{protection} {settings.limit:.12g}',
                     f':SOUR:{source}:LEV {settings.value:.12g}',
                     f':SOUR:{source}:READ:BACK ON', f':SENS:{sense}:NPLC {settings.nplc:.12g}',
                     f':SENS:{sense}:AZER {int(settings.autozero)}',
                     ':SENS:COUNT 1', ':FORM:DATA ASC']
        for command in commands:
            self.instrument.write(command)
            self.check_error(command)
        # Reject silently adjusted fixed ranges / protection, rather than showing
        # the requested value as applied when hardware selected something else.
        checks = [(f':SOUR:{source}:{protection}?', settings.limit),
                  (f':SENS:{sense}:NPLC?', settings.nplc),
                  (f':SENS:{sense}:AZER?', int(settings.autozero))]
        if settings.source_range is not None:
            checks.append((f':SOUR:{source}:RANG?', settings.source_range))
        if settings.measure_range is not None:
            checks.append((f':SENS:{sense}:RANG?', settings.measure_range))
        for command, expected in checks:
            raw = self.instrument.query(command)
            try:
                actual = float(raw)
            except ValueError as exc:
                raise RuntimeError(f'Invalid readback for {command}: {raw}') from exc
            self.check_error(command)
            if not math.isclose(actual, expected, rel_tol=1e-5, abs_tol=1e-15):
                raise RuntimeError(f'Instrument adjusted {command}: requested {expected:g}, read back {actual:g}. '
                                   'Review the requested settings and instrument capabilities.')
        self.settings = settings
        return history

    def begin_host_sweep(self):
        """The UI settling delay is authoritative; avoid stacking auto delay."""
        source = 'VOLT' if self.settings.mode == 'voltage' else 'CURR'
        delay = float(self.instrument.query(f':SOUR:{source}:DEL?'))
        auto = int(self.instrument.query(f':SOUR:{source}:DEL:AUTO?'))
        if not math.isfinite(delay) or delay < 0 or auto not in (0, 1):
            raise RuntimeError('Invalid source delay readback.')
        self._saved_source_delay = (source, delay, auto)
        for command in (f':SOUR:{source}:DEL:AUTO OFF', f':SOUR:{source}:DEL 0'):
            self.instrument.write(command)
            self.check_error(command)

    def _restore_source_delay(self):
        if self._saved_source_delay is not None:
            source, delay, auto = self._saved_source_delay
            for command in (f':SOUR:{source}:DEL {delay:.12g}', f':SOUR:{source}:DEL:AUTO {auto}'):
                self.instrument.write(command)
                self.check_error(command)
            self._saved_source_delay = None

    def set_level(self, value, *, check=True):
        if self.settings is None:
            raise ValueError('Apply parameters before changing the source level.')
        candidate = replace(self.settings, value=value)
        candidate.validate()
        source = 'VOLT' if candidate.mode == 'voltage' else 'CURR'
        command = f':SOUR:{source}:LEV {value:.12g}'
        self.instrument.write(command)
        if check:
            self.check_error(command)
        self.settings = candidate

    def output(self, enabled):
        if enabled and self.settings is None:
            raise ValueError('Apply parameters before enabling output.')
        self.instrument.write(':OUTP ON' if enabled else ':OUTP OFF')
        actual = self.output_state()
        if actual != enabled:
            raise RuntimeError('Output change was not confirmed by the instrument.')
        if enabled:
            self.check_error(':OUTP ON')
        else:
            self._restore_source_delay()
        return actual

    def measure(self):
        if self.settings is None:
            raise ValueError('Apply parameters before measuring.')
        raw = self.instrument.query(':READ? "defbuffer1", SOUR, READ')
        try:
            values = [float(x) for x in raw.strip().split(',')]
        except ValueError as exc:
            raise RuntimeError(f'Invalid measurement: {raw}') from exc
        if len(values) != 2 or any(not math.isfinite(x) or abs(x) >= 9e36 for x in values):
            raise RuntimeError(f'Invalid/overrange measurement: {raw}')
        self.check_error(':READ? \"defbuffer1\", SOUR, READ')
        return tuple(values if self.settings.mode == 'voltage' else reversed(values))

    def measure_frame(self):
        """One VISA round trip; retain per-point output/error checks and device time.

        SCPI multi-query responses use semicolons. Split only twice because a
        quoted error message may itself contain semicolons (e.g. its timestamp).
        """
        if self.settings is None:
            raise ValueError('Apply parameters before measuring.')
        command = ':READ? "defbuffer1", SOUR, READ, REL;:OUTP?;:SYST:ERR?'
        raw = self.instrument.query(command).strip()
        try:
            readings, output, error = raw.split(';', 2)
            code = int(error.split(',', 1)[0])
            values = [float(value) for value in readings.split(',')]
        except ValueError as exc:
            raise RuntimeError(f'Invalid measurement frame: {raw}') from exc
        if code:
            raise RuntimeError(f'Instrument error during source/measure: {error}')
        if len(values) != 3 or any(not math.isfinite(v) or abs(v) >= 9e36 for v in values) or values[2] < 0:
            raise RuntimeError(f'Invalid/overrange measurement frame: {raw}')
        output = output.strip().upper()
        if output not in {'0', '1', 'OFF', 'ON'}:
            raise RuntimeError(f'Unknown output state: {output}')
        v, i = values[:2] if self.settings.mode == 'voltage' else values[1::-1]
        return v, i, output in {'1', 'ON'}, values[2]

    def close(self):
        self.instrument.close()


@dataclass(frozen=True)
class ManualSample:
    timestamp: str
    segment: int
    elapsed_s: float
    device: str
    address: str
    channel: str
    voltage_v: float
    current_a: float
    kind: str = 'time'
    source_value: float | None = None
    source_mode: str = ''
    sweep_leg: int = 0
    output_enabled: bool | None = None
    power_w: float | None = None
    device_status: str = ''
    instrument_time_s: float | None = None
    host_elapsed_s: float = 0.
    read_duration_s: float = 0.
    sample_interval_s: float | None = None
    missed_periods: int = 0
    host_monotonic_s: float = 0.
    system_run_id: str = ""
    device_id: str = ""
    device_role: str = ""
    system_elapsed_s: float | None = None
    path_index: tuple = ()


def export_samples(path, samples):
    with open(path, 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ManualSample.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(sample) for sample in samples)


def open_manual_adapter(address, dp100_provider=None):
    from .power_supply_control import DP100_ADDRESS, DP100Manual, HP6614CManual, supply_kind
    from .hp6614c import HP6614CRunner
    if address == DP100_ADDRESS:
        if dp100_provider is None:
            raise ValueError('DP100 requires the shared application USB connection.')
        return DP100Manual(dp100_provider())
    instrument = HP6614CRunner()._open_resource(address, verify_identity=False, skip_open_clear=True)
    try:
        identity = instrument.query('*IDN?')
        if is_keithley2450(identity):
            return Keithley2450Manual(instrument)
        if supply_kind(identity) == 'hp6614c':
            return HP6614CManual(instrument)
        raise ValueError(f'Manual control is unsupported for {identity}')
    except Exception:
        instrument.close()
        raise


class ManualSession:
    """One I/O worker; Tk submits commands and consumes immutable events.

    Ownership begins before open is queued, ends only after OFF confirmation.
    Failed cleanup keeps ownership. Idle connections survive window close.
    """
    def __init__(self, adapter_factory=open_manual_adapter, *, bus=None, passive_open=False):
        self.adapter_factory = adapter_factory
        self.bus = bus
        self.io_priority = 1
        self.passive_open = passive_open
        self.events = queue.Queue()
        self.commands = queue.Queue()
        self.claimed = False
        self.releasing = False
        self.connected = False
        self.acquiring = False
        self.output = None
        self.configured = False
        self.address = ''
        self.identity = ''
        self.channel = '1'
        self.fault = ''
        self.adapter = None
        self.verified = False
        self.cache = {}
        self.segment = 0
        self.segment_start = time.monotonic()
        self.sweeping = False
        self.sweep_values = ()
        self._show_sweep_progress = False
        self.sweep_index = 0
        self.sweep_phase = 'set'
        self.sweep_delay = 0.
        self.interval = 0.5
        self.next_sample = 0.0
        self.next_status = 0.0
        self.missed_periods = 0
        self._instrument_origin = None
        self._last_elapsed = None
        self.halt = threading.Event()
        self.thread = threading.Thread(target=self._run, name='manual-instrument', daemon=True)
        self.thread.start()

    def claim(self, address):
        if self.claimed:
            raise RuntimeError('Release the current instrument first.')
        self.claimed = True
        self.address = address
        self.releasing = False
        self.submit('open', address)

    def submit(self, action, value=None):
        if action == 'release':
            self.releasing = True
        self.commands.put((action, value))

    def sweep_progress(self):
        """Progress of the executed plan, independent of editable UI parameters."""
        if not self._show_sweep_progress or not self.sweep_values:
            return dict(sweep_points_total=0, sweep_points_done=0,
                        sweep_segment_current=0, sweep_segments_total=0)
        values = self.sweep_values
        done = min(self.sweep_index, len(values))
        # During a run index identifies the next/current point. After stopping,
        # retain the last acquired segment rather than advancing to an unread leg.
        index = min(done if self.sweeping else max(0, done - 1), len(values) - 1)
        leg = values.segment_for_index(index)
        return dict(sweep_points_total=len(values), sweep_points_done=done,
                    sweep_segment_current=leg + 1, sweep_segments_total=values.segments)

    def _state(self):
        state = {key: getattr(self, key) for key in
                         ('claimed', 'connected', 'acquiring', 'output', 'configured', 'address',
                          'identity', 'channel', 'fault', 'releasing', 'sweeping', 'sweep_index')}
        state.update(self.sweep_progress())
        state['readback'] = dict(getattr(self.adapter, 'readback', {})) if self.connected else {}
        self.events.put(('state', state))

    def _release(self):
        self.acquiring = False
        self.sweeping = False
        if self.fault and self.adapter is not None:
            # Recreate a broken transport only during an explicit cleanup retry.
            # This path can only turn output OFF, never resume acquisition.
            try:
                self.adapter.close()
            except Exception:
                pass
            self.verified = False
            self.cache.pop(self.address, None)
            self.adapter = None
            self.adapter = self.adapter_factory(self.address)
            self.adapter.identify()
            self.verified = True
            self.cache[self.address] = self.adapter
        if self.adapter is not None:
            self.output = None
            self.output = self.adapter.output(False)
        elif self.claimed and self.fault:
            self.adapter = self.adapter_factory(self.address)
            self.adapter.identify()
            self.verified = True
            self.cache[self.address] = self.adapter
            self.output = self.adapter.output(False)
        self.claimed = False
        self.connected = False
        self.configured = False
        self.releasing = False
        self.fault = ''
        self.adapter = None

    def _handle(self, action, value):
        if action == "check":
            self.output = self.adapter.output_state()
            return
        if action == "detach":
            # Roll back a read-only open without touching pre-existing output.
            self.acquiring = self.claimed = self.connected = self.configured = self.releasing = False
            self.adapter = None
            return
        if action == 'open':
            self._show_sweep_progress = False
            self.fault = ''
            self.verified = False
            self.configured = False
            self.output = None
            self.adapter = self.cache.get(value)
            if self.adapter is None:
                self.adapter = self.adapter_factory(value)
                self.cache[value] = self.adapter
            self.identity = self.adapter.identify()
            self.verified = True
            self.output = self.adapter.output_state()
            self.adapter.settings = None
            self.connected = True
            if self.passive_open:
                self.next_status = math.inf
        elif action == 'release':
            self._release()
        elif action == 'stop':
            self.acquiring = False
            if self.sweeping:
                self._finish_sweep('Sweep stopped; output OFF.')
        elif action == 'off':
            self.acquiring = False
            if self.sweeping:
                self.configured = False
            self.sweeping = False
            self.output = None
            self.output = self.adapter.output(False)
        else:
            if not self.connected or self.adapter is None:
                raise ValueError('Device disconnected; release and reconnect before controlling it.')
            if action == 'clear_protection':
                self.adapter.clear_protection()
                self.output = self.adapter.output_state()
            elif action == 'apply':
                if self.acquiring:
                    raise ValueError('Stop acquisition before applying parameters.')
                history = self.adapter.apply(value)
                self._show_sweep_progress = False
                self.configured = True
                self.output = self.adapter.output_state()
                if history:
                    return f'Apply complete. {len(history)} pre-existing instrument errors recorded in the application log; no new command errors.'
            elif action == 'sweep':
                self._start_sweep(value)
            elif action == 'on':
                self.output = None
                self.output = self.adapter.output(True)
            elif action in {'start', 'once'}:
                if not self.configured:
                    raise ValueError('Apply parameters before measuring.')
                if self.acquiring:
                    raise ValueError('Continuous acquisition is already running.')
                if action == 'start':
                    if not math.isfinite(value) or not 0 <= value <= 3600:
                        raise ValueError('Sample period must be 0–3600 seconds; 0 means fastest available.')
                    self.interval = value
                self._show_sweep_progress = False
                self.segment += 1
                self.segment_start = time.monotonic()
                self.missed_periods = 0
                self._instrument_origin = None
                self._last_elapsed = None
                self._acquisition_metadata('continuous' if action == 'start' else 'once', self.adapter.settings)
                if action == 'start':
                    self.acquiring = True
                    self.next_sample = time.monotonic()
                else:
                    self._sample()
            else:
                raise ValueError(f'Unknown action: {action}')

    def _start_sweep(self, sweep):
        if not self.configured or self.acquiring or self.output is not False:
            raise ValueError('Apply parameters, stop acquisition and turn output OFF before starting I-V.')
        settings = self.adapter.settings
        if not isinstance(settings, ManualSettings):
            raise ValueError('I-V scanning is only supported for Keithley 2450.')
        values = sweep.values(settings)  # Validate every point before any output command.
        self.sweep_values = values
        self.sweep_index = 0
        self.sweep_delay = sweep.delay
        # Preserve the selected range; auto remains auto as in manual control.
        self.adapter.apply(replace(settings, value=values[0]))
        self.adapter.begin_host_sweep()
        self.output = None
        self.output = self.adapter.output(True)
        self.segment += 1
        self.segment_start = time.monotonic()
        self.missed_periods = 0
        self._instrument_origin = None
        self._last_elapsed = None
        self.sweeping = self.acquiring = True
        self._show_sweep_progress = True
        self._acquisition_metadata('iv', settings, sweep)
        self.sweep_phase = 'read'
        self.next_sample = time.monotonic() + self.sweep_delay

    def _acquisition_metadata(self, kind, settings, sweep=None):
        self.events.put(('acquisition', dict(
            run_id=str(uuid.uuid4()), started_at=datetime.now(timezone.utc).isoformat(),
            segment=self.segment, kind=kind, device=self.identity, address=self.address,
            channel=self.channel, settings=asdict(settings),
            sweep=asdict(sweep) if sweep is not None else None,
            interval_s=self.interval if kind == 'continuous' else None)))

    def _finish_sweep(self, message):
        self.acquiring = self.sweeping = False
        self.configured = False  # Manual setpoint changed; require an explicit Apply.
        self.output = None
        self.output = self.adapter.output(False)
        self.adapter.settings = None
        self.events.put(('notice', message + ' Apply parameters before manual output.'))

    def _sweep_tick(self):
        if self.sweep_phase == 'set':
            # The following measurement frame checks the error queue before any
            # sample is published, covering this source command as well.
            self.adapter.set_level(self.sweep_values[self.sweep_index], check=False)
            self.sweep_phase = 'read'
            self.next_sample = time.monotonic() + self.sweep_delay
        else:
            self._sample()
            if self.output is not True:
                raise RuntimeError('Output turned off during I-V sweep; scan aborted.')
            self.sweep_index += 1
            if self.sweep_index == len(self.sweep_values):
                self._finish_sweep(f'Sweep complete: {self.sweep_index} points; output OFF.')
            else:
                self.sweep_phase = 'set'
                self.next_sample = time.monotonic()

    def _sample(self):
        started = time.monotonic()
        if self._last_elapsed is None and self.acquiring and not self.sweeping:
            self.next_sample = started
        device_time = None
        if isinstance(self.adapter, Keithley2450Manual):
            voltage, current, self.output, device_time = self.adapter.measure_frame()
        else:
            voltage, current = self.adapter.measure()
            self.output = self.adapter.output_state()
        completed = time.monotonic()
        host_elapsed = completed - self.segment_start
        elapsed = host_elapsed
        if device_time is not None:
            if self._instrument_origin is None:
                self._instrument_origin = device_time
            elapsed = device_time - self._instrument_origin
            if self._last_elapsed is not None and elapsed < self._last_elapsed:
                raise RuntimeError('Instrument clock moved backwards; acquisition stopped.')
        interval = None if self._last_elapsed is None else elapsed - self._last_elapsed
        self._last_elapsed = elapsed
        if not self.sweeping and self.acquiring and self.interval > 0:
            self.next_sample += self.interval
            if self.next_sample < completed:
                missed = math.ceil((completed - self.next_sample) / self.interval)
                self.missed_periods += missed
                self.next_sample += missed * self.interval
        else:
            self.next_sample = completed
        self.events.put(('sample', ManualSample(
            datetime.now(timezone.utc).isoformat(timespec='milliseconds'), self.segment,
            elapsed, self.identity, self.address, self.channel,
            voltage, current, 'iv' if self.sweeping else 'time',
            self.sweep_values[self.sweep_index] if self.sweeping else None,
            self.adapter.settings.mode if self.sweeping else '',
            self.sweep_values.segment_for_index(self.sweep_index)
            if self.sweeping else 0, self.output, voltage * current,
            str(getattr(self.adapter, 'readback', {}).get('mode', '')) + ' ' +
            str(getattr(self.adapter, 'readback', {}).get('protection', '')),
            device_time, host_elapsed, completed - started, interval, self.missed_periods, completed)))

    def _failed(self, exc, *, opening=False):
        self.acquiring = False
        self.sweeping = False
        self.connected = False
        self.configured = False
        self.fault = str(exc)
        self.output = None
        if self.adapter is not None and self.verified and not (opening and self.passive_open):
            self.adapter.settings = None
            try:
                self.output = self.adapter.output(False)
            except Exception as cleanup:
                self.fault += f'; output OFF failed / state unknown: {cleanup}'
        if opening and (not self.verified or self.passive_open):
            # No control command has been sent: never send SCPI output commands
            # to an unidentified (possibly different) device.
            if self.adapter is not None:
                try:
                    self.adapter.close()
                except Exception:
                    pass
            self.cache.pop(self.address, None)
            self.adapter = None
            self.claimed = False
        self.releasing = False
        self.events.put(('error', self.fault))

    def _run(self):
        timer = AcquisitionTimer()
        while not self.halt.is_set():
            timer.set_active(self.acquiring and not self.releasing)
            action = None
            try:
                timeout = .05
                if self.claimed and self.connected and not self.releasing:
                    due = self.next_sample if self.acquiring else self.next_status
                    timeout = min(timeout, max(0., due - time.monotonic()))
                action, value = self.commands.get(timeout=timeout)
            except queue.Empty:
                pass
            guard = self.bus.transaction(self.address, -1 if action in {"off", "release", "stop"} else self.io_priority) if self.bus else None
            try:
                if guard is not None:
                    guard.__enter__()
                if action is not None:
                    if not self.releasing or action in {'open', 'release', 'off', 'stop'}:
                        notice = self._handle(action, value)
                        self.events.put(('done', action))
                        if notice:
                            self.events.put(('notice', notice))
                    else:
                        self.events.put(('cancelled', action))
                elif self.claimed and self.connected and not self.releasing:
                    now = time.monotonic()
                    if self.acquiring and now >= self.next_sample:
                        if self.sweeping:
                            self._sweep_tick()
                        else:
                            self._sample()
                    elif not self.acquiring and now >= self.next_status:
                        self.output = self.adapter.output_state()
                        self.next_status = now + 1.0
                    else:
                        continue
                else:
                    continue
            except ExistingInstrumentErrors as exc:
                # A queue that cannot be drained remains a blocker.
                # Keep the connection usable for explicit recovery.
                self.events.put(('error', str(exc)))
                self.events.put(('failed', action))
            except ValueError as exc:
                self.events.put(('error', str(exc)))
                if action in {'open', 'release', 'off'} or self.sweeping:
                    self._failed(exc, opening=action == 'open')
                self.events.put(('failed', action))
            except Exception as exc:
                self._failed(exc, opening=action == 'open')
                self.events.put(('failed', action))
            finally:
                if guard is not None:
                    guard.__exit__(None, None, None)
            self._state()
        timer.set_active(False)
        for adapter in self.cache.values():
            try:
                adapter.close()
            except Exception:
                pass

    def shutdown(self):
        if self.claimed:
            raise RuntimeError('Confirm output OFF and release before shutdown.')
        self.halt.set()
