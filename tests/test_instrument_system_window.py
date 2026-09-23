import time
import tkinter as tk
import unittest
from unittest.mock import Mock

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.instrument_control import ManualSession, Keithley2450Manual
from semi_auto_probe.ui.instrument_window import InstrumentWindow
from test_instrument_control import FakeVisa


class SystemWindowTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        ProbeApp._configure_theme(self.root)
        self.visas = {f'GPIB0::{i}::INSTR': FakeVisa() for i in (18, 19, 20)}
        self.rows = [dict(address=a, device=v.identity, status='Connected') for a, v in self.visas.items()]
        self.session = ManualSession(lambda a: Keithley2450Manual(self.visas[a]))
        self.window = InstrumentWindow(self.root, rows=lambda: self.rows, busy=lambda: False,
            scanning=lambda: False, refresh=Mock(), activity=Mock(), session=self.session)
        self.window.show()
        self.root.update()

    def pump(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail(self.window.status.get())

    def open(self):
        self.window.checked_devices.update(self.visas)
        self.window.open_selected()
        self.pump(lambda: not self.window.pending and self.window.edit_device)

    def tearDown(self):
        for v in self.visas.values():
            v.fail_off = v.disconnected = False
        if self.window.control_session.claimed:
            self.window.release()
            self.pump(lambda: not self.window.control_session.claimed and not self.window.pending)
        self.window.destroy()
        self.root.destroy()

    def test_tabs_preserve_inputs_without_writes_or_view_switch(self):
        self.open()
        w = self.window
        first, second, third = self.visas
        w.value.set('0.12')
        w.iv_log_y.set(True)
        baseline = {a: len(v.commands) for a, v in self.visas.items()}
        w._edit_member(second)
        w.value.set('0.7')
        w._edit_member(first)
        self.assertEqual(w.value.get(), '0.12')
        self.assertEqual(w.view_device, first)
        self.assertTrue(w.iv_log_y.get())
        for a, v in self.visas.items():
            self.assertTrue(all(command == ':OUTP?' for command in v.commands[baseline[a]:]))
        w._edit_member(second)
        self.assertEqual(w.value.get(), '0.7')
        self.assertFalse(w.sidebar.winfo_ismapped())

    def test_apply_run_switch_views_compact_and_raw_data(self):
        self.open()
        w = self.window
        w.sweep_stop.set('.2')
        w.sweep_step.set('.1')
        w._system_apply(True)
        self.pump(lambda: not w.pending)
        self.assertTrue(all(s.configured for s in w.system.sessions.values()), w.status.get())
        w._start_system()
        self.pump(lambda: bool(w.system.runs) and not w.pending)
        self.assertEqual(len([p for p in w.samples if p.device_role == 'scan']), 3)
        main = w.scan_device.get()
        aux = w.aux_order[0]
        w.iv_log_y.set(True)
        w._view_member(aux)
        self.assertEqual(w.plot_mode.get(), 'Time')
        w._view_member(main)
        self.assertTrue(w.iv_log_y.get())
        before = len(w.system.samples)
        w.replot()
        self.assertEqual(len(w.system.samples), before)
        w.compact.set(True)
        w.set_compact()
        self.root.update()
        self.assertFalse(w.form.winfo_ismapped())
        self.assertTrue(w.aux_readouts.winfo_ismapped())
        self.assertTrue(w.monitor_off.winfo_ismapped())
        self.assertEqual(w.monitor_off.cget('text'), 'All OFF')

    def test_mixed_device_forms_and_minimum_layout(self):
        from test_manual_supplies import FakeHP, FakeDP
        from semi_auto_probe.power_supply_control import HP6614CManual, DP100Manual
        hp, dp = FakeHP(), FakeDP()
        main = next(iter(self.visas))
        self.rows[:] = [self.rows[0], dict(address='GPIB0::5::INSTR', device=hp.query('*IDN?'), status='Connected'),
            dict(address='USB / vendor DLL', device='DP100', status='App not connected')]
        factories = {main: lambda: Keithley2450Manual(self.visas[main]),
            'GPIB0::5::INSTR': lambda: HP6614CManual(hp), 'USB / vendor DLL': lambda: DP100Manual(dp)}
        self.session.adapter_factory = lambda a: factories[a]()
        w = self.window
        w._sync_devices()
        w.checked_devices.update(factories)
        w.open_selected()
        self.pump(lambda: not w.pending and w.edit_device)
        for address, kind in [('GPIB0::5::INSTR', 'hp6614c'), ('USB / vendor DLL', 'dp100')]:
            w._edit_member(address)
            self.assertEqual(w.supply_type, kind)
            self.assertTrue(w.supply_fields.winfo_manager())
        w._edit_member(main)
        w.geometry('1060x710')
        w.status.set('System error: output OFF not confirmed. ' * 40)
        w._update_system_status()
        self.assertIn('Click for details', w.system_status_summary.get())
        self.root.update()
        for button in (w.apply_button, w.system_start_button, w.monitor_off):
            self.assertTrue(button.winfo_ismapped())
            self.assertLess(button.winfo_rooty() + button.winfo_height(), w.winfo_rooty() + w.winfo_height())
        w.sweep_stop.set('.1')
        w.sweep_step.set('.1')
        w._system_apply(True)
        self.pump(lambda: not w.pending)
        self.assertTrue(all(s.configured for s in w.system.sessions.values()), w.status.get())
        w._start_system()
        self.pump(lambda: bool(w.system.runs) and not w.pending)
        w.request_close()
        self.pump(lambda: not w.control_session.claimed and w.wm_state() == 'withdrawn')
        self.assertFalse(hp.on)
        self.assertFalse(dp.state['output'])
        self.assertTrue(w.samples)

    def test_checkbox_limit_and_busy_or_unsupported_rejected(self):
        from types import SimpleNamespace
        self.rows.append(dict(address='GPIB0::21::INSTR', device=self.rows[0]['device'], status='Connected'))
        self.rows.append(dict(address='GPIB0::22::INSTR', device='Unsupported device', status='Connected'))
        self.window._sync_devices()
        items = self.window.tree.get_children()
        for i in range(4):
            self.window.tree.focus(self.window.tree.get_children()[i])
            self.window._check_device(SimpleNamespace(keysym='space'))
        self.assertEqual(len(self.window.checked_devices), 3)
        self.window.tree.focus(self.window.tree.get_children()[4])
        self.window._check_device(SimpleNamespace(keysym='space'))
        self.assertNotIn('GPIB0::22::INSTR', self.window.checked_devices)
        self.window.busy = lambda: True
        self.window.tree.focus(self.window.tree.get_children()[0])
        self.window._check_device(SimpleNamespace(keysym='space'))
        self.assertEqual(len(self.window.checked_devices), 3)

    def test_group_to_single_keeps_connection_and_distinct_plot_ids(self):
        self.open()
        w = self.window
        main = w.scan_device.get()
        w.sweep_stop.set('.1')
        w.sweep_step.set('.1')
        w._system_apply(True)
        self.pump(lambda: not w.pending)
        w._start_system()
        self.pump(lambda: bool(w.system.runs) and not w.pending)
        old_segments = set(w.segments)
        old_count = len(w.samples)
        w.release()
        self.pump(lambda: not w.control_session.claimed and not w.pending)
        w.checked_devices.add(main)
        w.open_selected()
        self.pump(lambda: w.system is None and w.snapshot.get('connected') and not w.pending)
        self.assertFalse(self.visas[main].closed)
        w.apply()
        self.pump(lambda: w.snapshot.get('configured') and not w.pending)
        w._send('once')
        self.pump(lambda: len(w.samples) > old_count)
        self.assertNotIn(w.samples[-1].segment, old_segments)
        self.assertEqual(w.export_button.cget('text'), 'Export CSV')
