"""Tk integration with injected instruments; no hardware discovery or commands."""
import time
import math
import tkinter as tk
import unittest
from unittest.mock import Mock

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.instrument_control import Keithley2450Manual, ManualSession, FUNCTION_LABELS, ManualSweep
from semi_auto_probe.ui.instrument_window import InstrumentWindow, IV_FUNCTION_LABELS
from test_instrument_control import FakeVisa


class InstrumentWindowTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        ProbeApp._configure_theme(self.root)
        self.visa = FakeVisa()
        self.session = ManualSession(lambda address: Keithley2450Manual(self.visa))
        self.rows = [dict(device=self.visa.identity, address='GPIB0::18::INSTR', status='Connected', detail='')]
        self.busy = False
        self.activity = Mock()
        self.window = InstrumentWindow(self.root, rows=lambda: self.rows, busy=lambda: self.busy,
                                       scanning=lambda: False, refresh=Mock(), activity=self.activity, session=self.session)
        self.window.show()
        self.root.update()

    def pump(self, predicate=lambda: False, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(0.01)
        self.assertTrue(predicate(), self.window.status.get())

    def open(self):
        self.window.tree.selection_set(self.window.tree.get_children()[0])
        self.window.open_selected()
        self.pump(lambda: self.window.snapshot.get('connected') and not self.window.pending)

    def apply(self):
        self.window.apply()
        self.pump(lambda: self.window.snapshot.get('configured') and not self.window.pending)

    def tearDown(self):
        self.visa.disconnected = False
        self.visa.fail_off = False
        if self.session.claimed:
            self.window.release()
            self.pump(lambda: not self.session.claimed and not self.window.pending)
        self.root.update_idletasks()
        self.window.destroy()
        self.session.thread.join(2)
        self.root.destroy()

    def test_iv_history_spans_run_and_fresh_markers_follow_latest(self):
        from semi_auto_probe.instrument_control import ManualSample
        import numpy as np
        from matplotlib.colors import to_rgb
        for cycle in range(300):
            for offset in range(3):
                self.window._add_sample(ManualSample('now', 1, cycle * 3 + offset,
                    '2450', 'GPIB', '1', offset + 1., cycle + offset + 1.,
                    'iv', offset + 1., 'voltage', cycle * 4))
        self.window._draw()
        curves = self.window.plot_samples[1].display_curves()
        self.assertEqual(len(curves), 100)
        self.assertEqual(curves[0][0].current_a, 1)
        self.assertEqual(curves[-1][-1].current_a, 302)
        self.assertTrue(any(100 < c[-1].current_a < 200 for c in curves))
        self.assertEqual(len(self.window.samples), 900)
        self.assertEqual(list(self.window.iv_current.get_xdata()), [3])
        self.assertEqual(list(self.window.iv_current.get_ydata()), [302])
        self.assertTrue(all(line.get_zorder() < self.window.iv_line.get_zorder()
                            for line in self.window.iv_history_lines))
        colors = self.window.iv_recent.get_facecolors()
        self.assertEqual(len(colors), 10)
        np.testing.assert_allclose(colors[0, :3], to_rgb('#20a88a'))
        np.testing.assert_allclose(colors[-1, :3], to_rgb('#ff922e'))
        self.window.iv_recent_count.set('4')
        self.window._draw()
        self.assertEqual(len(self.window.iv_recent.get_offsets()), 4)
        self.window.replot()
        self.assertEqual(len(self.window.samples), 900)
        self.assertEqual(len(self.window.iv_current.get_xdata()), 0)
        self.assertEqual(len(self.window.iv_recent.get_offsets()), 0)
        self.assertTrue(all(len(line.get_xdata()) == 0 for line in self.window.iv_history_lines))

    def test_device_picker_collapses_after_open_and_can_expand(self):
        self.assertTrue(self.window.sidebar.winfo_ismapped())
        self.assertFalse(self.window.detail.winfo_ismapped())
        self.assertLess(self.window.winfo_width(), 600)
        self.open()
        self.root.update()
        self.assertTrue(self.window.detail.winfo_ismapped())
        self.assertFalse(self.window.sidebar.winfo_ismapped())
        self.assertTrue(self.window.sidebar_rail.winfo_ismapped())
        baseline = list(self.visa.commands)
        self.window.toggle_sidebar()
        self.root.update()
        self.assertTrue(self.window.sidebar.winfo_ismapped())
        self.window.toggle_sidebar()
        self.window.compact.set(True)
        self.window.set_compact()
        self.window.compact.set(False)
        self.window.set_compact()
        self.root.update()
        self.assertFalse(self.window.sidebar.winfo_ismapped())
        self.assertTrue(self.window.sidebar_rail.winfo_ismapped())
        # The independent one-second status poll may run during Tk relayout.
        # Layout changes must not emit parameter, output or acquisition commands.
        self.assertTrue(all(command == ':OUTP?' for command in self.visa.commands[len(baseline):]))

    def test_plot_controls_share_row_and_status_shares_action_row(self):
        self.open()
        self.window.output_text.set('OUTPUT ON · I-V 10000000')
        self.root.update()
        self.assertEqual(self.window.log_x_button.master, self.window.fresh_box.master)
        self.assertEqual(self.window.log_y_button.master, self.window.fresh_box.master)
        self.assertFalse(hasattr(self.window.navigation, 'winfo_ismapped'))
        self.assertLess(abs(self.window.output_label.winfo_rooty() - self.window.monitor_off.winfo_rooty()), 12)
        self.assertLessEqual(self.window.output_label.winfo_rootx() + self.window.output_label.winfo_width(),
                             self.window.monitor_stop.master.winfo_rootx())

    def test_empty_and_unsupported_device(self):
        self.rows.clear()
        self.window.known_devices.clear()
        self.window._sync_devices()
        self.assertIn('No connected', self.window.status.get())
        self.rows.append(dict(device='KEYSIGHT,B1500A,123', address='GPIB0::17::INSTR', status='Connected', detail=''))
        self.window._sync_devices()
        self.window.tree.selection_set(self.window.tree.get_children()[0])
        self.window.open_selected()
        self.assertFalse(self.session.claimed)
        self.assertIn('not available', self.window.status.get())
        self.assertEqual(self.visa.commands, [])

    def test_busy_device_cannot_be_opened(self):
        self.busy = True
        self.window._sync_devices()
        self.window.tree.selection_set(self.window.tree.get_children()[0])
        self.window.open_selected()
        self.assertFalse(self.session.claimed)
        self.assertEqual(self.visa.commands, [])

    def test_plot_pause_does_not_stop_acquisition_and_close_retains_data(self):
        self.open()
        self.apply()
        self.window._send('on')
        self.pump(lambda: self.window.snapshot.get('output') is True and not self.window.pending)
        self.window.interval.set('0.05')
        self.window.paused.set(True)
        self.window.start()
        self.pump(lambda: len(self.window.samples) >= 3)
        self.assertEqual(len(self.window.lines[0].get_xdata()), 0)
        self.assertTrue(self.session.acquiring)
        self.window.resume()
        self.pump(lambda: len(self.window.lines[0].get_xdata()) >= 3)
        self.window.request_close()
        self.pump(lambda: self.window.wm_state() == 'withdrawn')
        count = len(self.window.samples)
        self.assertFalse(self.visa.on)
        self.assertFalse(self.session.claimed)
        self.window.show()
        self.root.update()
        self.assertEqual(len(self.window.samples), count)
        self.assertFalse(self.visa.closed)
        self.assertEqual(self.window.output_text.get(), 'OUTPUT OFF')

    def test_failed_close_keeps_window_visible_and_allows_retry(self):
        self.open()
        self.apply()
        self.visa.fail_off = True
        self.window.request_close()
        self.pump(lambda: bool(self.window.snapshot.get('fault')))
        self.assertNotEqual(self.window.wm_state(), 'withdrawn')
        self.assertIn('UNKNOWN', self.window.output_text.get())
        self.assertTrue(self.session.claimed)
        self.visa.fail_off = False
        self.window.request_close()
        self.pump(lambda: self.window.wm_state() == 'withdrawn')

    def test_control_fields_do_not_send_commands_until_apply(self):
        self.open()
        baseline = list(self.visa.commands)
        self.window.value.set('0.1')
        self.root.update()
        self.assertEqual(self.visa.commands, baseline)
        self.assertTrue(self.window.on_button.instate(['disabled']))
        self.apply()
        self.assertIn(':SOUR:VOLT:LEV 0.1', self.visa.commands)
        self.assertFalse(self.visa.on)

    def test_switching_device_turns_output_off_before_new_control_page(self):
        self.open()
        self.apply()
        self.window._send('on')
        self.pump(lambda: self.window.snapshot.get('output') is True and not self.window.pending)
        self.rows.append(dict(device='KEYSIGHT,B1500A,123', address='GPIB0::17::INSTR', status='Connected'))
        self.window._sync_devices()
        self.window.tree.selection_set(self.window.tree.get_children()[-1])
        self.window.open_selected()
        self.pump(lambda: not self.session.claimed and 'B1500A' in self.window.heading.get())
        self.assertFalse(self.visa.on)
        self.assertIn('not available', self.window.status.get())

    def test_missing_device_is_marked_disconnected_after_refresh(self):
        self.rows.clear()
        self.window._sync_devices()
        self.assertEqual(next(iter(self.window.devices.values()))['display_state'], 'Disconnected')

    def test_function_switch_updates_units_and_range_lists(self):
        self.open()
        self.window.function.set(FUNCTION_LABELS['current'])
        self.window._mode_changed()
        self.assertEqual(self.window.mode.get(), 'current')
        self.assertEqual(self.window.value_label.get(), 'Set current (A)')
        self.assertIn('10 nA', self.window.source_range_box.cget('values'))
        self.assertIn('200 V', self.window.measure_range_box.cget('values'))
        self.window.value.set('500nA')
        self.window.limit.set('1V')
        self.window.source_range.set('1 uA')
        self.window.measure_range.set('2 V')
        self.apply()
        self.assertAlmostEqual(self.session.adapter.settings.value, 500e-9, delta=1e-18)
        self.assertIn(':SOUR:CURR:LEV 5e-07', self.visa.commands)
        self.assertIn(':SENS:VOLT:RANG 2', self.visa.commands)

    def test_front_terminal_removes_rear_only_measure_ranges(self):
        self.window.measure_range.set('10 nA')
        self.window.terminal.set('front')
        self.window._update_range_options()
        self.assertEqual(self.window.measure_range.get(), 'Auto')
        self.assertNotIn('10 nA', self.window.measure_range_box.cget('values'))

    def test_voltage_input_supports_si_and_rejects_current_units(self):
        self.open()
        self.window.value.set('100mV')
        self.window.limit.set('10u')
        self.window.source_range.set('200 mV')
        self.window.measure_range.set('10 uA')
        self.apply()
        self.assertEqual(self.session.adapter.settings.value, .1)
        self.assertAlmostEqual(self.session.adapter.settings.limit, 1e-5)
        self.assertIn('source 200 mV', self.window.applied.get())
        before = len(self.visa.commands)
        self.window.value.set('100mA')
        self.window.apply()
        self.assertIn('Expected V', self.window.status.get())
        self.assertEqual(len(self.visa.commands), before)

    def test_measurement_settings_apply_and_lock_during_acquisition(self):
        self.open()
        baseline = list(self.visa.commands)
        self.window.nplc.set('0.25')
        self.window.autozero.set('OFF')
        self.root.update()
        self.assertEqual(self.visa.commands, baseline)
        self.assertEqual(str(self.window.nplc_box.cget('state')), 'normal')
        self.apply()
        self.assertEqual(self.session.adapter.settings.nplc, .25)
        self.assertFalse(self.session.adapter.settings.autozero)
        self.assertIn('NPLC 0.25 / Auto zero OFF', self.window.applied.get())
        self.window.start()
        self.pump(lambda: self.window.snapshot.get('acquiring') and not self.window.pending)
        self.assertTrue(self.window.nplc_box.instate(['disabled']))
        self.assertTrue(self.window.autozero_box.instate(['disabled']))

    def test_invalid_nplc_is_rejected_before_io(self):
        self.open()
        for value in ('invalid', 'nan', '0', '11'):
            before = list(self.visa.commands)
            self.window.nplc.set(value)
            self.window.apply()
            self.assertEqual(self.visa.commands, before)
            self.assertFalse(self.window.pending)

    def test_compact_pin_restore_does_not_change_instrument(self):
        self.open()
        self.apply()
        baseline = list(self.visa.commands)
        self.window.topmost.set(True)
        self.window.set_topmost()
        self.window.compact.set(True)
        self.window.set_compact()
        self.root.update()
        self.assertTrue(self.window.attributes('-topmost'))
        self.assertFalse(self.window.form.winfo_ismapped())
        self.assertFalse(self.window.sidebar.winfo_ismapped())
        self.assertTrue(self.window.display.winfo_ismapped())
        self.assertLess(self.window.winfo_width(), 800)
        self.assertTrue(self.window.monitor_off.winfo_ismapped())
        self.assertGreater(self.window.canvas.get_tk_widget().winfo_height(), 100)
        self.window.compact.set(False)
        self.window.set_compact()
        self.root.update()
        self.assertTrue(self.window.form.winfo_ismapped())
        self.assertEqual([c for c in self.visa.commands if c != ':OUTP?'],
                         [c for c in baseline if c != ':OUTP?'])

    def test_replot_keeps_data_and_continues_acquisition(self):
        self.open()
        self.apply()
        self.window.interval.set('0.05')
        self.window.start()
        self.pump(lambda: len(self.window.samples) >= 3)
        count = len(self.window.samples)
        self.window.replot()
        self.assertEqual(len(self.window.samples), count)
        self.assertEqual(len(self.window.lines[0].get_xdata()), 0)
        self.assertTrue(self.session.acquiring)
        self.pump(lambda: len(self.window.lines[0].get_xdata()) > 0)
        self.assertGreater(len(self.window.samples), count)

    def test_iv_functions_apply_and_start_inline(self):
        self.open()
        for mode, stop, step, source in (('voltage', '100mV', '50mV', 'VOLT'),
                                          ('current', '2uA', '1uA', 'CURR')):
            self.window.function.set(IV_FUNCTION_LABELS[mode])
            self.window._mode_changed()
            self.window.sweep_stop.set(stop)
            self.window.sweep_step.set(step)
            self.window.sweep_delay.set('0')
            self.root.update()
            self.assertTrue(self.window.sweep_fields.winfo_ismapped())
            self.assertFalse(self.window.manual_actions.winfo_ismapped())
            self.assertEqual(self.window.mode.get(), mode)
            self.apply()
            before = len(self.window.samples)
            self.window.iv_button.invoke()
            self.pump(lambda: len(self.window.samples) == before + 3 and not self.session.acquiring)
            self.assertFalse(self.visa.on)
            self.assertEqual(self.window.samples[-1].source_mode, mode)
            self.assertIn(f':SOUR:FUNC:MODE {source}', self.visa.commands)
        self.window.function.set(FUNCTION_LABELS['voltage'])
        self.window._mode_changed()
        self.root.update()
        self.assertTrue(self.window.manual_actions.winfo_ismapped())
        self.assertFalse(self.window.sweep_fields.winfo_ismapped())

    def test_iv_edits_require_apply_again(self):
        self.open()
        self.window.function.set(IV_FUNCTION_LABELS['voltage'])
        self.window._mode_changed()
        self.apply()
        self.window.sweep_stop.set('2')
        before = list(self.visa.commands)
        self.window.start_iv()
        self.assertEqual(self.visa.commands, before)
        self.assertIn('Click Apply', self.window.status.get())
        self.assertFalse(self.visa.on)

    def test_smaller_measure_range_reduces_limit_without_io(self):
        self.open()
        self.window.function.set(IV_FUNCTION_LABELS['voltage'])
        self.window._mode_changed()
        baseline = list(self.visa.commands)
        self.window.measure_range.set('1 uA')
        self.window.measure_range_box.event_generate('<<ComboboxSelected>>')
        self.assertEqual(self.window.limit.get(), '1 uA')
        self.assertIn('Click Apply', self.window.status.get())
        self.assertEqual(self.visa.commands, baseline)
        self.apply()
        self.assertEqual(self.session.adapter.settings.limit, 1e-6)
        self.window.measure_range.set('10 uA')
        self.window._measure_range_changed()
        self.assertEqual(self.window.limit.get(), '1 uA')
        self.window.measure_range.set('Auto')
        self.window._measure_range_changed()
        self.assertEqual(self.window.limit.get(), '1 uA')

    def test_smaller_voltage_measure_range_reduces_voltage_protection(self):
        self.open()
        self.window.function.set(IV_FUNCTION_LABELS['current'])
        self.window._mode_changed()
        self.window.measure_range.set('2 V')
        self.window._measure_range_changed()
        self.assertEqual(self.window.limit.get(), '2 V')
        self.window.limit.set('1V')
        self.window._measure_range_changed()
        self.assertEqual(self.window.limit.get(), '1V')

    def test_iv_incompatible_measure_range_can_be_corrected_without_reconnect(self):
        self.open()
        self.window.function.set(IV_FUNCTION_LABELS['voltage'])
        self.window._mode_changed()
        self.window.value.set('-10')
        self.window.sweep_low.set('-10')
        self.window.sweep_stop.set('10')
        self.window.sweep_step.set('.5')
        self.window.limit.set('10uA')
        self.window.measure_range.set('1 uA')
        baseline = list(self.visa.commands)
        self.window.apply()
        self.assertIn('protection limit', self.window.status.get())
        self.assertEqual(self.visa.commands, baseline)
        self.assertTrue(self.session.connected)
        self.assertFalse(self.session.fault)
        self.assertFalse(self.window.pending)
        self.window.measure_range.set('10 uA')
        self.apply()
        self.assertAlmostEqual(self.session.adapter.settings.limit, 1e-5, delta=1e-15)
        self.assertFalse(self.visa.on)

    def test_inline_segments_apply_and_scan(self):
        self.open()
        self.window.function.set(IV_FUNCTION_LABELS['voltage'])
        self.window._mode_changed()
        baseline = list(self.visa.commands)
        self.window.value.set('0')
        self.window.sweep_stop.set('1')
        self.window.sweep_low.set('-1')
        self.window.sweep_step.set('1')
        self.window.sweep_delay.set('0')
        self.window.sweep_direction.set('down')
        self.window.sweep_segments.set('3')
        self.assertEqual(self.visa.commands, baseline)
        self.assertFalse(hasattr(self.window, 'path_button'))
        self.apply()
        self.window.start_iv()
        self.pump(lambda: len(self.window.samples) == 4 and not self.session.acquiring, timeout=4)
        self.assertEqual([p.source_value for p in self.window.samples], [0,-1,0,1])
        self.assertFalse(self.visa.on)

    def test_iv_controls_fit_minimum_window(self):
        self.open()
        self.window.function.set(IV_FUNCTION_LABELS['voltage'])
        self.window._mode_changed()
        self.apply()
        self.window.geometry('1060x710')
        self.root.update()
        self.assertTrue(self.window.iv_button.winfo_ismapped())
        bottom = self.window.iv_button.winfo_rooty() + self.window.iv_button.winfo_height()
        self.assertLess(bottom, self.window.winfo_rooty() + self.window.winfo_height() - 40)

    def test_iv_x_selection_uses_setpoints_without_modifying_measurements(self):
        self.open()
        self.apply()
        self.window._send('sweep', ManualSweep(0, .1, .05, 0))
        self.pump(lambda: len(self.window.samples) == 3 and not self.session.acquiring)
        before = list(self.window.samples)
        self.window.iv_x_source.set('Set V')
        self.window._iv_x_changed()
        self.assertEqual(list(self.window.iv_line.get_xdata()), [0, .05, .1])
        self.assertEqual(self.window.iv_axis.get_xlabel(), 'Set voltage (V)')
        self.assertEqual(list(self.window.iv_line.get_ydata()), [.000002] * 3)
        self.window.iv_x_source.set('Measured V')
        self.window._iv_x_changed()
        self.assertEqual(list(self.window.iv_line.get_xdata()), [.098] * 3)
        self.assertEqual(self.window.samples, before)

    def test_set_voltage_not_substituted_with_current_setpoint(self):
        self.open()
        self.window.function.set(IV_FUNCTION_LABELS['current'])
        self.window._mode_changed()
        self.window.sweep_stop.set('2u')
        self.window.sweep_step.set('1u')
        self.window.sweep_delay.set('0')
        self.apply()
        self.window.start_iv()
        self.pump(lambda: len(self.window.samples) == 3 and not self.session.acquiring)
        self.window.iv_x_source.set('Set V')
        self.window._iv_x_changed()
        self.assertEqual(self.window.iv_x_source.get(), 'Measured V')
        self.assertEqual(list(self.window.iv_line.get_xdata()), [.000002] * 3)

    def test_plot_ticks_use_engineering_units(self):
        self.assertEqual(self.window.iv_axis.yaxis.get_major_formatter()(70e-9), '70 nA')
        self.assertIn('V', self.window.iv_axis.xaxis.get_major_formatter()(2e-6))
        self.assertNotIn('e-', self.window.axes[0].yaxis.get_major_formatter()(2e-6))
        self.assertEqual(self.window.axes[1].yaxis.get_major_formatter()(3e-3), '3 mA')

    def test_iv_log_axes_use_absolute_values_and_preserve_raw_data(self):
        from semi_auto_probe.instrument_control import ManualSample
        samples = [ManualSample('2026-09-22T00:00:00.000+00:00', 1, i, '2450', 'GPIB', '1', v, a,
                                'iv', v * 2, 'voltage')
                   for i, (v, a) in enumerate(((-2., -1e-9), (0., 0.), (3., 2e-9)))]
        for sample in samples:
            self.window._add_sample(sample)
        for log_x, log_y in ((False, True), (True, False), (True, True)):
            self.window.iv_log_x.set(log_x)
            self.window.iv_log_y.set(log_y)
            self.window._iv_x_changed()
            self.assertEqual(self.window.iv_axis.get_xscale(), 'log' if log_x else 'linear')
            self.assertEqual(self.window.iv_axis.get_yscale(), 'log' if log_y else 'linear')
            self.assertEqual(self.window.iv_line.get_xdata()[0], 2 if log_x else -2)
            self.assertEqual(self.window.iv_line.get_ydata()[0], 1e-9 if log_y else -1e-9)
            self.assertTrue(math.isnan(self.window.iv_line.get_xdata()[1]))
            self.assertTrue(math.isnan(self.window.iv_line.get_ydata()[1]))
        self.window.iv_x_source.set('Set V')
        self.window._iv_x_changed()
        self.assertEqual(self.window.iv_line.get_xdata()[0], 4)
        self.window.iv_log_x.set(False)
        self.window.iv_log_y.set(False)
        self.window._iv_x_changed()
        self.assertEqual(list(self.window.iv_line.get_xdata()), [-4, 0, 6])
        self.assertEqual(list(self.window.iv_line.get_ydata()), [-1e-9, 0, 2e-9])
        self.assertEqual(self.window.samples, samples)
        self.assertEqual(self.window.iv_axis.yaxis.get_major_formatter()(1e-9), '1 nA')

    def test_empty_and_zero_only_log_plot(self):
        import warnings
        from semi_auto_probe.instrument_control import ManualSample
        self.window.plot_mode.set('I-V')
        self.window.iv_log_x.set(True)
        self.window.iv_log_y.set(True)
        with warnings.catch_warnings():
            warnings.simplefilter('error', UserWarning)
            self.window._iv_x_changed()
            self.window.canvas.draw()
            self.window._add_sample(ManualSample('now', 1, 0, '2450', 'GPIB', '1', 0, 0, 'iv', 0, 'voltage'))
            self.window._draw()
            self.window.canvas.draw()
        self.assertTrue(math.isnan(self.window.iv_line.get_xdata()[0]))
        self.window.replot()
        self.assertEqual(len(self.window.iv_line.get_xdata()), 0)

    def test_iv_graph_uses_measured_voltage_and_current(self):
        self.open()
        self.apply()
        self.window._send('sweep', ManualSweep(0, .1, .05, 0))
        self.pump(lambda: len(self.window.samples) == 3 and not self.session.acquiring)
        self.pump(lambda: len(self.window.iv_line.get_xdata()) == 3)
        self.assertEqual(self.window.plot_mode.get(), 'I-V')
        self.assertTrue(self.window.iv_axis.get_visible())
        self.assertFalse(self.window.axes[0].get_visible())
        self.assertEqual(list(self.window.iv_line.get_xdata()), [.098] * 3)
        self.assertEqual(list(self.window.iv_line.get_ydata()), [.000002] * 3)
        self.assertIn('uA', self.window.current.get())
        self.window.replot()
        self.assertEqual(len(self.window.iv_line.get_xdata()), 0)
        self.assertEqual(len(self.window.samples), 3)
        self.assertFalse(self.visa.on)

    def test_controls_fit_minimum_window(self):
        self.open()
        self.apply()
        self.window.geometry('1060x710')
        self.root.update()
        self.assertTrue(self.window.stop_button.winfo_ismapped())
        bottom = self.window.stop_button.winfo_rooty() + self.window.stop_button.winfo_height()
        self.assertLess(bottom, self.window.winfo_rooty() + self.window.winfo_height() - 40)

    def test_controls_fit_default_window(self):
        self.open()
        self.root.update_idletasks()
        self.assertTrue(self.window.stop_button.winfo_ismapped())
        bottom = self.window.stop_button.winfo_rooty() + self.window.stop_button.winfo_height()
        self.assertLess(bottom, self.window.winfo_rooty() + self.window.winfo_height() - 40)
        self.assertGreater(self.window.canvas.get_tk_widget().winfo_width(), 250)
        self.assertGreater(self.window.canvas.get_tk_widget().winfo_height(), 150)


if __name__ == '__main__':
    unittest.main()

