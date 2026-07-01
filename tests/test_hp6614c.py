from __future__ import annotations

import unittest

from semi_auto_probe.hp6614c import (
    HP6614C_TEST_OUTPUT,
    HP6614C_TEST_TRANSFER,
    HP6614CVoltageConfig,
    HP6614CRunner,
    analyze_hp6614c_transfer_records,
    hp6614c_transfer_heatmap_value,
    hp6614c_sweep_config_from_params,
    hp6614c_voltage_config_from_params,
    verify_hp6614c_identity,
)


class FakeInstrument:
    def __init__(self) -> None:
        self.timeout = 0
        self.commands: list[str] = []

    def write(self, command: str) -> None:
        self.commands.append(command)

    def query(self, command: str) -> str:
        self.commands.append(command)
        if command == "*IDN?":
            return "HEWLETT-PACKARD,6614C,0,A.01.05"
        if command == "MEAS:VOLT?":
            return "1.234"
        if command == "MEAS:CURR?":
            return "5.67E-7"
        return "0"

    def close(self) -> None:
        self.commands.append("CLOSE")


class HP6614CTests(unittest.TestCase):
    def test_identity_accepts_hp_6614c(self) -> None:
        verify_hp6614c_identity("HEWLETT-PACKARD,6614C,0,A.01.05")
        verify_hp6614c_identity("AGILENT TECHNOLOGIES,6614C,0,A.01.05")
        verify_hp6614c_identity("KEYSIGHT TECHNOLOGIES,6614C,0,A.01.05")

    def test_identity_rejects_wrong_instrument(self) -> None:
        with self.assertRaises(RuntimeError):
            verify_hp6614c_identity("KEITHLEY INSTRUMENTS,MODEL 2450,1234")

    def test_voltage_config_validation_rejects_negative_voltage(self) -> None:
        with self.assertRaisesRegex(ValueError, "0..100"):
            HP6614CVoltageConfig(voltage_v=-1).normalized()

    def test_voltage_params_parse_gate_voltage_and_current_limit(self) -> None:
        config = hp6614c_voltage_config_from_params(
            {
                "resource": "GPIB0::5::INSTR",
                "gate_voltage_v": "3.5",
                "current_limit_a": "2e-6",
                "settle_s": "0.25",
                "output_off_after": "yes",
            }
        )

        self.assertEqual(config.resource_name, "GPIB0::5::INSTR")
        self.assertAlmostEqual(config.voltage_v, 3.5)
        self.assertAlmostEqual(config.current_limit_a, 2e-6)
        self.assertAlmostEqual(config.settle_s, 0.25)
        self.assertTrue(config.output_off_after)

    def test_runner_applies_voltage_safely_and_reads_back(self) -> None:
        instrument = FakeInstrument()
        runner = HP6614CRunner(instrument)

        reading = runner.apply_voltage(HP6614CVoltageConfig(voltage_v=1.2, current_limit_a=1e-6, settle_s=0, output_off_after=True))

        self.assertEqual(instrument.commands[:4], ["OUTP OFF", "VOLT 0", "CURR 1e-06", "VOLT 1.2"])
        self.assertIn("OUTP ON", instrument.commands)
        self.assertIn("MEAS:VOLT?", instrument.commands)
        self.assertIn("MEAS:CURR?", instrument.commands)
        self.assertEqual(instrument.commands[-1], "OUTP OFF")
        self.assertAlmostEqual(reading.voltage_v, 1.234)
        self.assertAlmostEqual(reading.current_a, 5.67e-7)

    def test_transfer_sweep_params_parse_keithley_drain_settings(self) -> None:
        config = hp6614c_sweep_config_from_params(
            {
                "hp_resource": "GPIB0::5::INSTR",
                "keithley_resource": "GPIB0::18::INSTR",
                "output_terminal": "front",
                "sweep_start_v": "0",
                "sweep_end_v": "2",
                "sweep_points": "5",
                "bias_values_v": "0.1,0.2",
                "drain_measure_range": "1e-7",
                "heatmap_metric": "ss",
                "skip_open_clear": "true",
                "readback": "false",
            },
            test_type=HP6614C_TEST_TRANSFER,
        )

        self.assertEqual(config.test_type, "transfer")
        self.assertEqual(config.scan_type, "forward")
        self.assertEqual(config.output_terminal, "front")
        self.assertEqual(config.sweep_values(), (0.0, 0.5, 1.0, 1.5, 2.0))
        self.assertEqual(config.bias_values_v, (0.1, 0.2))
        self.assertAlmostEqual(config.drain_measure_range or 0.0, 1e-7)
        self.assertEqual(config.heatmap_metric, "ss")
        self.assertAlmostEqual(config.gate_settle_s, 0.02)
        self.assertTrue(config.skip_open_clear)
        self.assertFalse(config.readback)

    def test_transfer_sweep_defaults_to_front_terminal(self) -> None:
        config = hp6614c_sweep_config_from_params({}, test_type=HP6614C_TEST_TRANSFER)

        self.assertEqual(config.output_terminal, "front")

    def test_transfer_sweep_accepts_double_gate_scan(self) -> None:
        config = hp6614c_sweep_config_from_params(
            {
                "sweep_start_v": "0",
                "sweep_end_v": "1",
                "sweep_points": "3",
                "scan_type": "double",
                "bias_values_v": "0.1",
            },
            test_type=HP6614C_TEST_TRANSFER,
        )

        self.assertEqual(config.scan_type, "double")
        self.assertEqual(config.sweep_values(), (0.0, 0.5, 1.0, 0.5, 0.0))

    def test_output_sweep_uses_unified_gate_bias_list(self) -> None:
        legacy_mode_key = "output_" + "gate_mode"
        legacy_vg_key = "output_" + "vg_1"
        config = hp6614c_sweep_config_from_params(
            {
                "bias_values_v": "1.2,1.6,2.0",
                legacy_mode_key: "Auto" + "Sweep",
                legacy_vg_key: "0.5",
            },
            test_type=HP6614C_TEST_OUTPUT,
        )

        self.assertEqual(config.bias_values_v, (1.2, 1.6, 2.0))
        self.assertAlmostEqual(config.sweep_end_v, -2.0)
        self.assertAlmostEqual(config.gate_settle_s, 0.02)

    def test_output_sweep_ignores_legacy_gate_boxes_without_bias_list(self) -> None:
        legacy_vg_1 = "output_" + "vg_1"
        legacy_vg_2 = "output_" + "vg_2"
        config = hp6614c_sweep_config_from_params(
            {
                legacy_vg_1: "1.2",
                legacy_vg_2: "1.6",
            },
            test_type=HP6614C_TEST_OUTPUT,
        )

        self.assertEqual(config.bias_values_v, (0.0,))

    def test_sweep_rejects_drain_current_limit_above_keithley_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "0..1 A"):
            hp6614c_sweep_config_from_params({"drain_current_limit_a": "2"}, test_type=HP6614C_TEST_TRANSFER)

    def test_transfer_heatmap_metrics_average_independent_curve_results(self) -> None:
        records = [
            {"drain_voltage_v": -1.0, "gate_voltage_v": 0.0, "drain_current_a": 1.0},
            {"drain_voltage_v": -1.0, "gate_voltage_v": 1.0, "drain_current_a": 10.0},
            {"drain_voltage_v": -1.0, "gate_voltage_v": 2.0, "drain_current_a": 100.0},
            {"drain_voltage_v": -2.0, "gate_voltage_v": 0.0, "drain_current_a": 1.0},
            {"drain_voltage_v": -2.0, "gate_voltage_v": 1.0, "drain_current_a": 100.0},
            {"drain_voltage_v": -2.0, "gate_voltage_v": 2.0, "drain_current_a": 10000.0},
        ]

        analysis = analyze_hp6614c_transfer_records(records, "on_off_ratio")

        self.assertEqual([result.drain_voltage_v for result in analysis.curves], [-1.0, -2.0])
        self.assertEqual([result.value for result in analysis.curves], [100.0, 10000.0])
        self.assertEqual(analysis.mean_value, 5050.0)
        self.assertEqual(hp6614c_transfer_heatmap_value(records, "on_off_ratio"), 5050.0)


if __name__ == "__main__":
    unittest.main()
