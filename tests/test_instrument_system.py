import csv
import io
import json
import queue
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

from semi_auto_probe.instrument_control import Keithley2450Manual, ManualSettings, ManualSweep
from semi_auto_probe.instrument_system import InstrumentSystem, SystemConfiguration, BusArbiter
from test_instrument_control import FakeVisa


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.visas = {f'GPIB0::{i}::INSTR': FakeVisa() for i in (18, 19, 20)}
        self.system = InstrumentSystem(lambda a: Keithley2450Manual(self.visas[a]), command_timeout=2)
        self.rows = [dict(address=a, device=v.identity) for a, v in self.visas.items()]
        self.log = []

    def wait(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while True:
                try:
                    self.log.append(self.system.events.get_nowait())
                except queue.Empty:
                    break
            if predicate():
                return
            time.sleep(.005)
        self.fail(f'Timeout: {self.system.state} {self.system.fault}')

    def done(self, action):
        self.wait(lambda: ('done', action) in self.log or bool(self.system.fault))
        self.assertFalse(self.system.fault)
        self.log.clear()

    def open(self, count=3):
        self.system.open(self.rows[:count])
        self.assertTrue(self.system.claimed)
        self.done('open')

    def config(self, delay=.003, points=4):
        addresses = list(self.system.sessions)
        settings = {a: ManualSettings('voltage', 0., 1e-5) for a in addresses}
        return SystemConfiguration(addresses[0], settings, ManualSweep(0., .1 * (points-1), .1, delay),
                                   tuple(addresses[1:]), {a: .01 for a in addresses[1:]}, .2)

    def applied(self, config):
        self.system.submit('apply_all', config.settings)
        self.done('apply_all')

    def tearDown(self):
        for v in self.visas.values():
            v.disconnected = v.fail_off = False
            if v.read_gate:
                v.read_gate.set()
        if self.system.claimed:
            self.system.submit('release')
            self.wait(lambda: not self.system.claimed)
        self.system.shutdown()
        self.system.thread.join(2)

    def test_three_devices_complete_export_and_roles(self):
        self.open()
        c = self.config()
        self.applied(c)
        self.system.submit('start', c)
        self.done('start')
        self.assertTrue(all(not v.on for v in self.visas.values()))
        self.assertEqual(len([p for p in self.system.samples if p.device_role == 'scan']), 4)
        self.assertEqual({p.device_id for p in self.system.samples}, set(self.visas))
        self.assertEqual(len({p.system_run_id for p in self.system.samples}), 1)
        self.assertTrue(all(p.system_elapsed_s >= 0 for p in self.system.samples))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'run.zip'
            self.system.export(path)
            with zipfile.ZipFile(path) as z:
                mapping = json.loads(z.read('devices.json'))
                rows = [r for name in mapping for r in csv.DictReader(io.StringIO(z.read(name).decode('utf-8-sig'))) ]
                self.assertEqual(len(rows), len(self.system.samples))
                self.assertEqual(len(json.loads(z.read('configuration.json'))['runs']), 1)

    def test_open_failure_does_not_touch_existing_output(self):
        self.visas[self.rows[0]['address']].on = True
        self.visas[self.rows[1]['address']].disconnected = True
        self.system.open(self.rows[:2])
        self.wait(lambda: self.system.state == 'fault')
        self.assertFalse(self.system.claimed)
        self.assertTrue(self.visas[self.rows[0]['address']].on)
        self.assertNotIn(':OUTP OFF', self.visas[self.rows[0]['address']].commands)

    def test_start_refuses_existing_on_without_taking_over(self):
        self.open(2)
        c = self.config()
        self.applied(c)
        self.visas[c.auxiliary_order[0]].on = True
        self.system.submit('start', c)
        self.wait(lambda: self.system.state == 'fault')
        self.assertTrue(self.visas[c.auxiliary_order[0]].on)
        self.assertFalse(self.system.runs)

    def test_stop_during_settle_closes_all(self):
        self.open()
        c = self.config()
        c.settle[c.auxiliary_order[0]] = 2
        self.applied(c)
        self.system.submit('start', c)
        self.wait(lambda: self.visas[c.auxiliary_order[0]].on)
        self.system.submit('stop')
        self.done('stop')
        self.assertTrue(all(not v.on for v in self.visas.values()))

    def test_failed_off_keeps_claim_other_devices_still_close(self):
        self.open()
        c = self.config(delay=.02, points=20)
        self.applied(c)
        self.system.submit('start', c)
        self.wait(lambda: self.system.state == 'running')
        bad = c.auxiliary_order[0]
        self.visas[bad].fail_off = True
        self.system.submit('release')
        self.wait(lambda: self.system.state == 'fault' and not self.system.releasing)
        self.wait(lambda: all(not s.claimed for a, s in self.system.sessions.items() if a != bad))
        self.assertTrue(self.system.sessions[bad].claimed)
        self.assertIsNone(self.system.sessions[bad].output)
        self.assertTrue(all(not v.on for a, v in self.visas.items() if a != bad))

    def test_aux_different_bus_does_not_gate_each_point(self):
        self.rows[1]['address'] = 'GPIB1::19::INSTR'
        self.visas[self.rows[1]['address']] = self.visas.pop('GPIB0::19::INSTR')
        self.open(2)
        c = self.config(delay=.002, points=20)
        self.applied(c)
        aux = self.visas[c.auxiliary_order[0]]
        aux.read_gate = threading.Event()
        self.system.submit('start', c)
        self.wait(lambda: len([p for p in self.system.samples if p.device_role == 'scan']) == 20)
        self.assertFalse(aux.read_gate.is_set())
        aux.read_gate.set()
        self.done('start')

    def test_order_settle_and_reverse_shutdown(self):
        calls = []
        original = self.system.factory
        def factory(address):
            adapter = original(address)
            output = adapter.output
            def record(enabled):
                result = output(enabled)
                calls.append((address, enabled, time.monotonic()))
                return result
            adapter.output = record
            return adapter
        self.system.factory = factory
        self.open()
        c = self.config()
        c.settle.update({a: .04 for a in c.auxiliary_order})
        self.applied(c)
        calls.clear()
        self.system.submit('start', c)
        self.done('start')
        ons = [(a, t) for a, enabled, t in calls if enabled]
        self.assertEqual([a for a, t in ons], [*c.auxiliary_order, c.scanner])
        self.assertGreaterEqual(ons[1][1] - ons[0][1], .035)
        self.assertGreaterEqual(ons[2][1] - ons[1][1], .035)
        offs = [a for a, enabled, t in calls if not enabled]
        self.assertEqual(offs[-3:], [c.scanner, *reversed(c.auxiliary_order)])

    def test_two_runs_and_reopen_use_distinct_ids_and_current_members(self):
        self.open(2)
        for _ in range(2):
            c = self.config()
            self.applied(c)
            self.system.submit('start', c)
            self.done('start')
        self.assertEqual(len({p.system_run_id for p in self.system.samples}), 2)
        self.system.submit('release')
        self.done('release')
        self.system.open(self.rows[1:])
        self.done('open')
        self.system.submit('off')
        self.done('off')
        self.assertEqual(len(self.system.runs), 2)

    def test_partial_apply_failure_prevents_run(self):
        self.open()
        c = self.config()
        self.visas[c.auxiliary_order[0]].error = '1131,"bad parameter"'
        with self.assertLogs('semi_auto_probe', level='WARNING'):
            self.system.submit('apply_all', c.settings)
            self.wait(lambda: self.system.state == 'fault')
        self.assertFalse(self.system.sessions[c.auxiliary_order[0]].configured)
        self.assertTrue(all(not v.on for v in self.visas.values()))
        self.visas[c.auxiliary_order[0]].error = '0,"No error"'

    def test_auxiliary_disconnect_stops_scanner(self):
        self.open()
        c = self.config(delay=.01, points=100)
        self.applied(c)
        self.system.submit('start', c)
        self.wait(lambda: self.system.state == 'running')
        bad = c.auxiliary_order[0]
        self.visas[bad].disconnected = True
        self.wait(lambda: self.system.state == 'fault')
        self.assertFalse(self.visas[c.scanner].on)
        self.assertTrue(self.system.sessions[bad].claimed)

    def test_mixed_2450_hp_dp100(self):
        from test_manual_supplies import FakeHP, FakeDP
        from semi_auto_probe.power_supply_control import HP6614CManual, DP100Manual, SupplySettings
        hp, dp = FakeHP(), FakeDP()
        main = self.rows[0]['address']
        hp_address, dp_address = 'GPIB0::5::INSTR', 'USB / vendor DLL'
        adapters = {main: lambda: Keithley2450Manual(self.visas[main]),
            hp_address: lambda: HP6614CManual(hp), dp_address: lambda: DP100Manual(dp)}
        self.system.factory = lambda a: adapters[a]()
        self.system.open([self.rows[0], dict(address=hp_address, device=hp.query('*IDN?')),
                          dict(address=dp_address, device='DP100')])
        self.done('open')
        c = SystemConfiguration(main, {main: ManualSettings('voltage', 0., 1e-5),
            hp_address: SupplySettings('hp6614c', value=1), dp_address: SupplySettings('dp100', value=1)},
            ManualSweep(0., .2, .1, .005), (hp_address, dp_address), {hp_address: .01, dp_address: .01}, .2)
        self.applied(c)
        self.system.submit('start', c)
        self.done('start')
        self.assertFalse(hp.on)
        self.assertFalse(dp.state['output'])
        self.assertTrue(dp.connected)
        self.assertEqual({p.device_id for p in self.system.samples}, set(adapters))

    def test_priority_nonpreemptive_fifo(self):
        bus = BusArbiter()
        order = []
        def worker(name, priority):
            with bus.transaction('GPIB0::19::INSTR', priority):
                order.append(name)
        with bus.transaction('GPIB0::18::INSTR'):
            low = threading.Thread(target=worker, args=('aux', 1)); low.start()
            time.sleep(.01)
            high = threading.Thread(target=worker, args=('scan', 0)); high.start()
            time.sleep(.01)
            self.assertFalse(order)
        low.join(); high.join()
        self.assertEqual(order, ['scan', 'aux'])
