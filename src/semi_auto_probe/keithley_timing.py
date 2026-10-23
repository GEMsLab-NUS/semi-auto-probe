"""Keithley I/V-t drive profiles shared by the editor and executor."""
from __future__ import annotations

import json
import math
import re
import csv
import io
from bisect import bisect_right
from dataclasses import dataclass
from typing import Mapping, Sequence

from .keithley2450 import normalize_sweep_mode
from .smu_settings import parse_si_value


@dataclass(frozen=True)
class KeithleyDrivePoint:
    time_s: float
    value: float


def parse_drive_profile(value: object, *, source_mode: object = "voltage") -> tuple[KeithleyDrivePoint, ...]:
    """Parse semicolon/newline rows or a JSON list of ``[time, value]`` pairs."""
    mode = normalize_sweep_mode(source_mode)
    unit = "V" if mode == "voltage" else "A"
    text = str(value or "").strip()
    if not text:
        raise ValueError("Drive profile needs at least one time/value row.")
    rows: Sequence[object]
    if text.startswith("["):
        loaded = json.loads(text)
        if not isinstance(loaded, list):
            raise ValueError("Drive profile JSON must be a list of [time, value] rows.")
        rows = loaded
    else:
        rows = [row for row in re.split(r"[;\r\n]+", text) if row.strip()]
    points: list[KeithleyDrivePoint] = []
    for index, row in enumerate(rows, start=1):
        if isinstance(row, (list, tuple)) and len(row) == 2:
            raw_time, raw_value = row
        else:
            columns = [part.strip() for part in str(row).split(",")]
            if len(columns) != 2:
                raise ValueError(f"Drive profile row {index} must contain time,value.")
            raw_time, raw_value = columns
        time_s = parse_si_value(raw_time, "s")
        drive_value = parse_si_value(raw_value, unit)
        if not math.isfinite(time_s) or not math.isfinite(drive_value):
            raise ValueError(f"Drive profile row {index} must contain finite values.")
        if time_s < 0:
            raise ValueError("Drive profile times must be nonnegative.")
        if points and time_s <= points[-1].time_s:
            raise ValueError("Drive profile times must be strictly increasing.")
        points.append(KeithleyDrivePoint(float(time_s), float(drive_value)))
    if not points:
        raise ValueError("Drive profile needs at least one time/value row.")
    if points[0].time_s != 0:
        raise ValueError("Drive profile must start at time 0 s.")
    return tuple(points)


def parse_drive_profile_document(value: object, *, source_mode: object = "voltage") -> tuple[KeithleyDrivePoint, ...]:
    """Parse an imported JSON, CSV, TSV or text two-column time/value document."""
    text = str(value or "").strip()
    try:
        return parse_drive_profile(text, source_mode=source_mode)
    except (ValueError, TypeError, json.JSONDecodeError) as original_error:
        original_message = str(original_error)
    if text.startswith("["):
        try:
            payload = json.loads(text)
            if isinstance(payload, list) and payload and all(isinstance(item, Mapping) for item in payload):
                rows = [
                    [item.get("time_s", item.get("time")), item.get("value")]
                    for item in payload
                ]
                return parse_drive_profile(json.dumps(rows), source_mode=source_mode)
        except (ValueError, TypeError, json.JSONDecodeError):
            pass
    for delimiter in ("\t", ",", ";"):
        rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)
                if row and any(cell.strip() for cell in row)]
        if not rows or any(len(row) != 2 for row in rows):
            continue
        first = tuple(cell.strip().lower() for cell in rows[0])
        if any("time" in cell for cell in first) and any("value" in cell or "drive" in cell for cell in first):
            rows = rows[1:]
        if not rows:
            continue
        try:
            return parse_drive_profile(json.dumps(rows), source_mode=source_mode)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    raise ValueError(f"Could not import a two-column time/value profile: {original_message}")


def drive_profile_from_params(params: Mapping[str, object]) -> tuple[KeithleyDrivePoint, ...]:
    return parse_drive_profile(params.get("profile_points", ""), source_mode=params.get("source_mode", "voltage"))


def drive_profile_from_laser_waveform(
    params: Mapping[str, object], *, source_mode: object = "voltage"
) -> tuple[KeithleyDrivePoint, ...]:
    """Project the shared laser waveform's voltage or current channel into a Keithley profile."""
    from .waveform import parse_waveform_points

    mode = normalize_sweep_mode(source_mode)
    return tuple(
        KeithleyDrivePoint(
            point.time_ms / 1000.0,
            point.current_a if mode == "current" else point.voltage_v,
        )
        for point in parse_waveform_points(params)
    )


def interpolate_drive_profile(points: Sequence[KeithleyDrivePoint], time_s: float) -> float:
    """Return the piecewise-linear drive value at ``time_s``; endpoints are held outside the profile."""
    if not points:
        raise ValueError("Drive profile needs at least one point.")
    if time_s < points[0].time_s:
        return points[0].value
    index = bisect_right([point.time_s for point in points], time_s) - 1
    if index >= len(points) - 1 or points[index].time_s == time_s:
        return points[index].value
    first, second = points[index], points[index + 1]
    ratio = (time_s - first.time_s) / (second.time_s - first.time_s)
    return first.value + ratio * (second.value - first.value)


def repeat_drive_profile(
    points: Sequence[KeithleyDrivePoint], cycles: object = 1
) -> tuple[KeithleyDrivePoint, ...]:
    """Repeat a profile, retaining duplicate boundary times as right-continuous reset edges."""
    try:
        numeric_count = float(str(cycles).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Waveform cycles must be a positive integer.") from exc
    if not math.isfinite(numeric_count) or not numeric_count.is_integer() or numeric_count < 1:
        raise ValueError("Waveform cycles must be a positive integer.")
    count = int(numeric_count)
    if not points:
        raise ValueError("Drive profile needs at least one point.")
    if count == 1:
        return tuple(points)
    duration = points[-1].time_s - points[0].time_s
    if duration <= 0:
        raise ValueError("A repeated waveform must have a positive duration.")
    repeated: list[KeithleyDrivePoint] = []
    for cycle in range(count):
        offset = cycle * duration
        repeated.extend(KeithleyDrivePoint(point.time_s + offset, point.value) for point in points)
    return tuple(repeated)


def serialize_drive_profile(points: Sequence[KeithleyDrivePoint]) -> str:
    return ";".join(f"{point.time_s:.12g},{point.value:.12g}" for point in points)
