from __future__ import annotations

import json
import math
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable

from .gds_stage_mapper import AuxiliaryPointOverlay, AffineCoordinateMapper, GDSCanvasViewer, GDSLayoutModel, MatrixOverlay, ToggleSwitch
from .img_matrix import fov_polygon_for_stage_target

AUTOTEST_PREVIEW_INTERVAL_MS = 45
AUTOTEST_OVERLAY_REDRAW_INTERVAL_MS = 90
PROBE_ASSIST_PROBES: tuple[tuple[str, str, str, str, str], ...] = (
    ("圆", "#34d399", "ring", "100", "0"),
    ("Low", "#38bdf8", "square", "-100", "0"),
    ("山", "#f43f5e", "diamond", "0", "100"),
)
WOBBTEST_FLOW_CARD_HEIGHT_Z = 464
WOBBTEST_FLOW_CARD_HEIGHT_ZXY = 544


@dataclass(frozen=True)
class AutoTestFlowStep:
    type_id: str
    params: dict[str, str]


@dataclass(frozen=True)
class AutoTestSettings:
    origin_u: float
    origin_v: float
    u_vector_u: float
    u_vector_v: float
    v_vector_u: float
    v_vector_v: float
    rows: int
    cols: int
    fov_width_um: float
    fov_height_um: float
    z_down_margin_um: float
    z_up_fast_percent: float
    z_fast_speed_percent: int
    z_slow_speed_percent: int
    name_pattern: str
    z_wobble_um: float = 0.0
    z_wobble_cycles: int = 0
    z_offset_um: float = 0.0
    measurement_steps: tuple[str, ...] = ()
    measurement_flow: tuple[AutoTestFlowStep, ...] = ()

    def normalized(self) -> "AutoTestSettings":
        values = (
            self.origin_u,
            self.origin_v,
            self.u_vector_u,
            self.u_vector_v,
            self.v_vector_u,
            self.v_vector_v,
            self.fov_width_um,
            self.fov_height_um,
            self.z_down_margin_um,
            self.z_up_fast_percent,
            self.z_fast_speed_percent,
            self.z_slow_speed_percent,
            self.z_wobble_um,
            self.z_offset_um,
        )
        if any(not math.isfinite(float(value)) for value in values):
            raise ValueError("AutoTest coordinates and dimensions must be finite.")
        rows = int(self.rows)
        cols = int(self.cols)
        if rows <= 0 or cols <= 0:
            raise ValueError("AutoTest rows and columns must be positive.")
        if rows > 500 or cols > 500:
            raise ValueError("AutoTest rows and columns are limited to 500.")
        if self.fov_width_um <= 0 or self.fov_height_um <= 0:
            raise ValueError("AutoTest FOV dimensions must be positive.")
        if math.hypot(self.u_vector_u, self.u_vector_v) <= 0:
            raise ValueError("AutoTest U vector must be non-zero.")
        if math.hypot(self.v_vector_u, self.v_vector_v) <= 0:
            raise ValueError("AutoTest V vector must be non-zero.")
        if self.z_down_margin_um < 0:
            raise ValueError("AutoTest Z down margin must be zero or positive.")
        if self.z_up_fast_percent < 0 or self.z_up_fast_percent > 100:
            raise ValueError("AutoTest Z up fast range must be between 0 and 100 percent.")
        z_fast_speed_percent = int(self.z_fast_speed_percent)
        z_slow_speed_percent = int(self.z_slow_speed_percent)
        if z_fast_speed_percent < 0 or z_fast_speed_percent > 100:
            raise ValueError("AutoTest fast Z speed must be between 0 and 100 percent.")
        if z_slow_speed_percent < 0 or z_slow_speed_percent > 100:
            raise ValueError("AutoTest slow Z speed must be between 0 and 100 percent.")
        z_wobble_um = float(self.z_wobble_um)
        z_wobble_cycles = int(float(self.z_wobble_cycles))
        z_offset_um = float(self.z_offset_um)
        if z_wobble_um < 0:
            raise ValueError("AutoTest Z wobble must be zero or positive.")
        if z_wobble_cycles < 0:
            raise ValueError("AutoTest Z wobble cycles must be zero or positive.")
        name_pattern = str(self.name_pattern).strip()
        if not name_pattern:
            raise ValueError("AutoTest point name pattern is required.")
        return AutoTestSettings(
            origin_u=float(self.origin_u),
            origin_v=float(self.origin_v),
            u_vector_u=float(self.u_vector_u),
            u_vector_v=float(self.u_vector_v),
            v_vector_u=float(self.v_vector_u),
            v_vector_v=float(self.v_vector_v),
            rows=rows,
            cols=cols,
            fov_width_um=float(self.fov_width_um),
            fov_height_um=float(self.fov_height_um),
            z_down_margin_um=float(self.z_down_margin_um),
            z_up_fast_percent=float(self.z_up_fast_percent),
            z_fast_speed_percent=z_fast_speed_percent,
            z_slow_speed_percent=z_slow_speed_percent,
            name_pattern=name_pattern,
            z_wobble_um=z_wobble_um,
            z_wobble_cycles=z_wobble_cycles,
            z_offset_um=z_offset_um,
            measurement_steps=tuple(str(step) for step in self.measurement_steps),
            measurement_flow=tuple(
                AutoTestFlowStep(str(step.type_id), {str(key): str(value) for key, value in step.params.items()})
                for step in self.measurement_flow
            ),
        )


@dataclass(frozen=True)
class AutoTestPoint:
    row: int
    col: int
    order: int
    name: str
    u: float
    v: float
    stage_x_um: float
    stage_y_um: float
    fov_polygon_gds: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class AutoTestPointSpec:
    name: str
    u: float
    v: float
    row: int = 0
    col: int = 0


@dataclass(frozen=True)
class AutoTestFlowParam:
    key: str
    label: str
    default: str


@dataclass(frozen=True)
class AutoTestFlowDefinition:
    type_id: str
    title: str
    description: str
    accent: str
    parameters: tuple[AutoTestFlowParam, ...]


@dataclass
class AutoTestFlowCard:
    card_id: str
    type_id: str
    params: dict[str, str]
    expanded: bool = False


AUTOTEST_FLOW_DEFINITIONS: tuple[AutoTestFlowDefinition, ...] = (
    AutoTestFlowDefinition(
        "wait",
        "Entity Pause",
        "Hold before the next measurement step.",
        "#38bdf8",
        (
            AutoTestFlowParam("duration_s", "Duration (s)", "1.0"),
            AutoTestFlowParam("reason", "Reason", "Settle"),
        ),
    ),
    AutoTestFlowDefinition(
        "iv",
        "Keithley IV",
        "Run a Keithley 2450 voltage or current sweep.",
        "#34d399",
        (
            AutoTestFlowParam("resource", "VISA resource", "GPIB0::18::INSTR"),
            AutoTestFlowParam("output_terminal", "Output terminal", "rear"),
            AutoTestFlowParam("sweep_mode", "Sweep mode", "voltage"),
            AutoTestFlowParam("bidirectional", "Bidirectional", "false"),
            AutoTestFlowParam("start", "Start", "0"),
            AutoTestFlowParam("stop", "Stop", "1"),
            AutoTestFlowParam("step", "Step", "0.05"),
            AutoTestFlowParam("voltage_limit_v", "Voltage limit V", "20"),
            AutoTestFlowParam("current_limit_a", "Current limit A", "1e-3"),
            AutoTestFlowParam("source_delay_s", "Delay s", "0.02"),
            AutoTestFlowParam("nplc", "NPLC", "1"),
            AutoTestFlowParam("output_statistics", "Output stats", "true"),
            AutoTestFlowParam("resistance_method", "Resistance method", "linear_fit"),
            AutoTestFlowParam("device_length_um", "Length um", "0"),
            AutoTestFlowParam("device_width_um", "Width um", "0"),
            AutoTestFlowParam("film_thickness_nm", "Thickness nm", "0"),
            AutoTestFlowParam("output_off_after", "Output off after", "true"),
        ),
    ),
    AutoTestFlowDefinition(
        "wobb_test",
        "WobbTest",
        "Optimize contact by wobbing Z, optionally followed by XY.",
        "#f97316",
        (
            AutoTestFlowParam("mode", "Mode", "Z"),
            AutoTestFlowParam("resource", "VISA resource", "GPIB0::18::INSTR"),
            AutoTestFlowParam("output_terminal", "Output terminal", "rear"),
            AutoTestFlowParam("bias_v", "Bias V", "0.1"),
            AutoTestFlowParam("current_limit_a", "Current limit A", "1e-5"),
            AutoTestFlowParam("z_lower_um", "Z lower um", "-2"),
            AutoTestFlowParam("z_upper_um", "Z upper um", "2"),
            AutoTestFlowParam("z_step_um", "Z step um", "0.5"),
            AutoTestFlowParam("xy_range_um", "XY range um", "2"),
            AutoTestFlowParam("xy_step_um", "XY step um", "1"),
            AutoTestFlowParam("xy_pattern", "XY pattern", "square"),
            AutoTestFlowParam("settle_s", "Settle s", "0.05"),
            AutoTestFlowParam("sample_count", "Samples", "5"),
            AutoTestFlowParam("nplc", "NPLC", "10"),
            AutoTestFlowParam("best_current", "Best current", "max_abs"),
        ),
    ),
    AutoTestFlowDefinition(
        "transfer",
        "Transfer Test",
        "Run a transfer sweep placeholder.",
        "#a78bfa",
        (
            AutoTestFlowParam("gate_start_v", "Gate start V", "-1"),
            AutoTestFlowParam("gate_stop_v", "Gate stop V", "1"),
            AutoTestFlowParam("gate_step_v", "Gate step V", "0.05"),
            AutoTestFlowParam("drain_v", "Drain V", "0.1"),
        ),
    ),
    AutoTestFlowDefinition(
        "it",
        "IT Test",
        "Run a current-time measurement placeholder.",
        "#fbbf24",
        (
            AutoTestFlowParam("duration_s", "Duration (s)", "10"),
            AutoTestFlowParam("sample_interval_s", "Sample interval (s)", "0.1"),
            AutoTestFlowParam("bias_v", "Bias V", "0.1"),
        ),
    ),
    AutoTestFlowDefinition(
        "light_control",
        "Light Control",
        "Set illumination state and intensity.",
        "#60a5fa",
        (
            AutoTestFlowParam("state", "State", "on"),
            AutoTestFlowParam("intensity_percent", "Intensity %", "50"),
        ),
    ),
    AutoTestFlowDefinition(
        "light_pulse",
        "Light Pulse",
        "Emit a timed light pulse sequence.",
        "#fb7185",
        (
            AutoTestFlowParam("width_ms", "Width ms", "10"),
            AutoTestFlowParam("period_ms", "Period ms", "100"),
            AutoTestFlowParam("count", "Count", "1"),
            AutoTestFlowParam("intensity_percent", "Intensity %", "100"),
        ),
    ),
    AutoTestFlowDefinition(
        "wavelength",
        "Switch Wavelength",
        "Switch light wavelength before measuring.",
        "#22d3ee",
        (
            AutoTestFlowParam("wavelength_nm", "Wavelength nm", "532"),
            AutoTestFlowParam("settle_s", "Settle (s)", "0.5"),
        ),
    ),
    AutoTestFlowDefinition(
        "photo",
        "Capture Photo",
        "Save a microscope image at this point.",
        "#f472b6",
        (
            AutoTestFlowParam("label", "Image label", "{point}"),
        ),
    ),
)


def autotest_flow_definitions_by_type() -> dict[str, AutoTestFlowDefinition]:
    return {definition.type_id: definition for definition in AUTOTEST_FLOW_DEFINITIONS}


def create_autotest_flow_card(type_id: str, card_id: str, *, expanded: bool = False) -> AutoTestFlowCard:
    definition = autotest_flow_definitions_by_type()[type_id]
    return AutoTestFlowCard(
        card_id=card_id,
        type_id=type_id,
        params={param.key: param.default for param in definition.parameters},
        expanded=expanded,
    )


def summarize_autotest_flow(cards: tuple[AutoTestFlowCard, ...] | list[AutoTestFlowCard]) -> str:
    definitions = autotest_flow_definitions_by_type()
    if not cards:
        return "Measurement flow: not configured"
    labels = [definitions.get(card.type_id, definitions["wait"]).title for card in cards]
    return "Measurement flow: " + " -> ".join(labels)


def legacy_measurement_steps_from_flow(cards: tuple[AutoTestFlowCard, ...] | list[AutoTestFlowCard]) -> tuple[str, ...]:
    steps: list[str] = []
    for card in cards:
        if card.type_id == "wait":
            steps.append("pause")
        elif card.type_id == "photo":
            steps.append("photo")
    return tuple(steps)


def measurement_flow_steps_from_cards(cards: tuple[AutoTestFlowCard, ...] | list[AutoTestFlowCard]) -> tuple[AutoTestFlowStep, ...]:
    return tuple(
        AutoTestFlowStep(
            type_id=str(card.type_id),
            params={str(key): str(value) for key, value in card.params.items()},
        )
        for card in cards
    )


def contact_wobble_offsets_um(wobble_um: float, wobble_cycles: int, offset_um: float) -> tuple[float, ...]:
    offsets: list[float] = []
    wobble = max(0.0, float(wobble_um))
    cycles = max(0, int(wobble_cycles))
    for _cycle in range(cycles):
        if wobble <= 0:
            break
        offsets.extend((wobble, -wobble, 0.0))
    final_offset = float(offset_um)
    if final_offset != 0.0 or (offsets and offsets[-1] != final_offset):
        if not offsets or offsets[-1] != final_offset:
            offsets.append(final_offset)
    return tuple(offsets)


def wobbtest_z_offsets_um(lower_um: float, upper_um: float, step_um: float) -> tuple[float, ...]:
    lower = min(0.0, float(lower_um))
    upper = max(0.0, float(upper_um))
    step = abs(float(step_um))
    if not all(math.isfinite(value) for value in (lower, upper, step)) or step <= 0:
        raise ValueError("WobbTest Z step must be a positive finite number.")
    negative_steps = int(math.floor(abs(lower) / step + 1e-9))
    positive_steps = int(math.floor(abs(upper) / step + 1e-9))
    offsets: list[float] = [0.0]
    paired_steps = min(negative_steps, positive_steps)
    for index in range(1, paired_steps + 1):
        offsets.extend((index * step, -index * step))
    if positive_steps > paired_steps:
        offsets.extend(index * step for index in range(paired_steps + 1, positive_steps + 1))
    if negative_steps > paired_steps:
        offsets.extend(-index * step for index in range(paired_steps + 1, negative_steps + 1))
    if upper > positive_steps * step + step * 1e-6:
        offsets.append(upper)
    if abs(lower) > negative_steps * step + step * 1e-6:
        offsets.append(lower)
    return tuple(dict.fromkeys(round(offset, 12) for offset in offsets))


def wobbtest_xy_offsets_um(range_um: float, step_um: float, pattern: str) -> tuple[tuple[float, float], ...]:
    radius = abs(float(range_um))
    step = abs(float(step_um))
    if not all(math.isfinite(value) for value in (radius, step)) or step <= 0:
        raise ValueError("WobbTest XY step must be a positive finite number.")
    if radius == 0:
        return ((0.0, 0.0),)
    normalized = str(pattern or "square").strip().lower()
    if normalized == "corners":
        return ((0.0, 0.0), (radius, radius), (-radius, radius), (-radius, -radius), (radius, -radius), (0.0, 0.0))
    offsets: list[tuple[float, float]] = [(0.0, 0.0)]
    rings = max(1, int(math.ceil(radius / step)))
    for ring in range(1, rings + 1):
        r = min(radius, ring * step)
        offsets.extend(((r, 0.0), (r, r), (0.0, r), (-r, r), (-r, 0.0), (-r, -r), (0.0, -r), (r, -r)))
        if normalized == "square":
            break
    offsets.append((0.0, 0.0))
    seen: set[tuple[float, float]] = set()
    unique: list[tuple[float, float]] = []
    for dx, dy in offsets:
        key = (round(dx, 12), round(dy, 12))
        if key not in seen or key == (0.0, 0.0):
            seen.add(key)
            unique.append(key)
    return tuple(unique)


def generate_autotest_points(settings: AutoTestSettings, mapper: AffineCoordinateMapper) -> tuple[AutoTestPoint, ...]:
    normalized = settings.normalized()
    points: list[AutoTestPoint] = []
    order = 1
    for row in range(normalized.rows):
        for col in range(normalized.cols):
            u = normalized.origin_u + col * normalized.u_vector_u + row * normalized.v_vector_u
            v = normalized.origin_v + col * normalized.u_vector_v + row * normalized.v_vector_v
            stage_x_um, stage_y_um = mapper.gds_to_stage(u, v)
            points.append(
                AutoTestPoint(
                    row=row,
                    col=col,
                    order=order,
                    name=compile_autotest_point_name(normalized.name_pattern, i_index=col, j_index=row),
                    u=u,
                    v=v,
                    stage_x_um=stage_x_um,
                    stage_y_um=stage_y_um,
                    fov_polygon_gds=fov_polygon_for_stage_target(
                        mapper,
                        stage_x_um,
                        stage_y_um,
                        normalized.fov_width_um,
                        normalized.fov_height_um,
                    ),
                )
            )
            order += 1
    return tuple(points)


def generate_autotest_points_from_specs(
    specs: tuple[AutoTestPointSpec, ...] | list[AutoTestPointSpec],
    settings: AutoTestSettings,
    mapper: AffineCoordinateMapper,
) -> tuple[AutoTestPoint, ...]:
    normalized = settings.normalized()
    points: list[AutoTestPoint] = []
    for order, spec in enumerate(specs, start=1):
        if not math.isfinite(float(spec.u)) or not math.isfinite(float(spec.v)):
            raise ValueError("Imported AutoTest point coordinates must be finite.")
        stage_x_um, stage_y_um = mapper.gds_to_stage(float(spec.u), float(spec.v))
        name = str(spec.name).strip() or f"Point{order:03d}"
        points.append(
            AutoTestPoint(
                row=int(spec.row),
                col=int(spec.col),
                order=order,
                name=name,
                u=float(spec.u),
                v=float(spec.v),
                stage_x_um=stage_x_um,
                stage_y_um=stage_y_um,
                fov_polygon_gds=fov_polygon_for_stage_target(
                    mapper,
                    stage_x_um,
                    stage_y_um,
                    normalized.fov_width_um,
                    normalized.fov_height_um,
                ),
            )
        )
    return tuple(points)


def autotest_point_specs_from_json_payload(payload: object) -> tuple[AutoTestPointSpec, ...]:
    if isinstance(payload, list):
        raw_points = payload
    elif isinstance(payload, dict):
        raw_points = payload.get("points")
    else:
        raise ValueError("AutoTest point list must be a JSON object or array.")
    if not isinstance(raw_points, list):
        raise ValueError("AutoTest point list JSON must contain a points array.")
    specs: list[AutoTestPointSpec] = []
    for index, item in enumerate(raw_points, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Point {index} must be an object.")
        try:
            u = float(item["u"])
            v = float(item["v"])
        except KeyError as exc:
            raise ValueError(f"Point {index} is missing {exc.args[0]}.") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Point {index} must have numeric u/v coordinates.") from exc
        if not math.isfinite(u) or not math.isfinite(v):
            raise ValueError(f"Point {index} must have finite u/v coordinates.")
        name = str(item.get("name") or f"Point{index:03d}").strip() or f"Point{index:03d}"
        row = int(float(item.get("row", index - 1)))
        col = int(float(item.get("col", 0)))
        specs.append(AutoTestPointSpec(name=name, u=u, v=v, row=row, col=col))
    if not specs:
        raise ValueError("AutoTest point list is empty.")
    return tuple(specs)


def autotest_point_specs_payload(
    specs: tuple[AutoTestPointSpec, ...] | list[AutoTestPointSpec],
    *,
    source: str = "manual",
    generator: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "format": "semi_auto_probe.autotest_point_list",
        "version": 1,
        "source": source,
        "points": [
            {
                "name": spec.name,
                "u": spec.u,
                "v": spec.v,
                "row": spec.row,
                "col": spec.col,
            }
            for spec in specs
        ],
    }
    if generator is not None:
        payload["generator"] = generator
    return payload


def generate_nested_autotest_point_specs(
    *,
    origin_u: float,
    origin_v: float,
    inner_u_vector_u: float,
    inner_u_vector_v: float,
    inner_v_vector_u: float,
    inner_v_vector_v: float,
    inner_cols: int,
    inner_rows: int,
    outer_u_vector_u: float,
    outer_u_vector_v: float,
    outer_v_vector_u: float,
    outer_v_vector_v: float,
    outer_cols: int,
    outer_rows: int,
    name_pattern: str,
) -> tuple[AutoTestPointSpec, ...]:
    counts = (inner_cols, inner_rows, outer_cols, outer_rows)
    if any(int(value) <= 0 for value in counts):
        raise ValueError("Nested AutoTest array counts must be positive.")
    values = (
        origin_u,
        origin_v,
        inner_u_vector_u,
        inner_u_vector_v,
        inner_v_vector_u,
        inner_v_vector_v,
        outer_u_vector_u,
        outer_u_vector_v,
        outer_v_vector_u,
        outer_v_vector_v,
    )
    if any(not math.isfinite(float(value)) for value in values):
        raise ValueError("Nested AutoTest array coordinates must be finite.")
    pattern = str(name_pattern).strip() or "Dev{bi}{bj}_{i}{j}"
    specs: list[AutoTestPointSpec] = []
    order = 1
    for outer_row in range(int(outer_rows)):
        for outer_col in range(int(outer_cols)):
            block_u = float(origin_u) + outer_col * float(outer_u_vector_u) + outer_row * float(outer_v_vector_u)
            block_v = float(origin_v) + outer_col * float(outer_u_vector_v) + outer_row * float(outer_v_vector_v)
            for inner_row in range(int(inner_rows)):
                for inner_col in range(int(inner_cols)):
                    u = block_u + inner_col * float(inner_u_vector_u) + inner_row * float(inner_v_vector_u)
                    v = block_v + inner_col * float(inner_u_vector_v) + inner_row * float(inner_v_vector_v)
                    name = compile_nested_autotest_point_name(
                        pattern,
                        block_i_index=outer_col,
                        block_j_index=outer_row,
                        i_index=inner_col,
                        j_index=inner_row,
                        order=order,
                    )
                    specs.append(
                        AutoTestPointSpec(
                            name=name,
                            u=u,
                            v=v,
                            row=outer_row * int(inner_rows) + inner_row,
                            col=outer_col * int(inner_cols) + inner_col,
                        )
                    )
                    order += 1
    return tuple(specs)


def compile_autotest_point_name(pattern: str, *, i_index: int, j_index: int) -> str:
    return str(pattern).replace("{i}", index_to_letters(i_index)).replace("{j}", str(j_index + 1))


def compile_nested_autotest_point_name(
    pattern: str,
    *,
    block_i_index: int,
    block_j_index: int,
    i_index: int,
    j_index: int,
    order: int,
) -> str:
    return (
        str(pattern)
        .replace("{bi}", index_to_letters(block_i_index))
        .replace("{bj}", str(block_j_index + 1))
        .replace("{i}", index_to_letters(i_index))
        .replace("{j}", str(j_index + 1))
        .replace("{n}", str(order))
    )


def index_to_letters(index: int) -> str:
    if index < 0:
        raise ValueError("Index must be zero or positive.")
    value = index
    letters = []
    while True:
        value, remainder = divmod(value, 26)
        letters.append(chr(ord("A") + remainder))
        if value == 0:
            break
        value -= 1
    return "".join(reversed(letters))


class RoundedSplitSlider(tk.Canvas):
    def __init__(
        self,
        parent: tk.Widget,
        variable: tk.DoubleVar,
        colors: dict[str, str],
        *,
        command: Callable[[], None] | None = None,
        width: int = 260,
        height: int = 30,
    ) -> None:
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=colors["surface"],
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.variable = variable
        self.colors = colors
        self.command = command
        self.track_left = "#22c7a9"
        self.track_right = "#223042"
        self.knob_fill = "#dffcf6"
        self.knob_outline = "#5eead4"
        self.variable.trace_add("write", lambda *_args: self._draw())
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Button-1>", self._set_from_event)
        self.bind("<B1-Motion>", self._set_from_event)
        self._draw()

    def _set_from_event(self, event: tk.Event) -> str:
        x0, x1 = self._track_bounds()
        if x1 <= x0:
            return "break"
        value = (min(max(float(event.x), x0), x1) - x0) / (x1 - x0) * 100.0
        self.variable.set(value)
        if self.command is not None:
            self.command()
        return "break"

    def _track_bounds(self) -> tuple[float, float]:
        return 14.0, max(float(self.winfo_width()) - 14.0, 14.0)

    def _draw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)
        x0, x1 = self._track_bounds()
        y = height / 2.0
        value = min(100.0, max(0.0, float(self.variable.get())))
        knob_x = x0 + (x1 - x0) * value / 100.0
        try:
            self.create_line(x0, y, knob_x, y, fill=self.track_left, width=9, capstyle=tk.ROUND)
            self.create_line(knob_x, y, x1, y, fill=self.track_right, width=9, capstyle=tk.ROUND)
        except tk.TclError:
            return
        radius = 8
        self.create_oval(
            knob_x - radius,
            y - radius,
            knob_x + radius,
            y + radius,
            fill=self.knob_fill,
            outline=self.knob_outline,
            width=2,
        )


class RangeSlider(tk.Canvas):
    def __init__(
        self,
        parent: tk.Widget,
        lower_var: tk.StringVar,
        upper_var: tk.StringVar,
        colors: dict[str, str],
        *,
        minimum: float = -20.0,
        maximum: float = 20.0,
        step: float = 0.5,
        command: Callable[[], None] | None = None,
        height: int = 38,
    ) -> None:
        super().__init__(
            parent,
            height=height,
            bg=colors["surface"],
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.lower_var = lower_var
        self.upper_var = upper_var
        self.colors = colors
        self.minimum = float(minimum)
        self.maximum = float(maximum)
        self.step = float(step)
        self.command = command
        self.active_thumb: str | None = None
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._lower_trace = self.lower_var.trace_add("write", lambda *_args: self._draw())
        self._upper_trace = self.upper_var.trace_add("write", lambda *_args: self._draw())
        self.bind("<Destroy>", self._on_destroy)
        self._draw()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        for variable, trace_name in ((self.lower_var, self._lower_trace), (self.upper_var, self._upper_trace)):
            try:
                variable.trace_remove("write", trace_name)
            except tk.TclError:
                pass

    def _track_bounds(self) -> tuple[float, float]:
        return 16.0, max(float(self.winfo_width()) - 16.0, 16.0)

    def _values(self) -> tuple[float, float]:
        try:
            lower = float(self.lower_var.get())
        except ValueError:
            lower = self.minimum
        try:
            upper = float(self.upper_var.get())
        except ValueError:
            upper = self.maximum
        lower = max(self.minimum, min(self.maximum, lower))
        upper = max(self.minimum, min(self.maximum, upper))
        if lower > upper:
            lower, upper = upper, lower
        return lower, upper

    def _value_to_x(self, value: float) -> float:
        x0, x1 = self._track_bounds()
        span = max(self.maximum - self.minimum, 1e-9)
        return x0 + (float(value) - self.minimum) / span * (x1 - x0)

    def _x_to_value(self, x: float) -> float:
        x0, x1 = self._track_bounds()
        ratio = (min(max(float(x), x0), x1) - x0) / max(x1 - x0, 1e-9)
        raw = self.minimum + ratio * (self.maximum - self.minimum)
        snapped = round(raw / self.step) * self.step
        return max(self.minimum, min(self.maximum, snapped))

    def _on_press(self, event: tk.Event) -> str:
        lower, upper = self._values()
        lower_x = self._value_to_x(lower)
        upper_x = self._value_to_x(upper)
        self.active_thumb = "lower" if abs(event.x - lower_x) <= abs(event.x - upper_x) else "upper"
        self._set_active_value(event.x)
        return "break"

    def _on_drag(self, event: tk.Event) -> str:
        self._set_active_value(event.x)
        return "break"

    def _on_release(self, _event: tk.Event) -> str:
        self.active_thumb = None
        return "break"

    def _set_active_value(self, x: float) -> None:
        lower, upper = self._values()
        value = self._x_to_value(x)
        if self.active_thumb == "lower":
            lower = min(value, upper)
        else:
            upper = max(value, lower)
        self.lower_var.set(f"{lower:.6g}")
        self.upper_var.set(f"{upper:.6g}")
        if self.command is not None:
            self.command()
        self._draw()

    def _draw(self) -> None:
        try:
            self.delete("all")
        except tk.TclError:
            return
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)
        x0, x1 = self._track_bounds()
        y = height / 2.0
        lower, upper = self._values()
        lower_x = self._value_to_x(lower)
        upper_x = self._value_to_x(upper)
        self.create_line(x0, y, x1, y, fill="#223042", width=9, capstyle=tk.ROUND)
        self.create_line(lower_x, y, upper_x, y, fill="#f97316", width=9, capstyle=tk.ROUND)
        for value in (-20, -10, 0, 10, 20):
            x = self._value_to_x(value)
            self.create_line(x, y + 9, x, y + 13, fill=self.colors["muted"], width=1)
            self.create_text(x, y + 20, text=str(value), fill=self.colors["muted"], font=("Segoe UI", 7), anchor="n")
        for x, label, active in ((lower_x, f"{lower:g}", self.active_thumb == "lower"), (upper_x, f"{upper:g}", self.active_thumb == "upper")):
            radius = 8 if active else 7
            self.create_oval(x - radius, y - radius, x + radius, y + radius, fill="#fff7ed", outline="#fdba74", width=2)
            self.create_text(x, y - 13, text=label, fill="#fed7aa", font=("Segoe UI Semibold", 8), anchor="s")


class AutoTestPanel:
    def __init__(
        self,
        parent: tk.Widget,
        colors: dict[str, str],
        *,
        get_stage_position_um: Callable[[], tuple[float, float] | tuple[float, float, float]],
        get_mapper: Callable[[], AffineCoordinateMapper | None],
        get_focusmap_ready: Callable[[], bool],
        get_layoutmap_ready: Callable[[], bool],
        get_microscope_preview: Callable[[], bytes | None] | None,
        fov_width_var: tk.StringVar,
        fov_height_var: tk.StringVar,
        start_run: Callable[[AutoTestSettings, tuple[AutoTestPoint, ...] | None], None],
        stop_run: Callable[[], None],
        set_status: Callable[[str], None] | None = None,
        on_overlay_changed: Callable[[list[MatrixOverlay]], None] | None = None,
        on_probe_assist_changed: Callable[[], None] | None = None,
    ) -> None:
        self.colors = colors
        self.get_stage_position_um = get_stage_position_um
        self.get_mapper = get_mapper
        self.get_focusmap_ready = get_focusmap_ready
        self.get_layoutmap_ready = get_layoutmap_ready
        self.get_microscope_preview = get_microscope_preview
        self.fov_width_var = fov_width_var
        self.fov_height_var = fov_height_var
        self.start_run = start_run
        self.stop_run = stop_run
        self.set_app_status = set_status
        self.on_overlay_changed = on_overlay_changed
        self.on_probe_assist_changed = on_probe_assist_changed
        self.model: GDSLayoutModel | None = None
        self.pending_pick: str | None = None
        self.selected_gds: tuple[float, float] | None = None
        self.microscope_photo: tk.PhotoImage | None = None
        self.microscope_payload_id: int | None = None
        self.status_poll_job: str | None = None
        self.microscope_poll_job: str | None = None
        self.preview_redraw_job: str | None = None
        self._preview_cache_key: tuple[object, ...] | None = None
        self._preview_cache_points: tuple[AutoTestPoint, ...] | None = None
        self.point_overlay_states: dict[tuple[int, int], str] = {}
        self.last_overlay_items: list[MatrixOverlay] = []
        self.running = False
        self.custom_point_specs: tuple[AutoTestPointSpec, ...] | None = None

        self.origin_u_var = tk.StringVar(value="")
        self.origin_v_var = tk.StringVar(value="")
        self.u_vector_u_var = tk.StringVar(value="1000")
        self.u_vector_v_var = tk.StringVar(value="0")
        self.v_vector_u_var = tk.StringVar(value="0")
        self.v_vector_v_var = tk.StringVar(value="1000")
        self.rows_var = tk.StringVar(value="3")
        self.cols_var = tk.StringVar(value="3")
        self.z_down_margin_var = tk.StringVar(value="100")
        self.z_up_fast_percent_var = tk.DoubleVar(value=50.0)
        self.z_up_fast_percent_text_var = tk.StringVar(value="Fast 50% / slow 50%")
        self.z_fast_speed_percent_var = tk.StringVar(value="50")
        self.z_slow_speed_percent_var = tk.StringVar(value="2")
        self.z_wobble_um_var = tk.StringVar(value="0")
        self.z_wobble_cycles_var = tk.StringVar(value="0")
        self.z_offset_um_var = tk.StringVar(value="0")
        self.approach_expanded_var = tk.BooleanVar(value=True)
        self.approach_toggle_button: ttk.Button | None = None
        self.approach_content_frame: ttk.Frame | None = None
        self.z_split_expanded_var = tk.BooleanVar(value=False)
        self.z_split_button: ttk.Button | None = None
        self.z_split_content_frame: ttk.Frame | None = None
        self.name_pattern_var = tk.StringVar(value="Dev{i}{j}")
        self.measure_pause_var = tk.BooleanVar(value=False)
        self.measure_photo_var = tk.BooleanVar(value=False)
        self.measurement_flow_cards: list[AutoTestFlowCard] = []
        self.measurement_flow_next_id = 1
        self._flow_drag: dict[str, object] | None = None
        self._flow_card_widgets: dict[str, tuple[int, tk.Frame]] = {}
        self._flow_card_render_state: dict[str, tuple[int, int, bool, str]] = {}
        self._flow_entry_vars: dict[tuple[str, str], tk.StringVar] = {}
        self._flow_placeholder_index: int | None = None
        self._flow_zoom = 1.0
        self._flow_editor_status_var = tk.StringVar(value="Drag modules from the library into the workspace.")
        self.cursor_var = tk.StringVar(value="Cursor u, v: -")
        self.selection_var = tk.StringVar(value="Selected: -")
        self.current_stage_var = tk.StringVar(value="Current stage: -")
        self.current_gds_var = tk.StringVar(value="Current GDS: -")
        self.viewport_var = tk.StringVar(value="Viewport: -")
        self.probe_assist_enabled_var = tk.BooleanVar(value=False)
        self.probe_assist_vars: dict[str, dict[str, tk.StringVar]] = {
            name: {"du": tk.StringVar(value=default_du), "dv": tk.StringVar(value=default_dv)}
            for name, _color, _style, default_du, default_dv in PROBE_ASSIST_PROBES
        }
        self.focusmap_status_var = tk.StringVar(value="FocusMap: checking")
        self.layoutmap_status_var = tk.StringVar(value="LayoutMap: checking")
        self.summary_var = tk.StringVar(value="Preview: set Origin, U/V vectors, rows and columns.")
        self.point_source_var = tk.StringVar(value="Point source: Base array")
        self.measurement_var = tk.StringVar(value="Measurement flow: not configured")
        self.status_var = tk.StringVar(value="Idle")

        self.frame = ttk.Frame(parent, style="App.TFrame")
        self.frame.grid(row=0, column=0, sticky="nsew")
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self._build_ui()
        self._schedule_status_poll()
        self._schedule_microscope_preview_poll()

    def _build_ui(self) -> None:
        pane = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        pane.grid(row=0, column=0, sticky="nsew")

        viewer_panel = ttk.Frame(pane, style="Panel.TFrame", padding=10)
        viewer_panel.columnconfigure(0, weight=0, minsize=180)
        viewer_panel.columnconfigure(1, weight=1)
        viewer_panel.rowconfigure(0, weight=1)

        left_panel = ttk.Frame(viewer_panel, style="Panel.TFrame")
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left_panel.columnconfigure(0, weight=1)
        self._build_left_panel(left_panel)

        canvas_panel = ttk.Frame(viewer_panel, style="Panel.TFrame")
        canvas_panel.grid(row=0, column=1, sticky="nsew")
        canvas_panel.columnconfigure(0, weight=1)
        canvas_panel.rowconfigure(0, weight=1)
        self.viewer = GDSCanvasViewer(
            canvas_panel,
            self.colors,
            on_cursor_gds=self._set_cursor_gds,
            on_select_gds=self._handle_gds_click,
        )
        pane.add(viewer_panel, weight=1)

        controls = ttk.Frame(pane, style="Panel.TFrame", padding=12)
        controls.columnconfigure(0, weight=1)
        self._build_controls(controls)
        pane.add(controls, weight=0)

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        preview = ttk.LabelFrame(parent, text="Microscope Live", padding=8)
        preview.grid(row=0, column=0, sticky="ew")
        preview.columnconfigure(0, weight=1)
        self.microscope_label = ttk.Label(preview, text="No microscope frame", anchor="center", style="Value.TLabel", padding=8)
        self.microscope_label.grid(row=0, column=0, sticky="ew")

        status = ttk.LabelFrame(parent, text="Prerequisites", padding=8)
        status.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        status.columnconfigure(0, weight=1)
        self.focusmap_status_label = tk.Label(
            status,
            textvariable=self.focusmap_status_var,
            anchor="w",
            padx=8,
            pady=5,
            bg=self.colors["surface_2"],
            fg=self.colors["muted"],
            font=("Cascadia Mono", 9),
        )
        self.focusmap_status_label.grid(row=0, column=0, sticky="ew")
        self.layoutmap_status_label = tk.Label(
            status,
            textvariable=self.layoutmap_status_var,
            anchor="w",
            padx=8,
            pady=5,
            bg=self.colors["surface_2"],
            fg=self.colors["muted"],
            font=("Cascadia Mono", 9),
        )
        self.layoutmap_status_label.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        stage = ttk.LabelFrame(parent, text="Stage XY", padding=8)
        stage.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        stage.columnconfigure(0, weight=1)
        self.stage_value_labels = self._build_stage_layout_grid(stage)

        self._build_probe_assist_panel(parent, row=3)

        viewport = ttk.LabelFrame(parent, text="GDS Viewport", padding=8)
        viewport.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        viewport.columnconfigure(0, weight=1)
        ttk.Label(viewport, textvariable=self.viewport_var, style="Value.TLabel", padding=6, wraplength=180).grid(row=0, column=0, sticky="ew")
        ttk.Button(viewport, text="Fit to View", command=lambda: self.viewer.fit_to_view()).grid(row=1, column=0, sticky="ew", pady=(6, 0))

    def _build_controls(self, parent: ttk.Frame) -> None:
        row = 0
        row = self._build_point_section(parent, row)
        row = self._build_approach_section(parent, row)
        row = self._build_measurement_section(parent, row)
        self._build_run_section(parent, row)
        self._bind_preview_updates()

    def _build_stage_layout_grid(self, parent: tk.Widget) -> dict[str, tk.Label]:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=0, column=0, sticky="ew")
        for column in range(5):
            frame.columnconfigure(column, weight=1, uniform="autotest_stage_compact")
        labels: dict[str, tk.Label] = {}
        fields = (
            ("stage_x", "X"),
            ("stage_y", "Y"),
            ("stage_z", "Z"),
            ("gds_u", "U"),
            ("gds_v", "V"),
        )
        for column_index, (key, label_text) in enumerate(fields):
            tile = tk.Frame(
                frame,
                bg=self.colors["surface_2"],
                highlightthickness=1,
                highlightbackground=self.colors["border"],
                bd=0,
            )
            tile.grid(row=0, column=column_index, sticky="ew", padx=(0, 4 if column_index < len(fields) - 1 else 0))
            tile.columnconfigure(0, weight=1)
            tk.Label(
                tile,
                text=label_text,
                anchor="w",
                padx=6,
                pady=0,
                bg=self.colors["surface_2"],
                fg=self.colors["muted"],
                font=("Segoe UI", 7),
            ).grid(row=0, column=0, sticky="ew", pady=(2, 0))
            value = tk.Label(
                tile,
                text="-",
                anchor="e",
                padx=6,
                pady=0,
                bg=self.colors["surface_2"],
                fg=self.colors["accent"],
                font=("Cascadia Mono", 9, "bold"),
            )
            value.grid(row=1, column=0, sticky="ew", pady=(0, 3))
            labels[key] = value
        return labels

    def _build_probe_assist_panel(self, parent: ttk.Frame, *, row: int) -> None:
        assist = ttk.LabelFrame(parent, text="Probe Assist", padding=8)
        assist.grid(row=row, column=0, sticky="ew", pady=(10, 0))
        assist.columnconfigure(1, weight=1)
        assist.columnconfigure(2, weight=1)
        header = ttk.Frame(assist, style="Panel.TFrame")
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Show configured probes", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ToggleSwitch(header, self.probe_assist_enabled_var, self.colors, command=self._on_probe_assist_setting_changed).grid(row=0, column=1, sticky="e")

        ttk.Label(assist, text="Probe", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 2), padx=(0, 6))
        ttk.Label(assist, text="dU", style="Muted.TLabel").grid(row=1, column=1, sticky="w", pady=(8, 2), padx=(0, 6))
        ttk.Label(assist, text="dV", style="Muted.TLabel").grid(row=1, column=2, sticky="w", pady=(8, 2))
        for row_index, (name, color, _style, _default_du, _default_dv) in enumerate(PROBE_ASSIST_PROBES, start=2):
            label = tk.Label(
                assist,
                text=name,
                bg=self.colors["surface"],
                fg=color,
                anchor="w",
                font=("Segoe UI Semibold", 8),
            )
            label.grid(row=row_index, column=0, sticky="ew", padx=(0, 6), pady=(3, 0))
            ttk.Entry(assist, textvariable=self.probe_assist_vars[name]["du"], width=7).grid(row=row_index, column=1, sticky="ew", padx=(0, 6), pady=(3, 0))
            ttk.Entry(assist, textvariable=self.probe_assist_vars[name]["dv"], width=7).grid(row=row_index, column=2, sticky="ew", pady=(3, 0))
            self.probe_assist_vars[name]["du"].trace_add("write", lambda *_args: self._on_probe_assist_setting_changed())
            self.probe_assist_vars[name]["dv"].trace_add("write", lambda *_args: self._on_probe_assist_setting_changed())

    def _set_stage_metric(self, key: str, value: str, *, available: bool = True) -> None:
        label = getattr(self, "stage_value_labels", {}).get(key)
        if label is None:
            return
        label.configure(text=value, fg=self.colors["accent"] if available else self.colors["muted"])

    def _section(self, parent: ttk.Frame, title: str, row: int) -> ttk.LabelFrame:
        section = ttk.LabelFrame(parent, text=title, padding=10)
        section.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        section.columnconfigure(0, weight=1)
        return section

    def _build_point_section(self, parent: ttk.Frame, row: int) -> int:
        section = self._section(parent, "Test Point", row)
        for column in range(5):
            section.columnconfigure(column, weight=1 if column in (1, 2, 4) else 0)
        for column, heading in enumerate(("Point", "U", "V", "Pick", "Count")):
            ttk.Label(section, text=heading, style="Muted.TLabel").grid(row=0, column=column, sticky="w", padx=(0, 6))
        rows = (
            ("Origin", self.origin_u_var, self.origin_v_var, "origin", None),
            ("vi", self.u_vector_u_var, self.u_vector_v_var, "u_vector", self.cols_var),
            ("vj", self.v_vector_u_var, self.v_vector_v_var, "v_vector", self.rows_var),
        )
        for index, (label, u_var, v_var, pick_kind, count_var) in enumerate(rows, start=1):
            ttk.Label(section, text=label, style="Panel.TLabel").grid(row=index, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
            ttk.Entry(section, textvariable=u_var, width=9).grid(row=index, column=1, sticky="ew", padx=(0, 5), pady=(6, 0))
            ttk.Entry(section, textvariable=v_var, width=9).grid(row=index, column=2, sticky="ew", padx=(0, 5), pady=(6, 0))
            ttk.Button(section, text="Pick", width=5, command=lambda kind=pick_kind: self._arm_pick(kind)).grid(row=index, column=3, sticky="ew", padx=(0, 10), pady=(6, 0))
            if count_var is None:
                ttk.Label(section, text="-", style="Muted.TLabel").grid(row=index, column=4, sticky="ew", pady=(6, 0))
            else:
                ttk.Spinbox(section, from_=1, to=500, increment=1, textvariable=count_var, width=7).grid(row=index, column=4, sticky="ew", pady=(6, 0))
        actions = ttk.Frame(section, style="Panel.TFrame")
        actions.grid(row=4, column=0, columnspan=5, sticky="ew", pady=(10, 0))
        actions.columnconfigure((0, 1, 2), weight=1, uniform="autotest_point_actions")
        ttk.Button(actions, text="Previous Points", command=self.previous_point).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(actions, text="Import List", command=self.import_point_list).grid(row=0, column=1, sticky="ew", padx=(4, 4))
        ttk.Button(actions, text="Generate List", command=self.open_generate_list_dialog).grid(row=0, column=2, sticky="ew", padx=(4, 0))
        ttk.Label(section, textvariable=self.point_source_var, style="Muted.TLabel").grid(row=5, column=0, columnspan=5, sticky="ew", pady=(6, 0))
        return row + 1

    def _build_approach_section(self, parent: ttk.Frame, row: int) -> int:
        section = self._section(parent, "Device Separate and Approach", row)
        section.columnconfigure(0, weight=1)
        self.approach_toggle_button = ttk.Button(section, text="Collapse", command=self._toggle_approach_section)
        self.approach_toggle_button.grid(row=0, column=0, sticky="ew")
        self.approach_content_frame = ttk.Frame(section, style="Panel.TFrame")
        self.approach_content_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        content = self.approach_content_frame
        content.columnconfigure((0, 1, 2), weight=1, uniform="autotest_approach")

        ttk.Label(content, text="Name pattern", style="Muted.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Entry(content, textvariable=self.name_pattern_var, width=14).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(3, 0))

        ttk.Label(content, text="Z down (um)", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 2), padx=(0, 6))
        ttk.Label(content, text="Fast speed", style="Muted.TLabel").grid(row=2, column=1, sticky="w", pady=(8, 2), padx=(0, 6))
        ttk.Label(content, text="Slow speed", style="Muted.TLabel").grid(row=2, column=2, sticky="w", pady=(8, 2))
        ttk.Entry(content, textvariable=self.z_down_margin_var, width=9).grid(row=3, column=0, sticky="ew", padx=(0, 6))
        fast_frame = ttk.Frame(content, style="Panel.TFrame")
        fast_frame.grid(row=3, column=1, sticky="ew", padx=(0, 6))
        fast_frame.columnconfigure(0, weight=1)
        ttk.Spinbox(fast_frame, from_=0, to=100, increment=1, textvariable=self.z_fast_speed_percent_var, width=7).grid(row=0, column=0, sticky="ew")
        ttk.Label(fast_frame, text="%", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(5, 0))
        slow_frame = ttk.Frame(content, style="Panel.TFrame")
        slow_frame.grid(row=3, column=2, sticky="ew")
        slow_frame.columnconfigure(0, weight=1)
        ttk.Spinbox(slow_frame, from_=0, to=100, increment=1, textvariable=self.z_slow_speed_percent_var, width=7).grid(row=0, column=0, sticky="ew")
        ttk.Label(slow_frame, text="%", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(5, 0))

        ttk.Label(content, text="Wobb (um)", style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(8, 2), padx=(0, 6))
        ttk.Label(content, text="Cycle", style="Muted.TLabel").grid(row=4, column=1, sticky="w", pady=(8, 2), padx=(0, 6))
        ttk.Label(content, text="Offset (um)", style="Muted.TLabel").grid(row=4, column=2, sticky="w", pady=(8, 2))
        ttk.Entry(content, textvariable=self.z_wobble_um_var, width=9).grid(row=5, column=0, sticky="ew", padx=(0, 6))
        ttk.Spinbox(content, from_=0, to=1000, increment=1, textvariable=self.z_wobble_cycles_var, width=7).grid(row=5, column=1, sticky="ew", padx=(0, 6))
        ttk.Entry(content, textvariable=self.z_offset_um_var, width=9).grid(row=5, column=2, sticky="ew")

        self.z_split_button = ttk.Button(content, text="Z UP SPLIT >", command=self._toggle_z_split_section)
        self.z_split_button.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.z_split_content_frame = ttk.Frame(content, style="Panel.TFrame")
        self.z_split_content_frame.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.z_split_content_frame.columnconfigure(0, weight=1)
        RoundedSplitSlider(self.z_split_content_frame, self.z_up_fast_percent_var, self.colors, command=self._on_z_up_split_changed).grid(row=0, column=0, sticky="ew")
        ttk.Label(self.z_split_content_frame, textvariable=self.z_up_fast_percent_text_var, style="Value.TLabel", padding=6).grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self._update_z_split_section_visibility()

        self.z_wobble_curve_canvas = tk.Canvas(content, height=92, bg="#05070a", highlightthickness=1, highlightbackground=self.colors["border"], bd=0)
        self.z_wobble_curve_canvas.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.z_wobble_curve_canvas.bind("<Configure>", lambda _event: self._draw_z_wobble_curve())
        ttk.Button(content, text="Preview Test Points", command=self.redraw_preview).grid(row=9, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self._update_approach_section_visibility()
        self._draw_z_wobble_curve()
        return row + 1

    def _toggle_approach_section(self) -> None:
        self.approach_expanded_var.set(not bool(self.approach_expanded_var.get()))
        self._update_approach_section_visibility()

    def _update_approach_section_visibility(self) -> None:
        if self.approach_toggle_button is None or self.approach_content_frame is None:
            return
        expanded = bool(self.approach_expanded_var.get())
        self.approach_toggle_button.configure(text="Collapse" if expanded else "Expand")
        if expanded:
            self.approach_content_frame.grid()
        else:
            self.approach_content_frame.grid_remove()

    def _toggle_z_split_section(self) -> None:
        self.z_split_expanded_var.set(not bool(self.z_split_expanded_var.get()))
        self._update_z_split_section_visibility()

    def _update_z_split_section_visibility(self) -> None:
        if self.z_split_button is None or self.z_split_content_frame is None:
            return
        expanded = bool(self.z_split_expanded_var.get())
        self.z_split_button.configure(text="Z UP SPLIT v" if expanded else "Z UP SPLIT >")
        if expanded:
            self.z_split_content_frame.grid()
        else:
            self.z_split_content_frame.grid_remove()

    def _build_measurement_section(self, parent: ttk.Frame, row: int) -> int:
        section = self._section(parent, "Measurement", row)
        section.columnconfigure(0, weight=1)
        tk.Button(
            section,
            text="Config Measure Flow",
            command=self._open_measurement_dialog,
            bg="#0f5f91",
            activebackground="#0e7490",
            fg="#e0f2fe",
            activeforeground="#f0f9ff",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#38bdf8",
            highlightcolor="#38bdf8",
            padx=12,
            pady=7,
            cursor="hand2",
            font=("Segoe UI Semibold", 10),
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(section, textvariable=self.measurement_var, style="Value.TLabel", padding=8, wraplength=320).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        return row + 1

    def _build_run_section(self, parent: ttk.Frame, row: int) -> int:
        section = self._section(parent, "Execution", row)
        section.columnconfigure((0, 1), weight=1)
        self.run_button = ttk.Button(section, text="Run AutoTest", style="Accent.TButton", command=self._start_run, state="disabled")
        self.run_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.stop_button = ttk.Button(section, text="Stop", command=self.stop_run, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ttk.Label(section, textvariable=self.status_var, style="Status.TLabel", padding=8, wraplength=320).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        return row + 1

    def _bind_preview_updates(self) -> None:
        for variable in (
            self.origin_u_var,
            self.origin_v_var,
            self.u_vector_u_var,
            self.u_vector_v_var,
            self.v_vector_u_var,
            self.v_vector_v_var,
            self.rows_var,
            self.cols_var,
            self.fov_width_var,
            self.fov_height_var,
            self.z_down_margin_var,
            self.z_fast_speed_percent_var,
            self.z_slow_speed_percent_var,
            self.z_wobble_um_var,
            self.z_wobble_cycles_var,
            self.z_offset_um_var,
            self.name_pattern_var,
        ):
            variable.trace_add("write", lambda *_args: self._on_preview_setting_changed())

    def _on_preview_setting_changed(self) -> None:
        self.redraw_preview()
        self._draw_z_wobble_curve()

    def set_layout_context(
        self,
        model: GDSLayoutModel | None,
        layer_visibility: dict[tuple[int, int], bool] | None = None,
    ) -> None:
        self.model = model
        if model is None:
            self.viewer.draw_message("Load a GDS file in LayoutMap first.")
            return
        self.viewer.set_model(model)
        if layer_visibility:
            self.viewer.layer_visibility.update(layer_visibility)
        self.viewer.redraw()
        self.status_var.set(f"Synced {model.path.name} from LayoutMap.")
        self.redraw_preview()

    def _set_cursor_gds(self, point: tuple[float, float] | None) -> None:
        if point is None:
            self.cursor_var.set("Cursor u, v: -")
        else:
            self.cursor_var.set(f"Cursor u, v: {point[0]:.6g}, {point[1]:.6g}")

    def _handle_gds_click(self, u: float, v: float) -> None:
        self.selected_gds = (u, v)
        self.viewer.set_selected_gds((u, v))
        self.selection_var.set(f"Selected u, v: {u:.6g}, {v:.6g}")
        if self.pending_pick is not None:
            self._apply_pick(self.pending_pick, u, v)
            self.pending_pick = None
            self.viewer.set_pick_mode(False)

    def _arm_pick(self, kind: str) -> None:
        self.pending_pick = kind
        self.viewer.set_pick_mode(True)
        labels = {
            "origin": "Click a GDS point to set AutoTest origin.",
            "u_vector": "Click the next U-axis point. Vector = clicked point - origin.",
            "v_vector": "Click the next V-axis point. Vector = clicked point - origin.",
        }
        self.status_var.set(labels.get(kind, "Click a GDS point."))

    def _apply_pick(self, kind: str, u: float, v: float) -> None:
        if kind == "origin":
            self.origin_u_var.set(f"{u:.12g}")
            self.origin_v_var.set(f"{v:.12g}")
            self.status_var.set("Origin set from GDS pick.")
            return
        origin_u, origin_v = self._origin_from_ui()
        delta_u = u - origin_u
        delta_v = v - origin_v
        if kind == "u_vector":
            self.u_vector_u_var.set(f"{delta_u:.12g}")
            self.u_vector_v_var.set(f"{delta_v:.12g}")
            self.status_var.set("U vector set from GDS pick.")
        elif kind == "v_vector":
            self.v_vector_u_var.set(f"{delta_u:.12g}")
            self.v_vector_v_var.set(f"{delta_v:.12g}")
            self.status_var.set("V vector set from GDS pick.")

    def use_current_stage_as_origin(self) -> None:
        mapper = self.get_mapper()
        if mapper is None:
            self.status_var.set("Bind LayoutMap mapping before using current stage as origin.")
            return
        try:
            x_um, y_um, _z_um = self._stage_position_xyz_um()
            u, v = mapper.stage_to_gds(x_um, y_um)
        except Exception as exc:
            self.status_var.set(f"Current stage origin unavailable: {exc}")
            return
        self.origin_u_var.set(f"{u:.12g}")
        self.origin_v_var.set(f"{v:.12g}")
        self.status_var.set("Origin set from current mapped stage position.")

    def previous_point(self) -> None:
        self.custom_point_specs = None
        self._preview_cache_key = None
        self._preview_cache_points = None
        self.point_source_var.set("Point source: Base array")
        try:
            origin_u, origin_v = self._origin_from_ui()
            vi_u = float(self.u_vector_u_var.get())
            vi_v = float(self.u_vector_v_var.get())
        except ValueError as exc:
            self.status_var.set(f"Previous point unavailable: {exc}")
            return
        self.origin_u_var.set(f"{origin_u - vi_u:.12g}")
        self.origin_v_var.set(f"{origin_v - vi_v:.12g}")
        self.status_var.set("Origin shifted to previous vi point.")
        self.redraw_preview()

    def import_point_list(self) -> None:
        path = filedialog.askopenfilename(
            title="Import AutoTest Point List",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            specs = autotest_point_specs_from_json_payload(payload)
        except Exception as exc:
            self.status_var.set(f"Import List failed: {exc}")
            return
        self._set_custom_point_specs(specs, f"Imported {len(specs)} point(s) from {Path(path).name}.")

    def _set_custom_point_specs(self, specs: tuple[AutoTestPointSpec, ...], message: str) -> None:
        self.custom_point_specs = tuple(specs)
        self._preview_cache_key = None
        self._preview_cache_points = None
        self.point_source_var.set(f"Point source: {len(specs)} imported/generated point(s)")
        self.status_var.set(message)
        self.redraw_preview()

    def open_generate_list_dialog(self) -> None:
        existing = getattr(self, "_generate_list_dialog", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        dialog = tk.Toplevel(self.frame)
        dialog.title("Generate AutoTest Point List")
        dialog.configure(bg=self.colors["bg"])
        dialog.columnconfigure(0, weight=0)
        dialog.columnconfigure(1, weight=1)
        dialog.rowconfigure(0, weight=1)
        self._generate_list_dialog = dialog

        try:
            origin_u_default, origin_v_default = self._origin_from_ui()
        except ValueError:
            origin_u_default, origin_v_default = 0.0, 0.0
        variables = {
            "origin_u": tk.StringVar(value=f"{origin_u_default:.12g}"),
            "origin_v": tk.StringVar(value=f"{origin_v_default:.12g}"),
            "inner_u_u": tk.StringVar(value=self.u_vector_u_var.get()),
            "inner_u_v": tk.StringVar(value=self.u_vector_v_var.get()),
            "inner_v_u": tk.StringVar(value=self.v_vector_u_var.get()),
            "inner_v_v": tk.StringVar(value=self.v_vector_v_var.get()),
            "inner_cols": tk.StringVar(value=self.cols_var.get()),
            "inner_rows": tk.StringVar(value=self.rows_var.get()),
            "outer_u_u": tk.StringVar(value="5000"),
            "outer_u_v": tk.StringVar(value="0"),
            "outer_v_u": tk.StringVar(value="0"),
            "outer_v_v": tk.StringVar(value="5000"),
            "outer_cols": tk.StringVar(value="2"),
            "outer_rows": tk.StringVar(value="2"),
            "name_pattern": tk.StringVar(value="Dev{bi}{bj}_{i}{j}"),
        }
        status_var = tk.StringVar(value="Configure nested arrays and preview point placement.")

        form = ttk.Frame(dialog, style="Panel.TFrame", padding=12)
        form.grid(row=0, column=0, sticky="nsew")
        for column in range(4):
            form.columnconfigure(column, weight=1 if column in (1, 3) else 0)

        def add_pair(row_index: int, title: str, u_key: str, v_key: str) -> int:
            ttk.Label(form, text=title, style="Muted.TLabel").grid(row=row_index, column=0, sticky="w", pady=(8 if row_index else 0, 2), padx=(0, 6))
            ttk.Label(form, text="U", style="Muted.TLabel").grid(row=row_index, column=1, sticky="w", pady=(8 if row_index else 0, 2))
            ttk.Label(form, text="V", style="Muted.TLabel").grid(row=row_index, column=3, sticky="w", pady=(8 if row_index else 0, 2))
            ttk.Entry(form, textvariable=variables[u_key], width=9).grid(row=row_index + 1, column=1, sticky="ew", padx=(0, 8))
            ttk.Entry(form, textvariable=variables[v_key], width=9).grid(row=row_index + 1, column=3, sticky="ew")
            return row_index + 2

        row_index = add_pair(0, "Origin", "origin_u", "origin_v")
        row_index = add_pair(row_index, "Inner i", "inner_u_u", "inner_u_v")
        row_index = add_pair(row_index, "Inner j", "inner_v_u", "inner_v_v")
        ttk.Label(form, text="Inner cols / rows", style="Muted.TLabel").grid(row=row_index, column=0, sticky="w", pady=(8, 2), padx=(0, 6))
        ttk.Entry(form, textvariable=variables["inner_cols"], width=7).grid(row=row_index, column=1, sticky="ew", pady=(8, 0), padx=(0, 8))
        ttk.Entry(form, textvariable=variables["inner_rows"], width=7).grid(row=row_index, column=3, sticky="ew", pady=(8, 0))
        row_index += 1
        row_index = add_pair(row_index, "Outer i", "outer_u_u", "outer_u_v")
        row_index = add_pair(row_index, "Outer j", "outer_v_u", "outer_v_v")
        ttk.Label(form, text="Outer cols / rows", style="Muted.TLabel").grid(row=row_index, column=0, sticky="w", pady=(8, 2), padx=(0, 6))
        ttk.Entry(form, textvariable=variables["outer_cols"], width=7).grid(row=row_index, column=1, sticky="ew", pady=(8, 0), padx=(0, 8))
        ttk.Entry(form, textvariable=variables["outer_rows"], width=7).grid(row=row_index, column=3, sticky="ew", pady=(8, 0))
        row_index += 1
        ttk.Label(form, text="Name pattern", style="Muted.TLabel").grid(row=row_index, column=0, sticky="w", pady=(8, 2), padx=(0, 6))
        ttk.Entry(form, textvariable=variables["name_pattern"], width=20).grid(row=row_index, column=1, columnspan=3, sticky="ew", pady=(8, 0))
        row_index += 1

        preview_frame = ttk.Frame(dialog, style="Panel.TFrame", padding=12)
        preview_frame.grid(row=0, column=1, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        preview_canvas = tk.Canvas(preview_frame, width=520, height=420, bg="#05070a", highlightthickness=1, highlightbackground=self.colors["border"])
        preview_canvas.grid(row=0, column=0, sticky="nsew")
        ttk.Label(preview_frame, textvariable=status_var, style="Muted.TLabel").grid(row=1, column=0, sticky="ew", pady=(8, 0))

        def current_specs() -> tuple[AutoTestPointSpec, ...]:
            return generate_nested_autotest_point_specs(
                origin_u=float(variables["origin_u"].get()),
                origin_v=float(variables["origin_v"].get()),
                inner_u_vector_u=float(variables["inner_u_u"].get()),
                inner_u_vector_v=float(variables["inner_u_v"].get()),
                inner_v_vector_u=float(variables["inner_v_u"].get()),
                inner_v_vector_v=float(variables["inner_v_v"].get()),
                inner_cols=int(float(variables["inner_cols"].get())),
                inner_rows=int(float(variables["inner_rows"].get())),
                outer_u_vector_u=float(variables["outer_u_u"].get()),
                outer_u_vector_v=float(variables["outer_u_v"].get()),
                outer_v_vector_u=float(variables["outer_v_u"].get()),
                outer_v_vector_v=float(variables["outer_v_v"].get()),
                outer_cols=int(float(variables["outer_cols"].get())),
                outer_rows=int(float(variables["outer_rows"].get())),
                name_pattern=variables["name_pattern"].get(),
            )

        def generator_params() -> dict[str, object]:
            return {key: variable.get() for key, variable in variables.items()}

        def draw_preview(*_args: object) -> None:
            preview_canvas.delete("all")
            try:
                specs = current_specs()
            except Exception as exc:
                status_var.set(f"Preview unavailable: {exc}")
                return
            width = max(preview_canvas.winfo_width(), 1)
            height = max(preview_canvas.winfo_height(), 1)
            min_u = min(spec.u for spec in specs)
            max_u = max(spec.u for spec in specs)
            min_v = min(spec.v for spec in specs)
            max_v = max(spec.v for spec in specs)
            span_u = max(max_u - min_u, 1e-9)
            span_v = max(max_v - min_v, 1e-9)
            scale = min((width - 48) / span_u, (height - 48) / span_v)
            center_u = (min_u + max_u) / 2.0
            center_v = (min_v + max_v) / 2.0
            for spec in specs:
                x = width / 2.0 + (spec.u - center_u) * scale
                y = height / 2.0 - (spec.v - center_v) * scale
                preview_canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#38bdf8", outline="#bae6fd")
            status_var.set(f"Preview: {len(specs)} point(s). Tokens: {{bi}}, {{bj}}, {{i}}, {{j}}, {{n}}.")

        def save_json() -> None:
            try:
                specs = current_specs()
            except Exception as exc:
                status_var.set(f"Save failed: {exc}")
                return
            path = filedialog.asksaveasfilename(
                title="Save AutoTest Point List JSON",
                defaultextension=".json",
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not path:
                return
            payload = autotest_point_specs_payload(specs, source="nested_generator", generator=generator_params())
            Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            status_var.set(f"Saved {Path(path).name}.")

        def save_list() -> None:
            try:
                specs = current_specs()
            except Exception as exc:
                status_var.set(f"Save failed: {exc}")
                return
            path = filedialog.asksaveasfilename(
                title="Save AutoTest Point List",
                defaultextension=".list",
                filetypes=(("List files", "*.list"), ("All files", "*.*")),
            )
            if not path:
                return
            lines = ["name,u,v,row,col"]
            lines.extend(f"{spec.name},{spec.u:.12g},{spec.v:.12g},{spec.row},{spec.col}" for spec in specs)
            Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
            status_var.set(f"Saved {Path(path).name}.")

        def direct_import() -> None:
            try:
                specs = current_specs()
            except Exception as exc:
                status_var.set(f"Import failed: {exc}")
                return
            autosave_dir = Path.cwd() / "autotest_session"
            autosave_dir.mkdir(parents=True, exist_ok=True)
            autosave_path = autosave_dir / "last_generated_point_list.json"
            autosave_payload = autotest_point_specs_payload(specs, source="nested_generator", generator=generator_params())
            autosave_path.write_text(json.dumps(autosave_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            self._set_custom_point_specs(specs, f"Generated and imported {len(specs)} AutoTest point(s); saved {autosave_path.name}.")
            dialog.destroy()

        footer = ttk.Frame(form, style="Panel.TFrame")
        footer.grid(row=row_index, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        footer.columnconfigure((0, 1, 2), weight=1, uniform="generate_list_actions")
        ttk.Button(footer, text="Save JSON", command=save_json).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(footer, text="Save List", command=save_list).grid(row=0, column=1, sticky="ew", padx=(4, 4))
        ttk.Button(footer, text="Direct Import", style="Accent.TButton", command=direct_import).grid(row=0, column=2, sticky="ew", padx=(4, 0))

        for variable in variables.values():
            variable.trace_add("write", draw_preview)
        preview_canvas.bind("<Configure>", draw_preview)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        draw_preview()

    def _origin_from_ui(self) -> tuple[float, float]:
        try:
            return float(self.origin_u_var.get()), float(self.origin_v_var.get())
        except ValueError as exc:
            raise ValueError("Set a numeric AutoTest origin first.") from exc

    def settings_from_ui(self) -> AutoTestSettings:
        try:
            return AutoTestSettings(
                origin_u=float(self.origin_u_var.get()),
                origin_v=float(self.origin_v_var.get()),
                u_vector_u=float(self.u_vector_u_var.get()),
                u_vector_v=float(self.u_vector_v_var.get()),
                v_vector_u=float(self.v_vector_u_var.get()),
                v_vector_v=float(self.v_vector_v_var.get()),
                rows=int(float(self.rows_var.get())),
                cols=int(float(self.cols_var.get())),
                fov_width_um=float(self.fov_width_var.get()),
                fov_height_um=float(self.fov_height_var.get()),
                z_down_margin_um=float(self.z_down_margin_var.get()),
                z_up_fast_percent=float(self.z_up_fast_percent_var.get()),
                z_fast_speed_percent=int(float(self.z_fast_speed_percent_var.get())),
                z_slow_speed_percent=int(float(self.z_slow_speed_percent_var.get())),
                name_pattern=self.name_pattern_var.get(),
                z_wobble_um=float(self.z_wobble_um_var.get()),
                z_wobble_cycles=int(float(self.z_wobble_cycles_var.get())),
                z_offset_um=float(self.z_offset_um_var.get()),
                measurement_steps=self.measurement_steps(),
                measurement_flow=self.measurement_flow(),
            ).normalized()
        except ValueError as exc:
            raise ValueError(f"Invalid AutoTest settings: {exc}") from exc

    def redraw_preview(self) -> None:
        if not hasattr(self, "viewer"):
            return
        mapper = self.get_mapper()
        if mapper is None:
            self._set_overlays([])
            self.summary_var.set("Preview: bind LayoutMap mapping first.")
            return
        try:
            settings = self.settings_from_ui()
            points = self._points_for_settings(settings, mapper)
        except Exception as exc:
            self._set_overlays([])
            self.summary_var.set(f"Preview unavailable: {exc}")
            return
        overlays = [
            (list(point.fov_polygon_gds), point.name, f"autotest_{self.point_overlay_states.get((point.row, point.col), 'pending')}")
            for point in points
        ]
        self._set_overlays(overlays)
        last = points[-1]
        source = "custom list" if self.custom_point_specs is not None else f"{settings.rows} x {settings.cols}"
        self.summary_var.set(
            f"Preview: {source} = {len(points)} test point(s). "
            f"Last {last.name} UV {last.u:.6g}, {last.v:.6g}; "
            f"Z down {settings.z_down_margin_um:.6g} um, Wobb {settings.z_wobble_um:.6g} um x {settings.z_wobble_cycles}, Offset {settings.z_offset_um:.6g} um."
        )

    def _points_for_settings(self, settings: AutoTestSettings, mapper: AffineCoordinateMapper) -> tuple[AutoTestPoint, ...]:
        key = (
            id(mapper),
            settings.origin_u,
            settings.origin_v,
            settings.u_vector_u,
            settings.u_vector_v,
            settings.v_vector_u,
            settings.v_vector_v,
            settings.rows,
            settings.cols,
            settings.fov_width_um,
            settings.fov_height_um,
            settings.name_pattern,
            self.custom_point_specs,
        )
        if self._preview_cache_key == key and self._preview_cache_points is not None:
            return self._preview_cache_points
        if self.custom_point_specs is not None:
            points = generate_autotest_points_from_specs(self.custom_point_specs, settings, mapper)
        else:
            points = generate_autotest_points(settings, mapper)
        self._preview_cache_key = key
        self._preview_cache_points = points
        return points

    def _start_run(self) -> None:
        if not self._prerequisites_ready():
            self.status_var.set("AutoTest requires both FocusMap and LayoutMap.")
            self._update_prerequisite_status()
            return
        mapper = self.get_mapper()
        if mapper is None:
            self.status_var.set("Bind LayoutMap mapping before running AutoTest.")
            return
        try:
            settings = self.settings_from_ui()
            points = self._points_for_settings(settings, mapper)
        except Exception as exc:
            self.status_var.set(str(exc))
            return
        self.point_overlay_states = {(point.row, point.col): "pending" for point in points}
        self.redraw_preview()
        self.start_run(settings, points)

    def set_running(self, running: bool) -> None:
        self.running = running
        self.stop_button.configure(state="normal" if running else "disabled")
        self._update_prerequisite_status()

    def set_progress(self, current: int, total: int, message: str, row: int | None = None, col: int | None = None, state: str | None = None) -> None:
        self.status_var.set(f"{message} ({current}/{total})")
        if row is not None and col is not None and state is not None:
            self.point_overlay_states[(row, col)] = state
            self._schedule_preview_redraw()

    def set_status(self, message: str) -> None:
        self.status_var.set(message)
        if self.set_app_status is not None:
            self.set_app_status(message)

    def _set_overlays(self, overlays: list[MatrixOverlay]) -> None:
        self.last_overlay_items = overlays
        self.viewer.set_matrix_overlay(overlays)
        if self.on_overlay_changed is not None:
            self.on_overlay_changed(overlays)

    def _schedule_status_poll(self) -> None:
        try:
            self._update_prerequisite_status()
            self._update_status_panel()
            self.status_poll_job = self.frame.after(300, self._schedule_status_poll)
        except tk.TclError:
            return

    def _schedule_microscope_preview_poll(self) -> None:
        try:
            self._update_microscope_preview()
            self.microscope_poll_job = self.frame.after(AUTOTEST_PREVIEW_INTERVAL_MS, self._schedule_microscope_preview_poll)
        except tk.TclError:
            return

    def _schedule_preview_redraw(self) -> None:
        if self.preview_redraw_job is not None:
            return
        try:
            self.preview_redraw_job = self.frame.after(AUTOTEST_OVERLAY_REDRAW_INTERVAL_MS, self._run_scheduled_preview_redraw)
        except tk.TclError:
            self.preview_redraw_job = None

    def _run_scheduled_preview_redraw(self) -> None:
        self.preview_redraw_job = None
        self.redraw_preview()

    def _prerequisites_ready(self) -> bool:
        return self.get_focusmap_ready() and self.get_layoutmap_ready()

    def _update_prerequisite_status(self) -> None:
        focusmap_ready = self.get_focusmap_ready()
        layoutmap_ready = self.get_layoutmap_ready()
        self.focusmap_status_var.set("FocusMap: ready" if focusmap_ready else "FocusMap: missing plane")
        self.layoutmap_status_var.set("LayoutMap: ready" if layoutmap_ready else "LayoutMap: missing mapping")
        self._set_prerequisite_chip(self.focusmap_status_label, focusmap_ready)
        self._set_prerequisite_chip(self.layoutmap_status_label, layoutmap_ready)
        if hasattr(self, "run_button"):
            self.run_button.configure(state="normal" if focusmap_ready and layoutmap_ready and not self.running else "disabled")

    def _set_prerequisite_chip(self, label: tk.Label, ready: bool) -> None:
        if ready:
            label.configure(fg="#bbf7d0", bg="#052e24")
        else:
            label.configure(fg="#fecdd3", bg="#4c0519")

    def measurement_steps(self) -> tuple[str, ...]:
        steps = legacy_measurement_steps_from_flow(self.measurement_flow_cards)
        if steps:
            return steps
        fallback_steps = []
        if self.measure_pause_var.get():
            fallback_steps.append("pause")
        if self.measure_photo_var.get():
            fallback_steps.append("photo")
        return tuple(fallback_steps)

    def measurement_flow(self) -> tuple[AutoTestFlowStep, ...]:
        return measurement_flow_steps_from_cards(self.measurement_flow_cards)

    def _update_measurement_summary(self) -> None:
        self.measure_pause_var.set(any(card.type_id == "wait" for card in self.measurement_flow_cards))
        self.measure_photo_var.set(any(card.type_id == "photo" for card in self.measurement_flow_cards))
        self.measurement_var.set(summarize_autotest_flow(self.measurement_flow_cards))

    def _update_z_up_split_text(self) -> None:
        fast = int(round(float(self.z_up_fast_percent_var.get())))
        self.z_up_fast_percent_text_var.set(f"Fast {fast}% / slow {100 - fast}%")

    def _on_z_up_split_changed(self) -> None:
        self._update_z_up_split_text()
        self.redraw_preview()

    def _draw_z_wobble_curve(self) -> None:
        canvas = getattr(self, "z_wobble_curve_canvas", None)
        if canvas is None:
            return
        try:
            canvas.delete("all")
            width = max(canvas.winfo_width(), 1)
            height = max(canvas.winfo_height(), 1)
            canvas.create_rectangle(0, 0, width, height, fill="#05070a", outline="")
            wobble_um = float(self.z_wobble_um_var.get() or 0)
            cycles = int(float(self.z_wobble_cycles_var.get() or 0))
            offset_um = float(self.z_offset_um_var.get() or 0)
            offsets = list(contact_wobble_offsets_um(wobble_um, cycles, offset_um))
        except Exception:
            try:
                canvas.create_text(10, 12, text="Wobb curve unavailable", anchor="nw", fill=self.colors["muted"], font=("Segoe UI", 8), tags="wobb_curve")
            except tk.TclError:
                pass
            return
        if not offsets:
            offsets = [0.0]
        values = [0.0, *offsets]
        max_abs = max(1e-9, max(abs(value) for value in values))
        left = 28
        right = width - 12
        top = 14
        bottom = height - 20
        zero_y = (top + bottom) / 2.0
        canvas.create_line(left, zero_y, right, zero_y, fill="#334155", width=1, dash=(3, 4))
        canvas.create_text(left, 5, text="contact wobb time-Z", anchor="nw", fill=self.colors["muted"], font=("Segoe UI", 8))
        points = []
        denominator = max(len(values) - 1, 1)
        for index, value in enumerate(values):
            x = left + (right - left) * index / denominator
            y = zero_y - (float(value) / max_abs) * (bottom - top) / 2.0
            points.append((x, y))
        if len(points) >= 2:
            coords: list[float] = []
            for x, y in points:
                coords.extend((x, y))
            canvas.create_line(coords, fill="#38bdf8", width=2)
        for x, y in points:
            canvas.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill="#f8fafc", outline="#38bdf8")
        canvas.create_text(left, bottom + 2, text=f"{min(values):.3g}..{max(values):.3g} um", anchor="sw", fill=self.colors["muted"], font=("Segoe UI", 8))

    def _update_status_panel(self) -> None:
        try:
            x_um, y_um, z_um = self._stage_position_xyz_um()
            self.current_stage_var.set(f"Current XYZ: {x_um:.6g}, {y_um:.6g}, {z_um:.6g} um")
            self._set_stage_metric("stage_x", f"{x_um:.3f}")
            self._set_stage_metric("stage_y", f"{y_um:.3f}")
            self._set_stage_metric("stage_z", f"{z_um:.3f}")
            if hasattr(self, "viewer"):
                self.viewport_var.set(self.viewer.viewport_status_text())
            mapper = self.get_mapper()
            if mapper is None:
                self.current_gds_var.set("Current GDS: bind LayoutMap first")
                self._set_stage_metric("gds_u", "-", available=False)
                self._set_stage_metric("gds_v", "-", available=False)
                self.viewer.set_stage_overlay(None, None)
                self.viewer.set_auxiliary_points([])
            else:
                u, v = mapper.stage_to_gds(x_um, y_um)
                self.current_gds_var.set(f"Current GDS u, v: {u:.6g}, {v:.6g}")
                self._set_stage_metric("gds_u", f"{u:.3f}")
                self._set_stage_metric("gds_v", f"{v:.3f}")
                self._update_current_stage_overlay(mapper, x_um, y_um)
        except Exception as exc:
            self.current_stage_var.set(f"Current stage unavailable: {exc}")
            self.current_gds_var.set("Current GDS: -")
            self.viewport_var.set("Viewport: -")
            for key in ("stage_x", "stage_y", "stage_z", "gds_u", "gds_v"):
                self._set_stage_metric(key, "-", available=False)
            self.viewer.set_stage_overlay(None, None)
            self.viewer.set_auxiliary_points([])

    def _update_current_stage_overlay(self, mapper: AffineCoordinateMapper, x_um: float, y_um: float) -> None:
        try:
            width_um = float(self.fov_width_var.get())
            height_um = float(self.fov_height_var.get())
            if width_um <= 0 or height_um <= 0:
                raise ValueError
            center_gds = mapper.stage_to_gds(x_um, y_um)
            corners_stage = [
                (x_um - width_um / 2.0, y_um - height_um / 2.0),
                (x_um + width_um / 2.0, y_um - height_um / 2.0),
                (x_um + width_um / 2.0, y_um + height_um / 2.0),
                (x_um - width_um / 2.0, y_um + height_um / 2.0),
            ]
            self.viewer.set_stage_overlay(center_gds, [mapper.stage_to_gds(x, y) for x, y in corners_stage])
            self._update_auxiliary_points(center_gds)
        except Exception:
            self.viewer.set_stage_overlay(None, None)
            self.viewer.set_auxiliary_points([])

    def _on_probe_assist_setting_changed(self) -> None:
        self._update_auxiliary_points()
        if self.on_probe_assist_changed is not None:
            self.on_probe_assist_changed()

    def probe_assist_enabled(self) -> bool:
        return bool(self.probe_assist_enabled_var.get())

    def probe_assist_specs(self) -> tuple[dict[str, object], ...]:
        specs: list[dict[str, object]] = []
        for name, color, style, _default_du, _default_dv in PROBE_ASSIST_PROBES:
            variables = self.probe_assist_vars[name]
            du = float(variables["du"].get() or 0.0)
            dv = float(variables["dv"].get() or 0.0)
            if not math.isfinite(du) or not math.isfinite(dv):
                raise ValueError(f"{name} Probe dU/dV must be finite.")
            specs.append({"label": name, "du": du, "dv": dv, "color": color, "style": style})
        return tuple(specs)

    def probe_assist_overlays_for_center(self, center_gds: tuple[float, float]) -> list[AuxiliaryPointOverlay]:
        if not self.probe_assist_enabled():
            return []
        overlays: list[AuxiliaryPointOverlay] = []
        for spec in self.probe_assist_specs():
            du = float(spec["du"])
            dv = float(spec["dv"])
            overlays.append(
                AuxiliaryPointOverlay(
                    point=(float(center_gds[0]) + du, float(center_gds[1]) + dv),
                    label=str(spec["label"]),
                    style=str(spec["style"]),
                    color=str(spec["color"]),
                )
            )
        return overlays

    def _update_auxiliary_points(self, center_gds: tuple[float, float] | None = None) -> None:
        if not hasattr(self, "viewer") or not self.probe_assist_enabled():
            if hasattr(self, "viewer"):
                self.viewer.set_auxiliary_points([])
            return
        mapper = self.get_mapper()
        if mapper is None:
            self.viewer.set_auxiliary_points([])
            return
        try:
            if center_gds is None:
                x_um, y_um, _z_um = self._stage_position_xyz_um()
                center_gds = mapper.stage_to_gds(x_um, y_um)
            self.viewer.set_auxiliary_points(self.probe_assist_overlays_for_center(center_gds))
        except Exception:
            self.viewer.set_auxiliary_points([])

    def _update_microscope_preview(self) -> None:
        if self.get_microscope_preview is None:
            return
        try:
            payload = self.get_microscope_preview()
        except Exception:
            return
        if not payload:
            return
        try:
            payload_id = id(payload)
            if payload_id == self.microscope_payload_id:
                return
            self.microscope_payload_id = payload_id
            self.microscope_photo = tk.PhotoImage(data=payload, format="PPM")
            self.microscope_label.configure(image=self.microscope_photo, text="")
        except tk.TclError:
            return

    def _stage_position_xyz_um(self) -> tuple[float, float, float]:
        values = self.get_stage_position_um()
        if len(values) == 2:
            x_um, y_um = values
            return float(x_um), float(y_um), 0.0
        x_um, y_um, z_um = values
        return float(x_um), float(y_um), float(z_um)

    def _open_measurement_dialog(self) -> None:
        existing = getattr(self, "_measurement_dialog", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                return
        except tk.TclError:
            pass
        self._discard_flow_widget_cache(destroy=False)
        dialog = tk.Toplevel(self.frame)
        self._measurement_dialog = dialog
        dialog.title("AutoTest Measurement Flow")
        dialog.transient(self.frame.winfo_toplevel())
        dialog.configure(bg=self.colors["surface"])
        dialog.geometry("1240x760")
        dialog.minsize(980, 620)
        dialog.columnconfigure(0, weight=0, minsize=260)
        dialog.columnconfigure(1, weight=1)
        dialog.rowconfigure(1, weight=1)

        header = tk.Frame(dialog, bg=self.colors["surface"], padx=18, pady=14)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="AutoTest Measurement Flow",
            bg=self.colors["surface"],
            fg=self.colors["text"],
            font=("Segoe UI Semibold", 15),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            textvariable=self.measurement_var,
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(4, 0))

        library = tk.Frame(dialog, bg=self.colors["surface_2"], padx=12, pady=12)
        library.grid(row=1, column=0, sticky="nsew", padx=(14, 8), pady=(0, 14))
        library.columnconfigure(0, weight=1)
        library.rowconfigure(2, weight=1)
        tk.Label(
            library,
            text="Module Library",
            bg=self.colors["surface_2"],
            fg=self.colors["text"],
            font=("Segoe UI Semibold", 11),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        tk.Label(
            library,
            text="Drag a card into the workspace.",
            bg=self.colors["surface_2"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(2, 10))
        library_canvas = tk.Canvas(library, bg=self.colors["surface_2"], highlightthickness=0, bd=0)
        library_canvas.grid(row=2, column=0, sticky="nsew")
        library_scrollbar = ttk.Scrollbar(library, orient=tk.VERTICAL, command=library_canvas.yview)
        library_scrollbar.grid(row=2, column=1, sticky="ns", padx=(8, 0))
        library_canvas.configure(yscrollcommand=library_scrollbar.set)
        library_body = tk.Frame(library_canvas, bg=self.colors["surface_2"])
        library_window = library_canvas.create_window(0, 0, window=library_body, anchor="nw")
        library_body.columnconfigure(0, weight=1)
        library_body.bind(
            "<Configure>",
            lambda _event: library_canvas.configure(scrollregion=library_canvas.bbox("all")),
        )
        library_canvas.bind(
            "<Configure>",
            lambda event: library_canvas.itemconfigure(library_window, width=event.width),
        )
        self._bind_mousewheel(library_canvas, lambda event: self._scroll_flow_library(library_canvas, event))
        self._build_flow_library(library_body, library_canvas)

        workspace_frame = tk.Frame(dialog, bg=self.colors["surface"])
        workspace_frame.grid(row=1, column=1, sticky="nsew", padx=(0, 14), pady=(0, 14))
        workspace_frame.columnconfigure(0, weight=1)
        workspace_frame.rowconfigure(0, weight=1)

        self._flow_canvas = tk.Canvas(
            workspace_frame,
            bg="#0c121a",
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            bd=0,
            xscrollincrement=12,
            yscrollincrement=12,
        )
        self._flow_canvas.grid(row=0, column=0, sticky="nsew")
        x_scrollbar = ttk.Scrollbar(workspace_frame, orient=tk.HORIZONTAL, command=self._flow_canvas.xview)
        x_scrollbar.grid(row=1, column=0, sticky="ew")
        y_scrollbar = ttk.Scrollbar(workspace_frame, orient=tk.VERTICAL, command=self._flow_canvas.yview)
        y_scrollbar.grid(row=0, column=1, sticky="ns")
        self._flow_canvas.configure(xscrollcommand=x_scrollbar.set, yscrollcommand=y_scrollbar.set)
        self._flow_canvas.bind("<Configure>", lambda _event: self._redraw_flow_workspace())
        self._bind_mousewheel(self._flow_canvas, self._handle_flow_canvas_wheel)
        self._flow_canvas.bind("<ButtonPress-2>", self._start_flow_canvas_pan)
        self._flow_canvas.bind("<B2-Motion>", self._pan_flow_canvas)
        self._flow_canvas.bind("<ButtonRelease-2>", self._end_flow_canvas_pan)
        self._flow_canvas.bind("<ButtonPress-3>", self._start_flow_canvas_pan)
        self._flow_canvas.bind("<B3-Motion>", self._pan_flow_canvas)
        self._flow_canvas.bind("<ButtonRelease-3>", self._end_flow_canvas_pan)

        footer = tk.Frame(workspace_frame, bg=self.colors["surface"], pady=10)
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        tk.Label(
            footer,
            textvariable=self._flow_editor_status_var,
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=(2, 8))
        ttk.Button(footer, text="Clear", command=self._clear_measurement_flow).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(footer, text="Close", command=lambda: self._close_measurement_dialog(dialog)).grid(row=0, column=2)

        self._redraw_flow_workspace()
        dialog.update_idletasks()
        dialog.protocol("WM_DELETE_WINDOW", lambda: self._close_measurement_dialog(dialog))
        dialog.grab_set()

    def _close_measurement_dialog(self, dialog: tk.Toplevel) -> None:
        self._discard_flow_widget_cache(destroy=True)
        if getattr(self, "_measurement_dialog", None) is dialog:
            self._measurement_dialog = None
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        try:
            dialog.destroy()
        except tk.TclError:
            pass

    def _discard_flow_widget_cache(self, *, destroy: bool) -> None:
        if destroy:
            for item_id, frame in list(self._flow_card_widgets.values()):
                try:
                    if hasattr(self, "_flow_canvas"):
                        self._flow_canvas.delete(item_id)
                except tk.TclError:
                    pass
                try:
                    frame.destroy()
                except tk.TclError:
                    pass
        self._flow_card_widgets.clear()
        self._flow_card_render_state.clear()

    def _build_flow_library(self, parent: tk.Frame, wheel_target: tk.Canvas | None = None) -> None:
        for row, definition in enumerate(AUTOTEST_FLOW_DEFINITIONS):
            card = tk.Frame(
                parent,
                bg=self.colors["surface"],
                highlightthickness=1,
                highlightbackground=self.colors["border"],
                bd=0,
                cursor="hand2",
            )
            card.grid(row=row, column=0, sticky="ew", pady=(0, 8))
            card.columnconfigure(1, weight=1)
            tk.Frame(card, width=4, bg=definition.accent).grid(row=0, column=0, rowspan=2, sticky="ns")
            title = tk.Label(
                card,
                text=definition.title,
                bg=self.colors["surface"],
                fg=self.colors["text"],
                font=("Segoe UI Semibold", 10),
                anchor="w",
                padx=10,
                pady=5,
            )
            title.grid(row=0, column=1, sticky="ew")
            desc = tk.Label(
                card,
                text=definition.description,
                bg=self.colors["surface"],
                fg=self.colors["muted"],
                font=("Segoe UI", 8),
                anchor="w",
                padx=10,
                pady=0,
                wraplength=210,
            )
            desc.grid(row=1, column=1, sticky="ew", pady=(0, 7))
            self._bind_flow_library_drag(card, definition.type_id)
            self._bind_flow_library_drag(title, definition.type_id)
            self._bind_flow_library_drag(desc, definition.type_id)
            if wheel_target is not None:
                for widget in (card, title, desc):
                    self._bind_mousewheel(widget, lambda event, target=wheel_target: self._scroll_flow_library(target, event))

    def _bind_flow_library_drag(self, widget: tk.Widget, type_id: str) -> None:
        widget.bind("<ButtonPress-1>", lambda event, value=type_id: self._start_library_flow_drag(value, event))
        widget.bind("<B1-Motion>", self._move_flow_drag)
        widget.bind("<ButtonRelease-1>", self._finish_flow_drag)

    def _bind_mousewheel(self, widget: tk.Widget, callback: Callable[[tk.Event], str]) -> None:
        widget.bind("<MouseWheel>", callback, add="+")
        widget.bind("<Button-4>", callback, add="+")
        widget.bind("<Button-5>", callback, add="+")

    def _wheel_units(self, event: tk.Event) -> int:
        if getattr(event, "num", None) == 4:
            return -3
        if getattr(event, "num", None) == 5:
            return 3
        delta = int(getattr(event, "delta", 0))
        if delta == 0:
            return 0
        return -1 * max(-6, min(6, delta // 120 if abs(delta) >= 120 else (1 if delta > 0 else -1))) * 3

    def _scroll_flow_library(self, canvas: tk.Canvas, event: tk.Event) -> str:
        units = self._wheel_units(event)
        if units:
            canvas.yview_scroll(units, "units")
        return "break"

    def _handle_flow_canvas_wheel(self, event: tk.Event) -> str:
        if not hasattr(self, "_flow_canvas"):
            return "break"
        if int(getattr(event, "state", 0)) & 0x0004:
            self._zoom_flow_canvas(event)
            return "break"
        units = self._wheel_units(event)
        if not units:
            return "break"
        if int(getattr(event, "state", 0)) & 0x0001:
            self._flow_canvas.xview_scroll(units, "units")
        else:
            self._flow_canvas.yview_scroll(units, "units")
        return "break"

    def _zoom_flow_canvas(self, event: tk.Event) -> None:
        old_zoom = self._flow_zoom
        direction = -self._wheel_units(event)
        factor = 1.08 if direction > 0 else 1 / 1.08
        self._flow_zoom = max(0.72, min(1.45, self._flow_zoom * factor))
        if abs(self._flow_zoom - old_zoom) < 0.001:
            return
        self._flow_editor_status_var.set(f"Canvas zoom: {self._flow_zoom * 100:.0f}%")
        for item_id, frame in list(self._flow_card_widgets.values()):
            self._flow_canvas.delete(item_id)
            frame.destroy()
        self._flow_card_widgets.clear()
        self._flow_card_render_state.clear()
        self._redraw_flow_workspace()

    def _start_flow_canvas_pan(self, event: tk.Event) -> str:
        self._flow_canvas.scan_mark(event.x, event.y)
        self._flow_canvas.configure(cursor="fleur")
        return "break"

    def _pan_flow_canvas(self, event: tk.Event) -> str:
        self._flow_canvas.scan_dragto(event.x, event.y, gain=1)
        return "break"

    def _end_flow_canvas_pan(self, _event: tk.Event) -> str:
        self._flow_canvas.configure(cursor="")
        return "break"

    def _start_library_flow_drag(self, type_id: str, event: tk.Event) -> str:
        self._flow_drag = {"source": "library", "type_id": type_id, "root_x": event.x_root, "root_y": event.y_root}
        self._flow_editor_status_var.set("Drop between cards to insert; later cards will move aside.")
        self._move_flow_drag(event)
        return "break"

    def _start_existing_flow_drag(self, card_id: str, event: tk.Event) -> str:
        self._flow_drag = {"source": "workspace", "card_id": card_id, "root_x": event.x_root, "root_y": event.y_root}
        self._flow_editor_status_var.set("Drag to reorder this card.")
        self._move_flow_drag(event)
        return "break"

    def _move_flow_drag(self, event: tk.Event) -> str:
        if self._flow_drag is None or not hasattr(self, "_flow_canvas"):
            return "break"
        canvas = self._flow_canvas
        canvas.delete("flow_ghost")
        if not self._point_in_flow_canvas(event.x_root, event.y_root):
            self._flow_placeholder_index = None
            self._redraw_flow_workspace()
            return "break"
        y = canvas.canvasy(event.y_root - canvas.winfo_rooty())
        drag_card_id = self._flow_drag.get("card_id")
        self._flow_placeholder_index = self._flow_insert_index_for_y(y, drag_card_id if isinstance(drag_card_id, str) else None)
        self._redraw_flow_workspace()
        return "break"

    def _finish_flow_drag(self, event: tk.Event) -> str:
        if self._flow_drag is None or not hasattr(self, "_flow_canvas"):
            return "break"
        placeholder = self._flow_placeholder_index
        if placeholder is not None and self._point_in_flow_canvas(event.x_root, event.y_root):
            if self._flow_drag.get("source") == "library":
                type_id = str(self._flow_drag.get("type_id", "wait"))
                card = create_autotest_flow_card(type_id, f"flow_{self.measurement_flow_next_id}")
                self.measurement_flow_next_id += 1
                self.measurement_flow_cards.insert(placeholder, card)
                self._flow_editor_status_var.set(f"Inserted {autotest_flow_definitions_by_type()[type_id].title}.")
            else:
                card_id = self._flow_drag.get("card_id")
                if isinstance(card_id, str):
                    self._move_flow_card_to_index(card_id, placeholder)
                    self._flow_editor_status_var.set("Card reordered.")
            self._update_measurement_summary()
        else:
            self._flow_editor_status_var.set("Drag modules from the library into the workspace.")
        self._flow_drag = None
        self._flow_placeholder_index = None
        self._redraw_flow_workspace(animate=True)
        return "break"

    def _point_in_flow_canvas(self, root_x: int, root_y: int) -> bool:
        canvas = self._flow_canvas
        return (
            canvas.winfo_rootx() <= root_x <= canvas.winfo_rootx() + canvas.winfo_width()
            and canvas.winfo_rooty() <= root_y <= canvas.winfo_rooty() + canvas.winfo_height()
        )

    def _flow_insert_index_for_y(self, y: float, dragged_card_id: str | None = None) -> int:
        cards = [card for card in self.measurement_flow_cards if card.card_id != dragged_card_id]
        current_y = self._flow_origin_y()
        for index, card in enumerate(cards):
            card_height = self._flow_card_height_for_card(card)
            if y < current_y + card_height / 2:
                return index
            current_y += card_height + self._flow_card_gap()
        return len(cards)

    def _flow_card_bounds(self, index: int, expanded: bool) -> tuple[int, int, int, int]:
        y = self._flow_origin_y()
        for card in self.measurement_flow_cards[:index]:
            y += self._flow_card_height_for_card(card) + self._flow_card_gap()
        card = self.measurement_flow_cards[index] if 0 <= index < len(self.measurement_flow_cards) else None
        height = self._flow_card_height_for_card(card) if card is not None else self._flow_card_height(expanded)
        return self._flow_origin_x(), y, self._flow_card_width(), height

    def _flow_origin_x(self) -> int:
        return int(42 * self._flow_zoom)

    def _flow_origin_y(self) -> int:
        return int(72 * self._flow_zoom)

    def _flow_card_width(self) -> int:
        canvas_width = getattr(self, "_flow_canvas", None).winfo_width() if hasattr(self, "_flow_canvas") else 720
        return max(int(520 * self._flow_zoom), min(int(canvas_width - 120), int(760 * self._flow_zoom)))

    def _flow_card_height(self, expanded: bool) -> int:
        return int((214 if expanded else 58) * self._flow_zoom)

    def _flow_card_height_for_card(self, card: AutoTestFlowCard) -> int:
        if not card.expanded:
            return self._flow_card_height(False)
        if card.type_id == "wobb_test":
            height = WOBBTEST_FLOW_CARD_HEIGHT_ZXY if self._wobb_card_mode_is_zxy(card) else WOBBTEST_FLOW_CARD_HEIGHT_Z
            return int(height * self._flow_zoom)
        definition = autotest_flow_definitions_by_type()[card.type_id]
        rows = max(1, math.ceil(len(definition.parameters) / 2))
        return int((116 + rows * 38) * self._flow_zoom)

    @staticmethod
    def _wobb_card_mode_is_zxy(card: AutoTestFlowCard) -> bool:
        value = str(card.params.get("mode", "Z")).strip().lower().replace("_", "-")
        return value in {"z-xy", "zxy", "xy"}

    def _flow_card_gap(self) -> int:
        return int(18 * self._flow_zoom)

    def _move_flow_card_to_index(self, card_id: str, index: int) -> None:
        card = self._flow_card_by_id(card_id)
        if card is None:
            return
        self.measurement_flow_cards = [item for item in self.measurement_flow_cards if item.card_id != card_id]
        bounded_index = max(0, min(index, len(self.measurement_flow_cards)))
        self.measurement_flow_cards.insert(bounded_index, card)

    def _flow_card_by_id(self, card_id: str) -> AutoTestFlowCard | None:
        for card in self.measurement_flow_cards:
            if card.card_id == card_id:
                return card
        return None

    def _clear_measurement_flow(self) -> None:
        self.measurement_flow_cards.clear()
        self._flow_editor_status_var.set("Measurement flow cleared.")
        self._update_measurement_summary()
        self._redraw_flow_workspace(animate=True)

    def _toggle_flow_card(self, card_id: str) -> None:
        card = self._flow_card_by_id(card_id)
        if card is None:
            return
        card.expanded = not card.expanded
        self._flow_editor_status_var.set("Card expanded." if card.expanded else "Card collapsed.")
        self._redraw_flow_workspace(animate=True)

    def _delete_flow_card(self, card_id: str) -> None:
        self.measurement_flow_cards = [card for card in self.measurement_flow_cards if card.card_id != card_id]
        self._flow_entry_vars = {key: value for key, value in self._flow_entry_vars.items() if key[0] != card_id}
        self._flow_editor_status_var.set("Card deleted.")
        self._update_measurement_summary()
        self._redraw_flow_workspace(animate=True)

    def _redraw_flow_workspace(self, animate: bool = False) -> None:
        if not hasattr(self, "_flow_canvas"):
            return
        canvas: tk.Canvas = self._flow_canvas
        canvas.delete("flow_bg")
        canvas.delete("flow_connector")
        canvas.delete("flow_placeholder")
        canvas.delete("flow_empty")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        expected_content_width = max(width, self._flow_origin_x() + self._flow_card_width() + int(80 * self._flow_zoom))
        self._draw_flow_background(canvas, expected_content_width, max(height, int(900 * self._flow_zoom)))
        canvas.tag_lower("flow_bg")

        display_cards = list(self.measurement_flow_cards)
        dragged_card_id = None
        if self._flow_drag is not None and self._flow_drag.get("source") == "workspace":
            candidate = self._flow_drag.get("card_id")
            if isinstance(candidate, str):
                dragged_card_id = candidate
                display_cards = [card for card in display_cards if card.card_id != dragged_card_id]

        missing_card_ids = set(self._flow_card_widgets) - {card.card_id for card in self.measurement_flow_cards}
        for card_id in missing_card_ids:
            item_id, frame = self._flow_card_widgets.pop(card_id)
            canvas.delete(item_id)
            frame.destroy()
            self._flow_card_render_state.pop(card_id, None)

        ordered_slots: list[AutoTestFlowCard | None] = display_cards
        if self._flow_placeholder_index is not None:
            bounded = max(0, min(self._flow_placeholder_index, len(display_cards)))
            ordered_slots = display_cards[:bounded] + [None] + display_cards[bounded:]

        connector_points: list[tuple[float, float, float]] = []
        target_positions: dict[str, tuple[int, int]] = {}
        current_y = self._flow_origin_y()
        card_x = self._flow_origin_x()
        card_width = self._flow_card_width()
        for index, card in enumerate(ordered_slots):
            if card is None:
                placeholder_height = self._flow_card_height(False)
                line_y = current_y + placeholder_height / 2
                canvas.create_line(
                    card_x,
                    line_y,
                    card_x + card_width,
                    line_y,
                    fill=self.colors["border_focus"],
                    width=max(2, int(3 * self._flow_zoom)),
                    tags=("flow_placeholder",),
                )
                canvas.create_text(
                    card_x + card_width - int(12 * self._flow_zoom),
                    line_y - int(10 * self._flow_zoom),
                    text="insert here",
                    anchor="e",
                    fill=self.colors["border_focus"],
                    font=("Segoe UI Semibold", 10),
                    tags=("flow_placeholder",),
                )
                current_y += placeholder_height + self._flow_card_gap()
                continue
            card_height = self._flow_card_height_for_card(card)
            x, y = card_x, current_y
            item_id, _frame = self._ensure_flow_card_widget(card, card_width, card_height)
            target_positions[card.card_id] = (x, y)
            if not animate:
                canvas.coords(item_id, x, y)
            connector_points.append((x + card_width / 2, y, y + card_height))
            current_y += card_height + self._flow_card_gap()

        if animate:
            self._animate_flow_positions(target_positions)
        else:
            for card_id, (item_id, _frame) in self._flow_card_widgets.items():
                if card_id not in target_positions and card_id != dragged_card_id:
                    canvas.itemconfigure(item_id, state="hidden")
                else:
                    canvas.itemconfigure(item_id, state="normal")
        self._draw_flow_connectors(connector_points)
        canvas.tag_raise("flow_card")
        content_height = max(height, current_y + int(80 * self._flow_zoom))
        canvas.configure(scrollregion=(0, 0, expected_content_width, content_height))
        if not self.measurement_flow_cards and self._flow_placeholder_index is None:
            canvas.create_text(
                width / 2,
                height / 2,
                text="Drag modules here to compose the AutoTest flow",
                fill=self.colors["muted"],
                font=("Segoe UI Semibold", 14),
                tags=("flow_empty",),
            )

    def _draw_flow_background(self, canvas: tk.Canvas, width: int, height: int) -> None:
        canvas.create_rectangle(0, 0, width + 1200, height, fill="#0c121a", outline="", tags=("flow_bg",))
        for x in range(0, width + 1200, 32):
            canvas.create_line(x, 0, x, height, fill="#101923", tags=("flow_bg",))
        for y in range(0, height, 32):
            canvas.create_line(0, y, width + 1200, y, fill="#101923", tags=("flow_bg",))
        canvas.create_text(
            32,
            28,
            text="Workspace",
            anchor="w",
            fill=self.colors["muted"],
            font=("Segoe UI Semibold", 11),
            tags=("flow_bg",),
        )

    def _draw_flow_connectors(self, centers: list[tuple[float, float, float]]) -> None:
        canvas: tk.Canvas = self._flow_canvas
        for index in range(len(centers) - 1):
            x1, _top1, bottom1 = centers[index]
            x2, top2, _bottom2 = centers[index + 1]
            mid_y = (bottom1 + top2) / 2
            canvas.create_line(
                x1,
                bottom1,
                x1,
                mid_y,
                x2,
                mid_y,
                x2,
                top2,
                smooth=True,
                fill="#38bdf8",
                width=2,
                tags=("flow_connector",),
            )
            canvas.create_polygon(
                x2 - 7,
                top2 - 7,
                x2,
                top2,
                x2 + 7,
                top2 - 7,
                fill="#38bdf8",
                outline="",
                tags=("flow_connector",),
            )

    def _ensure_flow_card_widget(self, card: AutoTestFlowCard, width: int, height: int) -> tuple[int, tk.Frame]:
        canvas: tk.Canvas = self._flow_canvas
        existing = self._flow_card_widgets.get(card.card_id)
        render_state = (width, height, card.expanded, tuple(sorted(card.params.items())))
        if existing is not None:
            item_id, frame = existing
            frame.configure(width=width, height=height)
            canvas.itemconfigure(item_id, width=width, height=height, state="normal")
            if self._flow_card_render_state.get(card.card_id) != render_state:
                self._render_flow_card_frame(frame, card, width)
                self._flow_card_render_state[card.card_id] = render_state
            return existing
        frame = self._create_flow_card_frame(card, width, height)
        item_id = canvas.create_window(0, 0, window=frame, width=width, height=height, anchor="nw", tags=("flow_card", card.card_id))
        self._flow_card_widgets[card.card_id] = (item_id, frame)
        self._flow_card_render_state[card.card_id] = render_state
        return item_id, frame

    def _create_flow_card_frame(self, card: AutoTestFlowCard, width: int, height: int) -> tk.Frame:
        definition = autotest_flow_definitions_by_type()[card.type_id]
        frame = tk.Frame(
            self._flow_canvas,
            width=width,
            height=height,
            bg=self.colors["surface"],
            highlightthickness=1,
            highlightbackground=definition.accent if card.expanded else self.colors["border"],
            bd=0,
        )
        frame.grid_propagate(False)
        self._render_flow_card_frame(frame, card, width)
        return frame

    def _render_flow_card_frame(self, frame: tk.Frame, card: AutoTestFlowCard, width: int) -> None:
        for child in frame.winfo_children():
            child.destroy()
        definition = autotest_flow_definitions_by_type()[card.type_id]
        frame.configure(
            bg=self.colors["surface"],
            highlightbackground=definition.accent if card.expanded else self.colors["border"],
        )
        frame.columnconfigure(1, weight=0)
        frame.columnconfigure(2, weight=1)
        tk.Frame(frame, width=5, bg=definition.accent).grid(row=0, column=0, rowspan=4, sticky="ns")
        title = tk.Label(
            frame,
            text=definition.title,
            bg=self.colors["surface"],
            fg=self.colors["text"],
            font=("Segoe UI Semibold", 10),
            anchor="w",
            padx=10,
            pady=8,
            cursor="fleur",
        )
        title.grid(row=0, column=1, sticky="ew")
        summary = tk.Label(
            frame,
            text=self._flow_card_param_summary(card),
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=("Segoe UI", 8),
            anchor="w",
            padx=8,
            wraplength=width - 250,
            justify="left",
        )
        summary.grid(row=0, column=2, sticky="ew")
        toggle = tk.Button(
            frame,
            text="Hide" if card.expanded else "Edit",
            command=lambda card_id=card.card_id: self._toggle_flow_card(card_id),
            bg=self.colors["surface_3"],
            activebackground="#223144",
            fg=self.colors["text"],
            activeforeground=self.colors["text"],
            relief="flat",
            bd=0,
            padx=6,
            pady=2,
            font=("Segoe UI", 8),
        )
        toggle.grid(row=0, column=3, sticky="e", padx=(0, 5))
        delete = tk.Button(
            frame,
            text="Del",
            command=lambda card_id=card.card_id: self._delete_flow_card(card_id),
            bg="#3f1018",
            activebackground="#4c0519",
            fg="#fecdd3",
            activeforeground="#ffe4e6",
            relief="flat",
            bd=0,
            padx=6,
            pady=2,
            font=("Segoe UI", 8),
        )
        delete.grid(row=0, column=4, sticky="e", padx=(0, 8))
        if card.expanded:
            summary.configure(wraplength=width - 34)
            summary.grid(row=1, column=1, columnspan=4, sticky="ew", pady=(0, 8))
            self._build_flow_card_params(frame, card, definition)
        self._bind_flow_card_drag(frame, card.card_id)
        self._bind_flow_card_drag(title, card.card_id)
        title.bind("<Double-Button-1>", lambda _event, card_id=card.card_id: self._toggle_flow_card(card_id))
        summary.bind("<Double-Button-1>", lambda _event, card_id=card.card_id: self._toggle_flow_card(card_id))
        frame.bind("<Double-Button-1>", lambda _event, card_id=card.card_id: self._toggle_flow_card(card_id))

    def _build_flow_card_params(self, frame: tk.Frame, card: AutoTestFlowCard, definition: AutoTestFlowDefinition) -> None:
        if card.type_id == "wobb_test":
            self._build_wobb_test_flow_card_params(frame, card, definition)
            return
        params = tk.Frame(frame, bg=self.colors["surface"], padx=10, pady=4)
        params.grid(row=2, column=1, columnspan=4, sticky="nsew")
        params.columnconfigure(1, weight=1)
        params.columnconfigure(3, weight=1)
        for index, param in enumerate(definition.parameters):
            row = index // 2
            column = (index % 2) * 2
            tk.Label(
                params,
                text=param.label,
                bg=self.colors["surface"],
                fg=self.colors["muted"],
                font=("Segoe UI", 8),
                anchor="w",
            ).grid(row=row, column=column, sticky="w", padx=(0 if column == 0 else 16, 8), pady=3)
            var = self._flow_entry_vars.get((card.card_id, param.key))
            if var is None:
                var = tk.StringVar(value=card.params.get(param.key, param.default))
                self._flow_entry_vars[(card.card_id, param.key)] = var
                var.trace_add("write", lambda *_args, target=card, key=param.key, value_var=var: self._set_flow_card_param(target, key, value_var.get()))
            choices = self._flow_param_choices(param.key)
            if choices:
                entry = ttk.Combobox(params, textvariable=var, values=choices, state="readonly", width=12)
                entry.grid(row=row, column=column + 1, sticky="ew", pady=3)
            else:
                entry = tk.Entry(
                    params,
                    textvariable=var,
                    bg=self.colors["input"],
                    fg=self.colors["text"],
                    insertbackground=self.colors["accent"],
                    relief="flat",
                    highlightthickness=1,
                    highlightbackground=self.colors["border"],
                    highlightcolor=self.colors["border_focus"],
                    font=("Cascadia Mono", 9),
                )
                entry.grid(row=row, column=column + 1, sticky="ew", pady=3)

    def _flow_card_param_var(self, card: AutoTestFlowCard, key: str, default: str) -> tk.StringVar:
        var = self._flow_entry_vars.get((card.card_id, key))
        if var is None:
            var = tk.StringVar(value=card.params.get(key, default))
            self._flow_entry_vars[(card.card_id, key)] = var
            var.trace_add("write", lambda *_args, target=card, param_key=key, value_var=var: self._set_flow_card_param(target, param_key, value_var.get()))
        return var

    def _build_wobb_test_flow_card_params(self, frame: tk.Frame, card: AutoTestFlowCard, definition: AutoTestFlowDefinition) -> None:
        defaults = {param.key: param.default for param in definition.parameters}
        params = tk.Frame(frame, bg=self.colors["surface"], padx=10, pady=2)
        params.grid(row=2, column=1, columnspan=4, sticky="nsew")
        params.columnconfigure(0, weight=1)

        def group(title: str, row: int) -> tk.LabelFrame:
            section = tk.LabelFrame(
                params,
                text=title,
                bg=self.colors["surface"],
                fg=self.colors["muted"],
                bd=0,
                highlightthickness=1,
                highlightbackground=self.colors["border"],
                padx=8,
                pady=5,
                font=("Segoe UI Semibold", 8),
            )
            section.grid(row=row, column=0, sticky="ew", pady=(0, 7))
            return section

        def entry(parent: tk.Widget, key: str, row: int, column: int, *, width: int = 9, padx: tuple[int, int] = (0, 6)) -> tk.Entry:
            var = self._flow_card_param_var(card, key, defaults.get(key, ""))
            widget = tk.Entry(
                parent,
                textvariable=var,
                bg=self.colors["input"],
                fg=self.colors["text"],
                insertbackground=self.colors["accent"],
                relief="flat",
                highlightthickness=1,
                highlightbackground=self.colors["border"],
                highlightcolor=self.colors["border_focus"],
                font=("Cascadia Mono", 9),
                width=width,
            )
            widget.grid(row=row, column=column, sticky="ew", padx=padx, pady=(2, 0))
            return widget

        def combo(parent: tk.Widget, key: str, values: tuple[str, ...], row: int, column: int, *, width: int = 10, padx: tuple[int, int] = (0, 6)) -> ttk.Combobox:
            var = self._flow_card_param_var(card, key, defaults.get(key, ""))
            widget = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=width)
            widget.grid(row=row, column=column, sticky="ew", padx=padx, pady=(2, 0))
            return widget

        mode_group = group("Mode", 0)
        mode_group.columnconfigure(1, weight=1)
        ttk.Label(mode_group, text="Wobb mode", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        combo(mode_group, "mode", ("Z", "Z-XY"), 0, 1, width=12, padx=(0, 0))

        z_group = group("Z Range", 1)
        z_group.columnconfigure((0, 1), weight=1, uniform=f"wobb_z_{card.card_id}")
        lower_var = self._flow_card_param_var(card, "z_lower_um", defaults["z_lower_um"])
        upper_var = self._flow_card_param_var(card, "z_upper_um", defaults["z_upper_um"])
        range_text = tk.StringVar()

        def update_range_text(*_args) -> None:
            range_text.set(f"Lower {lower_var.get()} um    Upper {upper_var.get()} um")

        lower_var.trace_add("write", update_range_text)
        upper_var.trace_add("write", update_range_text)
        update_range_text()
        ttk.Label(z_group, textvariable=range_text, style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        RangeSlider(z_group, lower_var, upper_var, self.colors, minimum=-20, maximum=20, step=0.5).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        ttk.Label(z_group, text="Step", style="Muted.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 6))
        ttk.Label(z_group, text="Settle s", style="Muted.TLabel").grid(row=2, column=1, sticky="w")
        combo(z_group, "z_step_um", ("0.5", "1", "2", "4"), 3, 0, width=8)
        entry(z_group, "settle_s", 3, 1, width=8, padx=(0, 0))

        row_index = 2
        if self._wobb_card_mode_is_zxy(card):
            xy_group = group("XY Wobb", row_index)
            xy_group.columnconfigure((0, 1, 2), weight=1, uniform=f"wobb_xy_{card.card_id}")
            ttk.Label(xy_group, text="Range um", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
            ttk.Label(xy_group, text="Step um", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(0, 6))
            ttk.Label(xy_group, text="Pattern", style="Muted.TLabel").grid(row=0, column=2, sticky="w")
            entry(xy_group, "xy_range_um", 1, 0)
            entry(xy_group, "xy_step_um", 1, 1)
            combo(xy_group, "xy_pattern", ("square", "corners", "spiral"), 1, 2, width=9, padx=(0, 0))
            row_index += 1

        electrical = group("Electrical Sampling", row_index)
        electrical.columnconfigure((0, 1, 2), weight=1, uniform=f"wobb_electrical_{card.card_id}")
        labels = (
            ("resource", "VISA"),
            ("output_terminal", "Terminal"),
            ("bias_v", "Bias V"),
            ("current_limit_a", "Limit A"),
            ("sample_count", "Samples"),
            ("nplc", "NPLC"),
            ("best_current", "Best"),
        )
        for index, (key, label) in enumerate(labels):
            row = (index // 3) * 2
            column = index % 3
            ttk.Label(electrical, text=label, style="Muted.TLabel").grid(row=row, column=column, sticky="w", padx=(0, 6), pady=(0 if row == 0 else 6, 0))
            if key == "output_terminal":
                combo(electrical, key, ("rear", "front"), row + 1, column)
            elif key == "best_current":
                combo(electrical, key, ("max_abs", "max", "min_abs", "min"), row + 1, column, width=9)
            else:
                entry(electrical, key, row + 1, column, width=10, padx=(0, 6 if column < 2 else 0))

    @staticmethod
    def _flow_param_choices(key: str) -> tuple[str, ...]:
        choices = {
            "output_terminal": ("rear", "front"),
            "sweep_mode": ("voltage", "current"),
            "bidirectional": ("false", "true"),
            "output_statistics": ("true", "false"),
            "resistance_method": ("linear_fit", "median_ratio"),
            "output_off_after": ("true", "false"),
            "mode": ("Z", "Z-XY"),
            "z_step_um": ("0.5", "1", "2", "4"),
            "xy_pattern": ("square", "corners", "spiral"),
            "best_current": ("max_abs", "max", "min_abs", "min"),
        }
        return choices.get(key, ())

    def _set_flow_card_param(self, card: AutoTestFlowCard, key: str, value: str) -> None:
        card.params[key] = value
        self._update_measurement_summary()
        if card.type_id == "wobb_test" and key == "mode":
            self._flow_editor_status_var.set(f"WobbTest mode: {value}.")
            self._redraw_flow_workspace(animate=True)

    def _flow_card_param_summary(self, card: AutoTestFlowCard) -> str:
        if card.type_id == "wobb_test":
            mode = "Z-XY" if self._wobb_card_mode_is_zxy(card) else "Z"
            lower = card.params.get("z_lower_um", "-2")
            upper = card.params.get("z_upper_um", "2")
            step = card.params.get("z_step_um", "0.5")
            samples = card.params.get("sample_count", "5")
            nplc = card.params.get("nplc", "10")
            parts = [f"Mode: {mode}", f"Z {lower}..{upper} um step {step}", f"{samples} samples @ NPLC {nplc}"]
            if mode == "Z-XY":
                parts.append(f"XY +/-{card.params.get('xy_range_um', '2')} um")
            return " | ".join(parts)
        definition = autotest_flow_definitions_by_type()[card.type_id]
        parts = []
        for param in definition.parameters[:2]:
            value = card.params.get(param.key, param.default)
            parts.append(f"{param.label}: {value}")
        if len(definition.parameters) > 2:
            parts.append(f"+{len(definition.parameters) - 2} more")
        return " | ".join(parts)

    def _bind_flow_card_drag(self, widget: tk.Widget, card_id: str) -> None:
        widget.bind("<ButtonPress-1>", lambda event, value=card_id: self._start_existing_flow_drag(value, event))
        widget.bind("<B1-Motion>", self._move_flow_drag)
        widget.bind("<ButtonRelease-1>", self._finish_flow_drag)

    def _animate_flow_positions(self, targets: dict[str, tuple[int, int]], step: int = 0, total_steps: int = 8) -> None:
        if not hasattr(self, "_flow_canvas"):
            return
        canvas: tk.Canvas = self._flow_canvas
        if step >= total_steps:
            for card_id, (x, y) in targets.items():
                item = self._flow_card_widgets.get(card_id)
                if item is not None:
                    canvas.coords(item[0], x, y)
                    canvas.itemconfigure(item[0], state="normal")
            return
        progress = 1 - (1 - (step + 1) / total_steps) ** 3
        for card_id, (target_x, target_y) in targets.items():
            item = self._flow_card_widgets.get(card_id)
            if item is None:
                continue
            item_id, _frame = item
            coords = canvas.coords(item_id)
            if not coords:
                canvas.coords(item_id, target_x, target_y)
                continue
            current_x, current_y = coords[0], coords[1]
            next_x = current_x + (target_x - current_x) * progress
            next_y = current_y + (target_y - current_y) * progress
            canvas.coords(item_id, next_x, next_y)
            canvas.itemconfigure(item_id, state="normal")
        canvas.after(16, lambda: self._animate_flow_positions(targets, step + 1, total_steps))
