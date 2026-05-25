from __future__ import annotations

import contextlib
import csv
import io
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

DEFAULT_B1500_RESOURCE = "GPIB0::17::INSTR"
B1500_TEST_TRANSFER = "transfer"
B1500_TEST_OUTPUT = "output"
B1500_SAVE_MODES = ("long", "wide", "both", "per_curve")
B1500_SMUS = ("smu1", "smu2", "smu3", "smu4")
DEFAULT_B1500_DRAIN_SMU = "smu3"
DEFAULT_B1500_GATE_SMU = "smu4"
B1500_ADC_HIGH_SPEED = "high_speed"
B1500_ADC_HIGH_RESOLUTION = "high_resolution"
B1500_ADC_TYPES = (B1500_ADC_HIGH_RESOLUTION, B1500_ADC_HIGH_SPEED)


@dataclass(frozen=True)
class B1500SweepConfig:
    test_type: str
    resource_name: str = DEFAULT_B1500_RESOURCE
    device_name: str = "{point}"
    experiment: str = "AutoTest"
    sample: str = "device_under_test"
    save_mode: str = "both"
    drain_smu: str = DEFAULT_B1500_DRAIN_SMU
    gate_smu: str = DEFAULT_B1500_GATE_SMU
    measure_gate_leak: bool = True
    abort_on_compliance: bool = False
    sweep_start_v: float = -40.0
    sweep_end_v: float = 40.0
    sweep_points: int = 201
    bias_values_v: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)
    drain_current_compliance_a: float = 10e-6
    gate_current_compliance_a: float = 1e-9
    avg_coefficient: int = -10
    step_delay_s: float = 0.02
    pre_settle_s: float = 1.0
    post_sweep_pause_s: float = 1.0
    measurement_adc: str = B1500_ADC_HIGH_SPEED
    high_speed_nplc: float = 10.0
    high_resolution_nplc: float = 10.0
    autozero: bool = False

    def normalized(self) -> "B1500SweepConfig":
        test_type = normalize_b1500_test_type(self.test_type)
        measurement_adc = normalize_b1500_adc_type(self.measurement_adc)
        save_mode = str(self.save_mode or "both").strip().lower()
        if save_mode not in B1500_SAVE_MODES:
            raise ValueError("B1500 save mode must be long, wide, both, or per_curve.")
        resource_name = str(self.resource_name or DEFAULT_B1500_RESOURCE).strip()
        if not resource_name:
            raise ValueError("B1500 VISA resource cannot be empty.")
        drain_smu = normalize_b1500_smu(self.drain_smu, default=DEFAULT_B1500_DRAIN_SMU)
        gate_smu = normalize_b1500_smu(self.gate_smu, default=DEFAULT_B1500_GATE_SMU)
        if drain_smu == gate_smu:
            raise ValueError("B1500 Drain and Gate SMU must be different.")
        bias_values = tuple(float(value) for value in self.bias_values_v)
        numeric = (
            self.sweep_start_v,
            self.sweep_end_v,
            self.drain_current_compliance_a,
            self.gate_current_compliance_a,
            self.avg_coefficient,
            self.step_delay_s,
            self.pre_settle_s,
            self.post_sweep_pause_s,
            self.high_speed_nplc,
            self.high_resolution_nplc,
            *bias_values,
        )
        if any(not math.isfinite(float(value)) for value in numeric):
            raise ValueError("B1500 numeric parameters must be finite.")
        sweep_points = int(float(self.sweep_points))
        if sweep_points < 2 or sweep_points > 100000:
            raise ValueError("B1500 sweep points must be in range 2..100000.")
        if not bias_values:
            raise ValueError("B1500 bias list cannot be empty.")
        if float(self.drain_current_compliance_a) <= 0 or float(self.gate_current_compliance_a) <= 0:
            raise ValueError("B1500 current compliances must be positive.")
        avg_coefficient = int(float(self.avg_coefficient))
        if avg_coefficient == 0 or avg_coefficient < -100:
            raise ValueError("B1500 averaging coefficient must be positive or a negative PLC value in range -1..-100.")
        if float(self.step_delay_s) < 0 or float(self.pre_settle_s) < 0 or float(self.post_sweep_pause_s) < 0:
            raise ValueError("B1500 delays must be zero or positive.")
        if float(self.high_speed_nplc) <= 0 or float(self.high_resolution_nplc) <= 0:
            raise ValueError("B1500 NPLC values must be positive.")
        return B1500SweepConfig(
            test_type=test_type,
            resource_name=resource_name,
            device_name=str(self.device_name or "{point}").strip() or "{point}",
            experiment=str(self.experiment or "AutoTest").strip() or "AutoTest",
            sample=str(self.sample or "device_under_test").strip() or "device_under_test",
            save_mode=save_mode,
            drain_smu=drain_smu,
            gate_smu=gate_smu,
            measure_gate_leak=bool(self.measure_gate_leak),
            abort_on_compliance=bool(self.abort_on_compliance),
            sweep_start_v=float(self.sweep_start_v),
            sweep_end_v=float(self.sweep_end_v),
            sweep_points=sweep_points,
            bias_values_v=bias_values,
            drain_current_compliance_a=float(self.drain_current_compliance_a),
            gate_current_compliance_a=float(self.gate_current_compliance_a),
            avg_coefficient=avg_coefficient,
            step_delay_s=float(self.step_delay_s),
            pre_settle_s=float(self.pre_settle_s),
            post_sweep_pause_s=float(self.post_sweep_pause_s),
            measurement_adc=measurement_adc,
            high_speed_nplc=float(self.high_speed_nplc),
            high_resolution_nplc=float(self.high_resolution_nplc),
            autozero=bool(self.autozero),
        )

    def summary(self) -> str:
        config = self.normalized()
        mapping = f"D={config.drain_smu.upper()} G={config.gate_smu.upper()}"
        if config.test_type == B1500_TEST_TRANSFER:
            return f"{config.resource_name} | {mapping} | Transfer Vg {config.sweep_start_v:g}->{config.sweep_end_v:g} V, Vd={format_float_list(config.bias_values_v)} V"
        return f"{config.resource_name} | {mapping} | Output Vd {config.sweep_start_v:g}->{config.sweep_end_v:g} V, Vg={format_float_list(config.bias_values_v)} V"


@dataclass(frozen=True)
class B1500SweepResult:
    test_type: str
    curve_count: int
    sample_count: int
    output_paths: tuple[Path, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "test_type": self.test_type,
            "curve_count": self.curve_count,
            "sample_count": self.sample_count,
            "output_paths": [str(path) for path in self.output_paths],
        }


def normalize_b1500_test_type(value: object) -> str:
    text = str(value or B1500_TEST_TRANSFER).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "transfer": B1500_TEST_TRANSFER,
        "id_vg": B1500_TEST_TRANSFER,
        "vg": B1500_TEST_TRANSFER,
        "output": B1500_TEST_OUTPUT,
        "output_curves": B1500_TEST_OUTPUT,
        "id_vd": B1500_TEST_OUTPUT,
        "vd": B1500_TEST_OUTPUT,
    }
    normalized = aliases.get(text, text)
    if normalized not in {B1500_TEST_TRANSFER, B1500_TEST_OUTPUT}:
        raise ValueError("B1500 test type must be transfer or output.")
    return normalized


def normalize_b1500_smu(value: object, *, default: str = DEFAULT_B1500_DRAIN_SMU) -> str:
    text = str(value or default).strip().lower().replace(" ", "").replace("-", "")
    aliases = {name: name for name in B1500_SMUS}
    aliases.update({name.upper(): name for name in B1500_SMUS})
    aliases.update({name[-1]: name for name in B1500_SMUS})
    normalized = aliases.get(text, text)
    if normalized not in B1500_SMUS:
        raise ValueError("B1500 SMU must be one of SMU1, SMU2, SMU3, or SMU4.")
    return normalized


def normalize_b1500_adc_type(value: object) -> str:
    text = str(value or B1500_ADC_HIGH_RESOLUTION).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "hr": B1500_ADC_HIGH_RESOLUTION,
        "highres": B1500_ADC_HIGH_RESOLUTION,
        "high_resolution": B1500_ADC_HIGH_RESOLUTION,
        "high_resolution_adc": B1500_ADC_HIGH_RESOLUTION,
        "hs": B1500_ADC_HIGH_SPEED,
        "highspeed": B1500_ADC_HIGH_SPEED,
        "high_speed": B1500_ADC_HIGH_SPEED,
        "high_speed_adc": B1500_ADC_HIGH_SPEED,
    }
    normalized = aliases.get(text, text)
    if normalized not in B1500_ADC_TYPES:
        raise ValueError("B1500 measurement ADC must be high_resolution or high_speed.")
    return normalized


def parse_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Cannot parse boolean value: {value!r}.")


def parse_float_list(value: object) -> tuple[float, ...]:
    text = str(value or "").strip()
    if not text:
        return ()
    if ":" in text and "," not in text:
        parts = [float(part.strip()) for part in text.split(":")]
        if len(parts) != 3:
            raise ValueError("Range list must use start:stop:step.")
        start, stop, step = parts
        if step == 0:
            raise ValueError("Range list step must be non-zero.")
        values: list[float] = []
        current = start
        if step > 0:
            while current <= stop + abs(step) * 1e-9:
                values.append(float(current))
                current += step
        else:
            while current >= stop - abs(step) * 1e-9:
                values.append(float(current))
                current += step
        return tuple(values)
    return tuple(float(part.strip()) for part in text.replace(";", ",").split(",") if part.strip())


def format_float_list(values: Sequence[float]) -> str:
    if len(values) > 4:
        return f"{values[0]:g},...,{values[-1]:g} ({len(values)})"
    return ",".join(f"{value:g}" for value in values)


def b1500_sweep_abort_setting(constants: Any, config: B1500SweepConfig) -> Any:
    return constants.Abort.ENABLED if config.abort_on_compliance else constants.Abort.DISABLED


def b1500_avg_coefficient_from_params(params: dict[str, str]) -> int:
    nplc = int(float(params.get("staircase_nplc", "10")))
    if nplc < 1 or nplc > 100:
        raise ValueError("B1500 staircase NPLC must be in range 1..100.")
    return -nplc


def b1500_config_from_params(params: dict[str, str], *, test_type: str) -> B1500SweepConfig:
    return B1500SweepConfig(
        test_type=test_type,
        resource_name=params.get("resource", params.get("resource_name", DEFAULT_B1500_RESOURCE)),
        device_name=params.get("device_name", "{point}"),
        experiment=params.get("experiment", "AutoTest"),
        sample=params.get("sample", "device_under_test"),
        save_mode=params.get("save_mode", "both"),
        drain_smu=params.get("drain_smu", DEFAULT_B1500_DRAIN_SMU),
        gate_smu=params.get("gate_smu", DEFAULT_B1500_GATE_SMU),
        measure_gate_leak=parse_bool(params.get("measure_gate_leak"), default=True),
        abort_on_compliance=parse_bool(params.get("abort_on_compliance"), default=False),
        sweep_start_v=float(params.get("sweep_start_v", params.get("start_v", "-40"))),
        sweep_end_v=float(params.get("sweep_end_v", params.get("end_v", "40"))),
        sweep_points=int(float(params.get("sweep_points", params.get("points", "201")))),
        bias_values_v=parse_float_list(params.get("bias_values_v", "0:5:1")),
        drain_current_compliance_a=float(params.get("drain_current_compliance_a", "1e-5")),
        gate_current_compliance_a=float(params.get("gate_current_compliance_a", "1e-9")),
        avg_coefficient=b1500_avg_coefficient_from_params(params),
        step_delay_s=float(params.get("step_delay_s", "0.02")),
        pre_settle_s=float(params.get("pre_settle_s", "1.0")),
        post_sweep_pause_s=float(params.get("post_sweep_pause_s", "1.0")),
        measurement_adc=params.get("measurement_adc", B1500_ADC_HIGH_SPEED),
        high_speed_nplc=float(params.get("high_speed_nplc", "10")),
        high_resolution_nplc=float(params.get("high_resolution_nplc", "10")),
        autozero=parse_bool(params.get("autozero"), default=False),
    ).normalized()


class KeysightB1500Runner:
    def __init__(self, instrument: Any | None = None) -> None:
        self.instrument = instrument
        self._owns_instrument = instrument is None

    def run(
        self,
        config: B1500SweepConfig,
        output_dir: Path,
        *,
        stop_requested: Callable[[], bool] | None = None,
        on_curve: Callable[[dict[str, object]], None] | None = None,
    ) -> B1500SweepResult:
        normalized = config.normalized()
        stop_requested = stop_requested or (lambda: False)
        output_dir.mkdir(parents=True, exist_ok=True)
        b1500 = self.instrument or self._open_resource(normalized.resource_name)
        self.instrument = b1500
        try:
            if normalized.test_type == B1500_TEST_TRANSFER:
                curves = self._measure_transfer(b1500, normalized, stop_requested=stop_requested, on_curve=on_curve)
            else:
                curves = self._measure_output(b1500, normalized, stop_requested=stop_requested, on_curve=on_curve)
            output_paths = self._save_curves(output_dir, normalized, curves)
            sample_count = sum(len(curve["x"]) for curve in curves)
            return B1500SweepResult(normalized.test_type, len(curves), sample_count, tuple(output_paths))
        finally:
            with contextlib.suppress(Exception):
                b1500.disable_channels()
            if self._owns_instrument:
                with contextlib.suppress(Exception):
                    b1500.close()
                self.instrument = None

    def _open_resource(self, resource_name: str):
        try:
            from qcodes.instrument import Instrument
            from qcodes.instrument_drivers.Keysight import KeysightB1500
        except ImportError as exc:
            raise RuntimeError("QCoDeS with the Keysight B1500 driver is required for B1500 testing. Install qcodes first.") from exc
        with contextlib.suppress(Exception):
            existing = Instrument.find_instrument("b1500")
            if existing is not None:
                existing.close()
        return KeysightB1500("b1500", address=resource_name)

    def _constants(self):
        try:
            from qcodes.instrument_drivers.Keysight.keysightb1500 import constants
        except ImportError as exc:
            raise RuntimeError("QCoDeS Keysight B1500 constants are unavailable.") from exc
        return constants

    def _measurement(self, b1500, config: B1500SweepConfig):
        try:
            from qcodes.dataset import Measurement, initialise_database, load_or_create_experiment
        except ImportError as exc:
            raise RuntimeError("QCoDeS dataset support is required for B1500 testing.") from exc
        initialise_database()
        exp = load_or_create_experiment(experiment_name=config.experiment, sample_name=config.sample)
        measurement = Measurement(exp=exp)
        measurement.register_parameter(b1500.run_iv_staircase_sweep)
        return measurement

    def _prepare_adc_and_channels(self, b1500, config: B1500SweepConfig) -> None:
        if config.measurement_adc == B1500_ADC_HIGH_RESOLUTION:
            b1500.use_nplc_for_high_resolution_adc(n=int(config.high_resolution_nplc))
            self._drain_smu(b1500, config).use_high_resolution_adc()
            self._gate_smu(b1500, config).use_high_resolution_adc()
        else:
            b1500.use_nplc_for_high_speed_adc(n=int(config.high_speed_nplc))
            self._drain_smu(b1500, config).use_high_speed_adc()
            self._gate_smu(b1500, config).use_high_speed_adc()
        b1500.autozero_enabled(config.autozero)
        with contextlib.suppress(Exception):
            b1500.disable_channels()

    def _preflight_for_sweep(self, b1500, config: B1500SweepConfig) -> None:
        with contextlib.suppress(Exception):
            b1500.abort()
        with contextlib.suppress(Exception):
            b1500.device_clear()
        with contextlib.suppress(Exception):
            b1500.clear_buffer_of_error_message()
        with contextlib.suppress(Exception):
            b1500.clear_timer_count()
        with contextlib.suppress(Exception):
            b1500.clear_buffer()
        with contextlib.suppress(Exception):
            b1500.disable_channels()
        with contextlib.suppress(Exception):
            self._drain_smu(b1500, config).enable_outputs()
            self._gate_smu(b1500, config).enable_outputs()

    def _run_staircase_with_retry(
        self,
        b1500,
        config: B1500SweepConfig,
        measurement_factory: Callable[[], Any],
        prepare_retry: Callable[[], None],
    ) -> tuple[tuple[Any, ...], np.ndarray]:
        try:
            return self._run_staircase_once(b1500, measurement_factory())
        except Exception as exc:
            if not is_b1500_sweep_aborted_error(exc):
                raise
            self._preflight_for_sweep(b1500, config)
            time.sleep(0.2)
            prepare_retry()
            return self._run_staircase_once(b1500, measurement_factory())

    def _run_staircase_once(self, b1500, measurement) -> tuple[tuple[Any, ...], np.ndarray]:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with measurement.run() as run:
                result = b1500.run_iv_staircase_sweep()
                setpoints = b1500.run_iv_staircase_sweep.setpoints[0]
                run.add_result((b1500.run_iv_staircase_sweep, result))
        return result, _to_1d_f64(setpoints)

    def _configure_drain_fixed(self, b1500, config: B1500SweepConfig, vd: float) -> None:
        constants = self._constants()
        smu = self._drain_smu(b1500, config)
        smu.source_config(
            output_range=constants.VOutputRange.AUTO,
            compliance=config.drain_current_compliance_a,
            compl_polarity=None,
            min_compliance_range=constants.IOutputRange.AUTO,
        )
        smu.i_measure_range_config(i_measure_range=constants.IMeasRange.AUTO)
        smu.enable_outputs()
        smu.voltage(float(vd))

    def _configure_gate_fixed(self, b1500, config: B1500SweepConfig, vg: float = 0.0) -> None:
        constants = self._constants()
        smu = self._gate_smu(b1500, config)
        smu.enable_outputs()
        smu.source_config(
            output_range=constants.VOutputRange.AUTO,
            compliance=config.gate_current_compliance_a,
            compl_polarity=None,
            min_compliance_range=constants.IOutputRange.AUTO,
        )
        smu.current_measurement_range(constants.IMeasRange.AUTO)
        smu.voltage(float(vg))

    def _configure_gate_sweep(self, b1500, config: B1500SweepConfig) -> None:
        constants = self._constants()
        smu = self._gate_smu(b1500, config)
        smu.source_config(
            output_range=constants.VOutputRange.AUTO,
            compliance=config.gate_current_compliance_a,
            compl_polarity=None,
            min_compliance_range=constants.IOutputRange.AUTO,
        )
        smu.current_measurement_range(constants.IMeasRange.AUTO)
        smu.enable_outputs()
        smu.voltage(0.0)
        smu.setup_staircase_sweep(
            v_src_range=constants.VOutputRange.AUTO,
            v_start=config.sweep_start_v,
            v_end=config.sweep_end_v,
            n_steps=config.sweep_points,
            av_coef=config.avg_coefficient,
            step_delay=config.step_delay_s,
            abort_enabled=b1500_sweep_abort_setting(constants, config),
            i_meas_range=constants.IMeasRange.AUTO,
            i_comp=config.gate_current_compliance_a,
            sweep_mode=constants.SweepMode.LINEAR,
        )

    def _configure_drain_sweep(self, b1500, config: B1500SweepConfig) -> None:
        constants = self._constants()
        smu = self._drain_smu(b1500, config)
        smu.enable_outputs()
        smu.voltage(0.0)
        smu.source_config(
            output_range=constants.VOutputRange.AUTO,
            compliance=config.drain_current_compliance_a,
            compl_polarity=None,
            min_compliance_range=constants.IOutputRange.AUTO,
        )
        smu.i_measure_range_config(i_measure_range=constants.IMeasRange.AUTO)
        smu.setup_staircase_sweep(
            v_src_range=constants.VOutputRange.AUTO,
            v_start=config.sweep_start_v,
            v_end=config.sweep_end_v,
            n_steps=config.sweep_points,
            av_coef=config.avg_coefficient,
            step_delay=config.step_delay_s,
            abort_enabled=b1500_sweep_abort_setting(constants, config),
            i_meas_range=constants.IMeasRange.AUTO,
            i_comp=config.drain_current_compliance_a,
            sweep_mode=constants.SweepMode.LINEAR,
        )

    def _set_measurement_mode_transfer(self, b1500, config: B1500SweepConfig) -> None:
        constants = self._constants()
        drain_smu = self._drain_smu(b1500, config)
        gate_smu = self._gate_smu(b1500, config)
        channels = (gate_smu.channels[0], drain_smu.channels[0]) if config.measure_gate_leak else (drain_smu.channels[0],)
        b1500.set_measurement_mode(mode=constants.MM.Mode.STAIRCASE_SWEEP, channels=channels)
        if config.measure_gate_leak:
            b1500.run_iv_staircase_sweep.set_names_labels_and_units(names=("ig", "id"), labels=("Gate current", "Drain current"), units=("A", "A"))
        else:
            b1500.run_iv_staircase_sweep.set_names_labels_and_units(names=("id",), labels=("Drain current",), units=("A",))

    def _set_measurement_mode_output(self, b1500, config: B1500SweepConfig) -> None:
        constants = self._constants()
        drain_smu = self._drain_smu(b1500, config)
        gate_smu = self._gate_smu(b1500, config)
        channels = (drain_smu.channels[0], gate_smu.channels[0]) if config.measure_gate_leak else (drain_smu.channels[0],)
        b1500.set_measurement_mode(mode=constants.MM.Mode.STAIRCASE_SWEEP, channels=channels)
        if config.measure_gate_leak:
            b1500.run_iv_staircase_sweep.set_names_labels_and_units(names=("id", "ig"), labels=("Drain current", "Gate current"), units=("A", "A"))
        else:
            b1500.run_iv_staircase_sweep.set_names_labels_and_units(names=("id",), labels=("Drain current",), units=("A",))

    def _drain_smu(self, b1500, config: B1500SweepConfig):
        return self._smu(b1500, config.drain_smu)

    def _gate_smu(self, b1500, config: B1500SweepConfig):
        return self._smu(b1500, config.gate_smu)

    def _smu(self, b1500, smu_name: str):
        normalized = normalize_b1500_smu(smu_name)
        try:
            return getattr(b1500, normalized)
        except AttributeError as exc:
            raise RuntimeError(f"Connected B1500 does not expose {normalized.upper()}.") from exc

    def _measure_transfer(self, b1500, config: B1500SweepConfig, *, stop_requested: Callable[[], bool], on_curve: Callable[[dict[str, object]], None] | None) -> list[dict[str, Any]]:
        self._prepare_adc_and_channels(b1500, config)
        curves: list[dict[str, Any]] = []
        for index, vd in enumerate(config.bias_values_v, start=1):
            if stop_requested():
                break

            def arm_transfer_sweep() -> None:
                self._configure_drain_fixed(b1500, config, vd)
                self._configure_gate_sweep(b1500, config)
                self._set_measurement_mode_transfer(b1500, config)

            self._preflight_for_sweep(b1500, config)
            arm_transfer_sweep()
            time.sleep(config.pre_settle_s)
            result, vg = self._run_staircase_with_retry(
                b1500,
                config,
                lambda: self._measurement(b1500, config),
                arm_transfer_sweep,
            )
            if config.measure_gate_leak:
                ig = _to_1d_f64(result[0])
                current = _to_1d_f64(result[1])
            else:
                ig = np.array([], dtype=np.float64)
                current = _to_1d_f64(result[0])
            _validate_curve_lengths("Transfer", current, vg, ig)
            curve = {"bias": float(vd), "x": vg, "id": current, "ig": ig}
            curves.append(curve)
            if on_curve is not None:
                on_curve(b1500_curve_payload(config.test_type, curve, index, len(config.bias_values_v)))
            time.sleep(config.post_sweep_pause_s)
        return curves

    def _measure_output(self, b1500, config: B1500SweepConfig, *, stop_requested: Callable[[], bool], on_curve: Callable[[dict[str, object]], None] | None) -> list[dict[str, Any]]:
        self._prepare_adc_and_channels(b1500, config)
        curves: list[dict[str, Any]] = []
        for index, vg_bias in enumerate(config.bias_values_v, start=1):
            if stop_requested():
                break

            def arm_output_sweep() -> None:
                self._configure_drain_sweep(b1500, config)
                self._configure_gate_fixed(b1500, config, float(vg_bias))
                self._set_measurement_mode_output(b1500, config)

            self._preflight_for_sweep(b1500, config)
            arm_output_sweep()
            time.sleep(config.pre_settle_s)
            result, vd = self._run_staircase_with_retry(
                b1500,
                config,
                lambda: self._measurement(b1500, config),
                arm_output_sweep,
            )
            if config.measure_gate_leak:
                current = _to_1d_f64(result[0])
                ig = _to_1d_f64(result[1])
            else:
                current = _to_1d_f64(result[0])
                ig = np.array([], dtype=np.float64)
            _validate_curve_lengths("Output", current, vd, ig)
            curve = {"bias": float(vg_bias), "x": vd, "id": current, "ig": ig}
            curves.append(curve)
            if on_curve is not None:
                on_curve(b1500_curve_payload(config.test_type, curve, index, len(config.bias_values_v)))
            time.sleep(config.post_sweep_pause_s)
        return curves

    def _save_curves(self, output_dir: Path, config: B1500SweepConfig, curves: list[dict[str, Any]]) -> list[Path]:
        if not curves:
            return []
        output_paths: list[Path] = []
        mode = config.save_mode
        x_name = "Vg_V" if config.test_type == B1500_TEST_TRANSFER else "Vd_V"
        bias_name = "Vd_V" if config.test_type == B1500_TEST_TRANSFER else "Vg_V"
        base = _safe_filename(f"{config.device_name}_{config.test_type}")
        if mode == "per_curve":
            for curve in curves:
                path = output_dir / f"{base}_{bias_name[:-2]}_{float(curve['bias']):+.3f}V.csv"
                rows = []
                for row_index, x in enumerate(curve["x"]):
                    row = {x_name: float(x), "Id_A": float(curve["id"][row_index]), bias_name: float(curve["bias"])}
                    if len(curve["ig"]):
                        row["Ig_A"] = float(curve["ig"][row_index])
                    rows.append(row)
                _write_dict_csv(path, rows)
                output_paths.append(path)
            return output_paths
        if mode in {"long", "both"}:
            rows = []
            for curve in curves:
                for idx, x in enumerate(curve["x"]):
                    row = {bias_name: float(curve["bias"]), x_name: float(x), "Id_A": float(curve["id"][idx])}
                    if len(curve["ig"]):
                        row["Ig_A"] = float(curve["ig"][idx])
                    rows.append(row)
            path = output_dir / f"{base}_ALL_long.csv"
            _write_dict_csv(path, rows)
            output_paths.append(path)
        if mode in {"wide", "both"}:
            x_ref = curves[0]["x"]
            for curve in curves[1:]:
                if len(curve["x"]) != len(x_ref) or np.any(np.abs(curve["x"] - x_ref) > 1e-12):
                    raise ValueError("B1500 sweep x grids are not identical; cannot write wide CSV.")
            path = output_dir / f"{base}_ALL_wide.csv"
            _write_wide_csv(path, x_name, bias_name, x_ref, curves, "id")
            output_paths.append(path)
            if len(curves[0]["ig"]):
                ig_path = output_dir / f"{base}_ALL_wide_Ig.csv"
                _write_wide_csv(ig_path, x_name, bias_name, x_ref, curves, "ig")
                output_paths.append(ig_path)
        return output_paths


def _to_1d_f64(value: object) -> np.ndarray:
    if value is None:
        return np.array([], dtype=np.float64)
    return np.asarray(value, dtype=np.float64).reshape(-1)


def is_b1500_sweep_aborted_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "+227" in text and "sweep measurement was aborted" in text


def b1500_curve_payload(test_type: str, curve: dict[str, Any], curve_index: int, curve_total: int) -> dict[str, object]:
    x_values = _to_1d_f64(curve.get("x"))
    id_values = _to_1d_f64(curve.get("id"))
    ig_values = _to_1d_f64(curve.get("ig"))
    return {
        "test_type": normalize_b1500_test_type(test_type),
        "curve_index": int(curve_index),
        "curve_total": int(curve_total),
        "bias_v": float(curve.get("bias", 0.0)),
        "points": len(x_values),
        "x": tuple(float(value) for value in x_values),
        "id": tuple(float(value) for value in id_values),
        "ig": tuple(float(value) for value in ig_values),
    }


def _validate_curve_lengths(label: str, current: np.ndarray, voltage: np.ndarray, gate: np.ndarray) -> None:
    if len(current) != len(voltage):
        raise ValueError(f"{label} current length {len(current)} != voltage length {len(voltage)}.")
    if len(gate) not in (0, len(voltage)):
        raise ValueError(f"{label} gate current length {len(gate)} != voltage length {len(voltage)}.")


def _write_dict_csv(path: Path, rows: list[dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_wide_csv(path: Path, x_name: str, bias_name: str, x_values: np.ndarray, curves: list[dict[str, Any]], key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [x_name] + [f"{bias_name}={float(curve['bias']):.12g}" for curve in curves]
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        for row_index, x in enumerate(x_values):
            writer.writerow([f"{float(x):.12e}", *[f"{float(curve[key][row_index]):.12e}" for curve in curves]])


def _safe_filename(value: str) -> str:
    import re

    return re.sub(r"[^0-9A-Za-z_.-]+", "_", value.strip() or "b1500")
