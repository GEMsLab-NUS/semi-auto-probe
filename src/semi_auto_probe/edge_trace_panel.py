from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, ttk

from .gds_edge_trace import EdgeTracePlan, build_edge_trace_plan, normalize_work_bounds
from .gds_stage_mapper import AffineCoordinateMapper, GDSCanvasViewer, GDSLayoutModel, ToggleSwitch, snap_gds_point


EDGE_TRACE_ACTION_SAFE = "move_safe"
EDGE_TRACE_ACTION_START = "move_start"
EDGE_TRACE_ACTION_CONTACT = "contact"
EDGE_TRACE_ACTION_SEGMENT = "run_segment"
EDGE_TRACE_ACTION_AUTO = "run_auto"
EDGE_TRACE_ACTIONS = {
    EDGE_TRACE_ACTION_SAFE,
    EDGE_TRACE_ACTION_START,
    EDGE_TRACE_ACTION_CONTACT,
    EDGE_TRACE_ACTION_SEGMENT,
    EDGE_TRACE_ACTION_AUTO,
}


class EdgeTraceCanvasViewer(GDSCanvasViewer):
    def __init__(
        self,
        parent: tk.Widget,
        colors: dict[str, str],
        *,
        on_cursor_gds: Callable[[tuple[float, float] | None], None],
        on_select_gds: Callable[[float, float], None],
        on_work_bounds: Callable[[tuple[float, float, float, float]], None],
    ) -> None:
        self.edge_plan: EdgeTracePlan | None = None
        self.work_bounds: tuple[float, float, float, float] | None = None
        self.completed_polyline_count = 0
        self.current_polyline_index: int | None = None
        self.needle_gds: tuple[float, float] | None = None
        self.current_needle_gds: tuple[float, float] | None = None
        self.range_pick_active = False
        self.range_drag_start_gds: tuple[float, float] | None = None
        self.range_drag_current_gds: tuple[float, float] | None = None
        self.on_work_bounds = on_work_bounds
        super().__init__(
            parent,
            colors,
            on_cursor_gds=on_cursor_gds,
            on_select_gds=on_select_gds,
        )

    def set_edge_plan(self, plan: EdgeTracePlan | None) -> None:
        self.edge_plan = plan
        self.completed_polyline_count = 0
        self.current_polyline_index = None
        self.needle_gds = None
        self._draw_overlay_items()

    def set_work_bounds(self, bounds: tuple[float, float, float, float] | None) -> None:
        self.work_bounds = normalize_work_bounds(bounds) if bounds is not None else None
        self._draw_overlay_items()

    def set_edge_progress(
        self,
        *,
        completed_polyline_count: int,
        current_polyline_index: int | None,
        needle_gds: tuple[float, float] | None,
    ) -> None:
        self.completed_polyline_count = max(0, int(completed_polyline_count))
        self.current_polyline_index = current_polyline_index
        self.needle_gds = needle_gds
        self._draw_overlay_items()

    def set_current_needle_gds(self, point: tuple[float, float] | None) -> None:
        if _same_optional_point(self.current_needle_gds, point):
            return
        self.current_needle_gds = point
        self._draw_overlay_items()

    def set_range_pick_mode(self, active: bool) -> None:
        self.range_pick_active = bool(active)
        self.range_drag_start_gds = None
        self.range_drag_current_gds = None
        self.canvas.configure(cursor="tcross" if active else "crosshair")
        self._draw_overlay_items()

    def _on_button_press(self, event: tk.Event) -> str:
        if self.range_pick_active and self.model is not None:
            self.range_drag_start_gds = self._event_gds(event)
            self.range_drag_current_gds = self.range_drag_start_gds
            self._draw_overlay_items()
            return "break"
        return super()._on_button_press(event)

    def _on_drag(self, event: tk.Event) -> str:
        if self.range_pick_active and self.model is not None and self.range_drag_start_gds is not None:
            self.range_drag_current_gds = self._event_gds(event)
            self._draw_overlay_items()
            return "break"
        return super()._on_drag(event)

    def _on_button_release(self, event: tk.Event) -> str:
        if self.range_pick_active and self.model is not None and self.range_drag_start_gds is not None:
            self.range_drag_current_gds = self._event_gds(event)
            try:
                bounds = normalize_work_bounds((*self.range_drag_start_gds, *self.range_drag_current_gds))
            except ValueError:
                self.set_range_pick_mode(False)
                return "break"
            self.work_bounds = bounds
            self.on_work_bounds(bounds)
            self.set_range_pick_mode(False)
            return "break"
        return super()._on_button_release(event)

    def _event_gds(self, event: tk.Event) -> tuple[float, float]:
        return snap_gds_point(self.transform.canvas_to_gds(float(event.x), float(event.y)), self.snap_grid_um)

    def _on_motion(self, event: tk.Event) -> None:
        if self.model is None:
            self.cursor_gds = None
            self._draw_cursor_only()
            self.on_cursor_gds(None)
            return
        self.cursor_gds = self._event_gds(event)
        self.on_cursor_gds(self.cursor_gds)
        self._draw_cursor_only()

    def _on_leave(self, _event: tk.Event) -> None:
        self.cursor_gds = None
        self.on_cursor_gds(None)
        self._draw_cursor_only()

    def _draw_cursor_only(self) -> None:
        try:
            self.canvas.delete("gds_cursor")
            if self.cursor_gds is not None:
                self._draw_cursor_crosshair(self.cursor_gds)
        except tk.TclError:
            return

    def _draw_overlay_items(self) -> None:
        super()._draw_overlay_items()
        try:
            self.canvas.delete("edge_trace_overlay")
            self._draw_work_bounds()
            self._draw_edge_plan()
            self._draw_current_needle()
            self._draw_needle()
        except tk.TclError:
            return

    def _draw_work_bounds(self) -> None:
        bounds = self.work_bounds
        if bounds is None and self.range_drag_start_gds is not None and self.range_drag_current_gds is not None:
            try:
                bounds = normalize_work_bounds((*self.range_drag_start_gds, *self.range_drag_current_gds))
            except ValueError:
                bounds = None
        if bounds is None:
            return
        min_u, min_v, max_u, max_v = bounds
        x1, y1 = self.transform.gds_to_canvas(min_u, max_v)
        x2, y2 = self.transform.gds_to_canvas(max_u, min_v)
        self.canvas.create_rectangle(
            x1,
            y1,
            x2,
            y2,
            outline="#fbbf24",
            width=2,
            dash=(7, 5),
            tags="edge_trace_overlay",
        )

    def _draw_edge_plan(self) -> None:
        if self.edge_plan is None:
            return
        for segment in self.edge_plan.segments:
            if segment.kind != "travel" or len(segment.points) < 2:
                continue
            coords = self._coords_for_points([point.gds for point in segment.points])
            if len(coords) >= 4:
                self.canvas.create_line(
                    coords,
                    fill="#94a3b8",
                    width=1,
                    dash=(8, 6),
                    arrow=tk.LAST,
                    arrowshape=(8, 10, 4),
                    tags="edge_trace_overlay",
                )
        for polyline in self.edge_plan.polylines:
            coords = self._coords_for_points([point.gds for point in polyline.points])
            if len(coords) < 4:
                continue
            if polyline.index <= self.completed_polyline_count:
                color = "#34d399"
                width = 3
            elif polyline.index == self.current_polyline_index:
                color = "#60a5fa"
                width = 3
            else:
                color = "#f59e0b"
                width = 2
            self.canvas.create_line(
                coords,
                fill=color,
                width=width,
                arrow=tk.LAST,
                arrowshape=(10, 12, 5),
                tags="edge_trace_overlay",
            )

    def _draw_needle(self) -> None:
        if self.needle_gds is None:
            return
        x, y = self.transform.gds_to_canvas(*self.needle_gds)
        self.canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill="#e0f2fe", outline="#0ea5e9", width=2, tags="edge_trace_overlay")

    def _draw_current_needle(self) -> None:
        if self.current_needle_gds is None:
            return
        x, y = self.transform.gds_to_canvas(*self.current_needle_gds)
        color = "#f43f5e"
        self.canvas.create_oval(x - 10, y - 10, x + 10, y + 10, outline=color, width=3, tags="edge_trace_overlay")
        self.canvas.create_line(x - 16, y, x + 16, y, fill=color, width=2, tags="edge_trace_overlay")
        self.canvas.create_line(x, y - 16, x, y + 16, fill=color, width=2, tags="edge_trace_overlay")
        self.canvas.create_text(
            x + 12,
            y - 12,
            text="Needle",
            anchor="sw",
            fill=color,
            font=("Segoe UI Semibold", 9),
            tags="edge_trace_overlay",
        )

    def _coords_for_points(self, points: list[tuple[float, float]]) -> list[float]:
        coords: list[float] = []
        for point in points:
            x, y = self.transform.gds_to_canvas(*point)
            coords.extend((x, y))
        return coords


class EdgeTracePanel:
    def __init__(
        self,
        parent: tk.Widget,
        colors: dict[str, str],
        *,
        get_stage_position_um: Callable[[], tuple[float, float] | tuple[float, float, float]],
        get_mapper: Callable[[], AffineCoordinateMapper | None],
        get_focus_z_um: Callable[[float, float], float | None],
        get_focusmap_ready: Callable[[], bool],
        get_layoutmap_ready: Callable[[], bool],
        get_layout_context: Callable[[], tuple[GDSLayoutModel | None, dict[tuple[int, int], bool]]],
        get_microscope_preview: Callable[[], bytes | None] | None,
        fov_width_var: tk.StringVar,
        fov_height_var: tk.StringVar,
        start_run: Callable[[EdgeTracePlan, str, int], None],
        stop_run: Callable[[], None],
        set_status: Callable[[str], None] | None = None,
    ) -> None:
        self.colors = colors
        self.get_stage_position_um = get_stage_position_um
        self.get_mapper = get_mapper
        self.get_focus_z_um = get_focus_z_um
        self.get_focusmap_ready = get_focusmap_ready
        self.get_layoutmap_ready = get_layoutmap_ready
        self.get_layout_context = get_layout_context
        self.get_microscope_preview = get_microscope_preview
        self.fov_width_var = fov_width_var
        self.fov_height_var = fov_height_var
        self.start_run = start_run
        self.stop_run = stop_run
        self.set_app_status = set_status

        self.model: GDSLayoutModel | None = None
        self.layer_visibility: dict[tuple[int, int], bool] = {}
        self.layer_by_label: dict[str, tuple[int, int]] = {}
        self.plan: EdgeTracePlan | None = None
        self.running = False
        self.active_polyline_offset = 0
        self.prep_state = "needs_safe"
        self.active_action: str | None = None
        self.status_poll_job: str | None = None
        self.microscope_poll_job: str | None = None
        self.microscope_photo: tk.PhotoImage | None = None
        self.microscope_payload_id: int | None = None

        self.cursor_var = tk.StringVar(value="Cursor u, v: -")
        self.selection_var = tk.StringVar(value="Selected: -")
        self.layoutmap_status_var = tk.StringVar(value="LayoutMap: checking")
        self.focusmap_status_var = tk.StringVar(value="FocusMap: checking")
        self.layer_var = tk.StringVar(value="")
        self.path_summary_var = tk.StringVar(value="Path: not planned")
        self.execution_step_var = tk.StringVar(value="Step: plan a trace")
        self.status_var = tk.StringVar(value="Idle")
        self.accept_mismatch_var = tk.BooleanVar(value=False)
        self.snap_grid_options = {
            "100 nm": 0.1,
            "1 um": 1.0,
            "5 um": 5.0,
            "10 um": 10.0,
        }
        self.snap_grid_var = tk.StringVar(value="1 um")
        self.peak_range_expanded_var = tk.BooleanVar(value=True)
        self.trace_parameter_expanded_var = tk.BooleanVar(value=True)

        self.min_u_var = tk.StringVar(value="")
        self.min_v_var = tk.StringVar(value="")
        self.max_u_var = tk.StringVar(value="")
        self.max_v_var = tk.StringVar(value="")
        self.offset_var = tk.StringVar(value="0")
        self.scratch_depth_var = tk.StringVar(value="0")
        self.lift_height_var = tk.StringVar(value="100")
        self.max_step_var = tk.StringVar(value="10")
        self.min_segment_var = tk.StringVar(value="2")

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
        viewer_panel.columnconfigure(0, weight=0, minsize=210)
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
        self.viewer = EdgeTraceCanvasViewer(
            canvas_panel,
            self.colors,
            on_cursor_gds=self._set_cursor_gds,
            on_select_gds=self._handle_gds_click,
            on_work_bounds=self._set_work_bounds_from_canvas,
        )
        self._update_snap_grid(update_status=False)
        pane.add(viewer_panel, weight=1)

        controls_shell = ttk.Frame(pane, style="Panel.TFrame")
        controls_shell.columnconfigure(0, weight=1)
        controls_shell.rowconfigure(0, weight=1)
        controls_canvas = tk.Canvas(
            controls_shell,
            bg=self.colors["surface"],
            bd=0,
            highlightthickness=0,
            width=330,
        )
        controls_scrollbar = ttk.Scrollbar(controls_shell, orient=tk.VERTICAL, command=controls_canvas.yview)
        controls_canvas.configure(yscrollcommand=controls_scrollbar.set)
        controls_canvas.grid(row=0, column=0, sticky="nsew")
        controls_scrollbar.grid(row=0, column=1, sticky="ns")
        controls = ttk.Frame(controls_canvas, style="Panel.TFrame", padding=10)
        controls.columnconfigure(0, weight=1)
        controls_window = controls_canvas.create_window((0, 0), window=controls, anchor="nw")

        def sync_scrollregion(_event: tk.Event | None = None) -> None:
            controls_canvas.configure(scrollregion=controls_canvas.bbox("all"))

        def sync_canvas_width(event: tk.Event) -> None:
            controls_canvas.itemconfigure(controls_window, width=event.width)

        def scroll_controls(event: tk.Event) -> str:
            delta = getattr(event, "delta", 0)
            if delta:
                controls_canvas.yview_scroll(int(-delta / 120), "units")
            return "break"

        controls.bind("<Configure>", sync_scrollregion)
        controls_canvas.bind("<Configure>", sync_canvas_width)
        controls_canvas.bind("<MouseWheel>", scroll_controls)
        controls.bind("<MouseWheel>", scroll_controls)
        self._build_controls(controls)
        pane.add(controls_shell, weight=0)

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        preview = ttk.LabelFrame(parent, text="Microscope Live", padding=8)
        preview.grid(row=0, column=0, sticky="ew")
        preview.columnconfigure(0, weight=1)
        self.microscope_label = ttk.Label(preview, text="No microscope frame", anchor="center", style="Value.TLabel", padding=8)
        self.microscope_label.grid(row=0, column=0, sticky="ew")

        prereq = ttk.LabelFrame(parent, text="Pre-Requirement", padding=8)
        prereq.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        prereq.columnconfigure(0, weight=1)
        self.focusmap_status_label = tk.Label(
            prereq,
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
            prereq,
            textvariable=self.layoutmap_status_var,
            anchor="w",
            padx=8,
            pady=5,
            bg=self.colors["surface_2"],
            fg=self.colors["muted"],
            font=("Cascadia Mono", 9),
        )
        self.layoutmap_status_label.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        stage = ttk.LabelFrame(parent, text="Coordinates", padding=8)
        stage.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        stage.columnconfigure(0, weight=1)
        self.stage_value_labels = self._build_stage_layout_grid(stage)

        pick = ttk.LabelFrame(parent, text="GDS Pick", padding=8)
        pick.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        pick.columnconfigure(0, weight=1)
        ttk.Label(pick, textvariable=self.cursor_var, style="Value.TLabel", padding=6, wraplength=190).grid(row=0, column=0, sticky="ew")
        ttk.Label(pick, textvariable=self.selection_var, style="Value.TLabel", padding=6, wraplength=190).grid(row=1, column=0, sticky="ew", pady=(5, 0))
        ttk.Button(pick, text="Fit to View", command=lambda: self.viewer.fit_to_view()).grid(row=2, column=0, sticky="ew", pady=(8, 0))

        summary = ttk.LabelFrame(parent, text="Plan Summary", padding=8)
        summary.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        summary.columnconfigure(0, weight=1)
        ttk.Label(summary, textvariable=self.path_summary_var, style="Value.TLabel", padding=6, wraplength=190).grid(row=0, column=0, sticky="ew")

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
                row_frame.columnconfigure(column, weight=1, uniform=f"edge_trace_stage_{row_index}")
            for column_index, (key, label_text) in enumerate(fields):
                tile = tk.Frame(
                    row_frame,
                    bg=self.colors["surface_2"],
                    highlightthickness=1,
                    highlightbackground=self.colors["border"],
                    bd=0,
                )
                tile.grid(row=0, column=column_index, sticky="ew", padx=(0, 5 if column_index < len(fields) - 1 else 0))
                tile.columnconfigure(0, weight=1)
                tk.Label(
                    tile,
                    text=label_text,
                    anchor="w",
                    padx=7,
                    pady=1,
                    bg=self.colors["surface_2"],
                    fg=self.colors["muted"],
                    font=("Segoe UI", 8),
                ).grid(row=0, column=0, sticky="ew", pady=(3, 0))
                value = tk.Label(
                    tile,
                    text="-",
                    anchor="e",
                    padx=7,
                    pady=1,
                    bg=self.colors["surface_2"],
                    fg=self.colors["accent"],
                    font=("Cascadia Mono", 11, "bold"),
                )
                value.grid(row=1, column=0, sticky="ew", pady=(0, 4))
                labels[key] = value
        return labels

    def _set_stage_metric(self, key: str, value: str, *, available: bool = True) -> None:
        label = getattr(self, "stage_value_labels", {}).get(key)
        if label is None:
            return
        label.configure(text=value, fg=self.colors["accent"] if available else self.colors["muted"])

    def _build_controls(self, parent: ttk.Frame) -> None:
        row = 0
        row = self._build_import_section(parent, row)
        row = self._build_range_section(parent, row)
        row = self._build_path_section(parent, row)
        self._build_execution_section(parent, row)

    def _build_import_section(self, parent: ttk.Frame, row: int) -> int:
        section = self._section(parent, "GDS Layout", row)
        section.columnconfigure(0, weight=1)
        ttk.Button(section, text="Sync from LayoutMap", style="Accent.TButton", command=self.sync_from_layoutmap).grid(row=0, column=0, sticky="ew")
        ttk.Button(section, text="Load GDS", command=self.load_gds_dialog).grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(section, text="Layer", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 2))
        self.layer_combo = ttk.Combobox(section, textvariable=self.layer_var, state="readonly", width=24)
        self.layer_combo.grid(row=3, column=0, sticky="ew")
        self.layer_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_selected_layer_visibility())
        mismatch_row = ttk.Frame(section, style="Panel.TFrame")
        mismatch_row.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        mismatch_row.columnconfigure(0, weight=1)
        ttk.Label(mismatch_row, text="Allow layout mismatch", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ToggleSwitch(mismatch_row, self.accept_mismatch_var, self.colors, command=self._on_mismatch_toggle).grid(row=0, column=1, sticky="e")
        return row + 1

    def _build_range_section(self, parent: ttk.Frame, row: int) -> int:
        section = self._collapsible_section(parent, "Peak Range", row, self.peak_range_expanded_var)
        for column in range(2):
            section.columnconfigure(column, weight=1, uniform="edge_trace_range")
        snap_row = ttk.Frame(section, style="Panel.TFrame")
        snap_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        snap_row.columnconfigure(1, weight=1)
        ttk.Label(snap_row, text="Grid snap", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        snap_combo = ttk.Combobox(
            snap_row,
            textvariable=self.snap_grid_var,
            values=tuple(self.snap_grid_options),
            state="readonly",
            width=12,
        )
        snap_combo.grid(row=0, column=1, sticky="ew")
        snap_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_snap_grid())
        fields = (
            ("Min U", self.min_u_var),
            ("Min V", self.min_v_var),
            ("Max U", self.max_u_var),
            ("Max V", self.max_v_var),
        )
        for index, (label, variable) in enumerate(fields):
            field_row = 1 + (index // 2) * 2
            ttk.Label(section, text=label, style="Muted.TLabel").grid(row=field_row, column=index % 2, sticky="w", padx=(0, 6), pady=(0 if index < 2 else 7, 2))
            ttk.Entry(section, textvariable=variable, width=10).grid(row=field_row + 1, column=index % 2, sticky="ew", padx=(0, 6))
        ttk.Button(section, text="Pick Range", command=lambda: self.viewer.set_range_pick_mode(True)).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(section, text="Apply Range", command=self.apply_work_range_from_entries).grid(row=6, column=0, sticky="ew", pady=(6, 0), padx=(0, 5))
        ttk.Button(section, text="Clear", command=self.clear_work_range).grid(row=6, column=1, sticky="ew", pady=(6, 0), padx=(5, 0))
        return row + 1

    def _build_path_section(self, parent: ttk.Frame, row: int) -> int:
        section = self._collapsible_section(parent, "Trace Parameter", row, self.trace_parameter_expanded_var)
        for column in range(2):
            section.columnconfigure(column, weight=1, uniform="edge_trace_path")
        fields = (
            ("Offset (um)", self.offset_var),
            ("Scratch depth (um)", self.scratch_depth_var),
            ("Safe distance (um)", self.lift_height_var),
            ("Mark step (um)", self.max_step_var),
            ("Min segment (um)", self.min_segment_var),
        )
        for index, (label, variable) in enumerate(fields):
            ttk.Label(section, text=label, style="Muted.TLabel").grid(row=index, column=0, sticky="w", pady=(0 if index == 0 else 7, 2))
            ttk.Entry(section, textvariable=variable, width=10).grid(row=index, column=1, sticky="ew", pady=(0 if index == 0 else 7, 2))
        ttk.Button(section, text="Plan Trace", style="Accent.TButton", command=self.plan_path).grid(row=len(fields), column=0, columnspan=2, sticky="ew", pady=(10, 0))
        return row + 1

    def _build_execution_section(self, parent: ttk.Frame, row: int) -> int:
        section = self._section(parent, "Execution", row)
        section.columnconfigure((0, 1), weight=1, uniform="edge_trace_exec")
        ttk.Label(section, textvariable=self.execution_step_var, style="Value.TLabel", padding=7, wraplength=300).grid(row=0, column=0, columnspan=2, sticky="ew")
        self.safe_button = ttk.Button(section, text="\u25b2 1. Move Safe Z", style="Accent.TButton", command=lambda: self._run_action(EDGE_TRACE_ACTION_SAFE), state="disabled")
        self.safe_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.start_button = ttk.Button(section, text="\u25ce 2. Move Start", command=lambda: self._run_action(EDGE_TRACE_ACTION_START), state="disabled")
        self.start_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.contact_button = ttk.Button(section, text="\u25bc 3. Contact", command=lambda: self._run_action(EDGE_TRACE_ACTION_CONTACT), state="disabled")
        self.contact_button.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.segment_button = ttk.Button(section, text="\u27a4 Run Segment", style="Accent.TButton", command=lambda: self._run_action(EDGE_TRACE_ACTION_SEGMENT), state="disabled")
        self.segment_button.grid(row=4, column=0, sticky="ew", padx=(0, 5), pady=(10, 0))
        self.auto_button = ttk.Button(section, text="\u26a1 Auto Run", command=lambda: self._run_action(EDGE_TRACE_ACTION_AUTO), state="disabled")
        self.auto_button.grid(row=4, column=1, sticky="ew", padx=(5, 0), pady=(10, 0))
        self.stop_button = ttk.Button(section, text="\u25a0 Stop", command=self.stop_run, state="disabled")
        self.stop_button.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(section, textvariable=self.status_var, style="Status.TLabel", padding=8, wraplength=320).grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        return row + 1

    def _section(self, parent: ttk.Frame, title: str, row: int) -> ttk.LabelFrame:
        section = ttk.LabelFrame(parent, text=title, padding=10)
        section.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        section.columnconfigure(0, weight=1)
        return section

    def _collapsible_section(self, parent: ttk.Frame, title: str, row: int, expanded_var: tk.BooleanVar) -> ttk.Frame:
        outer = ttk.Frame(parent, style="Panel.TFrame")
        outer.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        outer.columnconfigure(0, weight=1)

        header = tk.Frame(
            outer,
            bg=self.colors["surface_2"],
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            cursor="hand2",
        )
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        indicator = tk.Label(
            header,
            text="v" if expanded_var.get() else ">",
            bg=self.colors["surface_2"],
            fg=self.colors["accent"],
            width=2,
            font=("Segoe UI", 10, "bold"),
        )
        indicator.grid(row=0, column=0, sticky="w", padx=(8, 4), pady=8)
        title_label = tk.Label(
            header,
            text=title.upper(),
            bg=self.colors["surface_2"],
            fg=self.colors["text"],
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )
        title_label.grid(row=0, column=1, sticky="ew", pady=8)
        content = ttk.Frame(outer, style="Panel.TFrame", padding=(10, 8, 10, 10))
        content.grid(row=1, column=0, sticky="ew")
        content.columnconfigure(0, weight=1)

        def apply_visibility() -> None:
            if expanded_var.get():
                content.grid()
                indicator.configure(text="v")
            else:
                content.grid_remove()
                indicator.configure(text=">")

        def toggle(_event: tk.Event | None = None) -> str:
            expanded_var.set(not bool(expanded_var.get()))
            apply_visibility()
            return "break"

        for widget in (header, indicator, title_label):
            widget.bind("<Button-1>", toggle)
        apply_visibility()
        return content

    def sync_from_layoutmap(self) -> None:
        model, layer_visibility = self.get_layout_context()
        if model is None:
            self.set_status("Load a GDS file in LayoutMap first.")
            return
        self.set_layout_context(model, layer_visibility)
        self.set_status(f"Synced {model.path.name} from LayoutMap.")

    def load_gds_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title="Load GDS for EdgeTrace",
            filetypes=(("GDS files", "*.gds *.GDS"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            model = GDSLayoutModel.load(Path(path))
        except Exception as exc:
            self.set_status(f"GDS load failed: {exc}")
            return
        self.set_layout_context(model, None)
        self.set_status(f"Loaded {model.path.name}.")

    def set_layout_context(
        self,
        model: GDSLayoutModel | None,
        layer_visibility: dict[tuple[int, int], bool] | None = None,
    ) -> None:
        self.model = model
        self.layer_visibility = dict(layer_visibility or {})
        self.plan = None
        if model is None:
            self.viewer.draw_message("Load or sync a GDS file to begin.")
            self._rebuild_layer_options()
            self.path_summary_var.set("Path: not planned")
            self._update_execution_controls()
            return
        self.viewer.set_model(model)
        self._rebuild_layer_options()
        self._apply_selected_layer_visibility()
        self.viewer.set_edge_plan(None)
        self._update_execution_controls()

    def _rebuild_layer_options(self) -> None:
        self.layer_by_label.clear()
        values = []
        if self.model is not None:
            for layer in self.model.layers:
                label = f"L{layer[0]} / D{layer[1]}"
                self.layer_by_label[label] = layer
                values.append(label)
        self.layer_combo["values"] = tuple(values)
        if not values:
            self.layer_var.set("")
            return
        visible_layers = [layer for layer, visible in self.layer_visibility.items() if visible]
        selected = visible_layers[0] if visible_layers else self.model.layers[0] if self.model is not None else None
        for label, layer in self.layer_by_label.items():
            if layer == selected:
                self.layer_var.set(label)
                return
        self.layer_var.set(values[0])

    def _selected_layer(self) -> tuple[int, int]:
        layer = self.layer_by_label.get(self.layer_var.get())
        if layer is None:
            raise ValueError("Select a GDS layer.")
        return layer

    def _apply_selected_layer_visibility(self) -> None:
        if self.model is None:
            return
        selected = self._selected_layer()
        for layer in self.model.layers:
            self.viewer.layer_visibility[layer] = layer == selected
        self.viewer.redraw()

    def _set_cursor_gds(self, point: tuple[float, float] | None) -> None:
        if point is None:
            self.cursor_var.set("Cursor u, v: -")
        else:
            self.cursor_var.set(f"Cursor u, v: {point[0]:.6g}, {point[1]:.6g}")

    def _handle_gds_click(self, u: float, v: float) -> None:
        self.selection_var.set(f"Selected: {u:.6g}, {v:.6g}")

    def _update_snap_grid(self, *, update_status: bool = True) -> None:
        grid_um = self.snap_grid_options.get(self.snap_grid_var.get(), 1.0)
        if hasattr(self, "viewer"):
            self.viewer.set_snap_grid_um(grid_um)
        if update_status:
            self.set_status(f"Cursor grid snap: {self.snap_grid_var.get()}.")

    def _on_mismatch_toggle(self) -> None:
        if self.accept_mismatch_var.get():
            self.set_status("Layout mismatch override enabled: EdgeTrace may use a GDS file that differs from the LayoutMap binding.")
        else:
            self.set_status("Layout mismatch override disabled: EdgeTrace requires the same GDS as the LayoutMap binding.")

    def _set_work_bounds_from_canvas(self, bounds: tuple[float, float, float, float]) -> None:
        self._set_work_bound_vars(bounds)
        self.viewer.set_work_bounds(bounds)
        self.plan = None
        self.viewer.set_edge_plan(None)
        self.path_summary_var.set("Path: not planned")
        self.set_status("Work range set from canvas.")
        self._update_execution_controls()

    def _set_work_bound_vars(self, bounds: tuple[float, float, float, float]) -> None:
        min_u, min_v, max_u, max_v = normalize_work_bounds(bounds)
        self.min_u_var.set(f"{min_u:.12g}")
        self.min_v_var.set(f"{min_v:.12g}")
        self.max_u_var.set(f"{max_u:.12g}")
        self.max_v_var.set(f"{max_v:.12g}")

    def _work_bounds_from_entries(self) -> tuple[float, float, float, float]:
        return normalize_work_bounds(
            (
                _entry_float(self.min_u_var.get(), "Min U"),
                _entry_float(self.min_v_var.get(), "Min V"),
                _entry_float(self.max_u_var.get(), "Max U"),
                _entry_float(self.max_v_var.get(), "Max V"),
            )
        )

    def apply_work_range_from_entries(self) -> None:
        try:
            bounds = self._work_bounds_from_entries()
        except ValueError as exc:
            self.set_status(str(exc))
            return
        self.viewer.set_work_bounds(bounds)
        self.plan = None
        self.viewer.set_edge_plan(None)
        self.path_summary_var.set("Path: not planned")
        self.set_status("Work range applied.")
        self._update_execution_controls()

    def clear_work_range(self) -> None:
        for variable in (self.min_u_var, self.min_v_var, self.max_u_var, self.max_v_var):
            variable.set("")
        self.viewer.set_work_bounds(None)
        self.plan = None
        self.viewer.set_edge_plan(None)
        self.path_summary_var.set("Path: not planned")
        self.set_status("Work range cleared.")
        self._update_execution_controls()

    def use_current_fov_as_range(self) -> None:
        mapper = self.get_mapper()
        if mapper is None:
            self.set_status("Bind LayoutMap mapping before using current FOV.")
            return
        try:
            x_um, y_um, _z_um = self._stage_position_xyz_um()
            width_um = _entry_float(self.fov_width_var.get(), "FOV width")
            height_um = _entry_float(self.fov_height_var.get(), "FOV height")
            corners = (
                mapper.stage_to_gds(x_um - width_um / 2.0, y_um - height_um / 2.0),
                mapper.stage_to_gds(x_um + width_um / 2.0, y_um - height_um / 2.0),
                mapper.stage_to_gds(x_um + width_um / 2.0, y_um + height_um / 2.0),
                mapper.stage_to_gds(x_um - width_um / 2.0, y_um + height_um / 2.0),
            )
            bounds = (min(point[0] for point in corners), min(point[1] for point in corners), max(point[0] for point in corners), max(point[1] for point in corners))
        except Exception as exc:
            self.set_status(f"Current FOV unavailable: {exc}")
            return
        self._set_work_bounds_from_canvas(bounds)

    def plan_path(self) -> None:
        if self.model is None:
            self.set_status("Load or sync a GDS file before planning.")
            return
        mapper = self.get_mapper()
        if mapper is None:
            self.set_status("Bind LayoutMap mapping before planning EdgeTrace.")
            return
        if not self.get_focusmap_ready():
            self.set_status("Run or load FocusMap before planning EdgeTrace.")
            return
        try:
            self._require_matching_layout_context()
            bounds = self._work_bounds_from_entries()
            layer = self._selected_layer()
            current_x, current_y, _current_z = self._stage_position_xyz_um()
            current_gds = mapper.stage_to_gds(current_x, current_y)
            plan = build_edge_trace_plan(
                self.model,
                layer,
                bounds,
                offset_um=_entry_float(self.offset_var.get(), "Offset"),
                scratch_depth_um=_entry_float(self.scratch_depth_var.get(), "Scratch depth"),
                lift_height_um=_entry_float(self.lift_height_var.get(), "Safe distance"),
                max_step_um=_entry_float(self.max_step_var.get(), "Mark step"),
                min_segment_um=_entry_float(self.min_segment_var.get(), "Min segment"),
                mapper=mapper,
                focus_z_at_stage_um=self.get_focus_z_um,
                current_gds=current_gds,
            )
        except Exception as exc:
            self.set_status(f"Path planning failed: {exc}")
            return
        self.set_plan(plan)
        warning_text = f" Warning: {'; '.join(plan.warnings)}" if plan.warnings else ""
        self.set_status(f"Planned {len(plan.polylines)} edge path(s), {plan.point_count} point(s).{warning_text}")

    def _require_matching_layout_context(self) -> None:
        if self.model is None or self.accept_mismatch_var.get():
            return
        layout_model, _visibility = self.get_layout_context()
        if layout_model is None:
            raise ValueError("LayoutMap context is unavailable; enable mismatch override or sync from LayoutMap.")
        try:
            same_path = Path(self.model.path).resolve() == Path(layout_model.path).resolve()
        except Exception:
            same_path = Path(self.model.path) == Path(layout_model.path)
        if not same_path:
            raise ValueError("Loaded GDS does not match LayoutMap binding. Sync from LayoutMap or enable mismatch override.")

    def set_plan(self, plan: EdgeTracePlan | None) -> None:
        self.plan = plan
        self.active_polyline_offset = 0
        self.prep_state = "needs_safe" if plan is not None and plan.polylines else "done"
        self.active_action = None
        self.viewer.set_edge_plan(plan)
        self.viewer.set_work_bounds(plan.work_bounds if plan is not None else self._safe_work_bounds())
        if plan is None:
            self.path_summary_var.set("Path: not planned")
        else:
            self.path_summary_var.set(
                f"Path: {len(plan.polylines)} edge(s), {plan.point_count} point(s), "
                f"scratch {plan.total_scratch_length_um:.3g} um, travel {plan.total_travel_length_um:.3g} um"
            )
        self._update_execution_controls()

    def _run_action(self, action: str) -> None:
        if self.plan is None:
            self.set_status("Plan a trace before running EdgeTrace.")
            return
        if action not in EDGE_TRACE_ACTIONS:
            self.set_status(f"Unknown EdgeTrace action: {action}.")
            return
        if self.active_polyline_offset >= len(self.plan.polylines):
            self.set_status("All EdgeTrace paths are complete.")
            return
        if action == EDGE_TRACE_ACTION_SAFE:
            self.prep_state = "running_safe"
        elif action == EDGE_TRACE_ACTION_START:
            self.prep_state = "running_start"
        elif action == EDGE_TRACE_ACTION_CONTACT:
            self.prep_state = "running_contact"
        elif action in {EDGE_TRACE_ACTION_SEGMENT, EDGE_TRACE_ACTION_AUTO}:
            self.prep_state = "running_trace"
        self.active_action = action
        self.start_run(self.plan, action, self.active_polyline_offset)
        self._update_execution_controls()

    def set_running(self, running: bool) -> None:
        self.running = bool(running)
        self._update_execution_controls()

    def set_action_done(self, action: str, polyline_offset: int) -> None:
        if self.plan is None:
            return
        if action == EDGE_TRACE_ACTION_SAFE:
            self.prep_state = "safe"
        elif action == EDGE_TRACE_ACTION_START:
            self.prep_state = "at_start"
        elif action == EDGE_TRACE_ACTION_CONTACT:
            self.prep_state = "contact"
        elif action == EDGE_TRACE_ACTION_SEGMENT:
            self.active_polyline_offset = min(polyline_offset + 1, len(self.plan.polylines))
            self.prep_state = "done" if self.active_polyline_offset >= len(self.plan.polylines) else "needs_safe"
        elif action == EDGE_TRACE_ACTION_AUTO:
            self.active_polyline_offset = len(self.plan.polylines)
            self.prep_state = "done"
        self.active_action = None
        self._update_execution_controls()

    def set_action_failed(self, action: str | None = None) -> None:
        action = action or self.active_action
        if action == EDGE_TRACE_ACTION_SAFE:
            self.prep_state = "needs_safe"
        elif action == EDGE_TRACE_ACTION_START:
            self.prep_state = "safe"
        elif action == EDGE_TRACE_ACTION_CONTACT:
            self.prep_state = "at_start"
        elif action in {EDGE_TRACE_ACTION_SEGMENT, EDGE_TRACE_ACTION_AUTO}:
            self.prep_state = "contact"
        self.active_action = None
        self._update_execution_controls()

    def set_progress(
        self,
        current: int,
        total: int,
        message: str,
        *,
        polyline_index: int | None = None,
        completed_polyline_count: int | None = None,
        needle_gds: tuple[float, float] | None = None,
    ) -> None:
        completed = completed_polyline_count
        if completed is None:
            completed = max(0, min(int(current) - 1, int(total)))
        self.viewer.set_edge_progress(
            completed_polyline_count=completed,
            current_polyline_index=polyline_index,
            needle_gds=needle_gds,
        )
        self.status_var.set(f"{message} ({current}/{total})")

    def set_status(self, message: str) -> None:
        self.status_var.set(message)
        if self.set_app_status is not None:
            self.set_app_status(message)

    def _safe_work_bounds(self) -> tuple[float, float, float, float] | None:
        try:
            return self._work_bounds_from_entries()
        except ValueError:
            return None

    def _stage_position_xyz_um(self) -> tuple[float, float, float]:
        values = self.get_stage_position_um()
        if len(values) == 2:
            x_um, y_um = values
            return float(x_um), float(y_um), 0.0
        x_um, y_um, z_um = values
        return float(x_um), float(y_um), float(z_um)

    def _schedule_status_poll(self) -> None:
        try:
            self._poll_status()
            self.status_poll_job = self.frame.after(350, self._schedule_status_poll)
        except tk.TclError:
            return

    def _poll_status(self) -> None:
        layout_ready = self.get_layoutmap_ready()
        focus_ready = self.get_focusmap_ready()
        self.focusmap_status_var.set("FocusMap: ready" if focus_ready else "FocusMap: missing plane")
        self.layoutmap_status_var.set("LayoutMap: ready" if layout_ready else "LayoutMap: missing mapping")
        self._set_prerequisite_chip(self.focusmap_status_label, focus_ready)
        self._set_prerequisite_chip(self.layoutmap_status_label, layout_ready)
        try:
            x_um, y_um, z_um = self._stage_position_xyz_um()
            self._set_stage_metric("stage_x", f"{x_um:.3f}")
            self._set_stage_metric("stage_y", f"{y_um:.3f}")
            self._set_stage_metric("stage_z", f"{z_um:.3f}")
            mapper = self.get_mapper()
            if mapper is None:
                self._set_stage_metric("gds_u", "-", available=False)
                self._set_stage_metric("gds_v", "-", available=False)
                self.viewer.set_current_needle_gds(None)
            else:
                u, v = mapper.stage_to_gds(x_um, y_um)
                self._set_stage_metric("gds_u", f"{u:.3f}")
                self._set_stage_metric("gds_v", f"{v:.3f}")
                self.viewer.set_current_needle_gds((u, v))
        except Exception:
            for key in ("stage_x", "stage_y", "stage_z", "gds_u", "gds_v"):
                self._set_stage_metric(key, "-", available=False)
            self.viewer.set_current_needle_gds(None)
        self._update_execution_controls()

    def _set_prerequisite_chip(self, label: tk.Label, ready: bool) -> None:
        if ready:
            label.configure(fg="#bbf7d0", bg="#052e24")
        else:
            label.configure(fg="#fecdd3", bg="#4c0519")

    def _schedule_microscope_preview_poll(self) -> None:
        try:
            self._update_microscope_preview()
            self.microscope_poll_job = self.frame.after(300, self._schedule_microscope_preview_poll)
        except tk.TclError:
            return

    def _update_microscope_preview(self) -> None:
        if self.get_microscope_preview is None or not hasattr(self, "microscope_label"):
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

    def _update_execution_controls(self) -> None:
        has_plan = self.plan is not None and bool(self.plan.polylines)
        has_range = self._safe_work_bounds() is not None
        ready = has_plan and has_range and self.get_layoutmap_ready() and self.get_focusmap_ready()
        has_active = ready and self.plan is not None and self.active_polyline_offset < len(self.plan.polylines)
        idle = not self.running
        safe_enabled = has_active and idle
        start_enabled = has_active and idle and self.prep_state == "safe"
        contact_enabled = has_active and idle and self.prep_state == "at_start"
        trace_enabled = has_active and idle and self.prep_state == "contact"
        self.safe_button.configure(state="normal" if safe_enabled else "disabled")
        self.start_button.configure(state="normal" if start_enabled else "disabled")
        self.contact_button.configure(state="normal" if contact_enabled else "disabled")
        self.segment_button.configure(state="normal" if trace_enabled else "disabled")
        self.auto_button.configure(state="normal" if trace_enabled else "disabled")
        self.stop_button.configure(state="normal" if self.running else "disabled")
        self.execution_step_var.set(self._execution_step_text())

    def _execution_step_text(self) -> str:
        if self.plan is None or not self.plan.polylines:
            return "Step: plan a trace"
        total = len(self.plan.polylines)
        current = min(self.active_polyline_offset + 1, total)
        if self.active_polyline_offset >= total:
            return f"Step: complete ({total}/{total})"
        labels = {
            "needs_safe": "run 1. Move Safe Z",
            "running_safe": "moving to safe Z",
            "safe": "run 2. Move Start",
            "running_start": "moving to path start",
            "at_start": "run 3. Contact",
            "running_contact": "raising to contact",
            "contact": "ready to execute",
            "running_trace": "executing trace",
        }
        return f"Path {current}/{total}: {labels.get(self.prep_state, self.prep_state)}"


def _same_optional_point(
    first: tuple[float, float] | None,
    second: tuple[float, float] | None,
    *,
    tolerance: float = 1e-9,
) -> bool:
    if first is None or second is None:
        return first is None and second is None
    return abs(first[0] - second[0]) <= tolerance and abs(first[1] - second[1]) <= tolerance


def _entry_float(text: str, label: str) -> float:
    value = str(text).strip()
    if not value:
        raise ValueError(f"{label} is required.")
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite.")
    return number
