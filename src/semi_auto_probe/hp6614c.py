from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from .b1500 import B1500_TEST_OUTPUT, B1500_TEST_TRANSFER, format_float_list, parse_float_list
from .keithley2450 import DEFAULT_KEITHLEY2450_RESOURCE, normalize_output_terminal, parse_optional_range


DEFAULT_HP6614C_RESOURCE = "GPIB0::5::INSTR"
HP6614C_MAX_VOLTAGE_V = 100.0
HP6614C_MAX_CURRENT_A = 0.5
KEITHLEY2450_DRAIN_MAX_CURRENT_LIMIT_A = 1.0
HP6614C_TEST_TRANSFER = B1500_TEST_TRANSFER
HP6614C_TEST_OUTPUT = B1500_TEST_OUTPUT
HP6614C_SCAN_FORWARD = "forward"
HP6614C_SCAN_BACKWARD = "backward"
HP6614C_SCAN_DOUBLE = "double"
HP6614C_HEATMAP_VTH = "vth"
HP6614C_HEATMAP_SS = "ss"
HP6614C_HEATMAP_ON_OFF_RATIO = "on_off_ratio"
HP6614C_TRANSFER_HEATMAP_METRICS = (HP6614C_HEATMAP_VTH, HP6614C_HEATMAP_SS, HP6614C_HEATMAP_ON_OFF_RATIO)


class VisaInstrument(Protocol):
    timeout: int

    def write(self, command: str) -> object: ...

    def query(self, command: str) -> str: ...

    def close(self) -> object: ...


@dataclass(frozen=True)
class HP6614CReading:
    elapsed_s: float
    voltage_v: float
    current_a: float
    raw_voltage: str
    raw_current: str

    def to_dict(self) -> dict[str, object]:
        return {
            "elapsed_s": self.elapsed_s,
            "voltage_v": self.voltage_v,
            "current_a": self.current_a,
            "raw_voltage": self.raw_voltage,
            "raw_current": self.raw_current,
        }


@dataclass(frozen=True)
class HP6614CTransferCurveMetric:
    drain_voltage_v: float
    value: float

    def to_dict(self) -> dict[str, float]:
        return {"drain_voltage_v": self.drain_voltage_v, "value": self.value}


@dataclass(frozen=True)
class HP6614CTransferAnalysis:
    metric: str
    curves: tuple[HP6614CTransferCurveMetric, ...]
    mean_value: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "curves": [curve.to_dict() for curve in self.curves],
            "mean_value": self.mean_value,
        }


@dataclass(frozen=True)
class HP6614CVoltageConfig:
    resource_name: str = DEFAULT_HP6614C_RESOURCE
    voltage_v: float = 0.0
    current_limit_a: float = 1e-6
    settle_s: float = 0.1
    output_on: bool = True
    output_off_after: bool = False
    verify_identity: bool = True
    readback: bool = True
    skip_open_clear: bool = False

    def normalized(self) -> "HP6614CVoltageConfig":
        values = (self.voltage_v, self.current_limit_a, self.settle_s)
        if any(not math.isfinite(float(value)) for value in values):
            raise ValueError("HP 6614C voltage, current limit, and settle time must be finite.")
        voltage = float(self.voltage_v)
        current_limit = float(self.current_limit_a)
        settle_s = float(self.settle_s)
        if voltage < 0 or voltage > HP6614C_MAX_VOLTAGE_V:
            raise ValueError(f"HP 6614C voltage must be in range 0..{HP6614C_MAX_VOLTAGE_V:g} V.")
        if current_limit <= 0 or current_limit > HP6614C_MAX_CURRENT_A:
            raise ValueError(f"HP 6614C current limit must be in range 0..{HP6614C_MAX_CURRENT_A:g} A.")
        if settle_s < 0 or settle_s > 60:
            raise ValueError("HP 6614C settle time must be in range 0..60 seconds.")
        resource_name = str(self.resource_name or DEFAULT_HP6614C_RESOURCE).strip()
        if not resource_name:
            raise ValueError("HP 6614C VISA resource cannot be empty.")
        return HP6614CVoltageConfig(
            resource_name=resource_name,
            voltage_v=voltage,
            current_limit_a=current_limit,
            settle_s=settle_s,
            output_on=bool(self.output_on),
            output_off_after=bool(self.output_off_after),
            verify_identity=bool(self.verify_identity),
            readback=bool(self.readback),
            skip_open_clear=bool(self.skip_open_clear),
        )

    def summary(self) -> str:
        config = self.normalized()
        state = "on" if config.output_on else "off"
        return f"{config.resource_name} | Vg={config.voltage_v:g} V | Ilimit={config.current_limit_a:g} A | output {state}"


@dataclass(frozen=True)
class HP6614CSweepConfig:
    test_type: str
    hp_resource_name: str = DEFAULT_HP6614C_RESOURCE
    keithley_resource_name: str = DEFAULT_KEITHLEY2450_RESOURCE
    output_terminal: str = "front"
    device_name: str = "{point}"
    sweep_start_v: float = 0.0
    sweep_end_v: float = 2.0
    sweep_points: int = 41
    scan_type: str = HP6614C_SCAN_FORWARD
    bias_values_v: tuple[float, ...] = (-1.0, -2.0, -3.0)
    drain_current_limit_a: float = 1.0
    drain_measure_range: float | None = None
    gate_current_limit_a: float = 1e-6
    drain_nplc: float = 1.0
    sample_count: int = 1
    gate_settle_s: float = 0.02
    drain_settle_s: float = 0.02
    pre_settle_s: float = 0.2
    post_sweep_pause_s: float = 0.2
    verify_identity: bool = False
    readback: bool = True
    skip_open_clear: bool = True
    output_off_after: bool = True
    heatmap_metric: str = HP6614C_HEATMAP_VTH
    heatmap_values: bool = True

    def normalized(self) -> "HP6614CSweepConfig":
        test_type = normalize_hp6614c_test_type(self.test_type)
        heatmap_metric = normalize_hp6614c_heatmap_metric(self.heatmap_metric)
        hp_resource_name = str(self.hp_resource_name or DEFAULT_HP6614C_RESOURCE).strip()
        keithley_resource_name = str(self.keithley_resource_name or DEFAULT_KEITHLEY2450_RESOURCE).strip()
        if not hp_resource_name or not keithley_resource_name:
            raise ValueError("6614C/Keithley VISA resources cannot be empty.")
        output_terminal = normalize_output_terminal(self.output_terminal)
        bias_values = tuple(float(value) for value in self.bias_values_v)
        numeric = (
            self.sweep_start_v,
            self.sweep_end_v,
            self.drain_current_limit_a,
            1.0 if self.drain_measure_range is None else self.drain_measure_range,
            self.gate_current_limit_a,
            self.drain_nplc,
            self.sample_count,
            self.gate_settle_s,
            self.drain_settle_s,
            self.pre_settle_s,
            self.post_sweep_pause_s,
            *bias_values,
        )
        if any(not math.isfinite(float(value)) for value in numeric):
            raise ValueError("6614C sweep numeric parameters must be finite.")
        if not bias_values:
            raise ValueError("6614C bias list cannot be empty.")
        sweep_points = int(float(self.sweep_points))
        if sweep_points < 2 or sweep_points > 100000:
            raise ValueError("6614C sweep points must be in range 2..100000.")
        sample_count = int(float(self.sample_count))
        if sample_count <= 0 or sample_count > 10000:
            raise ValueError("6614C sample count must be in range 1..10000.")
        if float(self.drain_current_limit_a) <= 0 or float(self.drain_current_limit_a) > KEITHLEY2450_DRAIN_MAX_CURRENT_LIMIT_A:
            raise ValueError("6614C drain current limit must be in range 0..1 A for Keithley 2450.")
        if self.drain_measure_range is not None and float(self.drain_measure_range) <= 0:
            raise ValueError("6614C drain measurement range must be positive or auto.")
        if float(self.gate_current_limit_a) <= 0 or float(self.gate_current_limit_a) > HP6614C_MAX_CURRENT_A:
            raise ValueError(f"6614C gate current limit must be in range 0..{HP6614C_MAX_CURRENT_A:g} A.")
        if float(self.drain_nplc) <= 0 or float(self.drain_nplc) > 25:
            raise ValueError("6614C drain NPLC must be in range 0..25.")
        scan_type = normalize_hp6614c_scan_type(self.scan_type)
        if float(self.gate_settle_s) < 0 or float(self.drain_settle_s) < 0 or float(self.pre_settle_s) < 0 or float(self.post_sweep_pause_s) < 0:
            raise ValueError("6614C settle times must be zero or positive.")
        if test_type == HP6614C_TEST_TRANSFER:
            _validate_hp_gate_voltage(self.sweep_start_v)
            _validate_hp_gate_voltage(self.sweep_end_v)
        else:
            for value in bias_values:
                _validate_hp_gate_voltage(value)
        return HP6614CSweepConfig(
            test_type=test_type,
            hp_resource_name=hp_resource_name,
            keithley_resource_name=keithley_resource_name,
            output_terminal=output_terminal,
            device_name=str(self.device_name or "{point}").strip() or "{point}",
            sweep_start_v=float(self.sweep_start_v),
            sweep_end_v=float(self.sweep_end_v),
            sweep_points=sweep_points,
            scan_type=scan_type,
            bias_values_v=bias_values,
            drain_current_limit_a=float(self.drain_current_limit_a),
            drain_measure_range=None if self.drain_measure_range is None else float(self.drain_measure_range),
            gate_current_limit_a=float(self.gate_current_limit_a),
            drain_nplc=float(self.drain_nplc),
            sample_count=sample_count,
            gate_settle_s=float(self.gate_settle_s),
            drain_settle_s=float(self.drain_settle_s),
            pre_settle_s=float(self.pre_settle_s),
            post_sweep_pause_s=float(self.post_sweep_pause_s),
            verify_identity=bool(self.verify_identity),
            readback=bool(self.readback),
            skip_open_clear=bool(self.skip_open_clear),
            output_off_after=bool(self.output_off_after),
            heatmap_metric=heatmap_metric,
            heatmap_values=bool(self.heatmap_values),
        )

    def sweep_values(self) -> tuple[float, ...]:
        config = self.normalized()
        step = (config.sweep_end_v - config.sweep_start_v) / (config.sweep_points - 1)
        forward = tuple(config.sweep_start_v + step * index for index in range(config.sweep_points))
        if config.scan_type == HP6614C_SCAN_BACKWARD:
            return tuple(reversed(forward))
        if config.scan_type == HP6614C_SCAN_DOUBLE and len(forward) > 1:
            return forward + tuple(reversed(forward[:-1]))
        return forward

    def summary(self) -> str:
        config = self.normalized()
        range_text = "auto Id range" if config.drain_measure_range is None else f"Id range {config.drain_measure_range:g} A"
        if config.test_type == HP6614C_TEST_TRANSFER:
            return f"HP {config.hp_resource_name} + Keithley {config.keithley_resource_name} | Transfer {config.scan_type} Vg {config.sweep_start_v:g}->{config.sweep_end_v:g} V, Vd={format_float_list(config.bias_values_v)} V | {range_text}"
        return f"HP {config.hp_resource_name} + Keithley {config.keithley_resource_name} | Output Vd {config.sweep_start_v:g}->{config.sweep_end_v:g} V, Vg={format_float_list(config.bias_values_v)} V | {range_text}"


def parse_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on", "enable", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disable", "disabled"}:
        return False
    raise ValueError(f"Cannot parse boolean value: {value!r}.")


def hp6614c_voltage_config_from_params(params: dict[str, str]) -> HP6614CVoltageConfig:
    return HP6614CVoltageConfig(
        resource_name=params.get("resource", params.get("resource_name", DEFAULT_HP6614C_RESOURCE)),
        voltage_v=float(params.get("gate_voltage_v", params.get("voltage_v", params.get("voltage", 0.0)))),
        current_limit_a=float(params.get("current_limit_a", params.get("limit_a", params.get("compliance_a", 1e-6)))),
        settle_s=float(params.get("settle_s", params.get("delay_s", 0.1))),
        output_on=parse_bool(params.get("output_on", "true"), default=True),
        output_off_after=parse_bool(params.get("output_off_after", "false"), default=False),
        verify_identity=parse_bool(params.get("verify_identity", "true"), default=True),
        readback=parse_bool(params.get("readback", "true"), default=True),
        skip_open_clear=parse_bool(params.get("skip_open_clear", "false"), default=False),
    ).normalized()


def normalize_hp6614c_test_type(value: object) -> str:
    text = str(value or HP6614C_TEST_TRANSFER).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "transfer": HP6614C_TEST_TRANSFER,
        "id_vg": HP6614C_TEST_TRANSFER,
        "vg": HP6614C_TEST_TRANSFER,
        "output": HP6614C_TEST_OUTPUT,
        "id_vd": HP6614C_TEST_OUTPUT,
        "vd": HP6614C_TEST_OUTPUT,
    }
    normalized = aliases.get(text, text)
    if normalized not in {HP6614C_TEST_TRANSFER, HP6614C_TEST_OUTPUT}:
        raise ValueError("6614C test type must be transfer or output.")
    return normalized


def normalize_hp6614c_scan_type(value: object) -> str:
    text = str(value or HP6614C_SCAN_FORWARD).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "single": HP6614C_SCAN_FORWARD,
        "forward": HP6614C_SCAN_FORWARD,
        "forwards": HP6614C_SCAN_FORWARD,
        "start_to_end": HP6614C_SCAN_FORWARD,
        "backward": HP6614C_SCAN_BACKWARD,
        "backwards": HP6614C_SCAN_BACKWARD,
        "reverse": HP6614C_SCAN_BACKWARD,
        "end_to_start": HP6614C_SCAN_BACKWARD,
        "double": HP6614C_SCAN_DOUBLE,
        "dual": HP6614C_SCAN_DOUBLE,
        "bidirectional": HP6614C_SCAN_DOUBLE,
    }
    normalized = aliases.get(text, text)
    if normalized not in {HP6614C_SCAN_FORWARD, HP6614C_SCAN_BACKWARD, HP6614C_SCAN_DOUBLE}:
        raise ValueError("6614C scan type must be forward, backward, or double.")
    return normalized


def normalize_hp6614c_heatmap_metric(value: object) -> str:
    text = str(value or HP6614C_HEATMAP_VTH).strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
    aliases = {
        "vth": HP6614C_HEATMAP_VTH,
        "threshold": HP6614C_HEATMAP_VTH,
        "threshold_voltage": HP6614C_HEATMAP_VTH,
        "ss": HP6614C_HEATMAP_SS,
        "subthreshold": HP6614C_HEATMAP_SS,
        "subthreshold_swing": HP6614C_HEATMAP_SS,
        "onoff": HP6614C_HEATMAP_ON_OFF_RATIO,
        "on_off": HP6614C_HEATMAP_ON_OFF_RATIO,
        "on_off_ratio": HP6614C_HEATMAP_ON_OFF_RATIO,
        "ion_ioff": HP6614C_HEATMAP_ON_OFF_RATIO,
    }
    normalized = aliases.get(text, text)
    if normalized not in HP6614C_TRANSFER_HEATMAP_METRICS:
        raise ValueError("6614C transfer heatmap metric must be vth, ss, or on_off_ratio.")
    return normalized


def hp6614c_sweep_config_from_params(params: dict[str, str], *, test_type: str) -> HP6614CSweepConfig:
    normalized_test_type = normalize_hp6614c_test_type(test_type)
    default_bias = "-1:-1:-3" if normalized_test_type == HP6614C_TEST_TRANSFER else "0"
    default_stop = "-2" if normalized_test_type == HP6614C_TEST_OUTPUT else "2"
    return HP6614CSweepConfig(
        test_type=normalized_test_type,
        hp_resource_name=params.get("hp_resource", params.get("resource", DEFAULT_HP6614C_RESOURCE)),
        keithley_resource_name=params.get("keithley_resource", params.get("drain_resource", DEFAULT_KEITHLEY2450_RESOURCE)),
        output_terminal=params.get("output_terminal", "front"),
        device_name=params.get("device_name", "{point}"),
        sweep_start_v=float(params.get("sweep_start_v", params.get("start", 0.0))),
        sweep_end_v=float(params.get("sweep_end_v", params.get("stop", default_stop))),
        sweep_points=int(float(params.get("sweep_points", "41"))),
        scan_type=params.get("scan_type", HP6614C_SCAN_FORWARD),
        bias_values_v=parse_float_list(params.get("bias_values_v", default_bias)),
        drain_current_limit_a=float(params.get("drain_current_limit_a", params.get("current_limit_a", "1"))),
        drain_measure_range=parse_optional_range(params.get("drain_measure_range", params.get("measure_range", params.get("measurement_range", "auto")))),
        gate_current_limit_a=float(params.get("gate_current_limit_a", params.get("gate_limit_a", "1e-6"))),
        drain_nplc=float(params.get("drain_nplc", params.get("nplc", "1"))),
        sample_count=int(float(params.get("sample_count", "1"))),
        gate_settle_s=float(params.get("gate_settle_s", params.get("settle_s", "0.02"))),
        drain_settle_s=float(params.get("drain_settle_s", params.get("step_delay_s", params.get("source_delay_s", "0.02")))),
        pre_settle_s=float(params.get("pre_settle_s", "0.2")),
        post_sweep_pause_s=float(params.get("post_sweep_pause_s", "0.2")),
        verify_identity=parse_bool(params.get("verify_identity", "false"), default=False),
        readback=parse_bool(params.get("readback", "true"), default=True),
        skip_open_clear=parse_bool(params.get("skip_open_clear", "true"), default=True),
        output_off_after=parse_bool(params.get("output_off_after", "true"), default=True),
        heatmap_metric=params.get("heatmap_metric", HP6614C_HEATMAP_VTH),
        heatmap_values=parse_bool(params.get("heatmap_values", "true"), default=True),
    ).normalized()


def hp6614c_transfer_heatmap_value(records: list[dict[str, object]], metric: object) -> float | None:
    return analyze_hp6614c_transfer_records(records, metric).mean_value


def analyze_hp6614c_transfer_records(records: list[dict[str, object]], metric: object) -> HP6614CTransferAnalysis:
    normalized = normalize_hp6614c_heatmap_metric(metric)
    curves: list[HP6614CTransferCurveMetric] = []
    for drain_voltage_v, points in _transfer_points_by_drain(records):
        value = _transfer_metric_value(points, normalized)
        if value is not None and math.isfinite(value):
            curves.append(HP6614CTransferCurveMetric(drain_voltage_v=drain_voltage_v, value=float(value)))
    mean_value = sum(curve.value for curve in curves) / len(curves) if curves else None
    return HP6614CTransferAnalysis(metric=normalized, curves=tuple(curves), mean_value=mean_value)


def _transfer_metric_value(points: list[tuple[float, float]], metric: str) -> float | None:
    if len(points) < 3:
        return None
    currents = [current for _gate, current in points if current > 0 and math.isfinite(current)]
    if metric == HP6614C_HEATMAP_ON_OFF_RATIO:
        if not currents:
            return None
        off_current = min(currents)
        return max(currents) / off_current if off_current > 0 else None
    if metric == HP6614C_HEATMAP_SS:
        log_points = [(gate, math.log10(max(current, 1e-30))) for gate, current in points if current > 0 and math.isfinite(current)]
        slope = _max_abs_linear_fit_slope(log_points)
        if slope is None or slope == 0:
            return None
        return 1000.0 / abs(slope)
    slope_intercept = _max_abs_linear_fit(points)
    if slope_intercept is None:
        return None
    slope, intercept = slope_intercept
    if slope == 0:
        return None
    return -intercept / slope


def hp6614c_transfer_heatmap_label(metric: object) -> str:
    normalized = normalize_hp6614c_heatmap_metric(metric)
    if normalized == HP6614C_HEATMAP_SS:
        return "SS (mV/dec)"
    if normalized == HP6614C_HEATMAP_ON_OFF_RATIO:
        return "On-off ratio"
    return "Vth (V)"


def _transfer_points_by_drain(records: list[dict[str, object]]) -> list[tuple[float, list[tuple[float, float]]]]:
    by_drain: dict[float, list[dict[str, object]]] = {}
    for record in records:
        try:
            drain_v = float(record.get("drain_voltage_v"))
            gate_v = float(record.get("gate_voltage_v"))
            current = abs(float(record.get("drain_current_a")))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(drain_v) and math.isfinite(gate_v) and math.isfinite(current)):
            continue
        by_drain.setdefault(round(drain_v, 9), []).append(record)
    if not by_drain:
        return []
    curves: list[tuple[float, list[tuple[float, float]]]] = []
    for drain_voltage_v, curve_records in by_drain.items():
        by_gate: dict[float, list[float]] = {}
        for record in curve_records:
            try:
                gate_v = float(record.get("gate_voltage_v"))
                current = abs(float(record.get("drain_current_a")))
            except (TypeError, ValueError):
                continue
            if math.isfinite(gate_v) and math.isfinite(current):
                by_gate.setdefault(round(gate_v, 12), []).append(max(current, 1e-30))
        points: list[tuple[float, float]] = []
        for gate_v, currents in by_gate.items():
            currents = sorted(currents)
            middle = len(currents) // 2
            median = currents[middle] if len(currents) % 2 else (currents[middle - 1] + currents[middle]) / 2.0
            points.append((float(gate_v), median))
        curves.append((float(drain_voltage_v), sorted(points)))
    return curves


def _max_abs_linear_fit_slope(points: list[tuple[float, float]]) -> float | None:
    fit = _max_abs_linear_fit(points)
    return None if fit is None else fit[0]


def _max_abs_linear_fit(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    finite_points = [(float(x), float(y)) for x, y in points if math.isfinite(float(x)) and math.isfinite(float(y))]
    if len(finite_points) < 3:
        return None
    window_size = min(len(finite_points), max(3, int(round(len(finite_points) * 0.25))))
    best: tuple[float, float] | None = None
    best_score = 0.0
    for start in range(0, len(finite_points) - window_size + 1):
        window = finite_points[start : start + window_size]
        fit = _linear_fit(window)
        if fit is None:
            continue
        slope, intercept = fit
        score = abs(slope)
        if score > best_score:
            best_score = score
            best = (slope, intercept)
    return best


def _linear_fit(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    if len(points) < 2:
        return None
    mean_x = sum(x for x, _y in points) / len(points)
    mean_y = sum(y for _x, y in points) / len(points)
    denominator = sum((x - mean_x) ** 2 for x, _y in points)
    if denominator <= 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
    intercept = mean_y - slope * mean_x
    if not math.isfinite(slope) or not math.isfinite(intercept):
        return None
    return slope, intercept


class HP6614CRunner:
    def __init__(self, instrument: VisaInstrument | None = None) -> None:
        self.instrument = instrument
        self._owns_instrument = instrument is None

    def apply_voltage(
        self,
        config: HP6614CVoltageConfig,
        *,
        on_reading: Callable[[HP6614CReading], None] | None = None,
    ) -> HP6614CReading:
        normalized = config.normalized()
        instrument = self.instrument or self._open_resource(
            normalized.resource_name,
            verify_identity=normalized.verify_identity,
            skip_open_clear=normalized.skip_open_clear,
        )
        self.instrument = instrument
        started_at = time.monotonic()
        try:
            self._safe_configure_voltage(instrument, normalized)
            if normalized.output_on:
                instrument.write("OUTP ON")
            if normalized.settle_s > 0:
                time.sleep(normalized.settle_s)
            elapsed_s = time.monotonic() - started_at
            if normalized.readback:
                reading = self.measure_output(instrument=instrument, elapsed_s=elapsed_s)
            else:
                reading = HP6614CReading(
                    elapsed_s=elapsed_s,
                    voltage_v=normalized.voltage_v,
                    current_a=math.nan,
                    raw_voltage="readback disabled",
                    raw_current="readback disabled",
                )
            if on_reading is not None:
                on_reading(reading)
            return reading
        finally:
            if normalized.output_off_after:
                try:
                    instrument.write("OUTP OFF")
                except Exception:
                    pass
            if self._owns_instrument:
                try:
                    instrument.close()
                except Exception:
                    pass
                self.instrument = None

    def output_off(self) -> None:
        if self.instrument is not None:
            self.instrument.write("OUTP OFF")

    def measure_output(self, *, instrument: VisaInstrument | None = None, elapsed_s: float = 0.0) -> HP6614CReading:
        active = instrument or self.instrument
        if active is None:
            raise RuntimeError("No HP 6614C instrument is open.")
        raw_voltage = active.query("MEAS:VOLT?")
        raw_current = active.query("MEAS:CURR?")
        return HP6614CReading(
            elapsed_s=float(elapsed_s),
            voltage_v=_first_float(raw_voltage),
            current_a=_first_float(raw_current),
            raw_voltage=str(raw_voltage).strip(),
            raw_current=str(raw_current).strip(),
        )

    def _open_resource(self, resource_name: str, *, verify_identity: bool = True, skip_open_clear: bool = False) -> VisaInstrument:
        try:
            import pyvisa
        except ImportError as exc:
            raise RuntimeError("PyVISA is required for HP 6614C control. Install pyvisa and a VISA backend first.") from exc
        manager = pyvisa.ResourceManager()
        if skip_open_clear:
            instrument = self._open_resource_without_clear(manager, resource_name)
        else:
            instrument = manager.open_resource(resource_name)
        instrument.timeout = 10000
        if verify_identity:
            try:
                verify_hp6614c_identity(instrument.query("*IDN?"))
            except Exception:
                try:
                    instrument.close()
                finally:
                    raise
        return instrument

    def _open_resource_without_clear(self, manager, resource_name: str) -> VisaInstrument:
        try:
            from pyvisa import constants
        except ImportError as exc:
            raise RuntimeError("PyVISA is required for HP 6614C control. Install pyvisa and a VISA backend first.") from exc
        info = manager.resource_info(resource_name, extended=True)
        try:
            resource_pyclass = manager._resource_classes[(info.interface_type, info.resource_class)]
        except KeyError:
            resource_pyclass = manager._resource_classes[(constants.InterfaceType.unknown, "")]
        instrument = resource_pyclass(manager, resource_name)
        session, _status = manager.open_bare_resource(resource_name)
        instrument.session = session
        instrument._logging_extra["session"] = session
        manager._created_resources.add(instrument)
        return instrument

    def _safe_configure_voltage(self, instrument: VisaInstrument, config: HP6614CVoltageConfig) -> None:
        instrument.write("OUTP OFF")
        instrument.write("VOLT 0")
        instrument.write(f"CURR {config.current_limit_a:.12g}")
        instrument.write(f"VOLT {config.voltage_v:.12g}")


def verify_hp6614c_identity(idn_response: str) -> None:
    text = str(idn_response).upper()
    has_vendor = any(vendor in text for vendor in ("HEWLETT-PACKARD", "HP", "AGILENT", "KEYSIGHT"))
    if not has_vendor or "6614C" not in text:
        raise RuntimeError(f"Connected VISA instrument does not look like an HP/Agilent 6614C: {idn_response!r}")


def _validate_hp_gate_voltage(value: object) -> None:
    voltage = float(value)
    if voltage < 0 or voltage > HP6614C_MAX_VOLTAGE_V:
        raise ValueError(f"HP 6614C gate voltage must be in range 0..{HP6614C_MAX_VOLTAGE_V:g} V.")


def _first_float(raw: str) -> float:
    for part in str(raw).replace(";", ",").split(","):
        text = part.strip()
        if not text:
            continue
        try:
            return float(text)
        except ValueError:
            continue
    return math.nan
