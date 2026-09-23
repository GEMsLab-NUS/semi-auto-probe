"""Workflow card -> persisted params -> real runner, using an injected VISA device."""
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from semi_auto_probe.auto_test import create_autotest_flow_card
from semi_auto_probe.workflow_library import workflow_payload, cards_from_workflow_payload
from semi_auto_probe.keithley2450 import IVSweepConfig, iv_sweep_config_from_params, Keithley2450IVRunner
from semi_auto_probe.flow_editor import MeasurementFlowEditor
from semi_auto_probe.workflow_runtime import NodeCapture
from test_instrument_control import FakeVisa


class WorkflowIVSegmentsTests(unittest.TestCase):
    def card(self):
        card = create_autotest_flow_card('iv', 'iv_test')
        card.params.update(start='0', stop='1V', low='-1V', step='1V', segments='3',
                           initial_direction='down', source_delay_s='0', current_limit_a='10uA',
                           measure_range='10uA', source_range='2V', nplc='.1', autozero='false')
        return card

    def test_new_card_exposes_segment_settings(self):
        card = self.card()
        self.assertNotIn('scan_type', card.params)
        keys = {p.key for p in MeasurementFlowEditor._inline_params(card)}
        self.assertTrue({'start', 'initial_direction', 'stop', 'low', 'segments', 'step'} <= keys)
        config = iv_sweep_config_from_params(card.params)
        self.assertEqual(tuple(config.sweep_values()), (0,-1,0,1))
        self.assertFalse(config.autozero)
        self.assertEqual(config.source_range, 2)

    def test_round_trip_preserves_new_fields(self):
        card = self.card()
        restored = cards_from_workflow_payload(workflow_payload([card]))[0]
        self.assertEqual(restored.params['segments'], '3')
        self.assertEqual(restored.params['low'], '-1V')
        config = iv_sweep_config_from_params(restored.params)
        self.assertEqual(tuple(config.sweep_values()), (0,-1,0,1))

    def test_old_paths_survive_load_and_resave(self):
        for path in ('single', 'double', 'dualpolar'):
            raw = dict(start='0', stop='1', step='.3', scan_type=path)
            card = create_autotest_flow_card('iv', 'old', saved_params=raw)
            expected = IVSweepConfig(start=0, stop=1, step=.3, scan_type=path).sweep_values()
            restored = cards_from_workflow_payload(workflow_payload([card]))[0]
            self.assertEqual(restored.params['segments'], '')
            self.assertEqual(iv_sweep_config_from_params(restored.params).sweep_values(), expected)

    def test_old_bidirectional_without_scan_type_is_preserved(self):
        card = create_autotest_flow_card('iv', 'old', saved_params={'start':'1', 'stop':'-1', 'step':'1', 'bidirectional':'true'})
        self.assertEqual(iv_sweep_config_from_params(card.params).sweep_values(), (1,0,-1,0,1))

    def test_runner_uses_same_settings_and_pair_readbacks(self):
        config = iv_sweep_config_from_params(self.card().params)
        visa = FakeVisa()
        runner = Keithley2450IVRunner(visa)
        samples = runner.run_sweep(config)
        self.assertEqual([s.source_value for s in samples], [0,-1,0,1])
        self.assertEqual([s.index for s in samples], list(range(1,5)))
        self.assertTrue(all(s.total == 4 for s in samples))
        self.assertEqual(samples[0].voltage_v, .098)
        capture = NodeCapture("iv_test", "iv", "run", SimpleNamespace(name="p", row=0, col=0), queue.Queue())
        capture.sample(samples[0])
        self.assertEqual(capture.family("current")["Current"].x, (samples[0].source_value,))
        self.assertEqual(capture.family("voltage")["Current"].y, (samples[0].voltage_v,))
        self.assertEqual(capture.family("current")["Current"].x_measured, (samples[0].voltage_v,))
        self.assertNotEqual(capture.family("current")["Current"].x[0], samples[0].voltage_v)
        self.assertIn(':SENS:CURR:NPLC 0.1', visa.commands)
        self.assertIn(':SENS:CURR:AZER 0', visa.commands)
        self.assertIn(':SOUR:VOLT:RANG 2', visa.commands)
        self.assertFalse(visa.on)

    def test_current_sweep_and_limit_validation_before_io(self):
        params = self.card().params
        params.update(sweep_mode='current', start='0', stop='1uA', low='-1uA', step='1uA',
                      voltage_limit_v='1V', source_range='1uA', measure_range='2V')
        config = iv_sweep_config_from_params(params)
        visa = FakeVisa()
        samples = Keithley2450IVRunner(visa).run_sweep(config)
        self.assertEqual(len(samples), 4)
        self.assertIn(':READ? "defbuffer1", READ, SOUR', visa.commands)
        self.assertFalse(visa.on)
        invalid = IVSweepConfig(segments=2, measure_range=1e-6, current_limit_a=1e-5)
        visa = FakeVisa()
        with self.assertRaises(ValueError):
            Keithley2450IVRunner(visa).run_sweep(invalid)
        self.assertEqual(visa.commands, [])

    def test_long_workflow_uses_lazy_plan(self):
        config = iv_sweep_config_from_params(dict(segments='2000', start='0', stop='1', low='-1', step='.001'))
        values = config.sweep_values()
        self.assertEqual(len(values), 2_000_001)
        self.assertNotIsInstance(values, (list, tuple))
        self.assertEqual(values[-1], 0)

    def test_stop_still_disables_output(self):
        visa = FakeVisa()
        runner = Keithley2450IVRunner(visa)
        samples = runner.run_sweep(iv_sweep_config_from_params(self.card().params), stop_requested=lambda: True)
        self.assertEqual(samples, [])
        self.assertFalse(visa.on)


class WorkflowIVEditorTests(unittest.TestCase):
    from test_flow_editor_blender import BlenderEditorTests as _Fixture
    setUp = _Fixture.setUp
    tearDown = _Fixture.tearDown

    def test_inspector_updates_units_segments_and_reduces_limit(self):
        card = create_autotest_flow_card('iv', 'iv_settings')
        self.editor.cards.append(card)
        self.editor._show_inspector(card)
        self.assertTrue({'segments','low','initial_direction','source_range','autozero'} <= self.editor.param_vars.keys())
        self.editor._param_changed(card, 'segments', '3')
        self.editor._param_changed(card, 'step', '100mV')
        self.assertEqual(card.params['step'], '0.1')
        self.editor._param_changed(card, 'measure_range', '1uA')
        self.assertEqual(card.params['current_limit_a'], '1e-06')
        self.assertEqual(self.editor.param_vars['current_limit_a'].get(), '1e-06')
        self.editor._param_changed(card, 'measure_range', 'auto')
        self.assertEqual(card.params['current_limit_a'], '1e-06')
        self.assertEqual(len(iv_sweep_config_from_params(card.params).sweep_values()), 31)
        self.editor._param_changed(card, 'segments', '2.5')
        self.assertEqual(card.params['segments'], '3')

    def test_opening_legacy_inspector_does_not_change_path(self):
        card = create_autotest_flow_card('iv', 'old_iv', saved_params={'scan_type':'dualpolar','start':'0','stop':'1','step':'.5'})
        self.editor.cards.append(card)
        expected = iv_sweep_config_from_params(card.params).sweep_values()
        self.editor._show_inspector(card)
        self.assertEqual(self.editor.param_vars['segments'].get(), '')
        self.assertEqual(iv_sweep_config_from_params(card.params).sweep_values(), expected)
