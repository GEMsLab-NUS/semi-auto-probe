from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict, dataclass
from typing import Mapping, Protocol, Sequence

import numpy as np

from .dp100 import DEFAULT_LASER_PARAMS, DP100Setpoint


@dataclass(frozen=True)
class WaveformPoint:
    time_ms: float
    voltage_v: float
    current_a: float


class WaveformDriver(Protocol):
    def set_output(self, setpoint: DP100Setpoint, enabled: bool) -> None: ...

    def configure_limits(self, setpoint: DP100Setpoint, *, ovp_v: float, ocp_a: float) -> None: ...


def calibrated_power_setpoint(
    target_power_mw: float,
    calibration: Sequence[tuple[float, float]],
    *,
    preset_index: int = 1,
) -> DP100Setpoint:
    """Interpolate a DP100 voltage from a measured electrical P–V curve."""
    if len(calibration) < 2:
        raise ValueError("Laser power calibration needs at least two measured I-V points.")
    measured_i, measured_v = np.array(sorted(calibration), dtype=float).T
    measured_p_mw = measured_i * measured_v * 1000.0
    if np.any(~np.isfinite(measured_p_mw)) or np.any(np.diff(measured_p_mw) <= 0):
        raise ValueError("Laser P-V calibration must contain strictly increasing power points.")
    target = float(target_power_mw)
    if not math.isfinite(target) or target < 0:
        raise ValueError("Laser target power must be finite and nonnegative.")
    # The current sweep cannot characterize powers below its first 1 mA
    # operating point. Treat that uncalibrated low region as dark.
    if target < float(measured_p_mw[0]):
        return DP100Setpoint(0, 0, preset_index)
    if target > float(measured_p_mw[-1]) + 1e-9:
        raise ValueError(
            f"Laser P-V calibration covers {measured_p_mw[0]:g} to {measured_p_mw[-1]:g} mW; "
            f"requested {target:g} mW is outside that range."
        )
    voltage = float(np.interp(target, measured_p_mw, measured_v))
    return DP100Setpoint(voltage, float(measured_i[-1]), preset_index)


def calibrated_response_program(params: Mapping[str, object], calibration: Sequence[tuple[float, float]]) -> tuple[list[WaveformPoint], list[dict[str, float | str]]]:
    """Build equal-drive-power pulses followed by a fixed-power width series."""
    if len(calibration) < 2:
        raise ValueError("Laser I-V calibration needs at least two measured points.")
    measured_i, measured_v = np.array(sorted(calibration), dtype=float).T
    measured_p_mw = measured_i * measured_v * 1000
    if np.any(np.diff(measured_p_mw) <= 0):
        raise ValueError("Laser P-V calibration must contain strictly increasing drive-power points.")

    def power_value(key: str, default: float) -> float:
        raw = str(params.get(key, "auto")).strip().lower()
        return default if raw in {"", "auto"} else float(raw)

    available_min_mw = float(measured_p_mw[0])
    available_max_mw = float(measured_p_mw[-1])
    power_min_mw = power_value("power_min_mw", available_min_mw)
    power_max_mw = power_value("power_max_mw", available_max_mw)
    power_levels = max(2, int(_float(params.get("power_levels"), 25)))
    power_width_ms = _float(params.get("power_width_ms", params.get("intensity_width_ms")), 200)
    gap = _float(params.get("pulse_gap_ms", params.get("pulse_period_ms")), 1000)
    lead = _float(params.get("program_lead_ms"), 100)
    width_min = _float(params.get("width_min_ms"), 100)
    width_max = _float(params.get("width_max_ms"), 500)
    width_step = _float(params.get("width_step_ms"), 100)
    width_power_mw = power_value("width_power_mw", (power_min_mw + power_max_mw) / 2)
    drive_current_limit = _float(params.get("pulse_current_limit_a"), float(measured_i[-1]))

    phase = str(params.get("response_phase", "both")).strip().lower()
    if phase == "intensity":
        phase = "power"
    if phase not in {"both", "power", "width"}:
        raise ValueError("Laser response phase must be both, power, or width.")
    required = [(gap, "dark gap between pulses"), (drive_current_limit, "pulse current limit")]
    if phase in {"both", "power"}:
        required.extend(((power_min_mw, "minimum drive power"), (power_max_mw, "maximum drive power"),
                         (power_width_ms, "power pulse width")))
    if phase in {"both", "width"}:
        required.extend(((width_min, "minimum pulse width"), (width_max, "maximum pulse width"),
                         (width_step, "pulse-width step"), (width_power_mw, "pulse-width drive power")))
    for value, label in required:
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"Laser response {label} must be finite and positive.")
    if power_min_mw > power_max_mw:
        raise ValueError("Laser drive-power minimum cannot exceed its maximum.")
    requested_powers = []
    if phase in {"both", "power"}:
        requested_powers.extend((power_min_mw, power_max_mw))
    if phase in {"both", "width"}:
        requested_powers.append(width_power_mw)
    if max(requested_powers) > available_max_mw + 1e-9 or min(requested_powers) < available_min_mw - 1e-9:
        raise ValueError(
            f"Laser P-V calibration covers {available_min_mw:g} to {available_max_mw:g} mW; "
            "requested pulse power is outside that range."
        )
    if not math.isfinite(lead) or lead < 0:
        raise ValueError("Laser response dark-baseline lead time must be finite and nonnegative.")

    up = np.linspace(power_min_mw, power_max_mw, power_levels).tolist()
    powers_mw = up + list(reversed(up[:-1]))
    widths = _inclusive_values(width_min, width_max, width_step)
    requests = []
    if phase in {"both", "power"}:
        requests.extend(("power", power_mw, power_width_ms) for power_mw in powers_mw)
    if phase in {"both", "width"}:
        requests.extend(("width", width_power_mw, width) for width in widths)

    edge = .001
    points = [WaveformPoint(0, 0, 0)]
    events: list[dict[str, float | str]] = []
    cursor = lead
    for event_phase, power_mw, width in requests:
        start = cursor
        voltage = float(np.interp(power_mw, measured_p_mw, measured_v))
        equivalent_current = float(np.interp(power_mw, measured_p_mw, measured_i))
        points.extend((WaveformPoint(start + edge, voltage, drive_current_limit),
                       WaveformPoint(start + width, voltage, drive_current_limit),
                       WaveformPoint(start + width + edge, 0, 0),
                       WaveformPoint(start + width + gap, 0, 0)))
        events.append({
            "phase": event_phase,
            "start_ms": start,
            "width_ms": width,
            "target_power_mw": power_mw,
            "calibrated_voltage_v": voltage,
            "equivalent_current_a": equivalent_current,
            "drive_current_limit_a": drive_current_limit,
        })
        cursor = start + width + gap
    return normalize_waveform_points(points), events


def _inclusive_values(start: float, stop: float, step: float) -> list[float]:
    count = int(math.floor((stop - start) / step + 1e-9))
    values = [start + index * step for index in range(count + 1)]
    if values[-1] < stop - step * 1e-6:
        values.append(stop)
    return values


def default_waveform_points(params: Mapping[str, object]) -> list[WaveformPoint]:
    delay = max(0.0, _float(params.get("delay_ms"), 100.0))
    width = max(0.001, _float(params.get("width_ms"), 100.0))
    period = max(delay + width + 0.002, _float(params.get("period_ms"), 500.0))
    peak_v = max(0.0, _float(params.get("peak_voltage_v"), float(DEFAULT_LASER_PARAMS["peak_voltage_v"])))
    peak_i = max(0.0, _float(params.get("peak_current_a"), float(DEFAULT_LASER_PARAMS["peak_current_a"])))
    base_v = max(0.0, _float(params.get("baseline_voltage_v"), 0.0))
    base_i = max(0.0, _float(params.get("baseline_current_a"), 0.0))
    edge = min(0.01, max(0.001, period / 100_000))
    return normalize_waveform_points(
        [
            WaveformPoint(0.0, base_v, base_i),
            WaveformPoint(max(edge, delay), base_v, base_i),
            WaveformPoint(max(edge * 2, delay + edge), peak_v, peak_i),
            WaveformPoint(delay + width, peak_v, peak_i),
            WaveformPoint(min(period, delay + width + edge), base_v, base_i),
            WaveformPoint(period, base_v, base_i),
        ]
    )


def parse_waveform_points(params: Mapping[str, object]) -> list[WaveformPoint]:
    raw = str(params.get("waveform_points", "")).strip()
    if not raw:
        return default_waveform_points(params)
    try:
        payload = json.loads(raw)
        points = [
            WaveformPoint(float(item["time_ms"]), float(item["voltage_v"]), float(item["current_a"]))
            for item in payload
        ]
        return normalize_waveform_points(points)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return default_waveform_points(params)


def serialize_waveform_points(points: Sequence[WaveformPoint]) -> str:
    normalized = normalize_waveform_points(points)
    return json.dumps([asdict(point) for point in normalized], separators=(",", ":"))


def normalize_waveform_points(points: Sequence[WaveformPoint]) -> list[WaveformPoint]:
    ordered = sorted(
        (WaveformPoint(max(0.0, float(point.time_ms)), max(0.0, float(point.voltage_v)), max(0.0, float(point.current_a))) for point in points),
        key=lambda point: point.time_ms,
    )
    result: list[WaveformPoint] = []
    for point in ordered:
        time_ms = point.time_ms
        if result and time_ms <= result[-1].time_ms:
            time_ms = result[-1].time_ms + 0.001
        result.append(WaveformPoint(time_ms, point.voltage_v, point.current_a))
    if len(result) < 2:
        raise ValueError("A waveform requires at least two points.")
    return result


def preset_waveform(name: str, *, duration_ms: float, peak_voltage_v: float, peak_current_a: float) -> list[WaveformPoint]:
    duration = max(1.0, float(duration_ms))
    voltage = max(0.0, float(peak_voltage_v))
    current = max(0.0, float(peak_current_a))
    if name == "Ramp":
        return [WaveformPoint(duration * index / 20, voltage * index / 20, current * index / 20) for index in range(21)]
    if name == "Staircase":
        return [WaveformPoint(duration * index / 4, voltage * index / 4, current * index / 4) for index in range(5)]
    edge = max(0.001, duration / 100_000)
    return [
        WaveformPoint(0, 0, 0),
        WaveformPoint(duration * 0.2, 0, 0),
        WaveformPoint(duration * 0.2 + edge, voltage, current),
        WaveformPoint(duration * 0.7, voltage, current),
        WaveformPoint(duration * 0.7 + edge, 0, 0),
        WaveformPoint(duration, 0, 0),
    ]


def run_dp100_waveform(
    driver: WaveformDriver,
    params: Mapping[str, object],
    *,
    stop_event: threading.Event | None = None,
) -> None:
    points = parse_waveform_points(params)
    preset = int(_float(params.get("preset_index"), 1))
    repeat = max(1, int(_float(params.get("count"), 1)))
    peak = DP100Setpoint(max(point.voltage_v for point in points), max(point.current_a for point in points), preset)
    driver.configure_limits(
        peak,
        ovp_v=max(peak.voltage_v, _float(params.get("ovp_v"), float(DEFAULT_LASER_PARAMS["ovp_v"]))),
        ocp_a=max(peak.current_a, _float(params.get("ocp_a"), float(DEFAULT_LASER_PARAMS["ocp_a"]))),
    )
    wait = stop_event.wait if stop_event is not None else _wait
    for _repeat in range(repeat):
        for index, point in enumerate(points):
            if index and wait(max(0.0, (point.time_ms - points[index - 1].time_ms) / 1000.0)):
                break
            setpoint = DP100Setpoint(point.voltage_v, point.current_a, preset)
            driver.set_output(setpoint, point.voltage_v > 0 and point.current_a > 0)
        if stop_event is not None and stop_event.is_set():
            break
    final_enabled = str(params.get("final_state", "off")).lower() == "baseline"
    final = points[0] if final_enabled else WaveformPoint(0, 0, 0)
    driver.set_output(DP100Setpoint(final.voltage_v, final.current_a, preset), final_enabled and final.voltage_v > 0 and final.current_a > 0)


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _wait(seconds: float) -> bool:
    threading.Event().wait(seconds)
    return False
