from __future__ import annotations

import unittest

from semi_auto_probe.keithley2450 import (
    ConstantVoltageCurrentConfig,
    IVSweepConfig,
    IVSweepSample,
    Keithley2450IVRunner,
    calculate_iv_statistics,
    constant_voltage_current_config_from_params,
    iv_sweep_config_from_params,
)


class FakeInstrument:
    def __init__(self) -> None:
        self.timeout = 0
        self.commands: list[str] = []
        self.read_index = 0

    def write(self, command: str) -> None:
        self.commands.append(command)

    def query(self, command: str) -> str:
        self.commands.append(command)
        self.read_index += 1
        return f"{self.read_index * 0.1},{self.read_index * 1e-6},100000"

    def close(self) -> None:
        self.commands.append("CLOSE")


class Keithley2450Tests(unittest.TestCase):
    def test_bidirectional_sweep_values_include_return_path(self) -> None:
        config = IVSweepConfig(start=-1, stop=1, step=1, bidirectional=True)

        self.assertEqual(config.sweep_values(), (-1.0, 0.0, 1.0, 0.0, -1.0))

    def test_params_parse_voltage_and_current_limits(self) -> None:
        config = iv_sweep_config_from_params(
            {
                "resource": "GPIB0::18::INSTR",
                "sweep_mode": "I",
                "output_terminal": "front",
                "start": "0",
                "stop": "1e-6",
                "step": "5e-7",
                "bidirectional": "yes",
                "voltage_limit_v": "5",
                "current_limit_a": "0.01",
                "output_statistics": "true",
                "resistance_method": "linear_fit",
                "device_length_um": "10",
                "device_width_um": "20",
                "film_thickness_nm": "50",
            }
        )

        self.assertEqual(config.sweep_mode, "current")
        self.assertEqual(config.output_terminal, "front")
        self.assertTrue(config.bidirectional)
        self.assertAlmostEqual(config.voltage_limit_v, 5)
        self.assertAlmostEqual(config.current_limit_a, 0.01)
        self.assertTrue(config.output_statistics)
        self.assertEqual(config.resistance_method, "linear_fit")
        self.assertAlmostEqual(config.device_length_um, 10)
        self.assertAlmostEqual(config.device_width_um, 20)
        self.assertAlmostEqual(config.film_thickness_nm, 50)

    def test_runner_configures_voltage_sweep_and_reports_samples(self) -> None:
        instrument = FakeInstrument()
        runner = Keithley2450IVRunner(instrument)
        samples = []

        result = runner.run_sweep(
            IVSweepConfig(start=0, stop=0.2, step=0.1, source_delay_s=0),
            on_sample=samples.append,
        )

        self.assertEqual(len(result), 3)
        self.assertEqual(len(samples), 3)
        self.assertIn(":SOUR:FUNC:MODE VOLT", instrument.commands)
        self.assertIn(":ROUT:TERM REAR", instrument.commands)
        self.assertIn(":SENS:CURR:PROT:LEV 0.001", instrument.commands)
        self.assertIn(":OUTP OFF", instrument.commands)
        self.assertAlmostEqual(result[-1].voltage_v, 0.3)
        self.assertAlmostEqual(result[-1].current_a, 3e-6)

    def test_runner_configures_constant_voltage_current_sampling(self) -> None:
        instrument = FakeInstrument()
        runner = Keithley2450IVRunner(instrument)

        result = runner.run_constant_voltage_current(
            ConstantVoltageCurrentConfig(voltage_v=0.1, current_limit_a=1e-5, sample_count=2),
        )

        self.assertEqual(len(result), 2)
        self.assertIn(":SOUR:VOLT:LEV 0.1", instrument.commands)
        self.assertIn(":SENS:CURR:PROT:LEV 1e-05", instrument.commands)
        self.assertIn(":OUTP ON", instrument.commands)
        self.assertAlmostEqual(result[0].voltage_v, 0.1)
        self.assertAlmostEqual(result[0].current_a, 1e-6)

    def test_constant_voltage_params_default_to_100mv_and_10ua(self) -> None:
        config = constant_voltage_current_config_from_params({})

        self.assertAlmostEqual(config.voltage_v, 0.1)
        self.assertAlmostEqual(config.current_limit_a, 1e-5)
        self.assertAlmostEqual(config.nplc, 10.0)

    def test_statistics_use_linear_iv_slope_and_geometry(self) -> None:
        samples = [
            IVSweepSample(1, 3, 0.0, -1.0, -1.0, -0.001, 1000.0, ""),
            IVSweepSample(2, 3, 0.1, 0.0, 0.0, 0.0, None, ""),
            IVSweepSample(3, 3, 0.2, 1.0, 1.0, 0.001, 1000.0, ""),
        ]
        config = IVSweepConfig(
            device_length_um=10.0,
            device_width_um=20.0,
            film_thickness_nm=50.0,
        )

        stats = calculate_iv_statistics(samples, config)

        self.assertAlmostEqual(stats.resistance_ohm or 0.0, 1000.0)
        self.assertAlmostEqual(stats.sheet_resistance_ohm_sq or 0.0, 2000.0)
        self.assertAlmostEqual(stats.resistivity_ohm_cm or 0.0, 1e-2)


if __name__ == "__main__":
    unittest.main()
