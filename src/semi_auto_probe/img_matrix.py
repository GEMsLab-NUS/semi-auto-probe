from __future__ import annotations

import math
import re
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Callable

from .gds_stage_mapper import AuxiliaryPointOverlay, AffineCoordinateMapper, GDSCanvasViewer, GDSLayoutModel, MatrixOverlay


IMGMATRIX_PREVIEW_INTERVAL_MS = 45
IMGMATRIX_OVERLAY_REDRAW_INTERVAL_MS = 90


@dataclass(frozen=True)
class ImgMatrixSettings:
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

    def normalized(self) -> "ImgMatrixSettings":
        values = (
            self.origin_u,
            self.origin_v,
            self.u_vector_u,
            self.u_vector_v,
            self.v_vector_u,
            self.v_vector_v,
            self.fov_width_um,
            self.fov_height_um,
        )
        if any(not math.isfinite(float(value)) for value in values):
            raise ValueError("ImgMatrix coordinates and FOV dimensions must be finite.")
        rows = int(self.rows)
        cols = int(self.cols)
        if rows <= 0 or cols <= 0:
            raise ValueError("ImgMatrix rows and columns must be positive.")
        if rows > 500 or cols > 500:
            raise ValueError("ImgMatrix rows and columns are limited to 500.")
        if self.fov_width_um <= 0 or self.fov_height_um <= 0:
            raise ValueError("ImgMatrix FOV dimensions must be positive.")
        if math.hypot(self.u_vector_u, self.u_vector_v) <= 0:
            raise ValueError("ImgMatrix U vector must be non-zero.")
        if math.hypot(self.v_vector_u, self.v_vector_v) <= 0:
            raise ValueError("ImgMatrix V vector must be non-zero.")
        return ImgMatrixSettings(
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
        )


@dataclass(frozen=True)
class ImgMatrixPoint:
    row: int
    col: int
    order: int
    u: float
    v: float
    stage_x_um: float
    stage_y_um: float
    fov_polygon_gds: tuple[tuple[float, float], ...]

    @property
    def filename(self) -> str:
        return imgmatrix_filename(self.row, self.col, self.u, self.v)


def generate_imgmatrix_points(settings: ImgMatrixSettings, mapper: AffineCoordinateMapper) -> tuple[ImgMatrixPoint, ...]:
    normalized = settings.normalized()
    points: list[ImgMatrixPoint] = []
    order = 1
    for row in range(normalized.rows):
        for col in range(normalized.cols):
            u = normalized.origin_u + col * normalized.u_vector_u + row * normalized.v_vector_u
            v = normalized.origin_v + col * normalized.u_vector_v + row * normalized.v_vector_v
            stage_x_um, stage_y_um = mapper.gds_to_stage(u, v)
            points.append(
                ImgMatrixPoint(
                    row=row,
                    col=col,
                    order=order,
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


def fov_polygon_for_stage_target(
    mapper: AffineCoordinateMapper,
    center_x_um: float,
    center_y_um: float,
    width_um: float,
    height_um: float,
) -> tuple[tuple[float, float], ...]:
    if width_um <= 0 or height_um <= 0:
        raise ValueError("FOV dimensions must be positive.")
    corners_stage = (
        (center_x_um - width_um / 2.0, center_y_um - height_um / 2.0),
        (center_x_um + width_um / 2.0, center_y_um - height_um / 2.0),
        (center_x_um + width_um / 2.0, center_y_um + height_um / 2.0),
        (center_x_um - width_um / 2.0, center_y_um + height_um / 2.0),
    )
    return tuple(mapper.stage_to_gds(x_um, y_um) for x_um, y_um in corners_stage)


def imgmatrix_filename(row: int, col: int, u: float, v: float) -> str:
    return f"r{row:03d}_c{col:03d}_u{_safe_coord(u)}_v{_safe_coord(v)}.png"


def session_manifest_path(session_dir: Path) -> Path:
    return session_dir / "manifest.json"


def _safe_coord(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    if text == "-0":
        text = "0"
    return re.sub(r"[^0-9A-Za-z.-]+", "_", text).replace("-", "m").replace(".", "p")


class ImgMatrixPanel:
    def __init__(
        self,
        parent: tk.Widget,
        colors: dict[str, str],
        *,
        get_stage_position_um: Callable[[], tuple[float, float] | tuple[float, float, float]],
        get_mapper: Callable[[], AffineCoordinateMapper | None],
        get_microscope_preview: Callable[[], bytes | None] | None,
        fov_width_var: tk.StringVar,
        fov_height_var: tk.StringVar,
        tile_acquisition_var: tk.StringVar,
        t_stack_frame_count_var: tk.StringVar,
        t_stack_fusion_var: tk.StringVar,
        t_stack_save_raw_var: tk.BooleanVar,
        z_stack_step_um_var: tk.StringVar,
        z_stack_range_um_var: tk.StringVar,
        z_stack_fusion_var: tk.StringVar,
        z_stack_return_var: tk.BooleanVar,
        z_stack_save_raw_var: tk.BooleanVar,
        start_run: Callable[[ImgMatrixSettings], None],
        stop_run: Callable[[], None],
        set_status: Callable[[str], None] | None = None,
        on_overlay_changed: Callable[[list[MatrixOverlay]], None] | None = None,
    ) -> None:
        self.colors = colors
        self.get_stage_position_um = get_stage_position_um
        self.get_mapper = get_mapper
        self.get_microscope_preview = get_microscope_preview
        self.fov_width_var = fov_width_var
        self.fov_height_var = fov_height_var
        self.tile_acquisition_var = tile_acquisition_var
        self.t_stack_frame_count_var = t_stack_frame_count_var
        self.t_stack_fusion_var = t_stack_fusion_var
        self.t_stack_save_raw_var = t_stack_save_raw_var
        self.z_stack_step_um_var = z_stack_step_um_var
        self.z_stack_range_um_var = z_stack_range_um_var
        self.z_stack_fusion_var = z_stack_fusion_var
        self.z_stack_return_var = z_stack_return_var
        self.z_stack_save_raw_var = z_stack_save_raw_var
        self.start_run = start_run
        self.stop_run = stop_run
        self.set_app_status = set_status
        self.on_overlay_changed = on_overlay_changed
        self.model: GDSLayoutModel | None = None
        self.pending_pick: str | None = None
        self.selected_gds: tuple[float, float] | None = None
        self.microscope_photo: tk.PhotoImage | None = None
        self.microscope_payload_id: int | None = None
        self.status_poll_job: str | None = None
        self.microscope_poll_job: str | None = None
        self.preview_redraw_job: str | None = None
        self._preview_cache_key: tuple[object, ...] | None = None
        self._preview_cache_points: tuple[ImgMatrixPoint, ...] | None = None
        self.preview_labels: list[ttk.Label] = []
        self.matrix_overlay_states: dict[tuple[int, int], str] = {}
        self.last_overlay_items: list[MatrixOverlay] = []

        self.origin_u_var = tk.StringVar(value="")
        self.origin_v_var = tk.StringVar(value="")
        self.u_vector_u_var = tk.StringVar(value="1000")
        self.u_vector_v_var = tk.StringVar(value="0")
        self.v_vector_u_var = tk.StringVar(value="0")
        self.v_vector_v_var = tk.StringVar(value="1000")
        self.rows_var = tk.StringVar(value="3")
        self.cols_var = tk.StringVar(value="3")
        self.cursor_var = tk.StringVar(value="Cursor u, v: -")
        self.selection_var = tk.StringVar(value="Selected: -")
        self.current_stage_var = tk.StringVar(value="Current stage: -")
        self.current_gds_var = tk.StringVar(value="Current GDS: -")
        self.viewport_var = tk.StringVar(value="Viewport: -")
        self.assist_enabled_var = tk.BooleanVar(value=False)
        self.assist_du_var = tk.StringVar(value="0")
        self.assist_dv_var = tk.StringVar(value="0")
        self.assist_style_var = tk.StringVar(value="cross")
        self.assist_color_var = tk.StringVar(value="#f43f5e")
        self.assist_label_var = tk.StringVar(value="Probe")
        self.matrix_summary_var = tk.StringVar(value="Preview: set Origin, U/V vectors, rows and columns.")
        self.status_var = tk.StringVar(value="Idle")
        self.session_var = tk.StringVar(value="Session: -")

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

        stage = ttk.LabelFrame(parent, text="Stage XY", padding=8)
        stage.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        stage.columnconfigure(0, weight=1)
        self.stage_value_labels = self._build_stage_layout_grid(stage)

        selection = ttk.LabelFrame(parent, text="GDS Pick", padding=8)
        selection.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        selection.columnconfigure(0, weight=1)
        ttk.Label(selection, textvariable=self.cursor_var, style="Value.TLabel", padding=6, wraplength=180).grid(row=0, column=0, sticky="ew")
        ttk.Label(selection, textvariable=self.selection_var, style="Value.TLabel", padding=6, wraplength=180).grid(row=1, column=0, sticky="ew", pady=(5, 0))
        ttk.Button(selection, text="Fit to View", command=lambda: self.viewer.fit_to_view()).grid(row=2, column=0, sticky="ew", pady=(8, 0))

        viewport = ttk.LabelFrame(parent, text="GDS Viewport", padding=8)
        viewport.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        viewport.columnconfigure(0, weight=1)
        ttk.Label(viewport, textvariable=self.viewport_var, style="Value.TLabel", padding=6, wraplength=180).grid(row=0, column=0, sticky="ew")

    def _build_probe_assist_panel(self, parent: ttk.Frame, *, row: int) -> None:
        assist = ttk.LabelFrame(parent, text="Probe Assist", padding=8)
        assist.grid(row=row, column=0, sticky="ew", pady=(10, 0))
        assist.columnconfigure((1, 3), weight=1)
        ttk.Checkbutton(assist, text="Show", variable=self.assist_enabled_var, command=self._update_auxiliary_points).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(assist, text="dU", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(7, 2), padx=(0, 4))
        ttk.Entry(assist, textvariable=self.assist_du_var, width=7).grid(row=1, column=1, sticky="ew", pady=(7, 2), padx=(0, 6))
        ttk.Label(assist, text="dV", style="Muted.TLabel").grid(row=1, column=2, sticky="w", pady=(7, 2), padx=(0, 4))
        ttk.Entry(assist, textvariable=self.assist_dv_var, width=7).grid(row=1, column=3, sticky="ew", pady=(7, 2))
        ttk.Label(assist, text="Style", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(5, 2), padx=(0, 4))
        style = ttk.Combobox(assist, textvariable=self.assist_style_var, values=("cross", "ring", "dot", "diamond", "square"), state="readonly", width=8)
        style.grid(row=2, column=1, sticky="ew", pady=(5, 2), padx=(0, 6))
        ttk.Label(assist, text="Color", style="Muted.TLabel").grid(row=2, column=2, sticky="w", pady=(5, 2), padx=(0, 4))
        color = ttk.Combobox(assist, textvariable=self.assist_color_var, values=("#f43f5e", "#38bdf8", "#f59e0b", "#34d399", "#e0f2fe"), width=9)
        color.grid(row=2, column=3, sticky="ew", pady=(5, 2))
        ttk.Label(assist, text="Label", style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=(5, 0), padx=(0, 4))
        ttk.Entry(assist, textvariable=self.assist_label_var, width=12).grid(row=3, column=1, columnspan=3, sticky="ew", pady=(5, 0))
        for variable in (
            self.assist_du_var,
            self.assist_dv_var,
            self.assist_style_var,
            self.assist_color_var,
            self.assist_label_var,
        ):
            variable.trace_add("write", lambda *_args: self._update_auxiliary_points())

    def _build_controls(self, parent: ttk.Frame) -> None:
        row = 0
        row = self._build_point_section(parent, row)
        row = self._build_matrix_section(parent, row)
        row = self._build_acquisition_section(parent, row)
        row = self._build_run_section(parent, row)
        self._bind_preview_updates()

    def _build_stage_layout_grid(self, parent: tk.Widget) -> dict[str, tk.Label]:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        labels: dict[str, tk.Label] = {}
        rows = (
            ("Stage XYZ", (("stage_x", "X"), ("stage_y", "Y"), ("stage_z", "Z"))),
            ("Layout UV", (("gds_u", "U"), ("gds_v", "V"))),
        )
        for row_index, (title, fields) in enumerate(rows):
            ttk.Label(frame, text=title, style="Muted.TLabel").grid(row=row_index * 2, column=0, sticky="w", pady=(0 if row_index == 0 else 7, 3))
            row_frame = ttk.Frame(frame, style="Panel.TFrame")
            row_frame.grid(row=row_index * 2 + 1, column=0, sticky="ew")
            for column in range(len(fields)):
                row_frame.columnconfigure(column, weight=1, uniform=f"imgmatrix_stage_{row_index}")
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
        section = self._section(parent, "GDS Basis", row)
        for column in range(4):
            section.columnconfigure(column, weight=1 if column in (1, 2) else 0)
        headings = ("Point", "U", "V", "Pick")
        for column, heading in enumerate(headings):
            ttk.Label(section, text=heading, style="Muted.TLabel").grid(row=0, column=column, sticky="w", padx=(0, 6))
        rows = (
            ("Origin", self.origin_u_var, self.origin_v_var, "origin"),
            ("U step", self.u_vector_u_var, self.u_vector_v_var, "u_vector"),
            ("V step", self.v_vector_u_var, self.v_vector_v_var, "v_vector"),
        )
        for index, (label, u_var, v_var, pick_kind) in enumerate(rows, start=1):
            ttk.Label(section, text=label, style="Panel.TLabel").grid(row=index, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
            ttk.Entry(section, textvariable=u_var, width=9).grid(row=index, column=1, sticky="ew", padx=(0, 5), pady=(6, 0))
            ttk.Entry(section, textvariable=v_var, width=9).grid(row=index, column=2, sticky="ew", padx=(0, 5), pady=(6, 0))
            ttk.Button(section, text="Pick", command=lambda kind=pick_kind: self._arm_pick(kind)).grid(row=index, column=3, sticky="ew", pady=(6, 0))
        ttk.Button(section, text="Use Current Stage as Origin", command=self.use_current_stage_as_origin).grid(row=4, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        return row + 1

    def _build_matrix_section(self, parent: ttk.Frame, row: int) -> int:
        section = self._section(parent, "Matrix", row)
        section.columnconfigure((1, 3), weight=1)
        ttk.Label(section, text="Rows", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Spinbox(section, from_=1, to=500, increment=1, textvariable=self.rows_var, width=8).grid(row=0, column=1, sticky="ew", padx=(0, 10))
        ttk.Label(section, text="Cols", style="Muted.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Spinbox(section, from_=1, to=500, increment=1, textvariable=self.cols_var, width=8).grid(row=0, column=3, sticky="ew")
        ttk.Label(section, text="FOV comes from Settings > LayoutBond FOV.", style="Muted.TLabel", wraplength=300).grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Button(section, text="Preview Matrix", command=self.redraw_matrix_preview).grid(row=2, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        ttk.Label(section, textvariable=self.matrix_summary_var, style="Value.TLabel", padding=8, wraplength=320).grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        return row + 1

    def _build_acquisition_section(self, parent: ttk.Frame, row: int) -> int:
        section = self._section(parent, "Acquisition", row)
        section.columnconfigure((0, 1), weight=1)
        ttk.Label(section, text="Tile Mode", style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        mode_combo = ttk.Combobox(
            section,
            textvariable=self.tile_acquisition_var,
            values=("Single Frame", "T-Stack", "Z-Stack"),
            state="readonly",
            width=16,
        )
        mode_combo.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_acquisition_fields())

        self.t_stack_widgets: list[tk.Widget] = []
        self.z_stack_widgets: list[tk.Widget] = []
        self._add_labeled_entry(section, 2, "T frames", self.t_stack_frame_count_var, self.t_stack_widgets, column=0)
        self._add_combo(section, 2, "T fusion", self.t_stack_fusion_var, ("average", "registered_average", "sharpness_fusion"), self.t_stack_widgets, column=1)
        t_raw = ttk.Checkbutton(section, text="Save raw T-stack", variable=self.t_stack_save_raw_var)
        t_raw.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.t_stack_widgets.append(t_raw)

        self._add_labeled_entry(section, 5, "Z step (um)", self.z_stack_step_um_var, self.z_stack_widgets, column=0)
        self._add_labeled_entry(section, 5, "Z range +/- (um)", self.z_stack_range_um_var, self.z_stack_widgets, column=1)
        self._add_combo(section, 7, "Z fusion", self.z_stack_fusion_var, ("laplacian", "tenengrad"), self.z_stack_widgets, column=0, columnspan=2)
        z_options = ttk.Frame(section, style="Panel.TFrame")
        z_options.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        z_options.columnconfigure((0, 1), weight=1)
        ttk.Checkbutton(z_options, text="Return to Z0", variable=self.z_stack_return_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(z_options, text="Save raw Z-stack", variable=self.z_stack_save_raw_var).grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.z_stack_widgets.append(z_options)
        self._update_acquisition_fields()
        return row + 1

    def _add_labeled_entry(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        registry: list[tk.Widget],
        *,
        column: int,
        columnspan: int = 1,
    ) -> None:
        label_widget = ttk.Label(parent, text=label, style="Muted.TLabel")
        label_widget.grid(row=row, column=column, columnspan=columnspan, sticky="w", pady=(8, 2), padx=(0, 5) if column == 0 else (5, 0))
        entry = ttk.Entry(parent, textvariable=variable, width=10)
        entry.grid(row=row + 1, column=column, columnspan=columnspan, sticky="ew", padx=(0, 5) if column == 0 else (5, 0))
        registry.extend([label_widget, entry])

    def _add_combo(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        values: tuple[str, ...],
        registry: list[tk.Widget],
        *,
        column: int,
        columnspan: int = 1,
    ) -> None:
        label_widget = ttk.Label(parent, text=label, style="Muted.TLabel")
        label_widget.grid(row=row, column=column, columnspan=columnspan, sticky="w", pady=(8, 2), padx=(0, 5) if column == 0 else (5, 0))
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", width=16)
        combo.grid(row=row + 1, column=column, columnspan=columnspan, sticky="ew", padx=(0, 5) if column == 0 else (5, 0))
        registry.extend([label_widget, combo])

    def _update_acquisition_fields(self) -> None:
        mode = self.tile_acquisition_var.get()
        for widget in self.t_stack_widgets:
            widget.grid() if mode == "T-Stack" else widget.grid_remove()
        for widget in self.z_stack_widgets:
            widget.grid() if mode == "Z-Stack" else widget.grid_remove()

    def _build_run_section(self, parent: ttk.Frame, row: int) -> int:
        section = self._section(parent, "Execution", row)
        section.columnconfigure((0, 1), weight=1)
        self.run_button = ttk.Button(section, text="Run ImgMatrix", style="Accent.TButton", command=self._start_run)
        self.run_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.stop_button = ttk.Button(section, text="Stop", command=self.stop_run, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ttk.Label(section, textvariable=self.status_var, style="Status.TLabel", padding=8, wraplength=320).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(section, textvariable=self.session_var, style="Value.TLabel", padding=8, wraplength=320).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
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
        ):
            variable.trace_add("write", lambda *_args: self._schedule_matrix_preview_redraw())

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
        self.redraw_matrix_preview()

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
            "origin": "Click a GDS point to set ImgMatrix origin.",
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

    def _origin_from_ui(self) -> tuple[float, float]:
        try:
            return float(self.origin_u_var.get()), float(self.origin_v_var.get())
        except ValueError as exc:
            raise ValueError("Set a numeric ImgMatrix origin first.") from exc

    def settings_from_ui(self) -> ImgMatrixSettings:
        try:
            return ImgMatrixSettings(
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
            ).normalized()
        except ValueError as exc:
            raise ValueError(f"Invalid ImgMatrix settings: {exc}") from exc

    def redraw_matrix_preview(self) -> None:
        if not hasattr(self, "viewer"):
            return
        mapper = self.get_mapper()
        if mapper is None:
            self._set_matrix_overlays([])
            self.matrix_summary_var.set("Preview: bind LayoutMap mapping first.")
            return
        try:
            settings = self.settings_from_ui()
            points = self._points_for_settings(settings, mapper)
        except Exception as exc:
            self._set_matrix_overlays([])
            self.matrix_summary_var.set(f"Preview unavailable: {exc}")
            return
        overlays = [
            (list(point.fov_polygon_gds), f"{point.row},{point.col}", self.matrix_overlay_states.get((point.row, point.col), "pending"))
            for point in points
        ]
        self._set_matrix_overlays(overlays)
        last = points[-1]
        self.matrix_summary_var.set(
            f"Preview: {settings.rows} x {settings.cols} = {len(points)} shots. "
            f"Last UV {last.u:.6g}, {last.v:.6g}."
        )

    def _points_for_settings(self, settings: ImgMatrixSettings, mapper: AffineCoordinateMapper) -> tuple[ImgMatrixPoint, ...]:
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
        )
        if self._preview_cache_key == key and self._preview_cache_points is not None:
            return self._preview_cache_points
        points = generate_imgmatrix_points(settings, mapper)
        self._preview_cache_key = key
        self._preview_cache_points = points
        return points

    def _start_run(self) -> None:
        try:
            settings = self.settings_from_ui()
        except Exception as exc:
            self.status_var.set(str(exc))
            return
        self.matrix_overlay_states = {(row, col): "pending" for row in range(settings.rows) for col in range(settings.cols)}
        self.redraw_matrix_preview()
        self.start_run(settings)

    def set_running(self, running: bool) -> None:
        self.run_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def set_progress(self, current: int, total: int, message: str, row: int | None = None, col: int | None = None, state: str | None = None) -> None:
        self.status_var.set(f"{message} ({current}/{total})")
        if row is not None and col is not None and state is not None:
            self.matrix_overlay_states[(row, col)] = state
            self._schedule_matrix_preview_redraw()

    def set_session_path(self, session_dir: Path | None) -> None:
        self.session_var.set(f"Session: {session_dir}" if session_dir else "Session: -")

    def set_status(self, message: str) -> None:
        self.status_var.set(message)
        if self.set_app_status is not None:
            self.set_app_status(message)

    def _set_matrix_overlays(self, overlays: list[MatrixOverlay]) -> None:
        self.last_overlay_items = overlays
        self.viewer.set_matrix_overlay(overlays)
        if self.on_overlay_changed is not None:
            self.on_overlay_changed(overlays)

    def _schedule_status_poll(self) -> None:
        try:
            self._update_status_panel()
            self.status_poll_job = self.frame.after(300, self._schedule_status_poll)
        except tk.TclError:
            return

    def _schedule_microscope_preview_poll(self) -> None:
        try:
            self._update_microscope_preview()
            self.microscope_poll_job = self.frame.after(IMGMATRIX_PREVIEW_INTERVAL_MS, self._schedule_microscope_preview_poll)
        except tk.TclError:
            return

    def _schedule_matrix_preview_redraw(self) -> None:
        if self.preview_redraw_job is not None:
            return
        try:
            self.preview_redraw_job = self.frame.after(IMGMATRIX_OVERLAY_REDRAW_INTERVAL_MS, self._run_scheduled_matrix_preview_redraw)
        except tk.TclError:
            self.preview_redraw_job = None

    def _run_scheduled_matrix_preview_redraw(self) -> None:
        self.preview_redraw_job = None
        self.redraw_matrix_preview()

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

    def _update_auxiliary_points(self, center_gds: tuple[float, float] | None = None) -> None:
        if not hasattr(self, "viewer") or not bool(self.assist_enabled_var.get()):
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
            du = float(self.assist_du_var.get())
            dv = float(self.assist_dv_var.get())
            if not math.isfinite(du) or not math.isfinite(dv):
                raise ValueError
            point = (center_gds[0] + du, center_gds[1] + dv)
            self.viewer.set_auxiliary_points(
                [
                    AuxiliaryPointOverlay(
                        point=point,
                        label=self.assist_label_var.get(),
                        style=self.assist_style_var.get(),
                        color=self.assist_color_var.get(),
                    )
                ]
            )
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
