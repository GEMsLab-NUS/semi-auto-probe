from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol


DEFAULT_KEITHLEY2450_RESOURCE = "GPIB0::18::INSTR"
IV_SWEEP_MODE_VOLTAGE = "voltage"
IV_SWEEP_MODE_CURRENT = "current"
IV_SWEEP_MODES = (IV_SWEEP_MODE_VOLTAGE, IV_SWEEP_MODE_CURRENT)
IV_SCAN_TYPE_SINGLE = "single"
IV_SCAN_TYPE_DOUBLE = "double"
IV_SCAN_TYPE_DUALPOLAR = "dualpolar"
IV_SCAN_TYPES = (IV_SCAN_TYPE_SINGLE, IV_SCAN_TYPE_DOUBLE, IV_SCAN_TYPE_DUALPOLAR)
OUTPUT_TERMINAL_FRONT = "front"
OUTPUT_TERMINAL_REAR = "rear"
OUTPUT_TERMINALS = (OUTPUT_TERMINAL_FRONT, OUTPUT_TERMINAL_REAR)
RESISTANCE_METHOD_LINEAR_FIT = "linear_fit"
RESISTANCE_METHOD_MEDIAN_RATIO = "median_ratio"
RESISTANCE_METHODS = (RESISTANCE_METHOD_LINEAR_FIT, RESISTANCE_METHOD_MEDIAN_RATIO)


class VisaInstrument(Protocol):
    timeout: int

    def write(self, command: str) -> object: ...

    def query(self, command: str) -> str: ...

    def close(self) -> object: ...


@dataclass(frozen=True)
class IVSweepSample:
    index: int
    total: int
    elapsed_s: float
    source_value: float
    voltage_v: float
    current_a: float
    resistance_ohm: float | None
    raw: str

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "total": self.total,
            "elapsed_s": self.elapsed_s,
            "source_value": self.source_value,
            "voltage_v": self.voltage_v,
            "current_a": self.current_a,
            "resistance_ohm": self.resistance_ohm,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class IVSweepStatistics:
    sample_count: int
    resistance_ohm: float | None
    resistance_method: str

    def to_dict(self) -> dict[str, object]:
        return {
            "sample_count": self.sample_count,
            "resistance_ohm": self.resistance_ohm,
            "resistance_method": self.resistance_method,
        }


@dataclass(frozen=True)
class ConstantVoltageCurrentConfig:
    resource_name: str = DEFAULT_KEITHLEY2450_RESOURCE
    output_terminal: str = OUTPUT_TERMINAL_REAR
    voltage_v: float = 0.1
    current_limit_a: float = 1e-5
    measure_range: float | None = None
    sample_count: int = 5
    nplc: float = 10.0
    output_off_after: bool = True

    def normalized(self) -> "ConstantVoltageCurrentConfig":
        output_terminal = normalize_output_terminal(self.output_terminal)
        values = (self.voltage_v, self.current_limit_a, 1.0 if self.measure_range is None else self.measure_range, self.nplc)
        if any(not math.isfinite(float(value)) for value in values):
            raise ValueError("Constant voltage current parameters must be finite.")
        if float(self.current_limit_a) <= 0:
            raise ValueError("Constant voltage current limit must be positive.")
        if self.measure_range is not None and float(self.measure_range) <= 0:
            raise ValueError("Constant voltage current measurement range must be positive or auto.")
        if float(self.nplc) <= 0 or float(self.nplc) > 25:
            raise ValueError("Constant voltage NPLC must be in range 0..25.")
        sample_count = int(float(self.sample_count))
        if sample_count <= 0 or sample_count > 10000:
            raise ValueError("Constant voltage sample count must be in range 1..10000.")
        resource_name = str(self.resource_name or DEFAULT_KEITHLEY2450_RESOURCE).strip()
        if not resource_name:
            raise ValueError("Keithley VISA resource cannot be empty.")
        return ConstantVoltageCurrentConfig(
            resource_name=resource_name,
            output_terminal=output_terminal,
            voltage_v=float(self.voltage_v),
            current_limit_a=float(self.current_limit_a),
            measure_range=None if self.measure_range is None else float(self.measure_range),
            sample_count=sample_count,
            nplc=float(self.nplc),
            output_off_after=bool(self.output_off_after),
        )

    def summary(self) -> str:
        return (
            f"{self.resource_name} | {self.output_terminal} terminal | "
            f"V={self.voltage_v:g} V | Ilimit={self.current_limit_a:g} A | {self.sample_count} samples"
        )


@dataclass(frozen=True)
class IVSweepConfig:
    resource_name: str = DEFAULT_KEITHLEY2450_RESOURCE
    output_terminal: str = OUTPUT_TERMINAL_REAR
    sweep_mode: str = IV_SWEEP_MODE_VOLTAGE
    start: float = 0.0
    stop: float = 1.0
    step: float = 0.05
    bidirectional: bool = False
    scan_type: str = IV_SCAN_TYPE_SINGLE
    voltage_limit_v: float = 20.0
    current_limit_a: float = 0.001
    measure_range: float | None = None
    source_delay_s: float = 0.02
    nplc: float = 1.0
    output_statistics: bool = True
    resistance_method: str = RESISTANCE_METHOD_LINEAR_FIT
    output_off_after: bool = True

    def normalized(self) -> "IVSweepConfig":
        mode = normalize_sweep_mode(self.sweep_mode)
        raw_scan_type = "" if self.bidirectional and self.scan_type == IV_SCAN_TYPE_SINGLE else self.scan_type
        scan_type = normalize_scan_type(raw_scan_type, bidirectional=self.bidirectional)
        output_terminal = normalize_output_terminal(self.output_terminal)
        resistance_method = normalize_resistance_method(self.resistance_method)
        values = (
            self.start,
            self.stop,
            self.step,
            self.voltage_limit_v,
            self.current_limit_a,
            1.0 if self.measure_range is None else self.measure_range,
            self.source_delay_s,
            self.nplc,
        )
        if any(not math.isfinite(float(value)) for value in values):
            raise ValueError("IV sweep numeric parameters must be finite.")
        if float(self.step) == 0:
            raise ValueError("IV sweep step must be non-zero.")
        if abs(float(self.step)) > abs(float(self.stop) - float(self.start)) and float(self.start) != float(self.stop):
            raise ValueError("IV sweep step is larger than the sweep span.")
        if float(self.voltage_limit_v) <= 0:
            raise ValueError("IV voltage limit must be positive.")
        if float(self.current_limit_a) <= 0:
            raise ValueError("IV current limit must be positive.")
        if self.measure_range is not None and float(self.measure_range) <= 0:
            raise ValueError("IV measurement range must be positive or auto.")
        if float(self.source_delay_s) < 0 or float(self.source_delay_s) > 60:
            raise ValueError("IV source delay must be in range 0..60 seconds.")
        if float(self.nplc) <= 0 or float(self.nplc) > 25:
            raise ValueError("IV NPLC must be in range 0..25.")
        resource_name = str(self.resource_name or DEFAULT_KEITHLEY2450_RESOURCE).strip()
        if not resource_name:
            raise ValueError("Keithley VISA resource cannot be empty.")
        return IVSweepConfig(
            resource_name=resource_name,
            output_terminal=output_terminal,
            sweep_mode=mode,
            start=float(self.start),
            stop=float(self.stop),
            step=float(self.step),
            bidirectional=scan_type == IV_SCAN_TYPE_DOUBLE,
            scan_type=scan_type,
            voltage_limit_v=float(self.voltage_limit_v),
            current_limit_a=float(self.current_limit_a),
            measure_range=None if self.measure_range is None else float(self.measure_range),
            source_delay_s=float(self.source_delay_s),
            nplc=float(self.nplc),
            output_statistics=bool(self.output_statistics),
            resistance_method=resistance_method,
            output_off_after=bool(self.output_off_after),
        )

    def sweep_values(self) -> tuple[float, ...]:
        config = self.normalized()
        forward = tuple(_inclusive_sweep_values(config.start, config.stop, config.step))
        if config.scan_type == IV_SCAN_TYPE_SINGLE or len(forward) <= 1:
            return forward
        if config.scan_type == IV_SCAN_TYPE_DOUBLE:
            reverse_step = -abs(config.step) if config.stop >= config.start else abs(config.step)
            reverse = tuple(_inclusive_sweep_values(config.stop + reverse_step, config.start, reverse_step))
            return forward + reverse
        values = list(forward)
        values.extend(_inclusive_sweep_values_excluding_start(config.stop, -config.stop, abs(config.step)))
        values.extend(_inclusive_sweep_values_excluding_start(-config.stop, config.start, abs(config.step)))
        return tuple(values)

    def summary(self) -> str:
        unit = "V" if normalize_sweep_mode(self.sweep_mode) == IV_SWEEP_MODE_VOLTAGE else "A"
        raw_scan_type = "" if self.bidirectional and self.scan_type == IV_SCAN_TYPE_SINGLE else self.scan_type
        scan_type = normalize_scan_type(raw_scan_type, bidirectional=self.bidirectional)
        range_text = "auto range" if self.measure_range is None else f"range {self.measure_range:g}"
        return f"{self.resource_name} | {self.output_terminal} terminal | {self.sweep_mode} {scan_type} {self.start:g}->{self.stop:g} step {self.step:g} {unit} | {range_text}"


def normalize_sweep_mode(value: object) -> str:
    text = str(value or IV_SWEEP_MODE_VOLTAGE).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "v": IV_SWEEP_MODE_VOLTAGE,
        "volt": IV_SWEEP_MODE_VOLTAGE,
        "voltage": IV_SWEEP_MODE_VOLTAGE,
        "v_sweep": IV_SWEEP_MODE_VOLTAGE,
        "voltage_sweep": IV_SWEEP_MODE_VOLTAGE,
        "i": IV_SWEEP_MODE_CURRENT,
        "curr": IV_SWEEP_MODE_CURRENT,
        "current": IV_SWEEP_MODE_CURRENT,
        "i_sweep": IV_SWEEP_MODE_CURRENT,
        "current_sweep": IV_SWEEP_MODE_CURRENT,
    }
    normalized = aliases.get(text, text)
    if normalized not in IV_SWEEP_MODES:
        raise ValueError("IV sweep mode must be voltage or current.")
    return normalized


def normalize_scan_type(value: object, *, bidirectional: bool = False) -> str:
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "": IV_SCAN_TYPE_DOUBLE if bidirectional else IV_SCAN_TYPE_SINGLE,
        "single": IV_SCAN_TYPE_SINGLE,
        "forward": IV_SCAN_TYPE_SINGLE,
        "0_to_vtop": IV_SCAN_TYPE_SINGLE,
        "double": IV_SCAN_TYPE_DOUBLE,
        "bidirectional": IV_SCAN_TYPE_DOUBLE,
        "both": IV_SCAN_TYPE_DOUBLE,
        "return": IV_SCAN_TYPE_DOUBLE,
        "dual": IV_SCAN_TYPE_DUALPOLAR,
        "dualpolar": IV_SCAN_TYPE_DUALPOLAR,
        "dual_polar": IV_SCAN_TYPE_DUALPOLAR,
        "bipolar": IV_SCAN_TYPE_DUALPOLAR,
    }
    normalized = aliases.get(text, text)
    if normalized not in IV_SCAN_TYPES:
        raise ValueError("IV scan type must be single, double, or dualpolar.")
    return normalized


def normalize_output_terminal(value: object) -> str:
    text = str(value or OUTPUT_TERMINAL_REAR).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "front": OUTPUT_TERMINAL_FRONT,
        "fron": OUTPUT_TERMINAL_FRONT,
        "front_panel": OUTPUT_TERMINAL_FRONT,
        "frontpanel": OUTPUT_TERMINAL_FRONT,
        "rear": OUTPUT_TERMINAL_REAR,
        "rear_panel": OUTPUT_TERMINAL_REAR,
        "rearpanel": OUTPUT_TERMINAL_REAR,
    }
    normalized = aliases.get(text, text)
    if normalized not in OUTPUT_TERMINALS:
        raise ValueError("Keithley output terminal must be front or rear.")
    return normalized


def normalize_resistance_method(value: object) -> str:
    text = str(value or RESISTANCE_METHOD_LINEAR_FIT).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "linear": RESISTANCE_METHOD_LINEAR_FIT,
        "fit": RESISTANCE_METHOD_LINEAR_FIT,
        "linear_fit": RESISTANCE_METHOD_LINEAR_FIT,
        "slope": RESISTANCE_METHOD_LINEAR_FIT,
        "median": RESISTANCE_METHOD_MEDIAN_RATIO,
        "median_ratio": RESISTANCE_METHOD_MEDIAN_RATIO,
        "v_over_i": RESISTANCE_METHOD_MEDIAN_RATIO,
    }
    normalized = aliases.get(text, text)
    if normalized not in RESISTANCE_METHODS:
        raise ValueError("IV resistance method must be linear_fit or median_ratio.")
    return normalized


def parse_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on", "bidirectional", "both", "double"}:
        return True
    if text in {"0", "false", "no", "n", "off", "single", "forward"}:
        return False
    raise ValueError(f"Cannot parse boolean value: {value!r}.")


def parse_optional_range(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in {"auto", "automatic", "autorange"}:
        return None
    return float(text)


def iv_sweep_config_from_params(params: dict[str, str]) -> IVSweepConfig:
    return iv_sweep_configs_from_params(params)[0]


def iv_sweep_configs_from_params(params: dict[str, str]) -> tuple[IVSweepConfig, ...]:
    mode = normalize_sweep_mode(params.get("sweep_mode", params.get("mode", "voltage")))
    stop_key = "stop_v" if mode == IV_SWEEP_MODE_VOLTAGE else "stop_a"
    raw_stop = params.get("vtop", params.get("stop", params.get(stop_key, 1.0)))
    stop_values = parse_vtop_values(raw_stop)
    legacy_bidirectional = parse_bool(params.get("bidirectional"), default=False)
    scan_type = normalize_scan_type(params.get("scan_type", params.get("direction", "")), bidirectional=legacy_bidirectional)
    configs = []
    for stop in stop_values:
        configs.append(
            IVSweepConfig(
                resource_name=params.get("resource", params.get("resource_name", DEFAULT_KEITHLEY2450_RESOURCE)),
                output_terminal=params.get("output_terminal", params.get("terminal", OUTPUT_TERMINAL_REAR)),
                sweep_mode=mode,
                start=float(params.get("start", params.get("start_v" if mode == IV_SWEEP_MODE_VOLTAGE else "start_a", 0.0))),
                stop=float(stop),
                step=float(params.get("step", params.get("step_v" if mode == IV_SWEEP_MODE_VOLTAGE else "step_a", 0.05))),
                bidirectional=scan_type == IV_SCAN_TYPE_DOUBLE,
                scan_type=scan_type,
                voltage_limit_v=float(params.get("voltage_limit_v", params.get("limit_v", 20.0))),
                current_limit_a=float(params.get("current_limit_a", params.get("limit_a", params.get("compliance_a", 0.001)))),
                measure_range=parse_optional_range(params.get("measure_range", params.get("measurement_range", params.get("range", "auto")))),
                source_delay_s=float(params.get("source_delay_s", params.get("delay_s", 0.02))),
                nplc=float(params.get("nplc", 1.0)),
                output_statistics=parse_bool(params.get("output_statistics", "true"), default=True),
                resistance_method=params.get("resistance_method", RESISTANCE_METHOD_LINEAR_FIT),
                output_off_after=parse_bool(params.get("output_off_after", "true"), default=True),
            ).normalized()
        )
    return tuple(configs)


def parse_vtop_values(value: object) -> tuple[float, ...]:
    text = str(value).strip().replace(chr(0xFF1A), ":")
    if not text:
        raise ValueError("IV Vtop cannot be empty.")
    if "," in text:
        values = tuple(float(part.strip()) for part in text.split(",") if part.strip())
        if not values:
            raise ValueError("IV Vtop list cannot be empty.")
        return values
    if ":" not in text:
        return (float(text),)
    parts = [float(part.strip()) for part in text.split(":") if part.strip()]
    if len(parts) != 3:
        raise ValueError("IV Vtop range must use start:step:end.")
    start, step, end = parts
    if step == 0:
        raise ValueError("IV Vtop range step must be non-zero.")
    direction = 1 if end >= start else -1
    step = abs(step) * direction
    values: list[float] = []
    current = start
    tolerance = abs(step) * 1e-9
    while (current - end) * direction <= tolerance:
        values.append(float(current))
        current += step
    if not values or abs(values[-1] - end) > abs(step) * 1e-6:
        values.append(float(end))
    return tuple(values)


def constant_voltage_current_config_from_params(params: dict[str, str]) -> ConstantVoltageCurrentConfig:
    return ConstantVoltageCurrentConfig(
        resource_name=params.get("resource", params.get("resource_name", DEFAULT_KEITHLEY2450_RESOURCE)),
        output_terminal=params.get("output_terminal", params.get("terminal", OUTPUT_TERMINAL_REAR)),
        voltage_v=float(params.get("bias_v", params.get("voltage_v", params.get("voltage", 0.1)))),
        current_limit_a=float(params.get("current_limit_a", params.get("limit_a", params.get("compliance_a", 1e-5)))),
        measure_range=parse_optional_range(params.get("measure_range", params.get("measurement_range", params.get("range", "auto")))),
        sample_count=int(float(params.get("sample_count", params.get("samples", 5)))),
        nplc=float(params.get("nplc", 10.0)),
        output_off_after=parse_bool(params.get("output_off_after", "true"), default=True),
    ).normalized()


def calculate_iv_statistics(samples: list[IVSweepSample] | tuple[IVSweepSample, ...], config: IVSweepConfig) -> IVSweepStatistics:
    normalized = config.normalized()
    if not normalized.output_statistics:
        return IVSweepStatistics(
            sample_count=len(samples),
            resistance_ohm=None,
            resistance_method=normalized.resistance_method,
        )
    resistance = _calculate_resistance_ohm(samples, normalized.resistance_method)
    return IVSweepStatistics(
        sample_count=len(samples),
        resistance_ohm=resistance,
        resistance_method=normalized.resistance_method,
    )


def _calculate_resistance_ohm(samples: list[IVSweepSample] | tuple[IVSweepSample, ...], method: str) -> float | None:
    if method == RESISTANCE_METHOD_MEDIAN_RATIO:
        return _median_resistance(samples)
    pairs = [
        (float(sample.current_a), float(sample.voltage_v))
        for sample in samples
        if math.isfinite(float(sample.current_a)) and math.isfinite(float(sample.voltage_v))
    ]
    if len(pairs) >= 2:
        mean_i = sum(current for current, _voltage in pairs) / len(pairs)
        mean_v = sum(voltage for _current, voltage in pairs) / len(pairs)
        denominator = sum((current - mean_i) ** 2 for current, _voltage in pairs)
        if denominator > 1e-30:
            numerator = sum((current - mean_i) * (voltage - mean_v) for current, voltage in pairs)
            slope = numerator / denominator
            if math.isfinite(slope):
                return slope
    return _median_resistance(samples)


def _median_resistance(samples: list[IVSweepSample] | tuple[IVSweepSample, ...]) -> float | None:
    values: list[float] = []
    for sample in samples:
        if sample.resistance_ohm is not None and math.isfinite(float(sample.resistance_ohm)):
            values.append(float(sample.resistance_ohm))
        elif abs(float(sample.current_a)) > 1e-15 and math.isfinite(float(sample.voltage_v)) and math.isfinite(float(sample.current_a)):
            values.append(float(sample.voltage_v) / float(sample.current_a))
    if not values:
        return None
    values.sort()
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2.0


def _inclusive_sweep_values(start: float, stop: float, step: float) -> Iterable[float]:
    if start == stop:
        yield float(start)
        return
    direction = 1.0 if stop > start else -1.0
    step_value = abs(float(step)) * direction
    value = float(start)
    tolerance = abs(step_value) * 1e-9
    index = 0
    while (value - stop) * direction <= tolerance:
        yield float(value)
        index += 1
        value = float(start) + index * step_value
    if abs((value - step_value) - stop) > abs(step_value) * 1e-6:
        yield float(stop)


def _inclusive_sweep_values_excluding_start(start: float, stop: float, step: float) -> Iterable[float]:
    values = tuple(_inclusive_sweep_values(start, stop, step))
    yield from values[1:]


class Keithley2450IVRunner:
    def __init__(self, instrument: VisaInstrument | None = None) -> None:
        self.instrument = instrument
        self._owns_instrument = instrument is None
        self._sweep_config: IVSweepConfig | None = None
        self._sweep_started_at = 0.0
        self._constant_config: ConstantVoltageCurrentConfig | None = None
        self._constant_source_value = 0.0
        self._constant_started_at = 0.0

    def run_sweep(
        self,
        config: IVSweepConfig,
        *,
        stop_requested: Callable[[], bool] | None = None,
        on_sample: Callable[[IVSweepSample], None] | None = None,
    ) -> list[IVSweepSample]:
        self.begin_sweep(config)
        try:
            return self.sample_sweep(stop_requested=stop_requested, on_sample=on_sample)
        finally:
            self.end_sweep()

    def begin_sweep(self, config: IVSweepConfig) -> IVSweepConfig:
        if self._sweep_config is not None or self._constant_config is not None:
            raise RuntimeError("Keithley measurement session is already active.")
        normalized = config.normalized()
        instrument = self.instrument or self._open_resource(normalized.resource_name)
        self.instrument = instrument
        try:
            self._configure_instrument(instrument, normalized)
            self._set_source_value(instrument, normalized.sweep_mode, normalized.start)
            instrument.write(":OUTP ON")
        except Exception:
            if self._owns_instrument:
                try:
                    instrument.close()
                except Exception:
                    pass
                self.instrument = None
            raise
        self._sweep_config = normalized
        self._sweep_started_at = time.monotonic()
        return normalized

    def set_sweep_source_value(self, source_value: float) -> None:
        config = self._sweep_config
        instrument = self.instrument
        if config is None or instrument is None:
            raise RuntimeError("Keithley sweep session is not active.")
        value = float(source_value)
        if not math.isfinite(value):
            raise ValueError("Keithley sweep source value must be finite.")
        self._set_source_value(instrument, config.sweep_mode, value)

    def sample_sweep(
        self,
        *,
        samples_per_point: int = 1,
        stop_requested: Callable[[], bool] | None = None,
        on_sample: Callable[[IVSweepSample], None] | None = None,
    ) -> list[IVSweepSample]:
        config = self._sweep_config
        instrument = self.instrument
        if config is None or instrument is None:
            raise RuntimeError("Keithley sweep session is not active.")
        repeat_count = int(samples_per_point)
        if repeat_count <= 0 or repeat_count > 10000:
            raise ValueError("Keithley samples per sweep point must be in range 1..10000.")
        values = config.sweep_values()
        total = len(values) * repeat_count
        samples: list[IVSweepSample] = []
        for source_value in values:
            if stop_requested is not None and stop_requested():
                break
            self._set_source_value(instrument, config.sweep_mode, source_value)
            if config.source_delay_s > 0:
                time.sleep(config.source_delay_s)
            for _repeat_index in range(repeat_count):
                if stop_requested is not None and stop_requested():
                    break
                raw = instrument.query(":READ?")
                sample = self._sample_from_reading(
                    raw,
                    index=len(samples) + 1,
                    total=total,
                    elapsed_s=time.monotonic() - self._sweep_started_at,
                    source_value=source_value,
                    sweep_mode=config.sweep_mode,
                )
                samples.append(sample)
                if on_sample is not None:
                    on_sample(sample)
        return samples

    def end_sweep(self) -> None:
        config = self._sweep_config
        instrument = self.instrument
        self._sweep_config = None
        if config is None or instrument is None:
            return
        if config.output_off_after:
            try:
                instrument.write(":OUTP OFF")
            except Exception:
                pass
        if self._owns_instrument:
            try:
                instrument.close()
            except Exception:
                pass
            self.instrument = None

    def run_constant_voltage_current(
        self,
        config: ConstantVoltageCurrentConfig,
        *,
        stop_requested: Callable[[], bool] | None = None,
        on_sample: Callable[[IVSweepSample], None] | None = None,
    ) -> list[IVSweepSample]:
        self.begin_constant_voltage_current(config)
        try:
            return self.sample_constant_voltage_current(stop_requested=stop_requested, on_sample=on_sample)
        finally:
            self.end_constant_voltage_current()

    def begin_constant_voltage_current(self, config: ConstantVoltageCurrentConfig) -> ConstantVoltageCurrentConfig:
        if self._constant_config is not None or self._sweep_config is not None:
            raise RuntimeError("Keithley measurement session is already active.")
        normalized = config.normalized()
        instrument = self.instrument or self._open_resource(normalized.resource_name)
        self.instrument = instrument
        try:
            self._configure_constant_voltage_current(instrument, normalized)
            instrument.write(":OUTP ON")
        except Exception:
            if self._owns_instrument:
                try:
                    instrument.close()
                except Exception:
                    pass
                self.instrument = None
            raise
        self._constant_config = normalized
        self._constant_source_value = normalized.voltage_v
        self._constant_started_at = time.monotonic()
        return normalized

    def set_constant_voltage(self, voltage_v: float) -> None:
        if self._constant_config is None or self.instrument is None:
            raise RuntimeError("Constant-voltage current session is not active.")
        voltage = float(voltage_v)
        if not math.isfinite(voltage):
            raise ValueError("Constant voltage level must be finite.")
        self.instrument.write(f":SOUR:VOLT:LEV {voltage:.12g}")
        self._constant_source_value = voltage

    def sample_constant_voltage_current(
        self,
        *,
        stop_requested: Callable[[], bool] | None = None,
        on_sample: Callable[[IVSweepSample], None] | None = None,
    ) -> list[IVSweepSample]:
        config = self._constant_config
        instrument = self.instrument
        if config is None or instrument is None:
            raise RuntimeError("Constant-voltage current session is not active.")
        samples: list[IVSweepSample] = []
        for index in range(1, config.sample_count + 1):
            if stop_requested is not None and stop_requested():
                break
            raw = instrument.query(":READ?")
            sample = self._sample_from_reading(
                raw,
                index=index,
                total=config.sample_count,
                elapsed_s=time.monotonic() - self._constant_started_at,
                source_value=self._constant_source_value,
                sweep_mode=IV_SWEEP_MODE_VOLTAGE,
            )
            samples.append(sample)
            if on_sample is not None:
                on_sample(sample)
        return samples

    def end_constant_voltage_current(self) -> None:
        config = self._constant_config
        instrument = self.instrument
        self._constant_config = None
        if config is None or instrument is None:
            return
        if config.output_off_after:
            try:
                instrument.write(":OUTP OFF")
            except Exception:
                pass
        if self._owns_instrument:
            try:
                instrument.close()
            except Exception:
                pass
            self.instrument = None

    def _open_resource(self, resource_name: str) -> VisaInstrument:
        try:
            import pyvisa
        except ImportError as exc:
            raise RuntimeError("PyVISA is required for Keithley IV testing. Install pyvisa and a VISA backend first.") from exc
        manager = pyvisa.ResourceManager()
        instrument = manager.open_resource(resource_name)
        instrument.timeout = 10000
        return instrument

    def _configure_instrument(self, instrument: VisaInstrument, config: IVSweepConfig) -> None:
        instrument.write("*RST")
        instrument.write(f":ROUT:TERM {'FRON' if config.output_terminal == OUTPUT_TERMINAL_FRONT else 'REAR'}")
        if config.sweep_mode == IV_SWEEP_MODE_VOLTAGE:
            instrument.write(":SOUR:FUNC:MODE VOLT")
            instrument.write(":SOUR:VOLT:RANG:AUTO ON")
            instrument.write(':SENS:FUNC "CURR"')
            instrument.write(f":SOUR:VOLT:ILIM {config.current_limit_a:.12g}")
            if config.measure_range is None:
                instrument.write(":SENS:CURR:RANG:AUTO ON")
            else:
                instrument.write(f":SENS:CURR:RANG {config.measure_range:.12g}")
            instrument.write(f":SENS:CURR:NPLC {config.nplc:.12g}")
        else:
            instrument.write(":SOUR:FUNC:MODE CURR")
            instrument.write(":SOUR:CURR:RANG:AUTO ON")
            instrument.write(':SENS:FUNC "VOLT"')
            instrument.write(f":SOUR:CURR:VLIM {config.voltage_limit_v:.12g}")
            if config.measure_range is None:
                instrument.write(":SENS:VOLT:RANG:AUTO ON")
            else:
                instrument.write(f":SENS:VOLT:RANG {config.measure_range:.12g}")
            instrument.write(f":SENS:VOLT:NPLC {config.nplc:.12g}")

    def _configure_constant_voltage_current(self, instrument: VisaInstrument, config: ConstantVoltageCurrentConfig) -> None:
        instrument.write("*RST")
        instrument.write(f":ROUT:TERM {'FRON' if config.output_terminal == OUTPUT_TERMINAL_FRONT else 'REAR'}")
        instrument.write(":SOUR:FUNC:MODE VOLT")
        instrument.write(":SOUR:VOLT:RANG:AUTO ON")
        instrument.write(f":SOUR:VOLT:LEV {config.voltage_v:.12g}")
        instrument.write(':SENS:FUNC "CURR"')
        instrument.write(f":SOUR:VOLT:ILIM {config.current_limit_a:.12g}")
        if config.measure_range is None:
            instrument.write(":SENS:CURR:RANG:AUTO ON")
        else:
            instrument.write(f":SENS:CURR:RANG {config.measure_range:.12g}")
        instrument.write(f":SENS:CURR:NPLC {config.nplc:.12g}")

    def _set_source_value(self, instrument: VisaInstrument, sweep_mode: str, source_value: float) -> None:
        if sweep_mode == IV_SWEEP_MODE_VOLTAGE:
            instrument.write(f":SOUR:VOLT:LEV {source_value:.12g}")
        else:
            instrument.write(f":SOUR:CURR:LEV {source_value:.12g}")

    @staticmethod
    def _sample_from_reading(
        raw: str,
        *,
        index: int,
        total: int,
        elapsed_s: float,
        source_value: float,
        sweep_mode: str,
    ) -> IVSweepSample:
        numbers = _parse_reading_numbers(raw)
        if len(numbers) >= 2:
            voltage = numbers[0]
            current = numbers[1]
        elif sweep_mode == IV_SWEEP_MODE_VOLTAGE:
            voltage = source_value
            current = numbers[0] if numbers else math.nan
        else:
            voltage = numbers[0] if numbers else math.nan
            current = source_value
        resistance = None
        if len(numbers) >= 3 and math.isfinite(numbers[2]):
            resistance = numbers[2]
        elif current and math.isfinite(voltage) and math.isfinite(current):
            resistance = voltage / current
        return IVSweepSample(
            index=index,
            total=total,
            elapsed_s=elapsed_s,
            source_value=source_value,
            voltage_v=voltage,
            current_a=current,
            resistance_ohm=resistance,
            raw=str(raw).strip(),
        )


def _parse_reading_numbers(raw: str) -> list[float]:
    numbers: list[float] = []
    for part in str(raw).replace(";", ",").split(","):
        text = part.strip()
        if not text:
            continue
        try:
            numbers.append(float(text))
        except ValueError:
            continue
    return numbers
