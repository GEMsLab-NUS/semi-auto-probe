import csv
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from dataclasses import replace
from semi_auto_probe.power_supply_control import SupplySettings, HP6614CManual, DP100Manual, supply_kind
from semi_auto_probe.dp100 import DP100Driver, DP100Reading
from semi_auto_probe.instrument_control import ManualSession, export_samples


class FakeHP:
    def __init__(self):
        self.values = {'VOLT':0., 'CURR':.01, 'VOLT:PROT':3.3, 'CURR:PROT:STAT':0, 'SENS:CURR:RANG':.5}
        self.on = False
        self.commands = []
        self.questionable = 0
        self.operation = 256
        self.closed = False
        self.fail_off = False
        self.disconnected = False
    def write(self, cmd):
        self.commands.append(cmd)
        if self.disconnected: raise IOError('disconnected')
        if cmd.startswith('OUTP '):
            if not self.fail_off or cmd != 'OUTP OFF': self.on = cmd.endswith('ON')
        elif cmd == 'OUTP:PROT:CLE': self.questionable = 0
        else:
            key, value = cmd.split(' ')
            self.values[key] = float(value)
    def query(self, cmd):
        if self.disconnected: raise IOError('disconnected')
        if cmd == '*IDN?': return 'HEWLETT-PACKARD,6614C,0,A.01.05'
        if cmd == 'OUTP?': return str(int(self.on))
        if cmd == 'STAT:OPER:COND?': return str(self.operation)
        if cmd == 'STAT:QUES:COND?': return str(self.questionable)
        if cmd == 'MEAS:VOLT?': return '1.234'
        if cmd == 'MEAS:CURR?': return '.002'
        if cmd == 'SYST:ERR?': return '0,"No error"'
        return str(self.values[cmd.rstrip('?')])
    def close(self): self.closed = True


class FakeDP:
    def __init__(self):
        self.connected = True
        self.state = dict(output=False, voltage=0., current=.01, ovp=3.3, ocp=.025, preset=1)
        self.commands = []
        self.fail_status = False
    def read_settings(self):
        if not self.connected or self.fail_status: raise IOError('DP100 status unavailable')
        return dict(self.state)
    def configure_limits(self, point, *, ovp_v, ocp_a):
        self.commands.append('limits')
        self.ovp, self.ocp = ovp_v, ocp_a
    def use_preset(self, point, *, ovp_v, ocp_a):
        self.commands.append('preset')
        self.state.update(preset=point.preset_index, voltage=point.voltage_v, current=point.current_a, ovp=ovp_v, ocp=ocp_a)
    def set_output(self, point, enabled):
        if not self.connected: raise IOError('DP100 disconnected')
        self.commands.append(('output', enabled))
        self.state.update(output=enabled, voltage=point.voltage_v, current=point.current_a, preset=point.preset_index)
    def read_output(self): return DP100Reading(1.232, .004)


class SupplyAdapterTests(unittest.TestCase):
    def test_validation_before_writes(self):
        for settings in (SupplySettings('dp100', value=31), SupplySettings('dp100', limit=.0001),
                         SupplySettings('dp100', preset=0), SupplySettings('hp6614c', value=-1),
                         SupplySettings('hp6614c', limit=.6), SupplySettings('hp6614c', measure_range=1)):
            with self.assertRaises(ValueError): settings.validate()

    def test_hp_confirmed_settings_output_measurement_protection(self):
        visa = FakeHP(); adapter = HP6614CManual(visa)
        self.assertIn('6614C', adapter.identify())
        self.assertEqual(visa.commands, [])
        settings = SupplySettings('hp6614c', value=1.2, measure_range=.02, ocp_enabled=True)
        adapter.apply(settings)
        self.assertFalse(adapter.output_state())
        self.assertEqual(adapter.readback['measure_range'], .02)
        self.assertTrue(adapter.output(True))
        self.assertEqual(adapter.measure(), (1.234, .002))
        visa.operation = 1024; visa.questionable = 2
        adapter.output_state()
        self.assertEqual(adapter.readback['mode'], 'CC')
        self.assertEqual(adapter.readback['protection'], 'OCP')
        adapter.clear_protection()
        self.assertFalse(visa.on)
        self.assertEqual(adapter.readback['protection'], 'Normal')

    def test_hp_high_range_reports_instrument_full_scale(self):
        visa = FakeHP()
        query = visa.query
        visa.query = lambda cmd: '0.5118' if cmd == 'SENS:CURR:RANG?' else query(cmd)
        adapter = HP6614CManual(visa)
        adapter.apply(SupplySettings('hp6614c', measure_range=.5))
        self.assertEqual(adapter.readback['measure_range'], .5118)
        with self.assertRaises(RuntimeError):
            adapter.apply(SupplySettings('hp6614c', measure_range=.02))

    def test_supply_apply_while_on_does_not_write(self):
        for adapter in (HP6614CManual(FakeHP()), DP100Manual(FakeDP())):
            kind = 'dp100' if isinstance(adapter, DP100Manual) else 'hp6614c'
            adapter.apply(SupplySettings(kind, value=1))
            adapter.output(True)
            driver = getattr(adapter, 'driver', getattr(adapter, 'instrument', None))
            previous = list(driver.commands)
            with self.assertRaises(ValueError): adapter.apply(SupplySettings(kind, value=2))
            self.assertEqual(driver.commands, previous)

    def test_dp100_readback_and_off_even_when_status_fails(self):
        driver = FakeDP(); adapter = DP100Manual(driver)
        adapter.identify()
        adapter.apply(SupplySettings('dp100', value=1.2, preset=3))
        self.assertEqual(driver.state['preset'], 3)
        self.assertTrue(adapter.output(True))
        self.assertEqual(adapter.measure(), (1.232, .004))
        driver.fail_status = True
        with self.assertRaises(IOError): adapter.output(False)
        self.assertIn(('output', False), driver.commands)
        self.assertFalse(driver.state['output'])
        adapter.close()
        self.assertTrue(driver.connected)

    def test_dp100_vendor_status_units_and_failure(self):
        driver = DP100Driver.__new__(DP100Driver)
        driver._api = Mock(ConnState=True)
        driver._api.GetCurrentBasic.return_value = (True, 2, 1, 3123, 25, 3300, 30)
        self.assertEqual(driver.read_settings(), dict(preset=2, output=True, voltage=3.123, current=.025, ovp=3.3, ocp=.03))
        driver._api.GetCurrentBasic.return_value = (False, 0, 0, 0, 0, 0, 0)
        with self.assertRaises(RuntimeError): driver.read_settings()

    def test_hp_off_failure_is_not_reported_as_off(self):
        visa = FakeHP(); adapter = HP6614CManual(visa)
        adapter.apply(SupplySettings('hp6614c'))
        adapter.output(True); visa.fail_off = True
        with self.assertRaises(RuntimeError): adapter.output(False)

    def test_invalid_measurement_is_rejected(self):
        visa = FakeHP(); adapter = HP6614CManual(visa)
        visa.query = lambda _: 'nan'
        with self.assertRaises(RuntimeError): adapter.measure()

    def test_supported_models_only(self):
        self.assertEqual(supply_kind('DP100'), 'dp100')
        self.assertEqual(supply_kind('HEWLETT-PACKARD,6614C,0'), 'hp6614c')
        self.assertIsNone(supply_kind('HEWLETT-PACKARD,6632B,0'))


class SupplySessionTests(unittest.TestCase):
    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.01)
        self.fail('Session did not reach expected state')

    def test_acquire_export_release_uses_measured_data(self):
        for kind, adapter in (('dp100', DP100Manual(FakeDP())), ('hp6614c', HP6614CManual(FakeHP()))):
            session = ManualSession(lambda address: adapter)
            try:
                session.claim(kind); self.wait(lambda: session.connected)
                session.submit('apply', SupplySettings(kind, value=1))
                self.wait(lambda: session.configured)
                session.submit('on'); self.wait(lambda: session.output is True)
                session.submit('start', .05); self.wait(lambda: session.acquiring)
                samples = []
                deadline = time.monotonic() + 2
                while len(samples) < 3 and time.monotonic() < deadline:
                    event, payload = session.events.get(timeout=2)
                    if event == 'sample': samples.append(payload)
                self.assertEqual(len(samples), 3)
                self.assertNotEqual(samples[0].voltage_v, 1)
                self.assertAlmostEqual(samples[0].power_w, samples[0].voltage_v * samples[0].current_a)
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'readings.csv'
                    export_samples(path, samples)
                    with path.open(encoding='utf-8-sig') as f:
                        rows = list(csv.DictReader(f))
                    self.assertEqual(len(rows), 3)
                    self.assertEqual(rows[0]['output_enabled'], 'True')
                session.submit('release'); self.wait(lambda: not session.claimed)
                self.assertFalse(session.output)
                self.assertFalse(session.acquiring)
            finally:
                if session.claimed:
                    session.submit('release'); self.wait(lambda: not session.claimed)
                session.shutdown(); session.thread.join(2)

    def test_disconnect_stops_acquisition_and_retains_claim_until_off_confirmed(self):
        driver = FakeDP(); adapter = DP100Manual(driver)
        session = ManualSession(lambda address: adapter)
        try:
            session.claim('dp100'); self.wait(lambda: session.connected)
            session.submit('apply', SupplySettings('dp100', value=1)); self.wait(lambda: session.configured)
            session.submit('on'); self.wait(lambda: session.output is True)
            session.submit('start', .05); self.wait(lambda: session.acquiring)
            driver.connected = False
            self.wait(lambda: not session.connected)
            self.assertFalse(session.acquiring)
            self.assertTrue(session.claimed)
            self.assertIsNone(session.output)
            driver.connected = True
            session.submit('release'); self.wait(lambda: not session.claimed)
            self.assertFalse(driver.state['output'])
        finally:
            driver.connected = True
            if session.claimed:
                session.submit('release'); self.wait(lambda: not session.claimed)
            session.shutdown(); session.thread.join(2)

    def test_failed_hp_release_keeps_ownership_until_retry(self):
        driver = FakeHP(); adapter = HP6614CManual(driver)
        session = ManualSession(lambda address: adapter)
        try:
            session.claim('hp'); self.wait(lambda: session.connected)
            session.submit('apply', SupplySettings('hp6614c')); self.wait(lambda: session.configured)
            session.submit('on'); self.wait(lambda: session.output is True)
            driver.fail_off = True
            session.submit('release'); self.wait(lambda: bool(session.fault))
            self.assertTrue(session.claimed)
            self.assertIsNone(session.output)
            driver.fail_off = False
            session.submit('release'); self.wait(lambda: not session.claimed)
            self.assertFalse(driver.on)
        finally:
            driver.fail_off = False
            if session.claimed:
                session.submit('release'); self.wait(lambda: not session.claimed)
            session.shutdown(); session.thread.join(2)
