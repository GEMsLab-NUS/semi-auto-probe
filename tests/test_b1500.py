from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from semi_auto_probe.b1500 import B1500_TEST_OUTPUT, B1500_TEST_TRANSFER, B1500SweepConfig, KeysightB1500Runner, b1500_avg_coefficient_from_params, b1500_config_from_params, b1500_curve_payload, b1500_sweep_abort_setting, is_b1500_sweep_aborted_error, parse_float_list, relax_b1500_iv_sweep_voltage_validator


class B1500ConfigTests(unittest.TestCase):
    def test_float_list_accepts_range_and_csv_values(self) -> None:
        self.assertEqual(parse_float_list("0:1:2"), (0.0, 1.0, 2.0))
        self.assertEqual(parse_float_list("0:1：2"), (0.0, 1.0, 2.0))
        self.assertEqual(parse_float_list("-1, 0, 1"), (-1.0, 0.0, 1.0))
        self.assertEqual(parse_float_list("[-8,-4,4,8]"), (-8.0, -4.0, 4.0, 8.0))

    def test_transfer_params_default_to_gpib_17(self) -> None:
        config = b1500_config_from_params({}, test_type=B1500_TEST_TRANSFER)

        self.assertEqual(config.resource_name, "GPIB0::17::INSTR")
        self.assertEqual(config.test_type, B1500_TEST_TRANSFER)
        self.assertEqual(config.drain_smu, "smu3")
        self.assertEqual(config.gate_smu, "smu4")
        self.assertEqual(config.scan_type, "single")
        self.assertEqual(config.measurement_adc, "high_resolution")
        self.assertEqual(config.avg_coefficient, -8)
        self.assertEqual(config.step_delay_s, 0.0)
        self.assertFalse(config.abort_on_compliance)
        self.assertAlmostEqual(config.high_resolution_nplc, 8.0)
        self.assertEqual(config.bias_values_v, (0.0, 1.0, 2.0, 3.0, 4.0, 5.0))
        self.assertTrue(config.measure_gate_leak)

    def test_output_config_normalizes_save_mode_and_bias_list(self) -> None:
        config = b1500_config_from_params(
            {
                "resource": "GPIB0::17::INSTR",
                "sweep_start_v": "0",
                "sweep_end_v": "10",
                "sweep_points": "101",
                "bias_values_v": "-25:25:25",
                "drain_smu": "SMU4",
                "gate_smu": "SMU3",
                "measurement_adc": "high_speed",
                "save_mode": "wide",
                "measure_gate_leak": "false",
                "abort_on_compliance": "false",
            },
            test_type=B1500_TEST_OUTPUT,
        )

        self.assertEqual(config.test_type, B1500_TEST_OUTPUT)
        self.assertEqual(config.drain_smu, "smu4")
        self.assertEqual(config.gate_smu, "smu3")
        self.assertEqual(config.measurement_adc, "high_speed")
        self.assertEqual(config.bias_values_v, (-25.0, 0.0, 25.0))
        self.assertEqual(config.save_mode, "wide")
        self.assertFalse(config.measure_gate_leak)
        self.assertFalse(config.abort_on_compliance)

    def test_sweep_abort_setting_can_disable_compliance_abort(self) -> None:
        class Abort:
            DISABLED = 1
            ENABLED = 2

        class Constants:
            pass

        Constants.Abort = Abort

        enabled = B1500SweepConfig(test_type=B1500_TEST_OUTPUT, abort_on_compliance=True).normalized()
        disabled = B1500SweepConfig(test_type=B1500_TEST_OUTPUT, abort_on_compliance=False).normalized()

        self.assertEqual(b1500_sweep_abort_setting(Constants, enabled), Abort.ENABLED)
        self.assertEqual(b1500_sweep_abort_setting(Constants, disabled), Abort.DISABLED)

    def test_config_rejects_same_drain_and_gate_smu(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be different"):
            B1500SweepConfig(test_type=B1500_TEST_OUTPUT, drain_smu="smu3", gate_smu="3").normalized()

    def test_config_accepts_negative_avg_coefficient_as_plc_mode(self) -> None:
        config = B1500SweepConfig(test_type=B1500_TEST_OUTPUT, avg_coefficient=-50).normalized()

        self.assertEqual(config.avg_coefficient, -50)

    def test_staircase_nplc_param_maps_to_negative_avg_coefficient(self) -> None:
        config = b1500_config_from_params({"staircase_nplc": "20"}, test_type=B1500_TEST_OUTPUT)

        self.assertEqual(config.avg_coefficient, -20)
        self.assertEqual(config.measurement_adc, "high_resolution")

    def test_transfer_config_accepts_double_scan_type_and_custom_vd_list(self) -> None:
        config = b1500_config_from_params(
            {
                "scan_type": "double",
                "bias_values_v": "[-8,-4,4,8]",
            },
            test_type=B1500_TEST_TRANSFER,
        )

        self.assertEqual(config.scan_type, "double")
        self.assertEqual(config.bias_values_v, (-8.0, -4.0, 4.0, 8.0))

    def test_staircase_nplc_rejects_out_of_range_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "1..100"):
            b1500_avg_coefficient_from_params({"staircase_nplc": "0"})

    def test_sweep_voltage_accepts_values_up_to_100v(self) -> None:
        config = B1500SweepConfig(test_type=B1500_TEST_TRANSFER, sweep_start_v=-80.0, sweep_end_v=80.0).normalized()

        self.assertEqual(config.sweep_start_v, -80.0)
        self.assertEqual(config.sweep_end_v, 80.0)

    def test_sweep_voltage_rejects_values_above_100v(self) -> None:
        with self.assertRaisesRegex(ValueError, "-100..100"):
            B1500SweepConfig(test_type=B1500_TEST_TRANSFER, sweep_start_v=-101.0).normalized()

    def test_relaxes_qcodes_iv_sweep_start_end_validator_to_100v(self) -> None:
        from qcodes.parameters import Parameter
        import qcodes.validators as vals

        class IVSweep:
            sweep_start = Parameter("sweep_start", vals=vals.Numbers(-25, 25))
            sweep_end = Parameter("sweep_end", vals=vals.Numbers(-25, 25))

        class SMU:
            iv_sweep = IVSweep()

        smu = SMU()
        relax_b1500_iv_sweep_voltage_validator(smu)

        smu.iv_sweep.sweep_start.validate(-100.0)
        smu.iv_sweep.sweep_end.validate(100.0)
        with self.assertRaises(ValueError):
            smu.iv_sweep.sweep_start.validate(-100.1)

    def test_save_curves_writes_long_wide_and_gate_leak_csvs(self) -> None:
        runner = KeysightB1500Runner(instrument=object())
        config = B1500SweepConfig(
            test_type=B1500_TEST_TRANSFER,
            device_name="DevA",
            save_mode="both",
            bias_values_v=(0.0, 1.0),
        ).normalized()
        curves = [
            {"bias": 0.0, "x": np.array([0.0, 1.0]), "id": np.array([1e-9, 2e-9]), "ig": np.array([1e-12, 2e-12])},
            {"bias": 1.0, "x": np.array([0.0, 1.0]), "id": np.array([3e-9, 4e-9]), "ig": np.array([3e-12, 4e-12])},
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = runner._save_curves(Path(temp_dir), config, curves)
            names = sorted(path.name for path in paths)
            with (Path(temp_dir) / "DevA_transfer_ALL_long.csv").open(newline="", encoding="utf-8") as file:
                rows = list(csv.reader(file))

        self.assertEqual(names, ["DevA_transfer_ALL_long.csv", "DevA_transfer_ALL_wide.csv", "DevA_transfer_ALL_wide_Ig.csv"])
        self.assertEqual(rows[0], ["Vd_V", "Vg_V", "Id_A", "Ig_A"])
        self.assertEqual(len(rows), 5)

    def test_curve_callback_payload_contains_plot_series(self) -> None:
        config = B1500SweepConfig(test_type=B1500_TEST_OUTPUT, bias_values_v=(1.0,)).normalized()
        curve = {"bias": 1.0, "x": np.array([0.0, 1.0]), "id": np.array([1e-9, 2e-9]), "ig": np.array([1e-12, 2e-12])}

        payload = b1500_curve_payload(config.test_type, curve, 1, 1)

        self.assertEqual(payload["x"], (0.0, 1.0))
        self.assertEqual(payload["id"], (1e-9, 2e-9))
        self.assertEqual(payload["ig"], (1e-12, 2e-12))

    def test_transfer_double_scan_runs_forward_and_reverse_vg(self) -> None:
        class DoubleScanRunner(KeysightB1500Runner):
            def __init__(self) -> None:
                super().__init__(instrument=object())
                self.last_sweep = (0.0, 0.0)

            def _prepare_adc_and_channels(self, b1500, config):
                return None

            def _preflight_for_sweep(self, b1500, config):
                return None

            def _configure_drain_fixed(self, b1500, config, vd):
                return None

            def _configure_gate_sweep(self, b1500, config, *, sweep_start_v=None, sweep_end_v=None):
                self.last_sweep = (float(sweep_start_v), float(sweep_end_v))

            def _set_measurement_mode_transfer(self, b1500, config):
                return None

            def _measurement(self, b1500, config):
                return object()

            def _run_staircase_with_retry(self, b1500, config, measurement_factory, prepare_retry):
                start, end = self.last_sweep
                vg = np.array([start, end])
                return (np.array([1e-12, 2e-12]), np.array([start * 1e-9, end * 1e-9])), vg

        config = B1500SweepConfig(test_type=B1500_TEST_TRANSFER, scan_type="double", sweep_start_v=-1, sweep_end_v=1, bias_values_v=(0.0,)).normalized()
        curves = DoubleScanRunner()._measure_transfer(object(), config, stop_requested=lambda: False, on_curve=None)

        self.assertEqual(tuple(curves[0]["x"]), (-1.0, 1.0, 1.0, -1.0))

    def test_aborted_error_detection_handles_qcodes_wrapped_non_runtime_error(self) -> None:
        exc = ValueError('(While setting this parameter received error: +227,"Sweep measurement was aborted.", getting b1500_run_iv_staircase_sweep)')

        self.assertTrue(is_b1500_sweep_aborted_error(exc))

    def test_staircase_retry_handles_qcodes_wrapped_abort_error(self) -> None:
        class RetryRunner(KeysightB1500Runner):
            def __init__(self) -> None:
                super().__init__(instrument=object())
                self.calls = 0
                self.preflight_count = 0

            def _run_staircase_once(self, b1500, measurement):
                self.calls += 1
                if self.calls == 1:
                    raise ValueError('(While setting this parameter received error: +227,"Sweep measurement was aborted.", getting b1500_run_iv_staircase_sweep)')
                return (np.array([1e-9]),), np.array([0.0])

            def _preflight_for_sweep(self, b1500, config):
                self.preflight_count += 1

        runner = RetryRunner()
        prepare_count = 0

        def prepare_retry() -> None:
            nonlocal prepare_count
            prepare_count += 1

        result, setpoints = runner._run_staircase_with_retry(
            object(),
            B1500SweepConfig(test_type=B1500_TEST_OUTPUT).normalized(),
            lambda: object(),
            prepare_retry,
        )

        self.assertEqual(runner.calls, 2)
        self.assertEqual(runner.preflight_count, 1)
        self.assertEqual(prepare_count, 1)
        self.assertEqual(float(setpoints[0]), 0.0)
        self.assertEqual(float(result[0][0]), 1e-9)


if __name__ == "__main__":
    unittest.main()
