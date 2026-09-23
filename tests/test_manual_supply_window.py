import unittest
import test_instrument_window as base
from test_manual_supplies import FakeDP, FakeHP
from semi_auto_probe.power_supply_control import DP100Manual, HP6614CManual
from semi_auto_probe.instrument_control import Keithley2450Manual


class SupplyWindowTests(unittest.TestCase):
    setUp = base.InstrumentWindowTests.setUp
    tearDown = base.InstrumentWindowTests.tearDown
    pump = base.InstrumentWindowTests.pump
    apply = base.InstrumentWindowTests.apply

    def supply(self, kind):
        driver = FakeDP() if kind == 'dp100' else FakeHP()
        adapter = DP100Manual(driver) if kind == 'dp100' else HP6614CManual(driver)
        self.session.adapter_factory = lambda address: adapter
        self.rows[:] = [dict(device='DP100' if kind == 'dp100' else 'HEWLETT-PACKARD,6614C,0',
                            address='USB / vendor DLL' if kind == 'dp100' else 'GPIB0::5::INSTR',
                            status='App not connected' if kind == 'dp100' else 'Connected', detail='')]
        self.window.known_devices.clear()
        self.window._sync_devices()
        self.window.tree.selection_set(self.window.tree.get_children()[0])
        self.window.open_selected()
        self.pump(lambda: self.window.snapshot.get('connected') and not self.window.pending)
        return driver

    def test_dp_usb_entry_visible_and_manual_readings_plotted(self):
        driver = self.supply('dp100')
        self.assertEqual(self.window.supply_type, 'dp100')
        self.assertFalse(self.window.mode_box.winfo_ismapped())
        self.assertTrue(self.window.supply_fields.winfo_ismapped())
        self.assertFalse(self.window.sidebar.winfo_ismapped())
        self.assertIn('Readback set:', self.window.supply_readback.get())
        self.window.supply_vars['voltage'].set('1.2V')
        self.apply()
        self.window._send('on')
        self.pump(lambda: self.window.snapshot['output'] is True and not self.window.pending)
        self.window.interval.set('0.05')
        self.window.start()
        self.pump(lambda: len(self.window.samples) >= 3)
        self.window._draw()
        self.assertEqual(self.window.samples[0].voltage_v, 1.232)
        self.assertIn('P ', self.window.updated.get())
        self.assertEqual(self.window.lines[0].get_ydata()[0], 1.232)
        self.window.request_close()
        self.pump(lambda: not self.session.claimed and not self.window.pending)
        self.assertFalse(driver.state['output'])
        self.assertTrue(driver.connected)
        self.assertGreaterEqual(len(self.window.samples), 3)

    def test_hp_panel_status_and_protection(self):
        driver = self.supply('hp6614c')
        self.window.supply_vars['measure_range'].set('20 mA')
        self.window.supply_vars['ocp_enabled'].set('ON')
        self.apply()
        self.assertEqual(driver.values['SENS:CURR:RANG'], .02)
        self.assertIn('CV', self.window.supply_readback.get())
        self.assertFalse(self.window.supply_optional_rows['preset'].winfo_ismapped())
        self.assertTrue(self.window.clear_protection_button.winfo_ismapped())
        self.window.geometry('1060x710')
        self.root.update()
        self.assertLess(self.window.stop_button.winfo_rooty() + self.window.stop_button.winfo_height(),
                        self.window.winfo_rooty() + self.window.winfo_height() - 30)
        driver.questionable = 2
        self.session.next_status = 0
        self.pump(lambda: self.window.supply_readback.get().endswith('· OCP'))
        self.window._send('clear_protection')
        self.pump(lambda: not self.window.pending)
        self.assertFalse(driver.on)
        self.assertEqual(driver.questionable, 0)

    def test_return_to_smu_restores_parameters(self):
        self.supply('dp100')
        self.window.release()
        self.pump(lambda: not self.session.claimed and not self.window.pending)
        self.session.adapter_factory = lambda address: Keithley2450Manual(self.visa)
        self.rows[:] = [dict(device=self.visa.identity, address='GPIB0::18::INSTR', status='Connected', detail='')]
        self.window.known_devices.clear()
        self.window._sync_devices()
        self.window.tree.selection_set(self.window.tree.get_children()[0])
        self.window.open_selected()
        self.pump(lambda: self.window.snapshot.get('connected') and not self.window.pending)
        self.assertTrue(self.window.mode_box.winfo_ismapped())
        self.assertFalse(self.window.supply_fields.winfo_ismapped())
        self.apply()
