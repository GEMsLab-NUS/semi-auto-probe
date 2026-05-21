from __future__ import annotations

import math
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable

from .gds_stage_mapper import AffineCoordinateMapper, GDSCanvasViewer, GDSLayoutModel, MatrixOverlay
from .img_matrix import fov_polygon_for_stage_target


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
    measurement_steps: tuple[str, ...] = ()

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
            measurement_steps=tuple(str(step) for step in self.measurement_steps),
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


def compile_autotest_point_name(pattern: str, *, i_index: int, j_index: int) -> str:
    return str(pattern).replace("{i}", index_to_letters(i_index)).replace("{j}", str(j_index + 1))


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
        start_run: Callable[[AutoTestSettings], None],
        stop_run: Callable[[], None],
        set_status: Callable[[str], None] | None = None,
        on_overlay_changed: Callable[[list[MatrixOverlay]], None] | None = None,
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
        self.model: GDSLayoutModel | None = None
        self.pending_pick: str | None = None
        self.selected_gds: tuple[float, float] | None = None
        self.microscope_photo: tk.PhotoImage | None = None
        self.status_poll_job: str | None = None
        self.point_overlay_states: dict[tuple[int, int], str] = {}
        self.last_overlay_items: list[MatrixOverlay] = []
        self.running = False

        self.origin_u_var = tk.StringVar(value="")
        self.origin_v_var = tk.StringVar(value="")
        self.u_vector_u_var = tk.StringVar(value="1000")
        self.u_vector_v_var = tk.StringVar(value="0")
        self.v_vector_u_var = tk.StringVar(value="0")
        self.v_vector_v_var = tk.StringVar(value="1000")
        self.rows_var = tk.StringVar(value="3")
        self.cols_var = tk.StringVar(value="3")
        self.z_down_margin_var = tk.StringVar(value="100")
        self.z_up_fast_percent_var = tk.DoubleVar(value=80.0)
        self.z_up_fast_percent_text_var = tk.StringVar(value="Fast 80% / slow 20%")
        self.z_fast_speed_percent_var = tk.StringVar(value="80")
        self.z_slow_speed_percent_var = tk.StringVar(value="20")
        self.name_pattern_var = tk.StringVar(value="Dev{i}{j}")
        self.measure_pause_var = tk.BooleanVar(value=False)
        self.measure_photo_var = tk.BooleanVar(value=False)
        self.cursor_var = tk.StringVar(value="Cursor u, v: -")
        self.selection_var = tk.StringVar(value="Selected: -")
        self.current_stage_var = tk.StringVar(value="Current stage: -")
        self.current_gds_var = tk.StringVar(value="Current GDS: -")
        self.focusmap_status_var = tk.StringVar(value="FocusMap: checking")
        self.layoutmap_status_var = tk.StringVar(value="LayoutMap: checking")
        self.summary_var = tk.StringVar(value="Preview: set Origin, U/V vectors, rows and columns.")
        self.measurement_var = tk.StringVar(value="Measurement flow: not configured")
        self.status_var = tk.StringVar(value="Idle")

        self.frame = ttk.Frame(parent, style="App.TFrame")
        self.frame.grid(row=0, column=0, sticky="nsew")
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self._build_ui()
        self._schedule_status_poll()

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

        selection = ttk.LabelFrame(parent, text="GDS Pick", padding=8)
        selection.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        selection.columnconfigure(0, weight=1)
        ttk.Label(selection, textvariable=self.cursor_var, style="Value.TLabel", padding=6, wraplength=180).grid(row=0, column=0, sticky="ew")
        ttk.Label(selection, textvariable=self.selection_var, style="Value.TLabel", padding=6, wraplength=180).grid(row=1, column=0, sticky="ew", pady=(5, 0))
        ttk.Button(selection, text="Fit to View", command=lambda: self.viewer.fit_to_view()).grid(row=2, column=0, sticky="ew", pady=(8, 0))

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
        frame.columnconfigure(0, weight=1)
        labels: dict[str, tk.Label] = {}
        rows = (
            ("Stage XY", (("stage_x", "X"), ("stage_y", "Y"))),
            ("Layout UV", (("gds_u", "U"), ("gds_v", "V"))),
        )
        for row_index, (title, fields) in enumerate(rows):
            ttk.Label(frame, text=title, style="Muted.TLabel").grid(row=row_index * 2, column=0, sticky="w", pady=(0 if row_index == 0 else 7, 3))
            row_frame = ttk.Frame(frame, style="Panel.TFrame")
            row_frame.grid(row=row_index * 2 + 1, column=0, sticky="ew")
            for column in range(len(fields)):
                row_frame.columnconfigure(column, weight=1, uniform=f"autotest_stage_{row_index}")
            for column_index, (key, label_text) in enumerate(fields):
                tile = tk.Frame(
                    row_frame,
                    bg=self.colors["surface_2"],
                    highlightthickness=1,
                    highlightbackground=self.colors["border"],
                    bd=0,
                )
                tile.grid(row=0, column=column_index, sticky="ew", padx=(0, 6 if column_index < len(fields) - 1 else 0))
                tile.columnconfigure(0, weight=1)
                tk.Label(
                    tile,
                    text=label_text,
                    anchor="w",
                    padx=9,
                    pady=1,
                    bg=self.colors["surface_2"],
                    fg=self.colors["muted"],
                    font=("Segoe UI", 8),
                ).grid(row=0, column=0, sticky="ew", pady=(3, 0))
                value = tk.Label(
                    tile,
                    text="-",
                    anchor="e",
                    padx=9,
                    pady=1,
                    bg=self.colors["surface_2"],
                    fg=self.colors["accent"],
                    font=("Cascadia Mono", 13, "bold"),
                )
                value.grid(row=1, column=0, sticky="ew", pady=(0, 4))
                labels[key] = value
        return labels

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
        ttk.Button(section, text="Previous Point", command=self.previous_point).grid(row=4, column=0, columnspan=5, sticky="ew", pady=(10, 0))
        return row + 1

    def _build_approach_section(self, parent: ttk.Frame, row: int) -> int:
        section = self._section(parent, "Device Separate and Approach", row)
        section.columnconfigure((1, 2, 3), weight=1, uniform="autotest_approach")
        ttk.Label(section, text="Name pattern", style="Muted.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Entry(section, textvariable=self.name_pattern_var, width=14).grid(row=1, column=0, columnspan=4, sticky="ew", pady=(3, 0))

        ttk.Label(section, text="Z down (um)", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 2), padx=(0, 6))
        ttk.Label(section, text="Fast speed", style="Muted.TLabel").grid(row=2, column=1, columnspan=2, sticky="w", pady=(8, 2), padx=(0, 6))
        ttk.Label(section, text="Slow speed", style="Muted.TLabel").grid(row=2, column=3, sticky="w", pady=(8, 2), padx=(0, 6))
        ttk.Entry(section, textvariable=self.z_down_margin_var, width=9).grid(row=3, column=0, sticky="ew", padx=(0, 6))
        fast_frame = ttk.Frame(section, style="Panel.TFrame")
        fast_frame.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(0, 8))
        fast_frame.columnconfigure(0, weight=1)
        ttk.Spinbox(fast_frame, from_=0, to=100, increment=1, textvariable=self.z_fast_speed_percent_var, width=7).grid(row=0, column=0, sticky="ew")
        ttk.Label(fast_frame, text="%", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(5, 0))
        slow_frame = ttk.Frame(section, style="Panel.TFrame")
        slow_frame.grid(row=3, column=3, sticky="ew")
        slow_frame.columnconfigure(0, weight=1)
        ttk.Spinbox(slow_frame, from_=0, to=100, increment=1, textvariable=self.z_slow_speed_percent_var, width=7).grid(row=0, column=0, sticky="ew")
        ttk.Label(slow_frame, text="%", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(5, 0))

        ttk.Label(section, text="Z up split", style="Muted.TLabel").grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 2))
        RoundedSplitSlider(section, self.z_up_fast_percent_var, self.colors, command=self._on_z_up_split_changed).grid(row=5, column=0, columnspan=4, sticky="ew")
        ttk.Label(section, textvariable=self.z_up_fast_percent_text_var, style="Value.TLabel", padding=6).grid(row=6, column=0, columnspan=4, sticky="ew", pady=(5, 0))
        ttk.Button(section, text="Preview Points", command=self.redraw_preview).grid(row=7, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        return row + 1

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
            self.name_pattern_var,
        ):
            variable.trace_add("write", lambda *_args: self.redraw_preview())

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
                measurement_steps=self.measurement_steps(),
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
            points = generate_autotest_points(settings, mapper)
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
        self.summary_var.set(
            f"Preview: {settings.rows} x {settings.cols} = {len(points)} test point(s). "
            f"Last {last.name} UV {last.u:.6g}, {last.v:.6g}; Z down {settings.z_down_margin_um:.6g} um."
        )

    def _start_run(self) -> None:
        if not self._prerequisites_ready():
            self.status_var.set("AutoTest requires both FocusMap and LayoutMap.")
            self._update_prerequisite_status()
            return
        try:
            settings = self.settings_from_ui()
        except Exception as exc:
            self.status_var.set(str(exc))
            return
        self.point_overlay_states = {(row, col): "pending" for row in range(settings.rows) for col in range(settings.cols)}
        self.redraw_preview()
        self.start_run(settings)

    def set_running(self, running: bool) -> None:
        self.running = running
        self.stop_button.configure(state="normal" if running else "disabled")
        self._update_prerequisite_status()

    def set_progress(self, current: int, total: int, message: str, row: int | None = None, col: int | None = None, state: str | None = None) -> None:
        self.status_var.set(f"{message} ({current}/{total})")
        if row is not None and col is not None and state is not None:
            self.point_overlay_states[(row, col)] = state
            self.redraw_preview()

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
            self._update_microscope_preview()
            self.status_poll_job = self.frame.after(300, self._schedule_status_poll)
        except tk.TclError:
            return

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
        steps = []
        if self.measure_pause_var.get():
            steps.append("pause")
        if self.measure_photo_var.get():
            steps.append("photo")
        return tuple(steps)

    def _update_measurement_summary(self) -> None:
        labels = []
        if self.measure_pause_var.get():
            labels.append("Pause")
        if self.measure_photo_var.get():
            labels.append("Photo")
        self.measurement_var.set("Measurement flow: " + (" -> ".join(labels) if labels else "not configured"))

    def _update_z_up_split_text(self) -> None:
        fast = int(round(float(self.z_up_fast_percent_var.get())))
        self.z_up_fast_percent_text_var.set(f"Fast {fast}% / slow {100 - fast}%")

    def _on_z_up_split_changed(self) -> None:
        self._update_z_up_split_text()
        self.redraw_preview()

    def _update_status_panel(self) -> None:
        try:
            x_um, y_um, z_um = self._stage_position_xyz_um()
            self.current_stage_var.set(f"Current XYZ: {x_um:.6g}, {y_um:.6g}, {z_um:.6g} um")
            self._set_stage_metric("stage_x", f"{x_um:.3f}")
            self._set_stage_metric("stage_y", f"{y_um:.3f}")
            mapper = self.get_mapper()
            if mapper is None:
                self.current_gds_var.set("Current GDS: bind LayoutMap first")
                self._set_stage_metric("gds_u", "-", available=False)
                self._set_stage_metric("gds_v", "-", available=False)
            else:
                u, v = mapper.stage_to_gds(x_um, y_um)
                self.current_gds_var.set(f"Current GDS u, v: {u:.6g}, {v:.6g}")
                self._set_stage_metric("gds_u", f"{u:.3f}")
                self._set_stage_metric("gds_v", f"{v:.3f}")
        except Exception as exc:
            self.current_stage_var.set(f"Current stage unavailable: {exc}")
            self.current_gds_var.set("Current GDS: -")
            for key in ("stage_x", "stage_y", "gds_u", "gds_v"):
                self._set_stage_metric(key, "-", available=False)

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
        dialog = tk.Toplevel(self.frame)
        dialog.title("AutoTest Measurement Flow")
        dialog.transient(self.frame.winfo_toplevel())
        dialog.configure(bg=self.colors["surface"])
        dialog.columnconfigure(0, weight=1)
        body = ttk.Frame(dialog, style="Panel.TFrame", padding=16)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="Measurement flow", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(body, text="Pause", variable=self.measure_pause_var, command=self._update_measurement_summary).grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Checkbutton(body, text="Photo", variable=self.measure_photo_var, command=self._update_measurement_summary).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(body, textvariable=self.measurement_var, style="Value.TLabel", padding=8, wraplength=360).grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(body, text="Close", command=dialog.destroy).grid(row=4, column=0, sticky="e", pady=(14, 0))
        dialog.update_idletasks()
        dialog.minsize(360, 190)
        dialog.grab_set()
