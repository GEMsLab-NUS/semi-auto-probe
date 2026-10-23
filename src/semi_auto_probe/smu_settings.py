"""Shared Keithley settings, SI parsing and lazy segment sweep planning."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from collections.abc import Sequence

VOLTAGE_RANGES = (0.02, 0.2, 2.0, 20.0, 200.0)
CURRENT_RANGES = (1e-8, 1e-7, 1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0)
FUNCTION_LABELS = {'voltage': 'Source V / Measure I', 'current': 'Source I / Measure V'}
SI_FACTORS = {'': 1.0, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'µ': 1e-6, 'μ': 1e-6,
              'm': 1e-3, 'k': 1e3, 'M': 1e6}


def parse_si_value(text, unit):
    """Parse a number with an optional SI prefix and dimension-checked unit."""
    match = re.fullmatch(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([pnumkMµμ]?)\s*([VAva]?)\s*', str(text))
    if not match:
        raise ValueError(f'Enter a finite {unit} value, e.g. 100m, 10u{unit}, or 1e-6.')
    number, prefix, supplied_unit = match.groups()
    if supplied_unit and supplied_unit.upper() != unit:
        raise ValueError(f'Expected {unit}, received {supplied_unit}.')
    value = float(number) * SI_FACTORS[prefix]
    if not math.isfinite(value):
        raise ValueError('Value must be finite.')
    return value


def format_si_value(value, unit):
    if value == 0:
        return f'0 {unit}'
    for scale, prefix in ((1e6, 'M'), (1e3, 'k'), (1, ''), (1e-3, 'm'), (1e-6, 'u'), (1e-9, 'n'), (1e-12, 'p')):
        if abs(value) >= scale * (1 - 1e-12):
            return f'{value / scale:.6g} {prefix}{unit}'
    return f'{value:.6g} {unit}'


class StableReading:
    """Fixed decimal places, sign and SI-prefix hysteresis for live readouts."""
    def __init__(self, unit):
        self.unit = unit
        self.scale = None

    def format(self, value):
        if not math.isfinite(value):
            return f'--- {self.unit}'
        scales = ((1e6, 'M'), (1e3, 'k'), (1., ''), (1e-3, 'm'),
                  (1e-6, 'u'), (1e-9, 'n'), (1e-12, 'p'))
        magnitude = abs(value)
        if self.scale is None or (magnitude and not .9 <= magnitude / self.scale[0] < 1000):
            self.scale = next((item for item in scales if magnitude >= item[0]),
                              (1e-12, 'p') if magnitude else (1., ''))
        scaled = value / self.scale[0]
        if abs(scaled) < .0005:
            scaled = 0.
        return f'{scaled:+08.3f} {self.scale[1]}{self.unit}'


MAX_SWEEP_POINTS = 10_000_000
MAX_SWEEP_SEGMENTS = 1_000_000


@dataclass(frozen=True, eq=False)
class SweepPoints(Sequence):
    """Constant-memory, random-access piecewise linear sweep."""
    start: float
    targets: tuple[float, ...]
    step: float
    leg_steps: tuple[int, ...]
    segments: int

    def __len__(self):
        cycles, remainder = divmod(self.segments, len(self.targets))
        return 1 + cycles * sum(self.leg_steps) + sum(self.leg_steps[:remainder])

    def _leg_at(self, index):
        """Return the absolute segment, cycle leg, and one-based leg offset."""
        cycle_points = sum(self.leg_steps)
        cycle, position = divmod(index - 1, cycle_points)
        for leg, leg_points in enumerate(self.leg_steps):
            if position < leg_points:
                return cycle * len(self.targets) + leg, leg, position + 1
            position -= leg_points
        raise IndexError(index)  # pragma: no cover - divmod keeps position in range

    def segment_for_index(self, index):
        """Return the zero-based segment containing a point in this plan."""
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return 0 if index == 0 else self._leg_at(index)[0]

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[i] for i in range(*index.indices(len(self))))
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        if index == 0:
            return self.start
        _, leg, offset = self._leg_at(index)
        start = self.start if leg == 0 else self.targets[leg - 1]
        end, steps = self.targets[leg], self.leg_steps[leg]
        if offset == steps:
            return end
        value = start + (self.step if end > start else -self.step) * offset
        return min(value, end) if end > start else max(value, end)

    def __eq__(self, other):
        if not isinstance(other, Sequence):
            return NotImplemented
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))


@dataclass(frozen=True)
class ManualSweep:
    start: float = 0.
    stop: float = 1.
    step: float = .05
    delay: float = .02
    low: float = -1.
    direction: str = 'up'
    segments: int = 1

    def values(self, settings):
        # stop is the upper bound (Top), retained as the internal field name.
        if not all(math.isfinite(v) for v in (self.start, self.stop, self.step, self.delay, self.low)):
            raise ValueError('Sweep parameters must be finite.')
        if self.step <= 0 or not 0 <= self.delay <= 60:
            raise ValueError('Step must be positive; settling time must be 0-60 s.')
        if self.direction not in {'up', 'down'}:
            raise ValueError('Select an initial direction: up or down.')
        if isinstance(self.segments, bool) or not isinstance(self.segments, int) or not 1 <= self.segments <= MAX_SWEEP_SEGMENTS:
            raise ValueError(f'Segments must be an integer from 1 to {MAX_SWEEP_SEGMENTS:,}.')
        if not self.low < self.stop or not self.low <= self.start <= self.stop:
            raise ValueError('Require Low < Top and Low <= Start <= Top.')
        if self.step > self.stop - self.low:
            raise ValueError('Step must not exceed the Low-to-Top span.')
        if (self.start == self.stop and self.direction == 'up') or (self.start == self.low and self.direction == 'down'):
            raise ValueError('At a boundary, choose an initial direction toward the other boundary.')
        first, other = (self.stop, self.low) if self.direction == 'up' else (self.low, self.stop)
        def steps(span):
            ratio = span / self.step
            if not math.isfinite(ratio) or ratio > MAX_SWEEP_POINTS:
                raise ValueError(f'Sweep exceeds {MAX_SWEEP_POINTS:,} total points; increase step or reduce segments.')
            # Absorb floating-point noise at exact integer step counts.
            nearest = round(ratio)
            return max(1, nearest if math.isclose(ratio, nearest, rel_tol=0, abs_tol=1e-9) else math.ceil(ratio))
        # One segment ends at one feature point. A complete cycle returns to
        # Start before visiting the opposite boundary:
        #   up:   Top -> Start -> Low -> Start
        #   down: Low -> Start -> Top -> Start
        # Remove zero-length legs when Start is itself a boundary.
        targets = []
        leg_steps = []
        previous = self.start
        for target in (first, self.start, other, self.start):
            if target == previous:
                continue
            targets.append(target)
            leg_steps.append(steps(abs(target - previous)))
            previous = target
        values = SweepPoints(self.start, tuple(targets), self.step, tuple(leg_steps), self.segments)
        if len(values) > MAX_SWEEP_POINTS:
            raise ValueError(f'Sweep exceeds {MAX_SWEEP_POINTS:,} total points; increase step or reduce segments.')
        # All legs are monotonic between these extrema. ManualSettings safety
        # constraints depend on magnitude, so checking visited extrema covers
        # the intermediate levels without iterating millions of points.
        for value in (self.start, first, other):
            replace(settings, value=value).validate()
        return values


def supported_ranges(quantity, terminal='rear'):
    if quantity == 'voltage':
        return VOLTAGE_RANGES
    if quantity == 'current':
        return CURRENT_RANGES if terminal == 'rear' else CURRENT_RANGES[2:]
    raise ValueError('Unsupported range quantity.')


def range_options(quantity, terminal='rear'):
    unit = 'V' if quantity == 'voltage' else 'A'
    return {'Auto': None, **{format_si_value(value, unit): value for value in supported_ranges(quantity, terminal)}}


@dataclass(frozen=True)
class ManualSettings:
    mode: str = 'voltage'
    value: float = 0.0
    limit: float = 1e-5
    terminal: str = 'rear'
    source_range: float | None = None
    measure_range: float | None = None
    nplc: float = 1.0
    autozero: bool = True

    def validate(self):
        if not math.isfinite(self.nplc) or not 0.01 <= self.nplc <= 10:
            raise ValueError("2450 NPLC must be between 0.01 and 10 power-line cycles.")
        if not isinstance(self.autozero, bool):
            raise ValueError("Auto zero must be ON or OFF.")
        if self.mode not in {'voltage', 'current'} or self.terminal not in {'front', 'rear'}:
            raise ValueError('Unsupported source mode or terminal.')
        if not all(math.isfinite(x) for x in (self.value, self.limit)):
            raise ValueError('Setpoint and protection limit must be finite.')
        voltage, current = ((abs(self.value), self.limit) if self.mode == 'voltage'
                            else (self.limit, abs(self.value)))
        if self.limit <= 0 or voltage > 200 or current > 1:
            raise ValueError('2450 limits: +/-200 V, +/-1 A; protection must be positive.')
        if voltage > 20 and current > 0.1:
            raise ValueError('2450: above 20 V, current must not exceed 0.1 A.')
        measured = 'current' if self.mode == 'voltage' else 'voltage'
        for label, value, quantity in (('Source', self.source_range, self.mode),
                                        ('Measurement', self.measure_range, measured)):
            if value is not None and value not in supported_ranges(quantity, self.terminal):
                raise ValueError(f'{label} range is unsupported for {quantity} on {self.terminal} terminals.')
        if self.measure_range is not None:
            unit = 'A' if self.mode == 'voltage' else 'V'
            low, high = self.measure_range * .001, self.measure_range * 1.05
            if self.limit <= low or self.limit > high * (1 + 1e-12):
                raise ValueError(
                    f'2450 protection limit {format_si_value(self.limit, unit)} is incompatible with '
                    f'fixed measurement range {format_si_value(self.measure_range, unit)}. '
                    f'Use a limit > {format_si_value(low, unit)} and <= {format_si_value(high, unit)}, '
                    'or select a compatible measurement range / Auto. No settings sent.')
        if self.source_range is not None and abs(self.value) > self.source_range * 1.05:
            raise ValueError('Source value exceeds the selected source range (105% full scale).')
        source_range = self.source_range or next(r for r in supported_ranges(self.mode, self.terminal)
                                                if abs(self.value) <= 1.05 * r)
        # The 200 V and 1 A ranges cannot be combined, even at a small setpoint.
        v_range, i_range = ((source_range, self.measure_range) if self.mode == 'voltage'
                            else (self.measure_range, source_range))
        if v_range == 200 and i_range == 1:
            raise ValueError('The 200 V and 1 A ranges cannot be combined.')
        if self.mode == 'voltage' and source_range == 200 and self.limit > 0.1:
            raise ValueError('The 200 V source range requires a current limit <= 100 mA.')
        if self.mode == 'current' and source_range == 1 and self.limit > 20:
            raise ValueError('The 1 A source range requires a voltage limit <= 20 V.')




def reduce_workflow_limit(params):
    """Return an explicit UI protection reduction for a newly selected range."""
    mode = params.get('source_mode', params.get('sweep_mode', 'voltage'))
    unit = 'A' if mode == 'voltage' else 'V'
    key = 'current_limit_a' if unit == 'A' else 'voltage_limit_v'
    try:
        selected = parse_si_value(params.get('measure_range', 'auto'), unit)
        previous = parse_si_value(params.get(key, '1e-3' if unit == 'A' else '20'), unit)
    except ValueError:
        return None
    if selected > 0 and previous > selected * 1.05 * (1 + 1e-12):
        return key, f'{selected:.12g}'
    return None
