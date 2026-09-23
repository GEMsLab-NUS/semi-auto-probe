from __future__ import annotations

import csv
import queue
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from semi_auto_probe.instrument_control import (
    Keithley2450Manual, ManualSample, ManualSession, ManualSettings, export_samples, is_keithley2450,
    parse_si_value, format_si_value, range_options, ManualSweep, StableReading,
)


class FakeVisa:
    def __init__(self):
        self.commands = []
        self.parameters = {}
        self.on = False
        self.identity = 'KEITHLEY INSTRUMENTS,MODEL 2450,123,1.0'
        self.reading = '0.098,0.000002'
        self.disconnected = False
        self.fail_off = False
        self.closed = False
        self.error = '0,"No error"'
        self.io_threads = set()
        self.read_gate = None

    def _record(self, command):
        self.commands.append(command)
        self.io_threads.add(threading.get_ident())
        if self.disconnected:
            raise OSError('device disconnected')

    def write(self, command):
        self._record(command)
        if ' ' in command:
            key, value = command.split(' ', 1)
            self.parameters[key] = value
        if command == ':OUTP ON':
            self.on = True
        if command == ':OUTP OFF':
            if self.fail_off:
                raise OSError('OFF failed')
            self.on = False

    def query(self, command):
        if ';' in command:
            return ';'.join(self.query(part) for part in command.split(';'))
        self._record(command)
        if command.endswith('?') and command[:-1] in self.parameters:
            return self.parameters[command[:-1]]
        if command.endswith(':DEL?'):
            return '0'
        if command.endswith(':DEL:AUTO?'):
            return '1'
        if command == '*IDN?':
            return self.identity
        if command == ':OUTP?':
            return str(int(self.on))
        if command == ':SYST:ERR?':
            return self.error
        if command.startswith(':READ?'):
            if self.read_gate is not None:
                self.read_gate.wait(2)
            return self.reading + (f',{time.monotonic():.9f}' if command.endswith(', REL') else '')
        raise AssertionError(command)

    def close(self):
        self.closed = True


RANGE_ERROR_1131 = '1131,"Parameter, source range, expected value from -1.05 to 1.05;2026/7/20 22:57:36.113"'


class ErrorQueueVisa(FakeVisa):
    """Model the device's destructive FIFO error query, not a constant reply."""
    def __init__(self, errors=(), reject_command=None):
        super().__init__()
        self.errors = list(errors)
        self.reject_command = reject_command

    def write(self, command):
        super().write(command)
        if command == self.reject_command:
            self.errors.append(RANGE_ERROR_1131)

    def query(self, command):
        if command == ':SYST:ERR?':
            self._record(command)
            return self.errors.pop(0) if self.errors else '0,"No error"'
        return super().query(command)


class EngineeringValueTests(unittest.TestCase):
    def test_prefixes_units_and_exponents(self):
        for text, unit, expected in (('100mV', 'V', .1), ('10uA', 'A', 1e-5),
                                     ('500n', 'A', 5e-7), ('-2.5 µA', 'A', -2.5e-6),
                                     ('1μA', 'A', 1e-6), ('1e-6', 'A', 1e-6),
                                     (' .5 V ', 'V', .5), ('2pA', 'A', 2e-12),
                                     ('1kV', 'V', 1000), ('1MA', 'A', 1e6)):
            with self.subTest(text=text):
                self.assertAlmostEqual(parse_si_value(text, unit), expected)
        self.assertEqual(format_si_value(1e-5, 'A'), '10 uA')

    def test_invalid_dimensions_or_syntax_are_rejected(self):
        for text in ('10mA', '1 2V', 'nan', 'inf', '1e999', '10mmV', '', '1V;*RST'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_si_value(text, 'V')

    def test_range_lists_are_model_and_terminal_specific(self):
        self.assertEqual(tuple(range_options('voltage')), ('Auto', '20 mV', '200 mV', '2 V', '20 V', '200 V'))
        self.assertEqual(len(range_options('current', 'rear')), 10)
        self.assertNotIn('10 nA', range_options('current', 'front'))
        self.assertNotIn('100 nA', range_options('current', 'front'))
        self.assertIn('1 uA', range_options('current', 'front'))

    def test_invalid_range_combinations_are_rejected_before_io(self):
        for settings in (ManualSettings(source_range=.3), ManualSettings(value=1, source_range=.02),
                         ManualSettings(terminal='front', measure_range=1e-8),
                         ManualSettings(source_range=200, measure_range=1),
                         ManualSettings('current', .001, 20, 'rear', 1, 200),
                         ManualSettings(source_range=200, limit=1)):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                settings.validate()


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.visa = FakeVisa()
        self.adapter = Keithley2450Manual(self.visa)

    def test_fixed_ranges_and_compliance_are_sent_in_both_functions(self):
        for settings, source, sense, limit in (
            (ManualSettings(value=.1, source_range=.2, measure_range=1e-5), 'VOLT', 'CURR', 'ILIM'),
            (ManualSettings('current', 1e-6, 1, 'rear', 1e-5, 2), 'CURR', 'VOLT', 'VLIM'),
        ):
            with self.subTest(mode=settings.mode):
                self.adapter.apply(settings)
                commands = self.visa.commands
                self.assertIn(f':SOUR:{source}:RANG {settings.source_range:.12g}', commands)
                self.assertIn(f':SENS:{sense}:RANG:AUTO OFF', commands)
                self.assertIn(f':SENS:{sense}:RANG {settings.measure_range:.12g}', commands)
                self.assertLess(commands.index(f':SENS:{sense}:RANG {settings.measure_range:.12g}'),
                                commands.index(f':SOUR:{source}:{limit} {settings.limit:.12g}'))
                self.assertIn(f':SOUR:{source}:{limit}?', commands)

    def test_range_reduction_zeroes_offline_source_before_selecting_range(self):
        self.adapter.apply(ManualSettings(value=1))
        self.visa.commands.clear()
        self.adapter.apply(ManualSettings(value=.01, source_range=.02))
        self.assertLess(self.visa.commands.index(':SOUR:VOLT:LEV 0'),
                        self.visa.commands.index(':SOUR:VOLT:RANG 0.02'))
        self.assertNotIn(':OUTP ON', self.visa.commands)

    def test_fixed_ranges_can_be_returned_to_auto(self):
        self.adapter.apply(ManualSettings(source_range=.2, measure_range=1e-5))
        self.visa.commands.clear()
        self.adapter.apply(ManualSettings())
        self.assertIn(':SOUR:VOLT:RANG:AUTO ON', self.visa.commands)
        self.assertIn(':SENS:CURR:RANG:AUTO ON', self.visa.commands)
        self.assertFalse(any(command.startswith(':SOUR:VOLT:RANG ') for command in self.visa.commands))

    def test_range_changes_are_rejected_while_output_is_on(self):
        self.adapter.apply(ManualSettings())
        self.adapter.output(True)
        with self.assertRaisesRegex(ValueError, 'OFF'):
            self.adapter.apply(ManualSettings(source_range=.2))
        self.assertTrue(self.visa.on)

    def test_silently_adjusted_range_does_not_count_as_applied(self):
        original_query = self.visa.query
        self.visa.query = lambda command: '0.001' if command == ':SENS:CURR:RANG?' else original_query(command)
        with self.assertRaisesRegex(RuntimeError, 'Instrument adjusted'):
            self.adapter.apply(ManualSettings(measure_range=1e-5))
        self.assertIsNone(self.adapter.settings)

    def test_identity_and_output_read_do_not_change_output(self):
        self.adapter.identify()
        self.assertFalse(self.adapter.output_state())
        self.assertEqual(self.visa.commands, ['*IDN?', ':OUTP?'])
        self.assertFalse(is_keithley2450('OTHER,2450,1'))
        self.assertFalse(is_keithley2450('KEITHLEY,2450-FAKE,1'))

    def test_voltage_measurement_uses_actual_source_readback(self):
        self.adapter.apply(ManualSettings(value=0.1))
        self.adapter.output(True)
        self.assertEqual(self.adapter.measure(), (0.098, 2e-6))
        self.assertIn(':SOUR:VOLT:READ:BACK ON', self.visa.commands)
        self.assertNotIn('*RST', self.visa.commands)

    def test_current_source_maps_measurement_units(self):
        self.visa.reading = '0.000002,0.098'
        self.adapter.apply(ManualSettings('current', 2e-6, 20))
        self.assertEqual(self.adapter.measure(), (0.098, 2e-6))
        self.assertIn(':SOUR:CURR:VLIM 20', self.visa.commands)

    def test_mode_and_terminal_changes_require_output_off(self):
        self.adapter.apply(ManualSettings())
        self.adapter.output(True)
        for settings in (ManualSettings('current', 0, 20), ManualSettings(terminal='front')):
            with self.assertRaisesRegex(ValueError, 'OFF'):
                self.adapter.apply(settings)
        self.adapter.apply(ManualSettings(value=0.2))
        self.assertTrue(self.visa.on)

    def test_invalid_parameters_issue_no_commands(self):
        for settings in (ManualSettings(value=float('nan')), ManualSettings(limit=0),
                         ManualSettings(value=201), ManualSettings(value=100, limit=1)):
            with self.assertRaises(ValueError):
                self.adapter.apply(settings)
        self.assertEqual(self.visa.commands, [])

    def test_bad_measurements_are_not_presented_as_readings(self):
        self.adapter.apply(ManualSettings())
        for raw in ('nan,1', '1', '1,2,3', '9.91e37,0', 'invalid'):
            self.visa.reading = raw
            with self.assertRaises(RuntimeError):
                self.adapter.measure()

    def test_off_requires_readback_confirmation(self):
        self.visa.on = True
        original = self.visa.write
        self.visa.write = lambda command: self.visa.commands.append(command)
        with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
            self.adapter.output(False)
        self.visa.write = original

    def test_twenty_existing_errors_are_recorded_then_apply_succeeds_once(self):
        errors = [RANGE_ERROR_1131] * 20
        visa = ErrorQueueVisa(errors)
        adapter = Keithley2450Manual(visa)
        with self.assertLogs('semi_auto_probe.instrument_control', level='WARNING') as logs:
            history = adapter.apply(ManualSettings(value=0.1))
        self.assertEqual(tuple(history), tuple(errors))
        self.assertEqual(len(logs.records), 20)
        self.assertEqual(visa.errors, [])
        self.assertEqual(adapter.settings.value, .1)
        self.assertNotIn('*CLS', visa.commands)
        self.assertNotIn('*RST', visa.commands)
        self.assertNotIn(':OUTP ON', visa.commands)

    def test_new_error_after_existing_errors_still_blocks_configuration(self):
        visa = ErrorQueueVisa([RANGE_ERROR_1131] * 20, reject_command=':SOUR:VOLT:LEV 0.1')
        adapter = Keithley2450Manual(visa)
        with self.assertLogs('semi_auto_probe.instrument_control', level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, 'after :SOUR:VOLT:LEV 0.1'):
                adapter.apply(ManualSettings(value=.1))
        self.assertIsNone(adapter.settings)
        self.assertNotIn(':SOUR:VOLT:READ:BACK ON', visa.commands)

    def test_new_range_error_names_command_and_stops_parameter_batch(self):
        command = ':SOUR:VOLT:RANG:AUTO ON'
        visa = ErrorQueueVisa(reject_command=command)
        adapter = Keithley2450Manual(visa)
        with self.assertRaises(RuntimeError) as caught:
            adapter.apply(ManualSettings(value=0.1))
        self.assertIn(command, str(caught.exception))
        self.assertIn('1131', str(caught.exception))
        self.assertNotIn(':SOUR:VOLT:LEV 0.1', visa.commands)
        self.assertIsNone(adapter.settings)

    def test_instrument_rejection_does_not_mark_configured(self):
        visa = ErrorQueueVisa(reject_command=':SOUR:VOLT:ILIM 1e-05')
        adapter = Keithley2450Manual(visa)
        with self.assertRaises(RuntimeError):
            adapter.apply(ManualSettings())
        self.assertIsNone(adapter.settings)

    def test_nonempty_error_queue_has_a_bounded_read_limit(self):
        self.visa.error = '-222,"Persistent error"'
        with self.assertLogs('semi_auto_probe.instrument_control', level='WARNING'):
            with self.assertRaisesRegex(RuntimeError, 'after 64 reads'):
                self.adapter.apply(ManualSettings())
        self.assertEqual(self.visa.commands.count(':SYST:ERR?'), 64)
        self.assertFalse(any(command.startswith(':SOUR') for command in self.visa.commands))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.visa = FakeVisa()
        self.factory = Mock(side_effect=lambda address: Keithley2450Manual(self.visa))
        self.session = ManualSession(self.factory)
        self.events = []

    def wait(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = self.session.events.get(timeout=0.02)
                self.events.append(event)
            except queue.Empty:
                pass
            if predicate():
                return
        self.fail(f'Timed out; events={self.events!r}')

    def command(self, action, value=None):
        self.events.clear()
        self.session.submit(action, value)
        self.wait(lambda: ('done', action) in self.events or ('failed', action) in self.events)

    def open(self):
        self.session.claim('GPIB0::18::INSTR')
        self.wait(lambda: self.session.connected)

    def configured(self):
        self.open()
        self.command('apply', ManualSettings(value=0.1))

    def tearDown(self):
        self.visa.disconnected = False
        self.visa.fail_off = False
        if self.visa.read_gate:
            self.visa.read_gate.set()
        if self.session.claimed:
            self.command('release')
        self.session.shutdown()
        self.session.thread.join(2)
        self.assertFalse(self.session.thread.is_alive())

    def test_claim_is_immediate_and_duplicate_claim_rejected(self):
        self.session.claim('GPIB0::18::INSTR')
        self.assertTrue(self.session.claimed)
        with self.assertRaises(RuntimeError):
            self.session.claim('OTHER')
        self.wait(lambda: self.session.connected)
        self.assertNotIn(':OUTP ON', self.visa.commands)

    def test_continuous_stop_keeps_output_release_turns_it_off(self):
        self.configured()
        self.command('on')
        self.command('start', 0.05)
        self.wait(lambda: any(kind == 'sample' for kind, _ in self.events))
        self.command('stop')
        self.assertFalse(self.session.acquiring)
        self.assertTrue(self.visa.on)
        self.command('release')
        self.assertFalse(self.visa.on)
        self.assertFalse(self.session.claimed)
        self.assertFalse(self.visa.closed)
        self.assertEqual(self.visa.io_threads, {self.session.thread.ident})

    def test_reopen_reuses_connection_but_requires_apply(self):
        self.configured()
        self.command('release')
        self.open()
        self.assertEqual(self.factory.call_count, 1)
        self.assertFalse(self.session.configured)
        self.assertIsNone(self.session.adapter.settings)

    def test_close_failure_retains_lock_and_unknown_output(self):
        self.configured()
        self.command('on')
        self.visa.fail_off = True
        self.command('release')
        self.assertTrue(self.session.claimed)
        self.assertIsNone(self.session.output)
        self.assertFalse(self.session.connected)
        self.assertIn('OFF failed', self.session.fault)
        with self.assertRaises(RuntimeError):
            self.session.shutdown()
        self.visa.fail_off = False
        self.command('release')
        self.assertFalse(self.session.claimed)

    def test_disconnect_stops_sampling_without_fabricating_data(self):
        self.configured()
        self.command('start', 0.05)
        self.wait(lambda: any(kind == 'sample' for kind, _ in self.events))
        self.visa.disconnected = True
        self.wait(lambda: bool(self.session.fault))
        self.assertFalse(self.session.acquiring)
        self.assertFalse(self.session.connected)
        self.assertIsNone(self.session.output)
        self.assertTrue(self.session.claimed)

    def test_release_discards_queued_output_enable(self):
        self.configured()
        self.visa.read_gate = threading.Event()
        self.session.submit('once')
        self.wait(lambda: any(command.startswith(':READ?') for command in self.visa.commands))
        self.session.submit('on')
        self.session.submit('release')
        self.visa.read_gate.set()
        self.wait(lambda: not self.session.claimed)
        self.assertNotIn(':OUTP ON', self.visa.commands)

    def test_existing_error_does_not_disconnect_or_force_output_change(self):
        self.visa = ErrorQueueVisa([RANGE_ERROR_1131])
        self.open()
        self.command('apply', ManualSettings(value=0.1))
        self.assertTrue(self.session.connected)
        self.assertTrue(self.session.claimed)
        self.assertEqual(self.session.fault, '')
        self.assertNotIn(':OUTP OFF', self.visa.commands)
        self.assertTrue(self.session.configured)
        self.wait(lambda: any(kind == 'notice' for kind, _ in self.events))
        self.assertIn(('done', 'apply'), self.events)

    def test_identity_mismatch_never_sends_output_commands(self):
        self.visa.identity = 'OTHER,UNKNOWN,123'
        self.session.claim('GPIB0::18::INSTR')
        self.wait(lambda: ('failed', 'open') in self.events)
        self.assertFalse(self.session.claimed)
        self.assertNotIn(':OUTP OFF', self.visa.commands)
        self.assertNotIn(':OUTP ON', self.visa.commands)

    def test_cleanup_identity_mismatch_keeps_lock_without_output_commands(self):
        self.configured()
        self.visa.fail_off = True
        self.command('release')
        self.visa.identity = 'OTHER,UNKNOWN,123'
        baseline = len(self.visa.commands)
        self.command('release')
        self.assertTrue(self.session.claimed)
        self.assertFalse(self.session.releasing)
        self.assertNotIn(':OUTP OFF', self.visa.commands[baseline:])
        self.visa.identity = 'KEITHLEY,2450,123'

    def test_parameter_error_is_not_reported_as_success(self):
        self.configured()
        self.command('apply', ManualSettings(value=1000))
        self.assertIn(('failed', 'apply'), self.events)
        self.assertNotIn(('done', 'apply'), self.events)

    def test_segments_and_csv_retain_measured_units_and_identity(self):
        self.configured()
        self.command('once')
        first = next(payload for kind, payload in self.events if kind == 'sample')
        self.command('once')
        second = next(payload for kind, payload in self.events if kind == 'sample')
        self.assertNotEqual(first.segment, second.segment)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'samples.csv'
            export_samples(path, [first, second])
            with path.open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['channel'], '1')
        self.assertEqual(float(rows[0]['voltage_v']), 0.098)
        self.assertEqual(rows[0]['address'], 'GPIB0::18::INSTR')


class SweepSessionTests(unittest.TestCase):
    setUp = SessionTests.setUp
    tearDown = SessionTests.tearDown
    wait = SessionTests.wait
    command = SessionTests.command
    open = SessionTests.open
    configured = SessionTests.configured
    def test_iv_completes_and_turns_output_off(self):
        self.configured()
        self.command('sweep', ManualSweep(0, .1, .05, 0, low=0, segments=2))
        self.wait(lambda: any(kind == 'notice' and value.startswith('Sweep complete:') for kind, value in self.events))
        points = [value for kind, value in self.events if kind == 'sample']
        self.assertEqual([p.source_value for p in points], [0, .05, .1, .05, 0])
        self.assertTrue(all(p.kind == 'iv' for p in points))
        self.assertFalse(self.visa.on)
        self.assertFalse(self.session.configured)
        self.assertTrue(self.session.claimed)

    def test_iv_settling_can_be_stopped_immediately(self):
        self.configured()
        self.command('sweep', ManualSweep(0, .1, .05, 60))
        self.assertTrue(self.visa.on)
        started = time.monotonic()
        self.command('stop')
        self.assertLess(time.monotonic() - started, .5)
        self.assertFalse(self.session.acquiring)
        self.assertFalse(self.visa.on)
        self.assertFalse(self.session.configured)

    def test_iv_rejects_invalid_endpoint_before_enabling(self):
        self.configured()
        before = len(self.visa.commands)
        self.command('sweep', ManualSweep(0, 201, 10, 0))
        self.assertIn(('failed', 'sweep'), self.events)
        self.assertNotIn(':OUTP ON', self.visa.commands[before:])
        self.assertFalse(self.session.acquiring)

    def test_release_during_iv_stops_scan_and_keeps_data(self):
        self.configured()
        self.command('sweep', ManualSweep(0, .1, .05, 0))
        self.wait(lambda: any(kind == 'sample' for kind, _ in self.events))
        self.command('release')
        self.assertFalse(self.session.claimed)
        self.assertFalse(self.session.sweeping)
        self.assertFalse(self.visa.on)

    def test_iv_read_failure_turns_output_off(self):
        self.configured()
        self.visa.reading = 'invalid'
        self.command('sweep', ManualSweep(0, .1, .05, 0))
        self.wait(lambda: bool(self.session.fault))
        self.assertFalse(self.visa.on)
        self.assertFalse(self.session.sweeping)
        self.assertTrue(self.session.claimed)

    def test_iv_current_source_mapping(self):
        self.open()
        self.command('apply', ManualSettings(mode='current', limit=1))
        self.command('sweep', ManualSweep(0, 2e-6, 1e-6, 0))
        self.wait(lambda: any(kind == 'notice' and value.startswith('Sweep complete:') for kind, value in self.events))
        self.assertIn(':SOUR:CURR:LEV 2e-06', self.visa.commands)
        points = [value for kind, value in self.events if kind == 'sample']
        self.assertEqual(len(points), 3)
        self.assertEqual(points[-1].voltage_v, .000002)
        self.assertFalse(self.visa.on)


class SweepValueTests(unittest.TestCase):
    def test_reverse_segment_contains_exact_endpoints(self):
        values = ManualSweep(0, 1, .3, low=0, segments=2).values(ManualSettings())
        self.assertEqual(values[-1], 0)
        self.assertEqual(values.count(1), 1)
        self.assertTrue(all(abs(a-b) <= .30000001 for a,b in zip(values, values[1:])))

    def test_bad_sweep_values_are_rejected(self):
        for sweep in (ManualSweep(step=0), ManualSweep(step=1e-20), ManualSweep(stop=float('nan')),
                      ManualSweep(delay=-1), ManualSweep(stop=0), ManualSweep(step=3)):
            with self.subTest(sweep=sweep), self.assertRaises(ValueError):
                sweep.values(ManualSettings())

    def test_live_reading_uses_fixed_decimals_and_hysteresis(self):
        reading = StableReading('A')
        self.assertEqual(reading.format(1.23456e-9), '+001.235 nA')
        self.assertEqual(reading.format(-2.5e-6), '-002.500 uA')
        self.assertEqual(reading.format(.99e-6), '+000.990 uA')
        self.assertEqual(reading.format(.8e-6), '+800.000 nA')
        self.assertEqual(reading.format(0), '+000.000 nA')


class MainIntegrationTests(unittest.TestCase):
    def test_automatic_entrypoints_reject_manual_ownership_before_motion(self):
        from semi_auto_probe.app import ProbeApp
        app = Mock()
        app._manual_instrument_blocks_measurement.return_value = True
        ProbeApp.start_autotest(app, None)
        ProbeApp._start_mapping(app, None)
        self.assertEqual(app._manual_instrument_blocks_measurement.call_count, 2)
        app._device_scan_in_progress.assert_not_called()

    def test_reopening_activates_existing_window(self):
        from semi_auto_probe.app import ProbeApp
        app = Mock()
        window = Mock()
        app._instrument_window = window
        ProbeApp._show_instrument(app)
        window.show.assert_called_once()
        app._ensure_device_status.assert_not_called()

    def test_exit_waits_for_instrument_cleanup(self):
        from semi_auto_probe.app import ProbeApp
        app = Mock()
        window = Mock()
        window.session.claimed = True
        window.session.fault = ''
        app._instrument_window = window
        ProbeApp.destroy(app)
        window.request_close.assert_called_once()
        app.after.assert_called_once_with(150, app.destroy)
        app.stop_camera.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class IntegrationSettingsTests(unittest.TestCase):
    def test_invalid_nplc_never_reaches_hardware(self):
        for value in (0, .009, 10.01, float('nan'), float('inf')):
            visa = FakeVisa()
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'NPLC'):
                Keithley2450Manual(visa).apply(ManualSettings(nplc=value))
            self.assertEqual(visa.commands, [])

    def test_integration_and_autozero_follow_measure_function(self):
        for mode, sense in (('voltage', 'CURR'), ('current', 'VOLT')):
            for nplc, autozero in ((.01, False), (.25, True), (10, True)):
                visa = FakeVisa()
                adapter = Keithley2450Manual(visa)
                settings = ManualSettings(mode=mode, nplc=nplc, autozero=autozero)
                adapter.apply(settings)
                self.assertIn(f':SENS:{sense}:NPLC {nplc:g}', visa.commands)
                self.assertIn(f':SENS:{sense}:AZER {int(autozero)}', visa.commands)
                self.assertIn(f':SENS:{sense}:NPLC?', visa.commands)
                self.assertIn(f':SENS:{sense}:AZER?', visa.commands)
                self.assertEqual(adapter.settings, settings)

    def test_unconfirmed_integration_does_not_mark_settings_applied(self):
        for command, response in ((':SENS:CURR:NPLC?', '1'), (':SENS:CURR:AZER?', '1')):
            visa = FakeVisa()
            query = visa.query
            visa.query = lambda cmd: response if cmd == command else query(cmd)
            adapter = Keithley2450Manual(visa)
            with self.subTest(command=command), self.assertRaisesRegex(RuntimeError, 'adjusted'):
                adapter.apply(ManualSettings(nplc=.25, autozero=False))
            self.assertIsNone(adapter.settings)


class FixedMeasureLimitTests(unittest.TestCase):
    def test_screenshot_combination_rejected_before_io(self):
        visa = FakeVisa()
        with self.assertRaisesRegex(ValueError, 'protection limit'):
            Keithley2450Manual(visa).apply(ManualSettings(value=-10, limit=10e-6, measure_range=1e-6))
        self.assertEqual(visa.commands, [])

    def test_voltage_protection_must_fit_fixed_voltage_measure_range(self):
        with self.assertRaisesRegex(ValueError, 'protection limit'):
            ManualSettings(mode='current', value=1e-6, limit=20, measure_range=2).validate()

    def test_compatible_fixed_ranges_and_auto_are_accepted(self):
        for settings in (ManualSettings(limit=1e-6, measure_range=1e-6),
                         ManualSettings(limit=10e-6, measure_range=10e-6),
                         ManualSettings(limit=10e-6),
                         ManualSettings(mode='current', limit=1, measure_range=2)):
            settings.validate()

    def test_fixed_range_limit_boundaries(self):
        ManualSettings(limit=1.05e-6, measure_range=1e-6).validate()
        for limit in (1e-9, 1.051e-6):
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, 'protection limit'):
                ManualSettings(limit=limit, measure_range=1e-6).validate()


class SegmentSweepTests(unittest.TestCase):
    def test_each_segment_ends_at_one_feature_point(self):
        for count, expected in (
            (1, (0, 1)),
            (2, (0, 1, 0)),
            (3, (0, 1, 0, -1)),
            (4, (0, 1, 0, -1, 0)),
        ):
            self.assertEqual(ManualSweep(step=1, segments=count).values(ManualSettings()), expected)

    def test_user_example_top_five_low_minus_four(self):
        sweep = ManualSweep(start=0, stop=5, low=-4, step=1, direction='up')
        expected = {
            1: (0, 1, 2, 3, 4, 5),
            2: (0, 1, 2, 3, 4, 5, 4, 3, 2, 1, 0),
            3: (0, 1, 2, 3, 4, 5, 4, 3, 2, 1, 0, -1, -2, -3, -4),
            4: (0, 1, 2, 3, 4, 5, 4, 3, 2, 1, 0, -1, -2, -3, -4, -3, -2, -1, 0),
        }
        for segments, values in expected.items():
            self.assertEqual(tuple(replace(sweep, segments=segments).values(ManualSettings())), values)

    def test_initial_down_and_boundary_start(self):
        self.assertEqual(ManualSweep(step=1, direction='down', segments=3).values(ManualSettings()), (0,-1,0,1))
        self.assertEqual(ManualSweep(start=1, step=1, direction='down', segments=2).values(ManualSettings()), (1,0,-1,0,1))

    def test_segment_index_tracks_feature_point_legs(self):
        values = ManualSweep(start=0, stop=1, low=-1, step=.5, segments=4).values(ManualSettings())
        self.assertEqual([values.segment_for_index(i) for i in range(len(values))],
                         [0, 0, 0, 1, 1, 2, 2, 3, 3])

    def test_arbitrary_start_and_nondivisible_steps(self):
        values = ManualSweep(start=.2, stop=1, low=-.7, step=.3, segments=3).values(ManualSettings())
        self.assertEqual(values[0], .2)
        self.assertEqual(values[-1], -.7)
        self.assertEqual(max(values), 1)
        self.assertEqual(min(values), -.7)
        self.assertFalse(any(a == b for a,b in zip(values, values[1:])))
        self.assertTrue(all(abs(a-b) <= .30000001 for a,b in zip(values, values[1:])))

    def test_invalid_segments_bounds_and_point_budget(self):
        for sweep in (ManualSweep(low=2), ManualSweep(start=2), ManualSweep(segments=0),
                      ManualSweep(segments=1.5), ManualSweep(segments=True), ManualSweep(direction='other'),
                      ManualSweep(low=-201, segments=2), ManualSweep(step=.000001, segments=10),
                      ManualSweep(start=1, direction='up')):
            with self.subTest(sweep=sweep), self.assertRaises(ValueError):
                sweep.values(ManualSettings())


class LongSweepTests(unittest.TestCase):
    def test_long_sweep_has_constant_memory_and_exact_turnarounds(self):
        import tracemalloc
        tracemalloc.start()
        try:
            values = ManualSweep(start=0, stop=1, low=-1, step=.001, segments=2000).values(ManualSettings())
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(len(values), 2_000_001)
        self.assertLess(peak, 100_000)
        self.assertEqual(values[0], 0)
        self.assertEqual(values[1000], 1)
        self.assertEqual(values[2000], 0)
        self.assertEqual(values[-1], 0)

    def test_million_segments_and_limit(self):
        values = ManualSweep(step=1, segments=1_000_000).values(ManualSettings())
        self.assertEqual(len(values), 1_000_001)
        self.assertEqual(values[-1], 0)
        with self.assertRaisesRegex(ValueError, 'Segments'):
            ManualSweep(segments=1_000_001).values(ManualSettings())
        with self.assertRaisesRegex(ValueError, '10,000,000'):
            ManualSweep(step=1e-6, segments=10).values(ManualSettings())

    def test_exact_maximum_points(self):
        values = ManualSweep(start=-1, stop=1, low=-1, step=2/9_999_999).values(ManualSettings())
        self.assertEqual(len(values), 10_000_000)
        self.assertEqual(values[-1], 1)
