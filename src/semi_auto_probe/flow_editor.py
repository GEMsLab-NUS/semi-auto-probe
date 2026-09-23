from __future__ import annotations

import math
import os
import heapq
import shutil
import json
import subprocess
import sys
import uuid
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .auto_test import (
    FLOW_CATEGORIES,
    AutoTestFlowCard,
    AutoTestFlowDefinition,
    AutoTestFlowParam,
    autotest_flow_definitions_by_type,
    create_autotest_flow_card,
    is_condition_flow_type,
    measurement_flow_steps_from_cards,
)
from .flow_graph import validate_flow_graph
from .workflow_ports import LIGHT_OUTPUT_TYPES, LIGHT_INPUT_TYPES
from .dp100 import DEFAULT_LASER_PARAMS
from .waveform import parse_waveform_points
from .workflow_library import DEFAULT_USERS, builtin_templates, cards_from_template, default_user_folder, ensure_workflow_library, load_workflow, overwrite_workflow, safe_name, save_workflow


NODE_WIDTH = 224
NODE_HEIGHT = 82
DIAMOND_WIDTH = 190
DIAMOND_HEIGHT = 108
CAPSULE_WIDTH = 112
CAPSULE_HEIGHT = 40
WORLD_EXTENT = 100_000
SNAP_GRID = 16
SNAP_TOLERANCE = 8
CANVAS_BG = "#202124"
NODE_BG = "#303135"
NODE_TEXT = "#eeeeee"
NODE_MUTED = "#b1b3ba"
INLINE_KEYS = {
    "iv": ("start", "initial_direction", "stop", "low", "segments", "step"),
    "photo_iv": ("start", "stop", "step"),
    "constant_current": ("voltage_v", "current_limit_a", "sample_count"),
    "laser_output": ("peak_voltage_v", "peak_current_a", "output_state"),
    "laser_pulse": ("peak_current_a", "width_ms", "count"),
    "laser_iv_calibration": ("calibration_current_min_a", "calibration_current_max_a", "calibration_current_step_a"),
    "photo_transient": ("bias_v", "duration_s", "sample_interval_ms"),
    "photo_power_steps": ("bias_v", "power_levels", "power_width_ms"),
    "photo_width_steps": ("bias_v", "width_min_ms", "width_max_ms"),
    "virtual_mask": ("black_power_mw", "white_power_mw", "settle_s"),
    "math_formula": ("expression", "output_type", "output_unit"),
    "plot_panel": ("layout",),
    "condition_metric": ("operator", "threshold"),
}
# A DC output can be a sequential setup node as well as a light-input source.
LIGHT_ONLY_TYPES = LIGHT_OUTPUT_TYPES - {"laser_output"}
BOUNDARY_TYPES = frozenset({"workflow_start", "workflow_end"})
from .workflow_ports import (DATA_ONLY_TYPES, DATA_INPUTS, DATA_OUTPUTS, RESULT_OUTPUTS, input_ports as registered_input_ports, visible_input_ports, output_ports, metric_type, metric_unit, compatible)
PORT_COLORS = {"control": "#38bdf8", "number": "#c084fc", "bool": "#fb923c", "waveform": "#facc15", "calibration": "#22c55e", "trace": "#38bdf8", "curve": "#818cf8", "plot": "#e879f9"}


def input_ports(card):
    # Partial text while typing an input declaration must not break canvas redraw.
    try:
        return visible_input_ports(card)
    except ValueError:
        return ()


PARAMETER_GROUPS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "virtual_mask": (
        ("Mask & Binding", ("image_path", "coordinate_frame", "bindings")),
        ("Power Mapping", ("black_power_mw", "white_power_mw", "invert_intensity")),
        ("Timing", ("settle_s",)),
    ),
    "laser_iv_calibration": (
        ("Calibration Sweep", ("calibration_voltage_v", "calibration_current_min_a", "calibration_current_max_a", "calibration_current_step_a")),
        ("Readback", ("calibration_settle_ms", "readback_timeout_s", "readback_interval_ms")),
        ("Protection", ("ovp_v", "ocp_a", "preset_index")),
    ),
    "photo_power_steps": (
        ("Detector", ("resource_name", "output_terminal", "bias_v", "current_limit_a", "measure_range", "verify_bias", "hold_during_mapping")),
        ("Acquisition", ("nplc", "sample_interval_ms")),
        ("Drive-power Sequence", ("power_min_mw", "power_max_mw", "power_levels", "power_width_ms", "pulse_gap_ms", "program_lead_ms")),
    ),
    "photo_width_steps": (
        ("Detector", ("resource_name", "output_terminal", "bias_v", "current_limit_a", "measure_range", "verify_bias", "hold_during_mapping")),
        ("Acquisition", ("nplc", "sample_interval_ms")),
        ("Pulse-width Sequence", ("width_power_mw", "width_min_ms", "width_max_ms", "width_step_ms", "pulse_gap_ms", "program_lead_ms")),
    ),
    "mapping_heatmap": (
        ("Plot Labels", ("primary_label", "secondary_label")),
    ),
    "constant_current": (
        ("Instrument", ("resource_name", "output_terminal")),
        ("Fixed Bias", ("voltage_v", "current_limit_a", "measure_range", "verify_bias")),
        ("Acquisition", ("nplc", "sample_count", "settle_s", "hold_during_mapping")),
        ("TEMT6000 Irradiance Estimate", ("optical_model", "wavelength_nm", "power_correction", "dark_current_a")),
    ),
    "laser_output": (
        ("Output", ("peak_voltage_v", "peak_current_a", "output_state", "preset_index")),
        ("Protection", ("ovp_v", "ocp_a")),
        ("Mapping", ("mapping_once", "settle_s")),
    ),
    "laser_pulse": (
        ("Levels", ("peak_voltage_v", "peak_current_a", "baseline_voltage_v", "baseline_current_a")),
        ("Timing", ("delay_ms", "rise_ms", "width_ms", "fall_ms", "period_ms", "count")),
        ("Protection & Finish", ("ovp_v", "ocp_a", "final_state", "preset_index")),
        ("Calibrated response suite", ("program", "calibration_voltage_v", "calibration_settle_ms", "intensity_min_a", "intensity_max_a", "intensity_step_a", "intensity_width_ms", "pulse_gap_ms", "program_lead_ms", "width_current_a", "width_min_ms", "width_max_ms", "width_step_ms")),
    ),
    "iv": (
        ("Instrument", ("resource", "output_terminal")),
        ("Sweep", ("sweep_mode", "start", "initial_direction", "stop", "low", "segments", "step")),
        ("Limits & Range", ("voltage_limit_v", "current_limit_a", "source_range", "measure_range")),
        ("Acquisition", ("source_delay_s", "nplc", "autozero", "output_off_after")),
        ("Result", ("output_statistics", "resistance_method", "plot_layout", "heatmap_values")),
    ),
    "wobb_test": (
        ("Instrument", ("resource", "output_terminal", "mode")),
        ("Electrical", ("bias_v", "current_limit_a", "nplc", "sample_count")),
        ("Z Search", ("z_lower_um", "z_upper_um", "z_step_um")),
        ("XY Search", ("xy_range_um", "xy_step_um", "xy_pattern")),
        ("Selection", ("settle_s", "best_current")),
    ),
    "hp6614c_transfer": (
        ("Instruments", ("hp_resource", "keithley_resource", "output_terminal", "device_name")),
        ("Sweep", ("sweep_start_v", "sweep_end_v", "sweep_points", "scan_type", "bias_values_v")),
        ("Limits & Range", ("drain_current_limit_a", "drain_measure_range", "gate_current_limit_a")),
        ("Acquisition", ("drain_nplc", "sample_count", "gate_settle_s", "step_delay_s", "pre_settle_s", "post_sweep_pause_s")),
        ("Result", ("heatmap_metric", "heatmap_values")),
    ),
    "hp6614c_output": (
        ("Instruments", ("hp_resource", "keithley_resource", "output_terminal", "device_name")),
        ("Sweep", ("sweep_start_v", "sweep_end_v", "sweep_points", "bias_values_v")),
        ("Limits & Range", ("drain_current_limit_a", "drain_measure_range", "gate_current_limit_a")),
        ("Acquisition", ("drain_nplc", "sample_count", "gate_settle_s", "step_delay_s", "pre_settle_s", "post_sweep_pause_s")),
        ("Result", ("heatmap_values",)),
    ),
    "b1500_transfer": (
        ("Device & Instrument", ("resource", "device_name", "experiment", "sample", "drain_smu", "gate_smu")),
        ("Sweep", ("sweep_start_v", "sweep_end_v", "sweep_points", "scan_type", "bias_values_v")),
        ("Compliance", ("drain_current_compliance_a", "gate_current_compliance_a", "measure_gate_leak", "abort_on_compliance")),
        ("Acquisition", ("staircase_nplc", "step_delay_s", "pre_settle_s", "post_sweep_pause_s", "measurement_adc", "autozero")),
        ("Result", ("save_mode",)),
    ),
    "b1500_output": (
        ("Device & Instrument", ("resource", "device_name", "experiment", "sample", "drain_smu", "gate_smu")),
        ("Sweep", ("sweep_start_v", "sweep_end_v", "sweep_points", "bias_values_v")),
        ("Compliance", ("drain_current_compliance_a", "gate_current_compliance_a", "measure_gate_leak", "abort_on_compliance")),
        ("Acquisition", ("staircase_nplc", "step_delay_s", "pre_settle_s", "post_sweep_pause_s", "measurement_adc", "autozero")),
        ("Result", ("save_mode",)),
    ),
    "photo_iv": (
        ("Instrument", ("resource", "output_terminal")),
        ("Sweep", ("start", "stop", "step", "current_limit_a")),
        ("Light Reference", ("dark_reference", "light_settle_s")),
        ("Extraction", ("metric", "bias_v")),
    ),
    "photo_transient": (
        ("Instrument", ("resource_name", "output_terminal")),
        ("Electrical", ("bias_v", "current_limit_a", "measure_range", "verify_bias", "hold_during_mapping")),
        ("Acquisition", ("nplc", "duration_s", "sample_interval_ms", "trigger_delay_ms")),
        ("Extraction", ("extract",)),
    ),
    "condition_metric": (
        ("Result Source", ("source_node", "metric")),
        ("Comparison", ("operator", "threshold", "missing_result")),
    ),
    "condition_device_state": (
        ("Result Source", ("source_node", "state")),
        ("Thresholds", ("resistance_threshold_ohm", "current_threshold_a", "missing_result")),
    ),
    "condition_compliance": (
        ("Result Source", ("source_node",)),
        ("Decision", ("expected", "missing_result")),
    ),
    "math_formula": (("Calculation", ("operator", "fallback_a", "fallback_b")),),
    "boolean_compare": (("Comparison", ("operator", "threshold", "missing_result")),),
    "boolean_logic": (("Logic", ("operator", "fallback_a", "fallback_b")),),
    "condition_boolean": (("Branch", ("fallback",)),),
}

WIDE_PARAMETER_KEYS = frozenset({
    "resource", "hp_resource", "keithley_resource", "device_name", "reason", "label",
    "source_node", "metric", "bias_values_v", "resistance_threshold_ohm", "image_path", "bindings",
})


from .flow_editor_ux import FlowEditorUX


class MeasurementFlowEditor(FlowEditorUX):
    def __init__(
        self,
        parent: tk.Widget,
        cards: list[AutoTestFlowCard],
        colors: dict[str, str],
        allocate_id: Callable[[], str],
        on_change: Callable[[], None],
        *,
        use_parent_window: bool = False,
    ) -> None:
        self._init_ux()
        self.cards = cards
        self.colors = colors
        self.allocate_id = allocate_id
        self.on_change = on_change
        self.definitions = autotest_flow_definitions_by_type()
        self.selected_id: str | None = None
        self.drag_id: str | None = None
        self.drag_offset = (0.0, 0.0)
        self.pending_edge: tuple[str, str] | None = None
        self._edge_dragging = False
        self.zoom = 1.0
        self._canvas_pan_active = False
        self._cut_start: tuple[float, float] | None = None
        self._library_insert_edge: tuple[str, str, str] | None = None
        self._initial_fit_pending = True
        self.library_open = {key: key in {"electrical", "light", "math", "conditions"} for key, _label, _items in FLOW_CATEGORIES}
        self.param_vars: dict[str, tk.StringVar] = {}
        self.output_vars: dict[str, tk.BooleanVar] = {}
        self.wave_drag: str | None = None
        self._waveform_editor_process: subprocess.Popen[bytes] | None = None

        self.window = parent if use_parent_window else tk.Toplevel(parent)
        self.window.help_topic = "WorkflowEditor"
        self.window.title("AutoTest Measurement Flow")
        self.window.geometry("1480x820")
        self.window.minsize(1120, 640)
        self.window.configure(bg=colors["surface"])
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        self._configure_editor_styles()

        self._build_header()
        self.body_pane = tk.PanedWindow(
            self.window,
            orient=tk.HORIZONTAL,
            bg=self.colors["border"],
            bd=0,
            sashwidth=7,
            sashrelief=tk.FLAT,
            showhandle=False,
        )
        self.body_pane.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 8))
        self._build_library()
        self._build_canvas()
        self._build_inspector()
        self._build_footer()
        self.window.bind("<Delete>", self._delete_selected)
        self.window.bind("<Control-z>", lambda event: self._history_move(-1, event))
        self.window.bind("<Control-y>", lambda event: self._history_move(1, event))
        self.window.bind("<Control-Shift-Z>", lambda event: self._history_move(1, event))
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._hydrate_positions()
        self._ensure_boundary_nodes()
        self.redraw()
        self._record_history()
        self.window.after_idle(self._set_initial_sidebar_width)
        if not use_parent_window:
            self.window.grab_set()

    def _set_initial_sidebar_width(self) -> None:
        try:
            self.body_pane.sash_place(0, 300, 0)
            self._fit_nodes()
        except tk.TclError:
            pass

    def _configure_editor_styles(self) -> None:
        style = ttk.Style(self.window)
        style.configure("Flow.Invalid.TEntry", fieldbackground="#522d35", foreground="#ffffff", bordercolor="#f87171")
        style.map("Flow.Invalid.TEntry", fieldbackground=[("!disabled", "#522d35")], foreground=[("!disabled", "#ffffff")])
        style.configure("Flow.TNotebook", background=self.colors["surface_2"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("Flow.TNotebook.Tab", background=self.colors["input"], foreground=self.colors["muted"], width=12, padding=(10, 8), borderwidth=1)
        style.map("Flow.TNotebook.Tab", background=[("selected", self.colors["surface_3"]), ("active", self.colors["border"])], foreground=[("selected", self.colors["text"]), ("active", self.colors["text"])])

    def _build_header(self) -> None:
        header = tk.Frame(self.window, bg=self.colors["surface"], padx=16, pady=12)
        self.header = header
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        tk.Label(header, text="Measurement Flow", bg=self.colors["surface"], fg=self.colors["text"], font=("Segoe UI Semibold", 15)).grid(row=0, column=0, sticky="w")
        tk.Label(header, text="Connect colored sockets. Click a value to edit; double-click a node title for all parameters.", bg=self.colors["surface"], fg=self.colors["muted"], font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(2, 0))
        legend = tk.Frame(header, bg=self.colors["surface"])
        legend.grid(row=2, column=0, sticky="w", pady=(7, 0))
        for column, (label, color) in enumerate((
            ("Flow", PORT_COLORS["control"]),
            ("Number", PORT_COLORS["number"]),
            ("Boolean", PORT_COLORS["bool"]),
            ("Waveform", PORT_COLORS["waveform"]),
            ("Calibration", PORT_COLORS["calibration"]),
            ("Curve", PORT_COLORS["curve"]),
            ("Plot", PORT_COLORS["plot"]),
        )):
            swatch = tk.Canvas(legend, width=12, height=12, bg=self.colors["surface"], bd=0, highlightthickness=0)
            swatch.create_oval(2, 2, 10, 10, fill=color, outline=color)
            swatch.grid(row=0, column=column * 2, padx=(8 if column else 0, 2))
            tk.Label(legend, text=label, bg=self.colors["surface"], fg=self.colors["muted"], font=("Segoe UI", 8)).grid(row=0, column=column * 2 + 1)
        ttk.Button(header, text="Fit", command=self._fit_nodes).grid(row=0, column=8, rowspan=2, padx=(8, 0))

    def _build_canvas(self) -> None:
        holder = tk.Frame(self.body_pane, bg=self.colors["surface"])
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(holder, bg=CANVAS_BG, bd=0, highlightthickness=0, scrollregion=(-WORLD_EXTENT, -WORLD_EXTENT, WORLD_EXTENT, WORLD_EXTENT))
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Button-1>", self._canvas_press)
        self.canvas.bind("<Double-1>", self._canvas_double_click)
        self.canvas.bind("<B1-Motion>", self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)
        self.canvas.bind("<Motion>", self._canvas_motion)
        self.canvas.bind("<Escape>", self._cancel_connection)
        self.canvas.bind("<ButtonPress-2>", self._start_canvas_pan)
        self.canvas.bind("<B2-Motion>", self._move_canvas_pan)
        self.canvas.bind("<ButtonRelease-2>", self._end_canvas_pan)
        self.canvas.bind("<ButtonPress-3>", self._start_edge_cut)
        self.canvas.bind("<B3-Motion>", self._move_edge_cut)
        self.canvas.bind("<ButtonRelease-3>", self._end_edge_cut)
        self.canvas.bind("<MouseWheel>", self._zoom_canvas)
        self.canvas.bind("<Button-4>", self._zoom_canvas)
        self.canvas.bind("<Button-5>", self._zoom_canvas)
        self.canvas.bind("<Configure>", self._canvas_configured, add="+")
        self.body_pane.add(holder, minsize=420, stretch="always")

    def _build_inspector(self) -> None:
        self.inspector = tk.Frame(self.body_pane, bg=self.colors["surface_2"], width=380, padx=12, pady=12)
        self.inspector.columnconfigure(0, weight=1)
        self.inspector.rowconfigure(2, weight=1)
        inspector_header = tk.Frame(self.inspector, bg=self.colors["surface_2"])
        inspector_header.grid(row=0, column=0, columnspan=2, sticky="ew")
        inspector_header.columnconfigure(0, weight=1)
        self.inspector_title = tk.Label(inspector_header, text="Node parameters", bg=self.colors["surface_2"], fg=self.colors["text"], font=("Segoe UI Semibold", 11), anchor="w")
        self.inspector_title.grid(row=0, column=0, sticky="ew")
        ttk.Button(inspector_header, text="Close", command=self._hide_inspector).grid(row=0, column=1, sticky="e")
        self.inspector_summary = tk.Label(self.inspector, text="", bg=self.colors["surface_2"], fg=self.colors["muted"], font=("Segoe UI", 8), anchor="w", justify="left", wraplength=340)
        self.inspector_summary.grid(row=1, column=0, sticky="ew", pady=(3, 8))
        self.param_canvas = tk.Canvas(self.inspector, bg=self.colors["surface_2"], highlightthickness=0, bd=0)
        self.param_canvas.grid(row=2, column=0, sticky="nsew")
        bar = ttk.Scrollbar(self.inspector, orient=tk.VERTICAL, command=self.param_canvas.yview)
        bar.grid(row=2, column=1, sticky="ns")
        self.param_canvas.configure(yscrollcommand=bar.set)
        self.param_body = tk.Frame(self.param_canvas, bg=self.colors["surface_2"])
        self.param_window = self.param_canvas.create_window(0, 0, window=self.param_body, anchor="nw")
        self.param_body.columnconfigure(0, weight=1)
        self.param_body.bind("<Configure>", lambda _event: self.param_canvas.configure(scrollregion=self.param_canvas.bbox("all")))
        self.param_canvas.bind("<Configure>", lambda event: self.param_canvas.itemconfigure(self.param_window, width=event.width))
        self.delete_node_button = ttk.Button(self.inspector, text="Delete node", style="Danger.TButton", command=self._delete_inspected)
        self.delete_node_button.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))

    def _build_library(self) -> None:
        panel = tk.Frame(self.body_pane, bg=self.colors["surface_2"], width=300, padx=10, pady=10)
        self.library_panel = panel
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        tabbar = tk.Frame(panel, bg=self.colors["surface_2"])
        tabbar.grid(row=0, column=0, sticky="ew")
        tabbar.columnconfigure((0, 1), weight=1, uniform="library_tabs")
        self.library_tab_buttons: dict[str, tk.Button] = {}
        for column, (key, label) in enumerate((("nodes", "Nodes"), ("workflows", "Workflows"))):
            button = tk.Button(tabbar, text=label, command=lambda value=key: self._select_library_tab(value), bg=self.colors["input"], fg=self.colors["muted"], activebackground=self.colors["surface_3"], activeforeground=self.colors["text"], relief="flat", bd=0, highlightthickness=1, highlightbackground=self.colors["border"], padx=10, pady=8, font=("Segoe UI Semibold", 9))
            button.grid(row=0, column=column, sticky="ew")
            self.library_tab_buttons[key] = button
        tab_content = tk.Frame(panel, bg=self.colors["surface_2"])
        tab_content.grid(row=1, column=0, sticky="nsew")
        tab_content.columnconfigure(0, weight=1)
        tab_content.rowconfigure(0, weight=1)
        nodes_tab = tk.Frame(tab_content, bg=self.colors["surface_2"], padx=6, pady=8)
        workflows_tab = tk.Frame(tab_content, bg=self.colors["surface_2"], padx=8, pady=8)
        self.library_tabs = {"nodes": nodes_tab, "workflows": workflows_tab}
        nodes_tab.columnconfigure(0, weight=1)
        nodes_tab.rowconfigure(0, weight=1)
        self.library_canvas = tk.Canvas(nodes_tab, bg=self.colors["surface_2"], bd=0, highlightthickness=0)
        self.library_canvas.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(nodes_tab, orient=tk.VERTICAL, command=self.library_canvas.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.library_canvas.configure(yscrollcommand=bar.set)
        self.library_body = tk.Frame(self.library_canvas, bg=self.colors["surface_2"])
        window = self.library_canvas.create_window(0, 0, window=self.library_body, anchor="nw")
        self.library_body.columnconfigure(0, weight=1)
        self.library_body.bind("<Configure>", lambda _event: self.library_canvas.configure(scrollregion=self.library_canvas.bbox("all")))
        self.library_canvas.bind("<Configure>", lambda event: self.library_canvas.itemconfigure(window, width=event.width))
        self._render_library()
        self._build_workflow_library(workflows_tab)
        self._select_library_tab("nodes")
        self.body_pane.add(panel, minsize=230, width=300, stretch="never")

    def _select_library_tab(self, key: str) -> None:
        for name, frame in self.library_tabs.items():
            if name == key:
                frame.grid(row=0, column=0, sticky="nsew")
                self.library_tab_buttons[name].configure(bg=self.colors["surface_3"], fg=self.colors["text"], highlightbackground=self.colors["border_focus"])
            else:
                frame.grid_remove()
                self.library_tab_buttons[name].configure(bg=self.colors["input"], fg=self.colors["muted"], highlightbackground=self.colors["border"])

    def _build_footer(self) -> None:
        footer = tk.Frame(self.window, bg=self.colors["surface"])
        footer.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 10))
        self.footer = footer
        footer.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Drag nodes • Click values to edit • Double-click title for details • Wheel to zoom • Right-drag to cut wires")
        tk.Label(footer, textvariable=self.status_var, bg=self.colors["surface"], fg=self.colors["muted"], font=("Segoe UI", 9), anchor="w").grid(row=0, column=0, sticky="ew")
        self.undo_button = ttk.Button(footer, text="Undo", command=lambda: self._history_move(-1))
        self.undo_button.grid(row=1, column=1, padx=6, pady=4)
        self.redo_button = ttk.Button(footer, text="Redo", command=lambda: self._history_move(1))
        self.redo_button.grid(row=1, column=2, padx=6, pady=4)
        ttk.Button(footer, text="Validate", command=self._validate).grid(row=0, column=1, padx=(8, 6))
        ttk.Button(footer, text="Clear", command=self._clear).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(footer, text="Close", command=self.close).grid(row=0, column=3)

    def _render_library(self) -> None:
        for child in self.library_body.winfo_children():
            child.destroy()
        row = 0
        for key, label, type_ids in FLOW_CATEGORIES:
            open_now = self.library_open[key]
            button = tk.Button(self.library_body, text=("v  " if open_now else ">  ") + label, command=lambda value=key: self._toggle_library(value), bg=self.colors["surface_3"], fg=self.colors["text"], activebackground=self.colors["input"], activeforeground=self.colors["text"], relief="flat", anchor="w", padx=8, pady=7, font=("Segoe UI Semibold", 9))
            button.grid(row=row, column=0, sticky="ew", pady=(0, 5))
            row += 1
            if not open_now:
                continue
            for type_id in type_ids:
                definition = self.definitions[type_id]
                card = tk.Frame(self.library_body, bg=self.colors["surface"], highlightthickness=1, highlightbackground=self.colors["border"], cursor="hand2")
                card.grid(row=row, column=0, sticky="ew", pady=(0, 6), padx=(6, 0))
                card.columnconfigure(1, weight=1)
                if is_condition_flow_type(type_id):
                    stripe = tk.Canvas(card, width=5, height=1, bg=self.colors["surface"], highlightthickness=0, bd=0)
                    stripe.grid(row=0, column=0, rowspan=2, sticky="ns")
                    stripe.bind("<Configure>", lambda event, canvas=stripe, color=definition.accent: self._draw_dashed_stripe(canvas, color, event.height))
                else:
                    tk.Frame(card, width=4, bg=definition.accent).grid(row=0, column=0, rowspan=2, sticky="ns")
                title = tk.Label(card, text=definition.title, bg=self.colors["surface"], fg=self.colors["text"], font=("Segoe UI Semibold", 9), anchor="w", padx=8, pady=5)
                title.grid(row=0, column=1, sticky="ew")
                desc = tk.Label(card, text=definition.description, bg=self.colors["surface"], fg=self.colors["muted"], font=("Segoe UI", 8), anchor="w", justify="left", padx=8, pady=3, wraplength=218)
                desc.grid(row=1, column=1, sticky="ew", pady=(0, 4))
                for widget in (card, title, desc):
                    widget.bind("<ButtonPress-1>", lambda event, value=type_id: self._library_press(value, event))
                    widget.bind("<B1-Motion>", self._library_drag)
                    widget.bind("<ButtonRelease-1>", self._library_release)
                row += 1

    @staticmethod
    def _draw_dashed_stripe(canvas: tk.Canvas, color: str, height: int) -> None:
        canvas.delete("all")
        canvas.create_line(2, 0, 2, max(1, height), fill=color, width=3, dash=(5, 4))

    def _build_workflow_library(self, parent: tk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        tree_holder = tk.Frame(parent, bg=self.colors["surface_2"])
        tree_holder.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        tree_holder.columnconfigure(0, weight=1)
        tree_holder.rowconfigure(0, weight=1)
        self.workflow_tree = ttk.Treeview(tree_holder, columns=("kind", "ref"), displaycolumns=(), show="tree", selectmode="browse")
        self.workflow_tree.grid(row=0, column=0, sticky="nsew")
        tree_bar = ttk.Scrollbar(tree_holder, orient=tk.VERTICAL, command=self.workflow_tree.yview)
        tree_bar.grid(row=0, column=1, sticky="ns")
        self.workflow_tree.configure(yscrollcommand=tree_bar.set)
        self.workflow_tree.bind("<Double-1>", lambda _event: self._load_selected_workflow())
        self.workflow_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_workflow_actions())
        actions = tk.Frame(parent, bg=self.colors["surface_2"])
        actions.grid(row=1, column=0, sticky="ew")
        actions.columnconfigure((0, 1), weight=1, uniform="workflow_actions")
        specs = (
            ("Save", self._save_current_workflow, 0, 0),
            ("Overwrite", self._overwrite_selected_workflow, 0, 1),
            ("Load", self._load_selected_workflow, 1, 0),
            ("Copy", lambda: self._transfer_workflow(move=False), 1, 1),
            ("Move", lambda: self._transfer_workflow(move=True), 2, 0),
            ("Delete", self._delete_workflow_item, 2, 1),
            ("New Folder", self._new_workflow_folder, 3, 0),
        )
        self.workflow_actions: dict[str, ttk.Button] = {}
        for label, command, row, column in specs:
            button = ttk.Button(actions, text=label, command=command)
            button.grid(row=row, column=column, sticky="ew", padx=(0, 3) if column == 0 else (3, 0), pady=(6 if row else 0, 0))
            self.workflow_actions[label] = button
        self._refresh_workflow_tree()

    def _refresh_workflow_tree(self) -> None:
        self.workflow_tree.delete(*self.workflow_tree.get_children())
        root = ensure_workflow_library()
        template_root = self.workflow_tree.insert("", "end", text="Template  [locked]", open=True, values=("template_root", ""))
        from .workflow_library import template_tree
        def insert_templates(parent, children):
            for label, value in children.items():
                if isinstance(value, dict):
                    category = self.workflow_tree.insert(parent, "end", text=label,
                        open=False, values=("template_category", ""))
                    insert_templates(category, value)
                else:
                    self.workflow_tree.insert(parent, "end", text=label, values=("template", value))
        insert_templates(template_root, template_tree())
        for username in DEFAULT_USERS:
            folder = root / username
            item = self.workflow_tree.insert("", "end", text=username, open=True, values=("user_root", str(folder)))
            self._populate_workflow_tree(item, folder)
        self._update_workflow_actions()

    def _populate_workflow_tree(self, parent_item: str, path: Path) -> None:
        for child in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            if child.is_dir():
                item = self.workflow_tree.insert(parent_item, "end", text=child.name, open=False, values=("folder", str(child)))
                self._populate_workflow_tree(item, child)
            elif child.suffix.lower() == ".json":
                self.workflow_tree.insert(parent_item, "end", text=child.stem, values=("workflow", str(child)))

    def _selected_workflow_item(self) -> tuple[str, str]:
        selected = self.workflow_tree.selection()
        if not selected:
            return "user_root", str(default_user_folder())
        values = self.workflow_tree.item(selected[0], "values")
        return (str(values[0]), str(values[1])) if len(values) >= 2 else ("user_root", str(default_user_folder()))

    def _selected_workflow_path(self) -> Path:
        kind, reference = self._selected_workflow_item()
        if kind in {"user_root", "folder", "workflow"}:
            return Path(reference)
        return default_user_folder()

    def _update_workflow_actions(self) -> None:
        if not hasattr(self, "workflow_actions"):
            return
        kind, _reference = self._selected_workflow_item()
        mutable = kind in {"folder", "workflow"}
        loadable = kind in {"template", "workflow"}
        writable = kind in {"user_root", "folder", "workflow"}
        states = {
            "Save": writable,
            "Overwrite": kind == "workflow",
            "New Folder": writable,
            "Load": loadable,
            "Copy": kind in {"template", "folder", "workflow"},
            "Move": mutable,
            "Delete": mutable,
        }
        for label, enabled in states.items():
            self.workflow_actions[label].configure(state="normal" if enabled else "disabled")

    def _load_template(self, name: str) -> None:
        loaded = cards_from_template(name)
        self._reset_edit_feedback()
        self.cards[:] = loaded
        self._ensure_boundary_nodes()
        self.selected_id = None
        self._hide_inspector()
        self._changed()
        self._fit_nodes()
        self.status_var.set(f"Loaded template: {name}")

    def _new_workflow_folder(self) -> None:
        name = simpledialog.askstring("New workflow folder", "Folder name:", parent=self.window)
        if not name:
            return
        base = self._selected_workflow_path()
        if base.is_file():
            base = base.parent
        (base / safe_name(name)).mkdir(parents=True, exist_ok=True)
        self._refresh_workflow_tree()

    def _save_current_workflow(self) -> None:
        self._finish_inline_edit()
        if self._field_errors:
            self._validate()
            self.status_var.set("Correct or cancel the highlighted parameter drafts before saving.")
            return
        name = simpledialog.askstring("Save workflow", "Workflow name:", parent=self.window)
        if not name:
            return
        base = self._selected_workflow_path()
        if base.is_file():
            base = base.parent
        target = (base / safe_name(name)).with_suffix(".json")
        if target.exists():
            self.status_var.set(f"{target.name} already exists. Select it and use Overwrite.")
            return
        path = save_workflow(target, self.cards, name=name)
        self._refresh_workflow_tree()
        self.status_var.set(f"Saved {path.name}")

    def _overwrite_selected_workflow(self) -> None:
        kind, reference = self._selected_workflow_item()
        if kind != "workflow":
            return
        self._finish_inline_edit()
        if self._field_errors:
            self._validate()
            self.status_var.set("Correct or cancel the highlighted parameter drafts before saving.")
            return
        path = Path(reference)
        root = ensure_workflow_library().resolve()
        if not path.is_file() or not path.resolve().is_relative_to(root):
            messagebox.showerror("Overwrite workflow", "Select an existing user workflow to overwrite.", parent=self.window)
            return
        try:
            overwrite_workflow(path, self.cards)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Overwrite workflow", str(exc), parent=self.window)
            return
        self.status_var.set(f"Overwrote {path.name}")

    def _load_selected_workflow(self) -> None:
        kind, reference = self._selected_workflow_item()
        if kind == "template":
            self._load_template(reference)
            return
        path = Path(reference) if kind == "workflow" else Path()
        if kind != "workflow" or not path.is_file():
            return
        try:
            loaded = load_workflow(path)
        except Exception as exc:
            messagebox.showerror("Load workflow", str(exc), parent=self.window)
            return
        self._reset_edit_feedback()
        self.cards[:] = loaded
        self._ensure_boundary_nodes()
        self.selected_id = None
        self._hide_inspector()
        self._changed()
        self._fit_nodes()
        self.status_var.set(f"Loaded {path.name}")

    def _transfer_workflow(self, *, move: bool) -> None:
        kind, reference = self._selected_workflow_item()
        if kind not in {"template", "folder", "workflow"} or (move and kind == "template"):
            return
        destination_text = simpledialog.askstring(
            "Move workflow" if move else "Copy workflow",
            "Destination under the user library:",
            initialvalue="Xinchuan",
            parent=self.window,
        )
        if not destination_text:
            return
        root = ensure_workflow_library().resolve()
        pieces = [safe_name(piece) for piece in destination_text.replace("\\", "/").split("/") if piece.strip()]
        if not pieces or pieces[0] not in DEFAULT_USERS:
            messagebox.showerror("Workflow library", "Destination must start with Xinchuan, Leo, or Micheal.", parent=self.window)
            return
        destination = root.joinpath(*pieces)
        destination.mkdir(parents=True, exist_ok=True)
        if kind == "template":
            save_workflow(destination / safe_name(reference), cards_from_template(reference), name=reference)
        else:
            source = Path(reference)
            target = destination / source.name
            if target.exists():
                messagebox.showerror("Workflow library", f"{target.name} already exists.", parent=self.window)
                return
            if move:
                shutil.move(str(source), str(target))
            elif source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        self._refresh_workflow_tree()

    def _delete_workflow_item(self) -> None:
        kind, reference = self._selected_workflow_item()
        if kind not in {"folder", "workflow"}:
            return
        path = Path(reference)
        if not messagebox.askyesno("Delete workflow", f"Delete {path.name}?", parent=self.window):
            return
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        self._refresh_workflow_tree()

    def _open_workflow_folder(self) -> None:
        path = self._selected_workflow_path()
        folder = path if path.is_dir() else path.parent
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def _next_card_id(self) -> str:
        existing = {card.card_id for card in self.cards}
        while True:
            candidate = self.allocate_id()
            if candidate not in existing:
                return candidate

    def create_waveform_node(self) -> None:
        center_x = self.canvas.canvasx(self.canvas.winfo_width() / 2) / self.zoom
        center_y = self.canvas.canvasy(self.canvas.winfo_height() / 2) / self.zoom
        card = create_autotest_flow_card("laser_pulse", self._next_card_id())
        card.x = center_x - NODE_WIDTH / 2
        card.y = center_y - NODE_HEIGHT / 2
        self.cards.append(card)
        self.selected_id = card.card_id
        self._changed()
        self._show_inspector(card)
        self.status_var.set("Waveform added. Edit its levels and timing in the right panel.")
        self.window.after_idle(lambda target=card: self._open_waveform_editor(target))

    def _canvas_double_click(self, _event: tk.Event) -> str:
        current = self.canvas.find_withtag("current")
        tags = self.canvas.gettags(current[0]) if current else ()
        marker = next((tag for tag in tags if tag.startswith("node:")), None)
        card = self._card(marker.split(":", 1)[1]) if marker else None
        if card is not None and not any(tag.startswith(("port:", "input:")) for tag in tags):
            self.drag_id = None
            if self._inspector_is_visible() and getattr(self, "_inspected_id", None) == card.card_id:
                self._hide_inspector()
            else:
                self.selected_id = card.card_id
                self._show_inspector(card)
            self.redraw()
        return "break"

    def _toggle_library(self, key: str) -> None:
        self.library_open[key] = not self.library_open[key]
        self._render_library()

    def _library_press(self, type_id: str, event: tk.Event) -> str:
        self._library_drag_type = type_id
        self._library_drag_root = (event.x_root, event.y_root)
        self.status_var.set("Drop the node on the canvas.")
        return "break"

    def _library_drag(self, event: tk.Event) -> str:
        self.status_var.set(f"Adding {self.definitions[self._library_drag_type].title}...")
        self.canvas.delete("library_ghost")
        self._library_insert_edge = None
        if self._inside_canvas(event.x_root, event.y_root):
            x = self.canvas.canvasx(event.x_root - self.canvas.winfo_rootx()) / self.zoom
            y = self.canvas.canvasy(event.y_root - self.canvas.winfo_rooty()) / self.zoom
            width = NODE_WIDTH
            height = 180
            x, y = self._snap_to_grid(x - width / 2), self._snap_to_grid(y - height / 2)
            self._draw_library_ghost(self._library_drag_type, x, y, width, height)
            if self._library_drag_type not in LIGHT_ONLY_TYPES:
                self._library_insert_edge = self._nearest_flow_edge(x + width / 2, y + height / 2, max_distance=24)
                if self._library_insert_edge is not None:
                    self._highlight_insert_edge(self._library_insert_edge)
        return "break"

    def _library_release(self, event: tk.Event) -> str:
        insert_edge = self._library_insert_edge
        self.canvas.delete("library_ghost")
        self._library_insert_edge = None
        if self._inside_canvas(event.x_root, event.y_root):
            x = self.canvas.canvasx(event.x_root - self.canvas.winfo_rootx()) / self.zoom
            y = self.canvas.canvasy(event.y_root - self.canvas.winfo_rooty()) / self.zoom
            card = create_autotest_flow_card(self._library_drag_type, self._next_card_id())
            card.x, card.y = self._snap_to_grid(x - NODE_WIDTH / 2), self._snap_to_grid(y - NODE_HEIGHT / 2)
            self.cards.append(card)
            if insert_edge is not None:
                source_id, key, target_id = insert_edge
                source = self._card(source_id)
                if source is not None and str(source.params.get(key, "")) == target_id:
                    source.params[key] = card.card_id
                    card.params["_true" if is_condition_flow_type(card.type_id) else "_next"] = target_id
            self.selected_id = card.card_id
            self._changed()
            self.status_var.set(f"Added {self.definitions[card.type_id].title}.")
        else:
            self.status_var.set("Drop cancelled.")
        return "break"

    def _draw_library_ghost(self, type_id: str, x: float, y: float, width: float, height: float) -> None:
        accent = self.definitions[type_id].accent
        if is_condition_flow_type(type_id):
            cx, cy = x + width / 2, y + height / 2
            self.canvas.create_polygon(cx, y, x + width, cy, cx, y + height, x, cy, fill=self.colors["surface_3"], stipple="gray50", outline=accent, dash=(6, 4), width=2, tags="library_ghost")
        else:
            self.canvas.create_rectangle(x, y, x + width, y + height, fill=self.colors["surface_3"], stipple="gray50", outline=accent, dash=(6, 4), width=2, tags="library_ghost")
        self.canvas.create_text(x + width / 2, y + height / 2, text=self.definitions[type_id].title, fill=self.colors["text"], font=("Segoe UI Semibold", 9), tags="library_ghost")
        if self.zoom != 1.0:
            self.canvas.scale("library_ghost", 0, 0, self.zoom, self.zoom)

    def _nearest_flow_edge(self, x: float, y: float, *, max_distance: float) -> tuple[str, str, str] | None:
        best: tuple[float, tuple[str, str, str]] | None = None
        for source in self.cards:
            keys = (("_true", "true"), ("_false", "false")) if is_condition_flow_type(source.type_id) else (("_next", "next"),)
            for key, kind in keys:
                target_id = str(source.params.get(key, ""))
                target = self._card(target_id)
                if target is None:
                    continue
                route = self._wire_route(source, target, kind)
                distance = min(self._point_segment_distance((x, y), first, second) for first, second in zip(route, route[1:]))
                if distance <= max_distance and (best is None or distance < best[0]):
                    best = (distance, (source.card_id, key, target_id))
        return best[1] if best else None

    def _highlight_insert_edge(self, edge: tuple[str, str, str]) -> None:
        source_id, key, target_id = edge
        source, target = self._card(source_id), self._card(target_id)
        if source is None or target is None:
            return
        kind = key[1:]
        points = self._wire_route(source, target, kind)
        scaled = [coordinate * self.zoom for coordinate in [v for point in points for v in point]]
        self.canvas.create_line(*scaled, fill="#f8fafc", width=5, dash=(7, 4), smooth=True, splinesteps=16, tags="library_ghost", state=tk.DISABLED)

    def _inside_canvas(self, root_x: int, root_y: int) -> bool:
        return self.canvas.winfo_rootx() <= root_x <= self.canvas.winfo_rootx() + self.canvas.winfo_width() and self.canvas.winfo_rooty() <= root_y <= self.canvas.winfo_rooty() + self.canvas.winfo_height()

    def _hydrate_positions(self) -> None:
        for index, card in enumerate(self.cards):
            if card.type_id == "math_formula" and "operator" in card.params:
                import json
                operation = card.params.pop("operator")
                card.params["expression"] = f"{operation}(a,b)" if operation in {"min", "max"} else f"a {operation} b"
                card.params["constants"] = json.dumps({"a": float(card.params.get("fallback_a", "0")), "b": float(card.params.get("fallback_b", "0"))})
            try:
                card.x = float(card.params.pop("_x", card.x))
                card.y = float(card.params.pop("_y", card.y))
            except ValueError:
                card.x, card.y = 100 + (index % 3) * 300, 90 + (index // 3) * 180
            card.params.pop("_node_id", None)

    def _ensure_boundary_nodes(self) -> None:
        start = next((card for card in self.cards if card.type_id == "workflow_start"), None)
        end = next((card for card in self.cards if card.type_id == "workflow_end"), None)
        content = [card for card in self.cards if card.type_id not in BOUNDARY_TYPES]
        min_x = min((card.x for card in content), default=-40.0)
        max_x = max((card.x + self._node_width(card) for card in content), default=180.0)
        center_y = sum((card.y + self._node_height(card) / 2 for card in content), 0.0) / len(content) if content else 100.0
        if start is None:
            start = create_autotest_flow_card("workflow_start", "workflow_start")
            if not content:
                start.params["_workflow_version"] = "2"
            start.x, start.y = min_x - 190, center_y - CAPSULE_HEIGHT / 2
            self.cards.insert(0, start)
        if end is None:
            end = create_autotest_flow_card("workflow_end", "workflow_end")
            end.x, end.y = max_x + 110, center_y - CAPSULE_HEIGHT / 2
            self.cards.append(end)
        if not content:
            start.params["_next"] = end.card_id
            return
        incoming = {card.card_id: 0 for card in content}
        for card in content:
            for key in ("_next", "_true", "_false"):
                target = str(card.params.get(key, ""))
                if target in incoming:
                    incoming[target] += 1
        auxiliary_sources = {card.params.get("_light_source") for card in content if card.params.get("_light_source")}
        roots = [card for card in content if incoming[card.card_id] == 0
                 and card.type_id not in LIGHT_ONLY_TYPES | DATA_ONLY_TYPES and card.card_id not in auxiliary_sources]
        if not start.params.get("_next"):
            start.params["_next"] = (roots[0].card_id if roots else end.card_id)
        for card in content:
            if card.type_id not in LIGHT_ONLY_TYPES | DATA_ONLY_TYPES and card.card_id not in auxiliary_sources and not is_condition_flow_type(card.type_id) and not card.params.get("_next"):
                card.params["_next"] = end.card_id

    def redraw(self) -> None:
        self._finish_inline_edit()
        for card in self.cards:
            if card.type_id in RESULT_OUTPUTS:
                card.params["_outputs"] = ",".join(self._selected_result_outputs(card))
                if getattr(self, "_inspected_id", None) == card.card_id:
                    selected = set(self._selected_result_outputs(card))
                    for metric, variable in self.output_vars.items():
                        variable.set(metric in selected)
        self.canvas.delete("all")
        self._draw_grid()
        self._routed_edges = []
        # Routing uses world coordinates: zoom/pan/selection do not change it.
        # Include every card (also unconnected obstacles) and parameter so direct
        # edits, layout changes, template loads and undo invalidate together.
        signature = tuple((c.card_id, c.type_id, c.x, c.y,
                           repr(sorted(c.params.items()))) for c in self.cards)
        previous = getattr(self, "_routing_signature", None)
        if signature != previous:
            geometry_only = (previous is not None and len(previous) == len(signature)
                             and all(old[:2] == new[:2] and old[4:] == new[4:]
                                     for old, new in zip(previous, signature)))
            if geometry_only:
                # Moving a node need not reroute distant, unaffected wires. Test
                # both old and new obstacle bounds so vacated corridors recover.
                moved = {new[0] for old, new in zip(previous, signature) if old[2:4] != new[2:4]}
                obstacles = []
                for old, new, card in zip(previous, signature, self.cards):
                    if card.card_id in moved:
                        bounds = self._node_bounds(card, 18)
                        obstacles.extend((bounds, (bounds[0]+old[2]-new[2], bounds[1]+old[3]-new[3],
                                                   bounds[2]+old[2]-new[2], bounds[3]+old[3]-new[3])))
                for key, points in list(self._wire_cache.items()):
                    if (key[0] in moved or key[1] in moved
                            or key in getattr(self, "_blocked_wire_cache", {})
                            or self._route_hits_obstacle(points, obstacles)
                            or self._route_hits_obstacle(self._route_cache.get(key, []), obstacles)):
                        self._wire_cache.pop(key, None)
                        self._route_cache.pop(key, None)
                        self._blocked_wire_cache.pop(key, None)
            else:
                self._route_cache = {}
                self._wire_cache = {}
                self._blocked_wire_cache = {}
            self._blocked_routes = list(self._blocked_wire_cache.values())
        self._routing_signature = None  # Commit only after routing completes.
        self._routing_spines = []
        self._routing_bounds = {}
        self._routing_edges = True
        for card in self.cards:
            self._draw_edges(card)
        self._routing_edges = False
        self._routing_signature = signature
        self._draw_crossing_bridges()
        for card in self.cards:
            self._draw_node(card)
        if not self.cards:
            self.canvas.create_text(0, 0, text="Drag nodes here to build a measurement graph", fill=self.colors["muted"], font=("Segoe UI", 13))
        if self.zoom != 1.0:
            self.canvas.scale("all", 0, 0, self.zoom, self.zoom)
        extent = WORLD_EXTENT * self.zoom
        self.canvas.configure(scrollregion=(-extent, -extent, extent, extent))
        self._paint_edge_highlight()

    def _draw_grid(self) -> None:
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        left = self.canvas.canvasx(0) / self.zoom
        right = self.canvas.canvasx(width) / self.zoom
        top = self.canvas.canvasy(0) / self.zoom
        bottom = self.canvas.canvasy(height) / self.zoom
        grid = 64 if self.zoom < .7 else 32
        first_x = math.floor(left / grid) * grid
        first_y = math.floor(top / grid) * grid
        for x in range(first_x, math.ceil(right / grid) * grid + 1, grid):
            color = "#38393d" if x % 128 == 0 else "#292a2e"
            self.canvas.create_line(x, top, x, bottom, fill=color, tags="grid")
        for y in range(first_y, math.ceil(bottom / grid) * grid + 1, grid):
            color = "#38393d" if y % 128 == 0 else "#292a2e"
            self.canvas.create_line(left, y, right, y, fill=color, tags="grid")

    def _refresh_grid(self):
        self.canvas.delete("grid")
        self._draw_grid()
        self.canvas.scale("grid", 0, 0, self.zoom, self.zoom)
        self.canvas.tag_lower("grid")

    def _center_canvas_origin(self) -> None:
        self.canvas.update_idletasks()
        self.canvas.xview_moveto(0.5 - self.canvas.winfo_width() / (4 * WORLD_EXTENT * self.zoom))
        self.canvas.yview_moveto(0.5 - self.canvas.winfo_height() / (4 * WORLD_EXTENT * self.zoom))
        self.redraw()

    def _canvas_configured(self, _event: tk.Event) -> None:
        if not self._initial_fit_pending or self.canvas.winfo_width() < 300 or self.canvas.winfo_height() < 240:
            return
        self._initial_fit_pending = False
        self.window.after(40, self._fit_nodes)

    def _draw_node(self, card: AutoTestFlowCard) -> None:
        definition = self.definitions[card.type_id]
        selected = card.card_id == self.selected_id
        x, y = card.x, card.y
        width, height = self._node_width(card), self._node_height(card)
        tags = ("node", f"node:{card.card_id}")
        self._rounded_box(x + 3, y + 4, x + width + 3, y + height + 4, 8,
                          fill="#17181a", outline="", tags=("shadow",))
        self._rounded_box(x, y, x + width, y + height, 8, fill=NODE_BG,
                          outline="#f2ad58" if selected else "#161719", width=2 if selected else 1, tags=tags)
        # Muted category color keeps the title readable while preserving identity.
        accent = definition.accent.lstrip("#")
        header = "#" + "".join(f"{int(int(accent[i:i+2], 16) * .40):02x}" for i in (0, 2, 4))
        self._rounded_box(x + 2, y + 2, x + width - 2, y + 31, 6,
                          fill=header, outline="", tags=tags)
        self.canvas.create_text(x + 12, y + 16, text=definition.title if len(definition.title) <= 28 else definition.title[:25] + "…", anchor="w", fill=NODE_TEXT,
                                font=("Segoe UI Semibold", self._zoom_font(9)), width=(width - 24) * self.zoom, tags=tags)
        for kind, label in self._node_outputs(card):
            px, py = self._socket_position(card, kind)
            self._draw_port(card, kind, px, py, label)
        for kind, label in self._node_inputs(card):
            px, py = self._socket_position(card, kind, incoming=True)
            self._draw_input_port(card, kind, px, py, label)
        top = self._parameter_top(card)
        params = self._inline_params(card)
        if card.type_id in RESULT_OUTPUTS and self.zoom >= .65:
            marker = ("outputs_button", f"outputs:{card.card_id}")
            self._rounded_box(x + 10, y + top - 30, x + width - 10, y + top - 10, 4,
                              fill="#42404e", outline="#656074", tags=marker)
            self.canvas.create_text(x + width / 2, y + top - 20,
                                    text=f"Outputs…  {len(self._selected_result_outputs(card))} exposed", fill=NODE_TEXT,
                                    font=("Segoe UI", self._zoom_font(8)), tags=marker)
        if params:
            self.canvas.create_line(x + 10, y + top - 6, x + width - 10, y + top - 6,
                                    fill="#45464b", tags=tags)
        if self.zoom >= .65:
            for index, param in enumerate(params):
                row_y = y + top + index * 38
                marker = ("inline", f"inline:{card.card_id}:{param.key}")
                self._rounded_box(x + 10, row_y, x + width - 10, row_y + 34, 4,
                                  fill="#242529", outline="#f87171" if (card.card_id, param.key) in self._field_errors else "#424349", tags=marker)
                self.canvas.create_text(x + 17, row_y + 8, text=param.label, anchor="w", fill=NODE_MUTED,
                                        font=("Segoe UI", self._zoom_font(7)), width=(width - 36) * self.zoom, tags=marker)
                value = str(self._draft_values.get((card.card_id, param.key), card.params.get(param.key, param.default)))
                if len(value) > 29:
                    value = value[:26] + "…"
                if self._choices(card.type_id, param.key):
                    value += "  ▾"
                self.canvas.create_text(x + 17, row_y + 23, text=value or "—", anchor="w", fill=NODE_TEXT,
                                        font=("Segoe UI", self._zoom_font(9)), width=(width - 36) * self.zoom, tags=marker)
        elif params:
            self.canvas.create_text(x + 12, y + top + 12, text="Zoom in to edit parameters", anchor="w",
                                    fill=NODE_MUTED, font=("Segoe UI", self._zoom_font(9)), tags=tags)
        if any(identity == card.card_id for identity, _key, _message in getattr(self, "_validation_issues", ())):
            self.canvas.create_text(x + width - 10, y + 16, text="!", fill="#ff9b92", font=("Segoe UI", self._zoom_font(11), "bold"), tags=tags)
        if card.type_id == "laser_pulse":
            self._draw_node_sparkline(card, x + 14, y + height - 42, x + width - 14, y + height - 12)

    def _rounded_box(self, x1, y1, x2, y2, radius, **kwargs):
        r = min(radius, (x2-x1)/2, (y2-y1)/2)
        return self.canvas.create_polygon(
            x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r, x2,y2,
            x2-r,y2, x1+r,y2, x1,y2, x1,y2-r, x1,y1+r, x1,y1,
            smooth=True, splinesteps=20, **kwargs)

    @staticmethod
    def _inline_params(card):
        definition = autotest_flow_definitions_by_type()[card.type_id]
        keys = INLINE_KEYS.get(card.type_id)
        if keys is None and card.type_id in {"b1500_transfer", "b1500_output", "hp6614c_transfer", "hp6614c_output"}:
            keys = ("sweep_start_v", "sweep_end_v", "sweep_points")
        if keys is None:
            excluded = WIDE_PARAMETER_KEYS | {"resource_name", "variables", "constants", "on_invalid"}
            return tuple(p for p in definition.parameters if p.key not in excluded)[:3]
        by_key = {p.key: p for p in definition.parameters}
        return tuple(by_key[k] for k in keys if k in by_key)

    def _node_outputs(self, card):
        ports = []
        if is_condition_flow_type(card.type_id):
            ports.extend((("true", "True"), ("false", "False")))
        elif card.type_id not in LIGHT_ONLY_TYPES | DATA_ONLY_TYPES and card.type_id != "workflow_end":
            ports.append(("next", "Flow"))
        if card.type_id in LIGHT_OUTPUT_TYPES:
            ports.append(("waveform@signal", "Waveform"))
        if card.type_id not in LIGHT_ONLY_TYPES | DATA_ONLY_TYPES | BOUNDARY_TYPES:
            ports.extend((f"{self._result_output_type(m)}@{m}", self._metric_label(m)) for m in self._selected_result_outputs(card))
        ports.extend((f"{dtype}@{name}", name.upper()) for name, dtype in output_ports(card))
        return ports

    def _node_inputs(self, card):
        ports = []
        if card.type_id not in LIGHT_ONLY_TYPES | DATA_ONLY_TYPES and card.type_id != "workflow_start":
            ports.append(("flow", "Flow"))
        if card.type_id in LIGHT_INPUT_TYPES:
            ports.append(("waveform@light", "Light"))
        ports.extend((f"{dtype}@{name}", self._plot_slot_label(card, name) if card.type_id == "plot_panel" else name.upper()) for name, dtype in input_ports(card))
        return ports

    def _socket_position(self, card, kind, incoming=False):
        ports = self._node_inputs(card) if incoming else self._node_outputs(card)
        index = next((i for i, (name, _) in enumerate(ports) if name == kind), None)
        if index is None:
            return None
        return (card.x if incoming else card.x + self._node_width(card), card.y + 48 + index * 22)

    def _parameter_top(self, card):
        return 42 + max(len(self._node_inputs(card)), len(self._node_outputs(card)), 1) * 22 + 10 + (26 if card.type_id in RESULT_OUTPUTS else 0)

    def _begin_inline_edit(self, card, key):
        self._finish_inline_edit()
        self._history_group = None
        params = self._inline_params(card)
        param = next((p for p in params if p.key == key), None)
        if param is None:
            return
        self.pending_edge = None
        self.drag_id = None
        self._canvas_pan_active = False
        self.canvas.delete("edge_preview")
        value = tk.StringVar(value=self._draft_values.get((card.card_id, key), card.params.get(key, param.default)))
        choices = self._choices(card.type_id, key)
        if choices:
            widget = ttk.Combobox(self.canvas, textvariable=value, values=choices, state="readonly",
                                  font=("Segoe UI", self._zoom_font(9)))
        else:
            widget = tk.Entry(self.canvas, textvariable=value, bg="#44464d", fg=NODE_TEXT,
                              insertbackground=NODE_TEXT, relief="flat", font=("Segoe UI", self._zoom_font(9)))
        y = card.y + self._parameter_top(card) + params.index(param) * 38 + 15
        item = self.canvas.create_window((card.x + 12) * self.zoom, y * self.zoom, anchor="nw",
                                         width=(self._node_width(card) - 24) * self.zoom, height=20 * self.zoom,
                                         window=widget, tags="inline_editor")
        self._inline_editor = (widget, item, value, card, key)
        widget.bind("<Return>", lambda e: self._finish_inline_edit(redraw=True))
        widget.bind("<Escape>", lambda e: self._finish_inline_edit(commit=False, redraw=True))
        widget.bind("<FocusOut>", lambda e: self._finish_inline_edit(redraw=True))
        widget.bind("<MouseWheel>", lambda e: "break")
        widget.bind("<Button-4>", lambda e: "break")
        widget.bind("<Button-5>", lambda e: "break")
        widget.focus_set()
        widget.selection_range(0, "end")
        self.status_var.set(f"{param.label} — Enter to apply, Esc to cancel; double-click the title for all parameters.")

    def _finish_inline_edit(self, commit=True, redraw=False):
        edit = getattr(self, "_inline_editor", None)
        if edit is None:
            return "break"
        widget, item, variable, card, key = edit
        value = variable.get()
        if commit:
            canonical, error = self._validate_parameter(card, key, value)
            self._field_feedback(card, key, value, error)
            if error and redraw:
                self.canvas.delete("inline_error")
                x = (card.x + 12) * self.zoom
                y = (card.y + self._node_height(card) + 8) * self.zoom
                self.canvas.create_text(x, y, text=error, fill="#fca5a5", anchor="nw", width=250,
                                        font=("Segoe UI", 9), tags="inline_error")
                return "break"
            if error:
                commit = False  # Keep the draft on the node when navigating away.
            else:
                value = canonical
        else:
            self._field_feedback(card, key, "", "")
        self.canvas.delete("inline_error")
        self._inline_editor = None
        widget.destroy()
        self.canvas.delete(item)
        if commit and value != str(card.params.get(key, "")) and self._card(card.card_id) is card:
            # Share the inspector's update path and formula preview, without a second model.
            if getattr(self, "_inspected_id", None) == card.card_id and key in self.param_vars:
                self.param_vars[key].set(value)
            else:
                self._param_changed(card, key, value)
        if redraw:
            self.redraw()
        return "break"

    def _cancel_connection(self, event=None):
        self._rewire = None
        self.pending_edge = None
        self._edge_dragging = False
        self.canvas.delete("edge_preview")
        self.canvas.configure(cursor="")
        self.status_var.set("Connection cancelled.")
        return "break"

    def _draw_node_sparkline(self, card: AutoTestFlowCard, x1: float, y1: float, x2: float, y2: float) -> None:
        points = parse_waveform_points(card.params)
        duration = max(0.001, points[-1].time_ms)
        peak = max(0.001, max(point.voltage_v for point in points))
        coordinates: list[float] = []
        for point in points:
            coordinates.extend((x1 + (x2 - x1) * point.time_ms / duration, y2 - (y2 - y1) * point.voltage_v / peak))
        self.canvas.create_line(*coordinates, fill="#fb7185", width=2, tags=("node", f"node:{card.card_id}"))

    def _draw_port(self, card, kind, x, y, label):
        self._draw_socket(card, kind, x, y, label, incoming=False)

    def _draw_input_port(self, card, kind, x, y, label):
        self._draw_socket(card, kind, x, y, label, incoming=True)

    def _draw_socket(self, card, kind, x, y, label, incoming):
        color = {"true": "#8ac782", "false": "#dc817c"}.get(kind, PORT_COLORS.get(self._port_type(kind), "#b9bdc6"))
        prefix = "input" if incoming else "port"
        tags = (prefix, f"{prefix}:{card.card_id}:{kind}")
        if incoming:
            tags += (f"node:{card.card_id}",)
        radius = max(5, 4 / self.zoom)
        self.canvas.create_oval(x-radius, y-radius, x+radius, y+radius, fill=color, outline="#18191c", width=1.5, tags=tags)
        if label and self.zoom >= .65:
            limit = 29 if card.type_id == "plot_panel" else 22
            label = label if len(label) <= limit else label[:limit-2] + "…"
            self.canvas.create_text(x + 12 if incoming else x - 12, y, text=label,
                                    anchor="w" if incoming else "e", fill=NODE_MUTED,
                                    font=("Segoe UI", self._zoom_font(8)), tags=tags)

    def _draw_edges(self, card: AutoTestFlowCard) -> None:
        keys = (("_true", "true", -19), ("_false", "false", 19)) if is_condition_flow_type(card.type_id) else (("_next", "next", 0),)
        for key, kind, offset in keys:
            target = self._card(str(card.params.get(key, "")))
            if target is None:
                continue
            color = "#8ac782" if kind == "true" else "#dc817c" if kind == "false" else PORT_COLORS["control"]
            self._draw_orthogonal_edge(card, target, kind, color, edge_key=key)
        light_source = self._card(str(card.params.get("_light_source", "")))
        if light_source is not None:
            self._draw_orthogonal_edge(light_source, card, "waveform@signal", PORT_COLORS["waveform"], target_kind="waveform@light", edge_key="_light_source")
        result_source = self._card(str(card.params.get("_result_source", "")))
        result_metric = str(card.params.get("_result_metric", ""))
        has_typed_result = any(card.params.get(f"_input_{name}_source") for name, _data_type in input_ports(card))
        if result_source is not None and result_metric and not has_typed_result:
            self._draw_orthogonal_edge(result_source, card, f"number@{result_metric}", PORT_COLORS["number"], target_kind="number", edge_key="_result_source")
        for input_name, input_type in input_ports(card):
            source = self._card(str(card.params.get(f"_input_{input_name}_source", "")))
            output = str(card.params.get(f"_input_{input_name}_output", ""))
            if source is not None and output:
                output_type = card.params.get(f"_input_{input_name}_type", input_type)
                self._draw_orthogonal_edge(source, card, f"{output_type}@{output}", PORT_COLORS[output_type], target_kind=f"{input_type}@{input_name}", edge_key=f"_input_{input_name}_source")

    def _wire_route(self, source, target, kind, *, target_kind="flow", preview=None):
        key = (source.card_id, target.card_id, kind, target_kind)
        if preview is None and key in getattr(self, "_wire_cache", {}):
            return self._wire_cache[key]
        route = self._orthogonal_route(source, target, kind, target_kind=target_kind, preview=preview)
        if preview is None and getattr(self, "_routing_edges", False):
            if not hasattr(self, "_route_cache"):
                self._route_cache = {}
            self._route_cache[key] = route
        if len(route) < 2:
            return route
        sx, sy = route[0]
        tx, ty = self._socket_position(target, target_kind, incoming=True) if preview is None else preview
        # Never turn an intentionally blocked stub into an apparent connection.
        if math.dist(route[-1], (tx, ty)) < .01 and tx - sx >= 50:
            bend = min(180, max(35, (tx-sx) * .45))
            curve = []
            for i in range(49):
                t = i / 48
                u = 1-t
                curve.append((u**3*sx + 3*u*u*t*(sx+bend) + 3*u*t*t*(tx-bend) + t**3*tx,
                              u**3*sy + 3*u*u*t*sy + 3*u*t*t*ty + t**3*ty))
            obstacles = [self._node_bounds(c, -.01 if c is source or c is target else 18) for c in self.cards]
            if not self._route_hits_obstacle(curve, obstacles):
                return self._simplify_route(curve)
        rounded = self._rounded_route_points(route, 16)
        return list(zip(rounded[::2], rounded[1::2]))

    def _draw_orthogonal_edge(self, source: AutoTestFlowCard, target: AutoTestFlowCard, kind: str, color: str, *, target_kind: str = "flow", preview: tuple[float, float] | None = None, edge_key: str = "") -> None:
        points = self._wire_route(source, target, kind, target_kind=target_kind, preview=preview)
        tags = ("edge", f"edge:{source.card_id}:{edge_key}:{target.card_id}") if preview is None else ("edge_preview",)
        width = 2.5 if self._port_type(kind) == "control" else 2
        if len(points) < 2:
            return
        if f"{source.card_id} → {target.card_id}" in getattr(self, "_blocked_routes", ()):
            color = "#ef4444"
        coordinates = [v for point in points for v in point]
        self.canvas.create_line(*coordinates, fill="#141518", width=width+2, smooth=False, tags=tags)
        self.canvas.create_line(*coordinates, fill=color, width=width, smooth=False, tags=tags)
        if preview is None and getattr(self, "_routing_edges", False):
            self._routed_edges.append((points, color, width, tags))
            # Keep the small orthogonal planning spine separate from the sampled
            # display curve used for hit testing and cutting. Feeding curve
            # samples back into routing creates thousands of false corridors.
            if not hasattr(self, "_routing_spines"):
                self._routing_spines = []
            self._routing_spines.append(self._route_cache.get(
                (source.card_id, target.card_id, kind, target_kind), []))
            if not hasattr(self, "_wire_cache"):
                self._wire_cache = {}
            self._wire_cache[source.card_id, target.card_id, kind, target_kind] = points
            if not hasattr(self, "_blocked_wire_cache"):
                self._blocked_wire_cache = {}
            if f"{source.card_id} → {target.card_id}" in getattr(self, "_blocked_routes", ()):
                self._blocked_wire_cache[source.card_id, target.card_id, kind, target_kind] = f"{source.card_id} → {target.card_id}"

    def _draw_crossing_bridges(self):
        """A small horizontal hop distinguishes crossings from connections."""
        segments = [(a, b, color, width, tags, index)
                    for index, (route, color, width, tags) in enumerate(self._routed_edges)
                    for a, b in zip(route, route[1:])]
        vertical = [segment for segment in segments if segment[0][0] == segment[1][0]]
        bridges = []
        for a, b, color, width, tags, owner in segments:
            if a[1] != b[1]:
                continue
            for c, d, _color, _width, _tags, other in vertical:
                if owner == other or c[0] != d[0]:
                    continue
                x, y = c[0], a[1]
                if not (min(a[0], b[0]) + 18 < x < max(a[0], b[0]) - 18
                        and min(c[1], d[1]) + 18 < y < max(c[1], d[1]) - 18):
                    continue
                if any(math.dist((x, y), center) < 16 for center in bridges):
                    continue
                bridges.append((x, y))
                radius = 5
                bridge_tags = (*tags, "edge_bridge")
                self.canvas.create_line(x-radius, y, x+radius, y,
                    fill=self.canvas.cget("background"), width=width+4, tags=bridge_tags)
                arc = [(x-radius*math.cos(i*math.pi/16), y-radius*math.sin(i*math.pi/16)) for i in range(17)]
                self.canvas.create_line(*[v for p in arc for v in p], fill=self.canvas.cget("background"),
                    width=width+4, smooth=False, tags=bridge_tags)
                self.canvas.create_line(*[v for p in arc for v in p], fill=color,
                    width=width, smooth=False, tags=bridge_tags)

    def _orthogonal_route(self, source: AutoTestFlowCard, target: AutoTestFlowCard, kind: str, *, target_kind: str = "flow", preview: tuple[float, float] | None = None) -> list[tuple[float, float]]:
        key = (source.card_id, target.card_id, kind, target_kind)
        if preview is None and not getattr(self, "_routing_edges", False) and key in getattr(self, "_route_cache", {}):
            return self._route_cache[key]
        start = self._socket_position(source, kind)
        end = self._socket_position(target, target_kind, incoming=True) if preview is None else preview
        if start is None or end is None:
            return []
        sx, sy = start
        tx, ty = end
        # Closely spaced neighbours can have overlapping padded borders even
        # though their bodies do not overlap. Use their clear shared corridor.
        if 0 < tx - sx < 60:
            mid = (sx + tx) / 2
            direct = [(sx, sy), (mid, sy), (mid, ty), (tx, ty)]
            bodies = [self._node_bounds(c, -.01 if c is source or c is target else 18) for c in self.cards]
            if not self._route_hits_obstacle(direct, bodies):
                return self._simplify_route(direct)
        obstacles = [self._node_bounds(card, 18) for card in self.cards]
        lane = sum(ord(character) for character in source.card_id) % 4 * 8
        data_type = kind.split("@", 1)[0]
        target_data_type = target_kind.split("@", 1)[0]
        source_lead = 30 + lane + (12 if kind == "false" else 0) + (20 if data_type == "waveform" else 34 if data_type in {"number", "bool", "calibration", "trace", "curve", "plot"} else 0)
        target_lead = 30 + lane + (20 if target_data_type == "waveform" else 34 if target_data_type in {"number", "bool", "calibration", "trace", "curve", "plot"} else 0)
        inner_start = (sx + source_lead, sy)
        inner_end = (tx - target_lead, ty)
        # Port leads alone may cross their owner's padded border. Shorten them
        # when another card is nearby; the main route must avoid ALL cards.
        others_source = [self._node_bounds(c, 18) for c in self.cards if c is not source]
        others_target = [self._node_bounds(c, 18) for c in self.cards if c is not target]
        for lead in (source_lead, 26, 20):
            candidate = (sx + lead, sy)
            if not self._route_hits_obstacle([(sx, sy), candidate], others_source):
                inner_start = candidate
                break
        for lead in (target_lead, 26, 20):
            candidate = (tx - lead, ty)
            if not self._route_hits_obstacle([candidate, (tx, ty)], others_target):
                inner_end = candidate
                break
        occupied = getattr(self, "_routing_spines", ()) if getattr(self, "_routing_edges", False) else []
        # Distant subgraphs should not multiply the local visibility grid.
        # Try nearby corridors first, then validate against EVERY obstacle;
        # fall back to the full graph when a wider detour is required.
        left, right = min(sx, tx)-160, max(sx, tx)+160
        top, bottom = min(sy, ty)-160, max(sy, ty)+160
        nearby = [box for box in obstacles if box[2] >= left and box[0] <= right
                  and box[3] >= top and box[1] <= bottom]
        local_occupied = [points for points in occupied if points
                          and max(p[0] for p in points) >= left and min(p[0] for p in points) <= right
                          and max(p[1] for p in points) >= top and min(p[1] for p in points) <= bottom]
        route = self._visibility_route(inner_start, inner_end, nearby, local_occupied)
        if len(nearby) != len(obstacles) and (not route or self._route_hits_obstacle(route, obstacles)):
            route = self._visibility_route(inner_start, inner_end, obstacles, occupied)
        if route:
            if not self._route_hits_obstacle([(sx, sy), inner_start], others_source) and not self._route_hits_obstacle([inner_end, (tx, ty)], others_target):
                return self._simplify_route([(sx, sy), inner_start, *route[1:-1], inner_end, (tx, ty)])
        # Overlapping cards can seal a port completely. Never invent a path
        # through a card: leave a visible disconnected stub until it is moved.
        if getattr(self, "_routing_edges", False):
            self._blocked_routes.append(f"{source.card_id} → {target.card_id}")
        return [(sx, sy), (sx + 8, sy)]

    def _incoming_flow_edges(self, target: AutoTestFlowCard) -> list[tuple[AutoTestFlowCard, str]]:
        incoming: list[tuple[AutoTestFlowCard, str]] = []
        for source in self.cards:
            for key, kind in (("_next", "next"), ("_true", "true"), ("_false", "false")):
                if str(source.params.get(key, "")) == target.card_id:
                    incoming.append((source, kind))
        return sorted(incoming, key=lambda item: (item[0].card_id, item[1]))

    def _incoming_flow_offsets(self, target: AutoTestFlowCard) -> tuple[float, ...]:
        incoming = self._incoming_flow_edges(target)
        if len(incoming) <= 1:
            return (0.0,) if incoming else ()
        spacing = min(16.0, max(8.0, (self._node_height(target) - 18) / max(1, len(incoming) - 1)))
        center = (len(incoming) - 1) / 2
        return tuple((index - center) * spacing for index in range(len(incoming)))

    def _flow_input_offset(self, target: AutoTestFlowCard, source: AutoTestFlowCard, kind: str) -> float:
        incoming = self._incoming_flow_edges(target)
        offsets = self._incoming_flow_offsets(target)
        for index, candidate in enumerate(incoming):
            if candidate[0] is source and candidate[1] == kind:
                return offsets[index]
        return 0.0

    def _zoom_font(self, size: int) -> int:
        return max(6, round(size * self.zoom))

    @classmethod
    def _visibility_route(
        cls,
        start: tuple[float, float],
        end: tuple[float, float],
        obstacles: list[tuple[float, float, float, float]],
        occupied: list[list[tuple[float, float]]] = (),
    ) -> list[tuple[float, float]]:
        sx, sy = start
        tx, ty = end
        x_values = {sx, sx + 28, tx - 28, tx}
        y_values = {sy, ty}
        for left, top, right, bottom in obstacles:
            x_values.update((left - 8, right + 8))
            y_values.update((top - 8, bottom + 8))
        for route in occupied:
            for a, b in zip(route, route[1:]):
                if a[0] == b[0]:
                    x_values.update((a[0] - 12, a[0] + 12))
                elif a[1] == b[1]:
                    y_values.update((a[1] - 12, a[1] + 12))
        # Index straight spans once, not once per visibility-grid edge. Most
        # occupied spans are outside the 12-unit clearance and need no overlap
        # calculation. The same grid segment is visited in both directions.
        spans = {True: [], False: []}
        for route in occupied:
            for a, b in zip(route, route[1:]):
                horizontal = a[1] == b[1]
                if not horizontal and a[0] != b[0]:
                    continue
                axis, fixed = (0, 1) if horizontal else (1, 0)
                spans[horizontal].append((a[fixed], min(a[axis], b[axis]), max(a[axis], b[axis])))
        spacing_cache = {}

        def spacing_cost(a, b):
            key = (a, b) if a <= b else (b, a)
            if key not in spacing_cache:
                horizontal = a[1] == b[1]
                axis, fixed = (0, 1) if horizontal else (1, 0)
                low, high = sorted((a[axis], b[axis]))
                penalty = 0.0
                for level, left, right in spans[horizontal]:
                    gap = abs(a[fixed] - level)
                    if gap >= 12:
                        continue
                    overlap = min(high, right) - max(low, left)
                    if overlap > 0:
                        penalty += (12 - gap) * overlap * 2
                spacing_cache[key] = penalty
            return spacing_cache[key]

        # Most editor links need only a single corridor. Evaluate these first
        # instead of rebuilding/searching a large visibility grid every drag.
        candidates = [[start, (tx, sy), end], [start, (sx, ty), end]]
        candidates += [[start, (sx, y), (tx, y), end] for y in sorted(y_values)]
        candidates += [[start, (x, sy), (x, ty), end] for x in sorted(x_values)]
        best_candidate, candidate_cost = None, float("inf")
        for candidate in candidates:
            candidate = cls._simplify_route(list(dict.fromkeys(candidate)))
            if cls._route_hits_obstacle(candidate, obstacles):
                continue
            cost = 36 * max(0, len(candidate)-2) + sum(
                math.dist(a, b) + spacing_cost(a, b)
                for a, b in zip(candidate, candidate[1:]))
            if cost < candidate_cost:
                best_candidate, candidate_cost = candidate, cost
        if best_candidate is not None:
            return best_candidate
        points = {
            (x, y)
            for x in x_values
            for y in y_values
            if not any(left < x < right and top < y < bottom for left, top, right, bottom in obstacles)
        }
        if start not in points or end not in points:
            return []
        neighbours: dict[tuple[float, float], list[tuple[float, float]]] = {point: [] for point in points}
        by_y: dict[float, list[tuple[float, float]]] = {}
        by_x: dict[float, list[tuple[float, float]]] = {}
        for point in points:
            by_y.setdefault(point[1], []).append(point)
            by_x.setdefault(point[0], []).append(point)
        for row in by_y.values():
            row.sort()
            cls._connect_visible_neighbours(row, neighbours, obstacles)
        for column in by_x.values():
            column.sort(key=lambda point: point[1])
            cls._connect_visible_neighbours(column, neighbours, obstacles)

        # Manhattan distance is an admissible A* bound: bend/spacing penalties
        # are nonnegative. Explore towards the destination instead of flooding
        # every corridor in the graph on each node movement.
        def remaining(point):
            return abs(point[0] - tx) + abs(point[1] - ty)

        queue = [(remaining(start), 0, 0.0, start, None, [start])]
        best: dict[tuple[tuple[float, float], str | None], float] = {(start, None): 0.0}
        sequence = 0
        while queue:
            _estimate, _order, cost, point, direction, path = heapq.heappop(queue)
            if point == end:
                return cls._simplify_route(path)
            if cost > best.get((point, direction), float("inf")):
                continue
            for neighbour in neighbours.get(point, ()):
                next_direction = "h" if neighbour[1] == point[1] else "v"
                distance = abs(neighbour[0] - point[0]) + abs(neighbour[1] - point[1])
                next_cost = cost + distance + (36 if direction and direction != next_direction else 0)
                next_cost += spacing_cost(point, neighbour)
                state = (neighbour, next_direction)
                if next_cost >= best.get(state, float("inf")):
                    continue
                best[state] = next_cost
                sequence += 1
                heapq.heappush(queue, (next_cost + remaining(neighbour), sequence, next_cost, neighbour, next_direction, path + [neighbour]))
        return []

    @classmethod
    def _connect_visible_neighbours(
        cls,
        ordered: list[tuple[float, float]],
        neighbours: dict[tuple[float, float], list[tuple[float, float]]],
        obstacles: list[tuple[float, float, float, float]],
    ) -> None:
        for first, second in zip(ordered, ordered[1:]):
            if not cls._route_hits_obstacle([first, second], obstacles):
                neighbours[first].append(second)
                neighbours[second].append(first)

    @staticmethod
    def _wire_spacing_cost(a, b, occupied):
        penalty = 0.0
        horizontal = a[1] == b[1]
        for route in occupied:
            for c, d in zip(route, route[1:]):
                if c[0] != d[0] and c[1] != d[1]:
                    continue
                if horizontal != (c[1] == d[1]):
                    continue
                axis, fixed = (0, 1) if horizontal else (1, 0)
                gap = abs(a[fixed] - c[fixed])
                overlap = min(max(a[axis], b[axis]), max(c[axis], d[axis])) - max(min(a[axis], b[axis]), min(c[axis], d[axis]))
                if overlap > 0 and gap < 12:
                    penalty += (12 - gap) * overlap * 2
        return penalty

    @staticmethod
    def _simplify_route(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        # Port leads and the middle route are planned separately. Their join
        # can retrace a straight segment; keep only its net displacement before
        # rounding corners, otherwise the overshoot appears as a dangling spur.
        result = []
        for point in points:
            if result and point == result[-1]:
                continue
            while len(result) >= 2:
                previous, current = result[-2:]
                if not (previous[0] == current[0] == point[0]
                        or previous[1] == current[1] == point[1]):
                    break
                result.pop()
            if not result or point != result[-1]:
                result.append(point)
        return result

    @staticmethod
    def _route_hits_obstacle(points: list[tuple[float, float]], obstacles: list[tuple[float, float, float, float]]) -> bool:
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            for left, top, right, bottom in obstacles:
                # Slab clipping also handles the diagonal segments of sampled curves.
                enter, leave = 0.0, 1.0
                for origin, delta, low, high in ((x1, x2-x1, left, right), (y1, y2-y1, top, bottom)):
                    if abs(delta) < 1e-12:
                        if origin < low or origin > high:
                            break
                    else:
                        a, b = sorted(((low-origin)/delta, (high-origin)/delta))
                        enter, leave = max(enter, a), min(leave, b)
                        if enter > leave:
                            break
                else:
                    return True
        return False

    def _node_bounds(self, card: AutoTestFlowCard, margin: float = 0) -> tuple[float, float, float, float]:
        cache = getattr(self, "_routing_bounds", None) if getattr(self, "_routing_edges", False) else None
        key = (id(card), margin)
        if cache is not None and key in cache:
            return cache[key]
        width = self._node_width(card)
        bounds = (card.x - margin, card.y - margin, card.x + width + margin, card.y + self._node_height(card) + margin)
        if cache is not None:
            cache[key] = bounds
        return bounds

    @staticmethod
    def _rounded_route_points(points: list[tuple[float, float]], radius: float) -> list[float]:
        if len(points) < 3:
            return [coordinate for point in points for coordinate in point]
        result: list[tuple[float, float]] = [points[0]]
        for index in range(1, len(points) - 1):
            previous, corner, following = points[index - 1], points[index], points[index + 1]
            incoming = max(1.0, math.dist(previous, corner))
            outgoing = max(1.0, math.dist(corner, following))
            r = min(radius, incoming / 3, outgoing / 3)
            before = (corner[0] + (previous[0] - corner[0]) * r / incoming, corner[1] + (previous[1] - corner[1]) * r / incoming)
            after = (corner[0] + (following[0] - corner[0]) * r / outgoing, corner[1] + (following[1] - corner[1]) * r / outgoing)
            result.append(before)
            for step in range(1, 9):
                t = step / 8
                result.append(((1-t)**2 * before[0] + 2*(1-t)*t * corner[0] + t*t * after[0],
                               (1-t)**2 * before[1] + 2*(1-t)*t * corner[1] + t*t * after[1]))
        result.append(points[-1])
        return [coordinate for point in result for coordinate in point]

    def _canvas_press(self, event: tk.Event) -> str:
        x, y = self.canvas.canvasx(event.x) / self.zoom, self.canvas.canvasy(event.y) / self.zoom
        current = self.canvas.find_withtag("current")
        tags = self.canvas.gettags(current[0]) if current else ()
        output_button = next((tag for tag in tags if tag.startswith("outputs:")), None)
        if output_button:
            card = self._card(output_button.split(":", 1)[1])
            if card:
                self._finish_inline_edit()
                self.pending_edge = None
                self.drag_id = None
                self.selected_id = card.card_id
                self._show_inspector(card)
                self.param_canvas.yview_moveto(0)
                self.redraw()
            return "break"
        inline = next((tag for tag in tags if tag.startswith("inline:")), None)
        if inline:
            _, card_id, key = inline.split(":", 2)
            card = self._card(card_id)
            if card is not None:
                self._begin_inline_edit(card, key)
            return "break"
        self._finish_inline_edit()
        self.canvas.focus_set()
        # Screen-space hit area remains generous even when zoomed out.
        if not any(tag.startswith("port:") for tag in tags):
            cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
            candidates = self.canvas.find_overlapping(cx-10, cy-10, cx+10, cy+10)
            for item in reversed(candidates):
                near = self.canvas.gettags(item)
                if any(tag.startswith("port:") for tag in near):
                    tags = near
                    break
        if self._begin_rewire_at(event):
            return "break"
        edge = self._edge_at(event)
        if edge and not any(tag.startswith(("port:", "input:", "node:")) for tag in tags):
            self.selected_edge = edge
            self.selected_id = None
            self.drag_id = None
            self.pending_edge = None
            self._paint_edge_highlight()
            return "break"
        self.selected_edge = None
        port = next((tag for tag in tags if tag.startswith("port:")), None)
        node = next((tag for tag in tags if tag.startswith("node:")), None)
        if port:
            _prefix, card_id, kind = port.split(":", 2)
            self.pending_edge = (card_id, kind)
            self._edge_dragging = False
            self.canvas.configure(cursor="crosshair")
            self.status_var.set(f"Select a target node for the {kind} connection.")
            return "break"
        if node:
            card_id = node.split(":", 1)[1]
            if self.pending_edge is not None and self.pending_edge[0] != card_id:
                source_id, kind = self.pending_edge
                target = self._connection_target_at(event, kind)
                if target is not None:
                    self._connect(source_id, kind, target[0], target[1])
                else:
                    self.status_var.set("Incompatible input. Select a highlighted input or press Esc.")
                    return "break"
                self.pending_edge = None
                self._edge_dragging = False
                self.canvas.delete("edge_preview")
                return "break"
            card = self._card(card_id)
            if card is not None:
                self.selected_id = card_id
                self.drag_id = card_id
                self.drag_offset = (x - card.x, y - card.y)
                self.redraw()
            return "break"
        self.pending_edge = None
        self._edge_dragging = False
        self.selected_id = None
        self.drag_id = None
        self._canvas_pan_active = True
        self.canvas.scan_mark(event.x, event.y)
        self.canvas.configure(cursor="fleur")
        self.redraw()
        return "break"

    def _canvas_drag(self, event: tk.Event) -> str:
        if self.pending_edge is not None:
            self._edge_dragging = True
            return self._canvas_motion(event)
        if self._canvas_pan_active:
            self.canvas.scan_dragto(event.x, event.y, gain=1)
            self._refresh_grid()
            return "break"
        card = self._card(self.drag_id)
        if card is None:
            return "break"
        x, y = self.canvas.canvasx(event.x) / self.zoom, self.canvas.canvasy(event.y) / self.zoom
        card.x, card.y, guides = self._snap_card_position(card, x - self.drag_offset[0], y - self.drag_offset[1])
        self.redraw()
        self._draw_alignment_guides(guides)
        return "break"

    def _canvas_release(self, event: tk.Event) -> str:
        if self._rewire is not None:
            return self._finish_rewire(event)
        if getattr(self, "_inline_editor", None) is not None:
            return "break"
        if self.pending_edge is not None and self._edge_dragging:
            source_id, kind = self.pending_edge
            target = self._connection_target_at(event, kind)
            if target is not None and target[0] != source_id:
                self._connect(source_id, kind, target[0], target[1])
            else:
                self.status_var.set("Connection cancelled.")
            self.pending_edge = None
            self._edge_dragging = False
            self.canvas.delete("edge_preview")
            self.redraw()
            return "break"
        if self.drag_id is not None:
            self._changed()
        self.drag_id = None
        self._canvas_pan_active = False
        self.canvas.configure(cursor="")
        self.redraw()
        if getattr(self, "_blocked_routes", None):
            self.status_var.set("Move overlapping cards to free the blocked connection ports: " + ", ".join(self._blocked_routes))
        return "break"

    def _snap_card_position(self, card: AutoTestFlowCard, x: float, y: float) -> tuple[float, float, list[tuple[str, float]]]:
        width, height = self._node_width(card), self._node_height(card)
        snapped_x, snapped_y = self._snap_to_grid(x), self._snap_to_grid(y)
        guides: list[tuple[str, float]] = []
        x_candidates = (snapped_x, snapped_x + width / 2, snapped_x + width)
        y_candidates = (snapped_y, snapped_y + height / 2, snapped_y + height)
        for other in self.cards:
            if other is card:
                continue
            other_x = (other.x, other.x + self._node_width(other) / 2, other.x + self._node_width(other))
            other_y = (other.y, other.y + self._node_height(other) / 2, other.y + self._node_height(other))
            match = next(((own, target) for own in x_candidates for target in other_x if abs(own - target) <= SNAP_TOLERANCE), None)
            if match:
                snapped_x += match[1] - match[0]
                guides.append(("v", match[1]))
                break
        for other in self.cards:
            if other is card:
                continue
            other_y = (other.y, other.y + self._node_height(other) / 2, other.y + self._node_height(other))
            match = next(((own, target) for own in y_candidates for target in other_y if abs(own - target) <= SNAP_TOLERANCE), None)
            if match:
                snapped_y += match[1] - match[0]
                guides.append(("h", match[1]))
                break
        return snapped_x, snapped_y, guides

    @staticmethod
    def _snap_to_grid(value: float) -> float:
        return round(value / SNAP_GRID) * SNAP_GRID

    def _draw_alignment_guides(self, guides: list[tuple[str, float]]) -> None:
        for orientation, value in guides:
            if orientation == "v":
                self.canvas.create_line(value * self.zoom, self.canvas.canvasy(0), value * self.zoom, self.canvas.canvasy(self.canvas.winfo_height()), fill="#22d3ee", dash=(4, 4), tags="alignment_guide")
            else:
                self.canvas.create_line(self.canvas.canvasx(0), value * self.zoom, self.canvas.canvasx(self.canvas.winfo_width()), value * self.zoom, fill="#22d3ee", dash=(4, 4), tags="alignment_guide")

    def _canvas_motion(self, event: tk.Event) -> str:
        if self._rewire is not None and self._rewire[1] == "source":
            return self._preview_source_rewire(event)
        self.canvas.delete("edge_preview")
        if self.pending_edge is None:
            self._hover_edge(event)
            current = self.canvas.find_withtag("current")
            tags = self.canvas.gettags(current[0]) if current else ()
            self.canvas.configure(cursor="hand2" if any(t.startswith(("port:", "inline:", "outputs:")) for t in tags) else "")
            marker = next((t for t in tags if t.startswith(("port:", "input:"))), None)
            if marker:
                prefix, card_id, socket = marker.split(":", 2)
                card = self._card(card_id)
                ports = self._node_inputs(card) if prefix == "input" else self._node_outputs(card)
                label = next((label for kind, label in ports if kind == socket), socket)
                origin = ""
                if prefix == "input" and card.type_id == "plot_panel":
                    name = socket.split("@", 1)[-1]
                    source = self._card(card.params.get(f"_input_{name}_source"))
                    if source:
                        origin = f" ← {self.definitions[source.type_id].title} [{source.card_id}]"
                self.status_var.set(f"{self.definitions[card.type_id].title} / {label} ({self._port_type(socket)}){origin}")
            return "break"
        source = self._card(self.pending_edge[0])
        if source is None:
            return "break"
        kind = self.pending_edge[1]
        target = self._connection_target_at(event, kind)
        self.canvas.configure(cursor="crosshair")
        # Legal sockets glow; the nearest valid socket captures the loose end.
        for card in self.cards:
            if card is source:
                continue
            for input_kind, _ in self._node_inputs(card):
                if compatible(self._port_type(kind), self._port_type(input_kind)):
                    x, y = self._socket_position(card, input_kind, incoming=True)
                    x, y = x * self.zoom, y * self.zoom
                    active = target == (card.card_id, input_kind)
                    r = 10 if active else 7
                    self.canvas.create_oval(x-r, y-r, x+r, y+r, outline="#e9e9ef" if active else "#727680",
                                            width=2 if active else 1, state=tk.DISABLED, tags="edge_preview")
        tx = self.canvas.canvasx(event.x) / self.zoom
        ty = self.canvas.canvasy(event.y) / self.zoom
        if target:
            target_card = self._card(target[0])
            tx, ty = self._socket_position(target_card, target[1], incoming=True)
            self.status_var.set(f"Release to connect to {self.definitions[target_card.type_id].title} / {target[1]} • Esc cancels")
        else:
            self.status_var.set("Drag to a compatible highlighted input • Esc cancels")
        points = (self._wire_route(source, target_card, kind, target_kind=target[1])
                  if target else self._wire_route(source, source, kind, preview=(tx, ty)))
        if len(points) < 2:
            self.status_var.set("The selected output is no longer exposed. Choose an output socket.")
            return "break"
        coords = [v * self.zoom for point in points for v in point]
        color = "#8ac782" if target else PORT_COLORS.get(self._port_type(kind), "#b9bdc6")
        self.canvas.create_line(*coords, fill=color, width=2.5, smooth=False, state=tk.DISABLED, tags="edge_preview")
        return "break"

    def _connection_target_at(self, event: tk.Event, kind: str) -> tuple[str, str] | None:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        output_type = self._port_type(kind)
        source_id = self.pending_edge[0] if self.pending_edge else None
        nearest = None
        for card in self.cards:
            if card.card_id == source_id:
                continue
            for target_kind, _ in self._node_inputs(card):
                px, py = self._socket_position(card, target_kind, incoming=True)
                distance = math.hypot(x-px*self.zoom, y-py*self.zoom)
                if distance <= 14 and compatible(output_type, self._port_type(target_kind)):
                    if nearest is None or distance < nearest[0]:
                        nearest = (distance, card.card_id, target_kind)
        if nearest:
            return nearest[1], nearest[2]
        items = self.canvas.find_overlapping(x, y, x, y)
        for item in reversed(items):
            tags = self.canvas.gettags(item)
            # An explicitly incompatible socket must not fall back to a different input.
            if any(tag.startswith("input:") for tag in tags):
                return None
            marker = next((tag for tag in tags if tag.startswith("node:")), None)
            if marker:
                card_id = marker.split(":", 1)[1]
                if card_id == source_id:
                    return None
                target_input = self._default_target_input(self._card(card_id), kind)
                if target_input:
                    return card_id, target_input
        return None

    @staticmethod
    def _port_type(kind: str) -> str:
        if kind in {"next", "true", "false", "flow"}:
            return "control"
        value = kind.split("@", 1)[0]
        return {"result": "number", "light": "waveform"}.get(value, value)

    def _default_target_input(self, target: AutoTestFlowCard | None, kind: str) -> str:
        if target is None:
            return ""
        output_type = self._port_type(kind)
        if output_type == "control":
            return "flow" if target.type_id not in (LIGHT_ONLY_TYPES | DATA_ONLY_TYPES) and target.type_id != "workflow_start" else ""
        if output_type == "waveform":
            return "waveform@light" if target.type_id in LIGHT_INPUT_TYPES else ""
        for name, data_type in input_ports(target):
            if compatible(output_type, data_type) and not target.params.get(f"_input_{name}_source"):
                return f"{data_type}@{name}"
        return ""

    def _start_canvas_pan(self, event: tk.Event) -> str:
        self._canvas_pan_active = True
        self.canvas.scan_mark(event.x, event.y)
        self.canvas.configure(cursor="fleur")
        return "break"

    def _start_edge_cut(self, event: tk.Event) -> str:
        self._cut_start = (self.canvas.canvasx(event.x) / self.zoom, self.canvas.canvasy(event.y) / self.zoom)
        self.canvas.configure(cursor="crosshair")
        return "break"

    def _move_edge_cut(self, event: tk.Event) -> str:
        if self._cut_start is None:
            return "break"
        self.canvas.delete("edge_cut")
        sx, sy = self._cut_start
        tx, ty = self.canvas.canvasx(event.x) / self.zoom, self.canvas.canvasy(event.y) / self.zoom
        self.canvas.create_line(sx * self.zoom, sy * self.zoom, tx * self.zoom, ty * self.zoom, fill="#fb7185", width=2, dash=(7, 4), tags="edge_cut", state=tk.DISABLED)
        return "break"

    def _end_edge_cut(self, event: tk.Event) -> str:
        if self._cut_start is None:
            return "break"
        end = (self.canvas.canvasx(event.x) / self.zoom, self.canvas.canvasy(event.y) / self.zoom)
        removed = self._cut_edges(self._cut_start, end)
        self._cut_start = None
        self.canvas.configure(cursor="")
        self.canvas.delete("edge_cut")
        self.status_var.set(f"Removed {removed} connection{'s' if removed != 1 else ''}." if removed else "No connection crossed.")
        if removed:
            self._changed()
        else:
            self.redraw()
        return "break"

    def _cut_edges(self, first: tuple[float, float], second: tuple[float, float]) -> int:
        removed = 0
        for source in self.cards:
            for key, kind in (("_next", "next"), ("_true", "true"), ("_false", "false")):
                target = self._card(str(source.params.get(key, "")))
                if target is not None and self._cut_crosses_route(first, second, self._wire_route(source, target, kind)):
                    source.params.pop(key, None)
                    removed += 1
            if source.params.get("_light_source"):
                light = self._card(str(source.params["_light_source"]))
                if light is not None and self._cut_crosses_route(first, second, self._wire_route(light, source, "waveform@signal", target_kind="waveform@light")):
                    source.params.pop("_light_source", None)
                    removed += 1
            result = self._card(str(source.params.get("_result_source", "")))
            metric = str(source.params.get("_result_metric", ""))
            if result is not None and metric and self._cut_crosses_route(first, second, self._wire_route(result, source, f"number@{metric}", target_kind="number@value")):
                source.params.pop("_result_source", None)
                source.params.pop("_result_metric", None)
                removed += 1
            for input_name, input_type in input_ports(source):
                input_source = self._card(str(source.params.get(f"_input_{input_name}_source", "")))
                output = str(source.params.get(f"_input_{input_name}_output", ""))
                if input_source is not None and output and self._cut_crosses_route(first, second, self._wire_route(input_source, source, f"{source.params.get(f'_input_{input_name}_type', input_type)}@{output}", target_kind=f"{input_type}@{input_name}")):
                    self._clear_input(source, f"_input_{input_name}")
                    removed += 1
        return removed

    @classmethod
    def _cut_crosses_route(cls, first: tuple[float, float], second: tuple[float, float], route: list[tuple[float, float]]) -> bool:
        return any(cls._segments_intersect(first, second, start, end) for start, end in zip(route, route[1:]))

    @staticmethod
    def _segments_intersect(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
        def orientation(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
            return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

        values = (orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b))
        if values[0] * values[1] > 0 or values[2] * values[3] > 0:
            return False
        return max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0])) and max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1]))

    @staticmethod
    def _point_segment_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
        dx, dy = end[0] - start[0], end[1] - start[1]
        if dx == 0 and dy == 0:
            return math.dist(point, start)
        ratio = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)))
        return math.dist(point, (start[0] + ratio * dx, start[1] + ratio * dy))

    def _move_canvas_pan(self, event: tk.Event) -> str:
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        self._refresh_grid()
        return "break"

    def _end_canvas_pan(self, _event: tk.Event) -> str:
        self._canvas_pan_active = False
        self.canvas.configure(cursor="")
        return "break"

    def _zoom_canvas(self, event: tk.Event) -> str:
        direction = 1 if getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4 else -1
        old_zoom = self.zoom
        new_zoom = max(0.45, min(2.2, old_zoom * (1.12 if direction > 0 else 1 / 1.12)))
        if abs(new_zoom - old_zoom) < 1e-6:
            return "break"
        world_x = self.canvas.canvasx(event.x) / old_zoom
        world_y = self.canvas.canvasy(event.y) / old_zoom
        self._finish_inline_edit()
        self.zoom = new_zoom
        self.redraw()
        extent = WORLD_EXTENT * new_zoom
        self.canvas.xview_moveto(max(0.0, min(1.0, (world_x * new_zoom - event.x + extent) / (2 * extent))))
        self.canvas.yview_moveto(max(0.0, min(1.0, (world_y * new_zoom - event.y + extent) / (2 * extent))))
        self._refresh_grid()
        self.status_var.set(f"Canvas zoom: {new_zoom * 100:.0f}%")
        return "break"

    def _connect(self, source_id: str, kind: str, target_id: str, target_input: str = "") -> bool:
        source = self._card(source_id)
        target = self._card(target_id)
        if source is None or target is None or source_id == target_id:
            return False
        if target.type_id == "plot_panel" and target_input and target_input not in {kind for kind, _ in self._node_inputs(target)}:
            self.status_var.set("This subplot is hidden by the current layout. Choose a visible input.")
            return False
        output_type = self._port_type(kind)
        if output_type in {"number", "bool", "calibration", "trace", "curve", "plot"}:
            if not target_input:
                target_input = self._default_target_input(target, kind)
            if not compatible(output_type, self._port_type(target_input)) or "@" not in target_input:
                self.status_var.set(f"A {output_type} output requires a {output_type} input port.")
                return False
            metric = kind.split("@", 1)[1]
            declared = dict(output_ports(source))
            declared.update((metric, metric_type(metric)) for metric in RESULT_OUTPUTS.get(source.type_id, ()))
            if declared.get(metric) != output_type:
                self.status_var.set("This result output is not available on the source node.")
                return False
            input_name = target_input.split("@", 1)[1]
            if target_input not in {name for name, _ in self._node_inputs(target)}:
                self.status_var.set("This input socket is unavailable.")
                return False
            target.params[f"_input_{input_name}_source"] = source_id
            target.params[f"_input_{input_name}_output"] = metric
            target.params[f"_input_{input_name}_type"] = output_type
            target.params[f"_input_{input_name}_unit"] = source.params.get("output_unit", "") if metric == "result" else metric_unit(metric)
            if is_condition_flow_type(target.type_id):
                target.params["_result_source"] = source_id
                target.params["_result_metric"] = metric
                target.params["source_node"] = source_id
            if target.type_id == "condition_metric" and output_type == "number":
                target.params["metric"] = metric
            self.status_var.set(f"Connected {self._metric_label(metric)} to {target_id}.")
            self._changed()
            return True
        if output_type == "waveform":
            if source.type_id not in LIGHT_OUTPUT_TYPES or target.type_id not in LIGHT_INPUT_TYPES:
                self.status_var.set("Light waveforms can only connect to a light input.")
                return False
            target.params["_light_source"] = source_id
            self.status_var.set(f"Connected waveform {source_id} to {target_id}.")
            self._changed()
            return True
        if output_type != "control" or source.type_id in LIGHT_ONLY_TYPES | DATA_ONLY_TYPES or target.type_id in LIGHT_ONLY_TYPES | DATA_ONLY_TYPES:
            self.status_var.set("Control-flow links require control ports.")
            return False
        if kind not in {name for name, _ in self._node_outputs(source)} or "flow" not in {name for name, _ in self._node_inputs(target)}:
            self.status_var.set("Control links require an output and a valid flow input.")
            return False
        key = "_" + kind
        source.params[key] = target_id
        self.status_var.set(f"Connected {source_id} {kind} to {target_id}.")
        self._changed()
        return True

    def _show_inspector(self, card: AutoTestFlowCard) -> None:
        self._inspected_id = card.card_id
        was_hidden = not self._inspector_is_visible()
        if was_hidden:
            self.body_pane.add(self.inspector, minsize=300, width=380, stretch="never")
        definition = self.definitions[card.type_id]
        self.delete_node_button.configure(state="disabled" if card.type_id in BOUNDARY_TYPES else "normal")
        self.inspector_title.configure(text=definition.title)
        self.inspector_summary.configure(text=definition.description)
        for child in self.param_body.winfo_children():
            child.destroy()
        self.param_vars.clear()
        self._field_widgets.clear()
        self._error_labels.clear()
        self.output_vars.clear()
        row = 0
        available_outputs = RESULT_OUTPUTS.get(card.type_id, ())
        if available_outputs:
            self._build_result_output_group(self.param_body, row, card, available_outputs)
            row += 1
        if card.type_id == "laser_pulse":
            tk.Label(self.param_body, text="WAVEFORM PREVIEW", bg=self.colors["surface_2"], fg=self.colors["accent"], font=("Segoe UI Semibold", 8), anchor="w").grid(row=row, column=0, sticky="ew", pady=(2, 5))
            row += 1
            self.wave_canvas = tk.Canvas(self.param_body, height=142, bg="#080d13", highlightthickness=1, highlightbackground=self.colors["border"], bd=0)
            self.wave_canvas.grid(row=row, column=0, sticky="ew", pady=(0, 10))
            row += 1
            ttk.Button(self.param_body, text="Edit Waveform", style="Accent.TButton", command=lambda target=card: self._open_waveform_editor(target)).grid(row=row, column=0, sticky="ew", pady=(0, 10))
            row += 1
        params_by_key = {param.key: param for param in definition.parameters}
        if card.type_id == "plot_panel":
            self._build_parameter_group(self.param_body, row, "Panel", [params_by_key["layout"]], card)
            row += 1
            notebook = ttk.Notebook(self.param_body)
            self._plot_slot_notebook = notebook
            self._plot_slot_pages = []
            notebook.grid(row=row, column=0, sticky="nsew")
            for i in range(1, 7):
                page = tk.Frame(notebook, bg=self.colors["surface_2"])
                notebook.add(page, text=str(i))
                self._plot_slot_pages.append(page)
                page.columnconfigure(0, weight=1)
                self._build_source_picker(page, 0, card, f"slot{i}", "plot")
                self._build_parameter_group(page, 1, f"Subplot {i}", [p for p in definition.parameters if p.key.startswith(f"slot{i}_")], card)
            self._sync_plot_slot_tabs(card)
            row += 1
        elif card.type_id == "virtual_mask":
            self._build_virtual_mask_summary(self.param_body, row, card)
            row += 1
            ttk.Button(
                self.param_body, text="Configure mask, mapping & power…", style="Accent.TButton",
                command=lambda target=card: self._open_virtual_mask_config(target),
            ).grid(row=row, column=0, sticky="ew", pady=(0, 10))
            row += 1
        else:
            for group_title, keys in self._parameter_groups(definition):
                group_params = [params_by_key[key] for key in keys if key in params_by_key]
                if not group_params:
                    continue
                self._build_parameter_group(self.param_body, row, group_title, group_params, card)
                row += 1
        if card.type_id == "math_formula":
            tk.Label(self.param_body, text="Functions: abs, sqrt, log, log10, exp, sin, cos, min, max, mean, std, peak, integral, slope.\nsegment(curve, start, stop): sample-index slice, stop excluded.\nExample: integral(segment(signal, 0, 100)) / area\nPreview uses constants and synthetic curves; it does not measure.", wraplength=310, justify="left", bg=self.colors["surface_2"], fg=self.colors["muted"]).grid(row=row, column=0, sticky="ew", pady=8)
            row += 1
            def apply_inputs():
                try:
                    names = {name for name, _dtype in registered_input_ports(card)}
                except ValueError as exc:
                    self.status_var.set(str(exc))
                    return
                for key in tuple(card.params):
                    if key.startswith("_input_") and key.rsplit("_", 1)[0][7:] not in names:
                        card.params.pop(key, None)
                self._show_inspector(card)
                self._changed()
            ttk.Button(self.param_body, text="Apply input definitions", command=apply_inputs).grid(row=row, column=0, sticky="ew")
            row += 1
            for name, dtype in input_ports(card):
                self._build_source_picker(self.param_body, row, card, name, dtype)
                row += 1
        if card.type_id == "laser_pulse":
            self._draw_waveform(card)
        if was_hidden:
            self.window.after_idle(lambda target=card: self._ensure_card_visible(target))

    def _build_source_picker(self, parent, row, card, name, expected):
        choices = {"Unconnected / constant": None}
        for source in self.cards:
            if source is card:
                continue
            outputs = (*output_ports(source), *((metric, metric_type(metric)) for metric in RESULT_OUTPUTS.get(source.type_id, ())))
            for output, dtype in outputs:
                if compatible(dtype, expected):
                    choices[f"{source.card_id} → {self._metric_label(output)} [{dtype}]"] = (source, output, dtype)
        current = next((label for label, entry in choices.items() if entry and entry[0].card_id == card.params.get(f"_input_{name}_source") and entry[1] == card.params.get(f"_input_{name}_output")), "Unconnected / constant")
        frame = tk.Frame(parent, bg=self.colors["surface_2"])
        frame.grid(row=row, column=0, sticky="ew", pady=4)
        tk.Label(frame, text=f"{name} source", bg=self.colors["surface_2"], fg=self.colors["text"]).pack(anchor="w")
        picker = ttk.Combobox(frame, values=tuple(choices), state="readonly", width=36)
        picker.set(current)
        picker.pack(fill="x")
        def apply(_event):
            entry = choices[picker.get()]
            if entry:
                source, output, dtype = entry
                selected = set(source.params.get("_outputs", "").split(",")) - {""}
                selected.add(output)
                source.params["_outputs"] = ",".join(sorted(selected))
                self._connect(source.card_id, f"{dtype}@{output}", card.card_id, f"{expected}@{name}")
            else:
                self._clear_input(card, f"_input_{name}")
                self._changed()
        picker.bind("<<ComboboxSelected>>", apply)

    def _ensure_card_visible(self, card: AutoTestFlowCard) -> None:
        self.window.update_idletasks()
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        left = self.canvas.canvasx(0) / self.zoom
        right = self.canvas.canvasx(width) / self.zoom
        top = self.canvas.canvasy(0) / self.zoom
        bottom = self.canvas.canvasy(height) / self.zoom
        card_left, card_top, card_right, card_bottom = self._node_bounds(card, 42)
        if card_left >= left and card_right <= right and card_top >= top and card_bottom <= bottom:
            return
        center_x = (card_left + card_right) / 2 * self.zoom
        center_y = (card_top + card_bottom) / 2 * self.zoom
        extent = WORLD_EXTENT * self.zoom
        self.canvas.xview_moveto(max(0.0, min(1.0, (center_x - width / 2 + extent) / (2 * extent))))
        self.canvas.yview_moveto(max(0.0, min(1.0, (center_y - height / 2 + extent) / (2 * extent))))
        self.redraw()

    def _build_parameter_group(self, parent: tk.Frame, row: int, title: str, params: list[AutoTestFlowParam], card: AutoTestFlowCard) -> None:
        section = tk.Frame(parent, bg=self.colors["surface_2"])
        section.grid(row=row, column=0, sticky="ew", pady=(5, 9))
        section.columnconfigure(0, weight=1)
        tk.Label(section, text=title.upper(), bg=self.colors["surface_2"], fg=self.colors["accent"], font=("Segoe UI Semibold", 8), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 3))
        ttk.Separator(section, orient=tk.HORIZONTAL).grid(row=1, column=0, sticky="ew", pady=(0, 4))
        fields = tk.Frame(section, bg=self.colors["surface_2"])
        fields.grid(row=2, column=0, sticky="ew")
        fields.columnconfigure((0, 1), weight=1, uniform="parameter_columns")
        field_row = 0
        column = 0
        for param in params:
            wide = param.key in WIDE_PARAMETER_KEYS or param.key in {"expression", "variables", "constants", "curve_series"}
            if wide and column:
                field_row += 1
                column = 0
            field = tk.Frame(fields, bg=self.colors["surface_2"])
            field.grid(
                row=field_row,
                column=0 if wide else column,
                columnspan=2 if wide else 1,
                sticky="ew",
                padx=(0, 0) if wide else ((0, 5) if column == 0 else (5, 0)),
                pady=(3, 4),
            )
            field.columnconfigure(0, weight=1)
            tk.Label(field, text=param.label, bg=self.colors["surface_2"], fg=self.colors["muted"], font=("Segoe UI", 8), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 2))
            variable = tk.StringVar(value=self._draft_values.get((card.card_id, param.key), card.params.get(param.key, param.default)))
            self.param_vars[param.key] = variable
            choices = self._choices(card.type_id, param.key)
            widget: tk.Widget
            if choices:
                state = "normal" if param.key in {"metric", "extract", "measure_range"} else "readonly"
                widget = ttk.Combobox(field, textvariable=variable, values=choices, state=state)
            else:
                widget = ttk.Entry(field, textvariable=variable)
            widget.grid(row=1, column=0, sticky="ew")
            self._field_widgets[card.card_id, param.key] = widget
            if (card.card_id, param.key) in self._field_errors and widget.winfo_class() == "TEntry":
                widget.configure(style="Flow.Invalid.TEntry")
            error_label = tk.Label(field, text=self._field_errors.get((card.card_id, param.key), ""),
                                   bg=self.colors["surface_2"], fg="#fca5a5", font=("Segoe UI", 8), wraplength=290 if wide else 145, anchor="w", justify="left")
            error_label.grid(row=2, column=0, sticky="ew")
            self._error_labels[card.card_id, param.key] = error_label
            widget.bind("<FocusOut>", lambda e, target=card, key=param.key: self._end_parameter_edit(target, key))
            widget.bind("<Return>", lambda e, target=card, key=param.key: self._end_parameter_edit(target, key))
            widget.bind("<Escape>", lambda e, target=card, key=param.key: self._discard_parameter_draft(target, key))
            variable.trace_add("write", lambda *_args, key=param.key, value=variable, target=card: self._param_changed(target, key, value.get()))
            if wide:
                field_row += 1
                column = 0
            else:
                column += 1
                if column >= 2:
                    field_row += 1
                    column = 0

    def _build_result_output_group(self, parent: tk.Frame, row: int, card: AutoTestFlowCard, outputs: tuple[str, ...]) -> None:
        section = tk.Frame(parent, bg=self.colors["surface_2"])
        section.grid(row=row, column=0, sticky="ew", pady=(5, 9))
        section.columnconfigure(0, weight=1)
        tk.Label(section, text="EXPOSE RESULT OUTPUTS", bg=self.colors["surface_2"], fg="#c084fc",
                 font=("Segoe UI Semibold", 8), anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 3))
        tk.Label(section, text="Check results to expose sockets. Unchecking disconnects their links.\nConnect each result socket to a Plot Slot; Flow controls execution.",
                 bg=self.colors["surface_2"], fg=self.colors["muted"], font=("Segoe UI", 8),
                 anchor="w", justify="left", wraplength=330).grid(row=1, column=0, sticky="ew", pady=(0, 6))
        selected = set(self._selected_result_outputs(card))
        self.result_output_widgets = {}
        for index, metric in enumerate(outputs):
            variable = tk.BooleanVar(value=metric in selected)
            self.output_vars[metric] = variable
            field = tk.Frame(section, bg=self.colors["surface_2"])
            field.grid(row=2+index, column=0, sticky="ew", pady=2)
            check = ttk.Checkbutton(field, text=self._metric_label(metric), variable=variable,
                                    command=lambda target=card: self._result_outputs_changed(target))
            check.pack(side="left")
            self.result_output_widgets[metric] = check
            tk.Label(field, text=metric_type(metric), bg=self.colors["surface_2"],
                     fg=PORT_COLORS[metric_type(metric)], font=("Segoe UI", 8)).pack(side="right")

    def _result_outputs_changed(self, card: AutoTestFlowCard) -> None:
        selected = {metric for metric in RESULT_OUTPUTS.get(card.type_id, ())
                    if self.output_vars.get(metric) and self.output_vars[metric].get()}
        removed = set(self._selected_result_outputs(card)) - selected
        disconnected = 0
        for target in self.cards:
            for key, source in list(target.params.items()):
                if key.startswith("_input_") and key.endswith("_source") and source == card.card_id:
                    prefix = key[:-len("_source")]
                    if target.params.get(prefix + "_output") in removed:
                        for suffix in ("source", "output", "type", "unit"):
                            target.params.pop(prefix + "_" + suffix, None)
                        disconnected += 1
            if target.params.get("_result_source") == card.card_id and target.params.get("_result_metric") in removed:
                target.params.pop("_result_source", None)
                target.params.pop("_result_metric", None)
                if target.params.get("source_node") == card.card_id:
                    target.params.pop("source_node", None)
        card.params["_outputs"] = ",".join(metric for metric in RESULT_OUTPUTS.get(card.type_id, ()) if metric in selected)
        self._changed()
        self.status_var.set(f"{len(selected)} result outputs exposed; {disconnected} result links disconnected.")

    def _selected_result_outputs(self, card: AutoTestFlowCard) -> tuple[str, ...]:
        selected = {value.strip() for value in str(card.params.get("_outputs", "")).split(",") if value.strip()}
        # Imported templates may have real data connections without an exposure list.
        # Include hidden Plot slots too, so changing layout never loses their sockets.
        for target in self.cards:
            for key, source in target.params.items():
                if key.startswith("_input_") and key.endswith("_source") and source == card.card_id:
                    selected.add(target.params.get(key[:-len("_source")] + "_output", ""))
            if target.params.get("_result_source") == card.card_id:
                selected.add(target.params.get("_result_metric", ""))
        return tuple(metric for metric in RESULT_OUTPUTS.get(card.type_id, ()) if metric in selected)

    @staticmethod
    def _metric_label(metric: str) -> str:
        names = {
            "live": "Live current (A)", "curve": "Current curves (A)",
            "live_voltage": "Live voltage (V)", "curve_voltage": "Voltage curves (V)",
            "live_resistance": "Live resistance (Ω)", "curve_resistance": "Resistance curves (Ω)",
            "vth": "Vth (V)", "on_off_ratio": "On/off ratio",
            "statistics.subthreshold_swing_v_dec": "SS (V/dec)",
            "statistics.mean_current_a": "Mean current (A)",
        }
        return names.get(metric, metric.rsplit(".", 1)[-1].replace("_", " ").title() + (f" ({metric_unit(metric)})" if metric_unit(metric) else ""))

    @staticmethod
    def _result_output_type(metric: str) -> str:
        return metric_type(metric)

    @staticmethod
    def _parameter_groups(definition: AutoTestFlowDefinition) -> tuple[tuple[str, tuple[str, ...]], ...]:
        configured = PARAMETER_GROUPS.get(definition.type_id, ())
        used = {key for _title, keys in configured for key in keys}
        remaining = tuple(param.key for param in definition.parameters if param.key not in used and param.key != "waveform_points")
        if not configured:
            return (("Parameters", remaining),)
        if remaining:
            return configured + (("Other", remaining),)
        return configured

    def _inspector_is_visible(self) -> bool:
        inspector_path = str(self.inspector)
        return any(str(pane) == inspector_path for pane in self.body_pane.panes())

    def _hide_inspector(self) -> None:
        self._inspected_id = None
        if self._inspector_is_visible():
            self.body_pane.forget(self.inspector)

    @staticmethod
    def _choices(type_id: str, key: str) -> tuple[str, ...]:
        if type_id == 'iv' and key in {'measure_range', 'source_range'}:
            return ()
        if key.endswith("scale"):
            return ("linear", "log")
        if key.endswith("_iv_x_axis"):
            return ("V set", "V measure")
        if key == "layout":
            return ("1x1", "1x2", "2x2", "2x3")
        if key == "output_type":
            return ("number", "bool")
        if key == "on_invalid":
            return ("invalid", "stop")
        if key == "operator":
            if type_id == "math_formula":
                return ("+", "-", "*", "/", "min", "max")
            if type_id == "boolean_logic":
                return ("AND", "OR", "XOR", "NOT")
            return (">", ">=", "<", "<=", "==", "!=")
        return {
            "output_terminal": ("rear", "front"),
            "coordinate_frame": ("xy", "uv"),
            "pattern": ("serpentine", "raster"),
            "sweep_mode": ("voltage", "current"),
            "initial_direction": ("up", "down"),
            "scan_type": ("single", "forward", "reverse", "bidirectional"),
            "mode": ("Z", "Z-XY"),
            "xy_pattern": ("square", "corners"),
            "best_current": ("max_abs", "max", "min"),
            "measure_range": ("auto",),
            "resistance_method": ("linear_fit", "pointwise_median"),
            "plot_layout": ("horizontal", "vertical"),
            "drain_smu": ("smu1", "smu2", "smu3", "smu4"),
            "gate_smu": ("smu1", "smu2", "smu3", "smu4"),
            "measurement_adc": ("high_resolution", "high_speed"),
            "save_mode": ("both", "long", "wide", "per_curve"),
            "output_statistics": ("true", "false"),
            "output_off_after": ("true", "false"),
            "mapping_once": ("false", "true"),
            "hold_during_mapping": ("false", "true"),
            "verify_bias": ("false", "true"),
            "program": ("custom", "calibrated_response_suite"),
            "optical_model": ("none", "TEMT6000"),
            "heatmap_values": ("true", "false"),
            "measure_gate_leak": ("true", "false"),
            "abort_on_compliance": ("false", "true"),
            "autozero": ("false", "true"),
            "missing_result": ("false", "true"),
            "fallback": ("false", "true"),
            "fallback_a": ("false", "true") if type_id == "boolean_logic" else (),
            "fallback_b": ("false", "true") if type_id == "boolean_logic" else (),
            "expected": ("true", "false"),
            "state": ("open_circuit", "short_circuit", "healthy"),
            "output_state": ("on", "off"),
            "final_state": ("off", "baseline"),
            "dark_reference": ("true", "false"),
            "metric": ("photocurrent_a", "photo_dark_ratio", "responsivity_a_w", "statistics.resistance_ohm", "vth", "on_off_ratio"),
            "extract": ("rise_fall_time", "peak_current", "steady_state", "gain"),
        }.get(key, ())

    def _build_virtual_mask_summary(self, parent: tk.Frame, row: int, card: AutoTestFlowCard) -> None:
        from .virtual_mask import parse_binding_points

        try:
            count = len(parse_binding_points(card.params.get("bindings", "")))
        except ValueError:
            count = 0
        black = card.params.get("black_power_mw", card.params.get("power_min_mw", "0"))
        white = card.params.get("white_power_mw", card.params.get("power_max_mw", "5"))
        inverted = str(card.params.get("invert_intensity", "false")).lower() in {"1", "true", "yes", "on"}
        section = tk.Frame(parent, bg=self.colors["surface_2"], padx=10, pady=10)
        section.grid(row=row, column=0, sticky="ew", pady=(5, 9))
        section.columnconfigure(0, weight=1)
        tk.Label(
            section,
            text=(
                f"Image: {Path(str(card.params.get('image_path', ''))).name or 'not selected'}\n"
                f"Binding: {card.params.get('coordinate_frame', 'xy').upper()} · {count} points\n"
                f"Laser power: black {black} mW → white {white} mW · "
                f"{'inverted' if inverted else 'normal'} intensity"
            ),
            bg=self.colors["surface_2"], fg=self.colors["text"], justify="left", anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        tk.Label(
            section,
            text="Image bindings only position the mask. Scan bounds, steps and path come from the main Mapping page. DP100 uses the fixed installed-laser profile.",
            bg=self.colors["surface_2"], fg=self.colors["muted"], justify="left", anchor="w", wraplength=330,
        ).grid(row=1, column=0, sticky="ew", pady=(7, 0))

    def _open_virtual_mask_config(self, card: AutoTestFlowCard) -> None:
        from .virtual_mask import MaskBindingPoint, load_grayscale, parse_binding_points

        dialog = tk.Toplevel(self.window)
        dialog.title("Virtual mask configuration")
        dialog.transient(self.window)
        dialog.geometry("1120x720")
        dialog.minsize(940, 620)
        dialog.columnconfigure(0, weight=3)
        dialog.columnconfigure(1, weight=2)
        dialog.rowconfigure(1, weight=1)

        path_var = tk.StringVar(value=str(card.params.get("image_path", "")))
        saved_frame = str(card.params.get("coordinate_frame", "xy")).lower()
        frame_var = tk.StringVar(value=saved_frame if saved_frame in {"xy", "uv"} else "xy")
        black_var = tk.StringVar(value=str(card.params.get("black_power_mw", card.params.get("power_min_mw", "0"))))
        white_var = tk.StringVar(value=str(card.params.get("white_power_mw", card.params.get("power_max_mw", "5"))))
        invert_var = tk.BooleanVar(value=str(card.params.get("invert_intensity", "false")).lower() in {"1", "true", "yes", "on"})
        pixel_x_var, pixel_y_var = tk.StringVar(), tk.StringVar()
        target_a_var, target_b_var = tk.StringVar(), tk.StringVar()
        status = tk.StringVar(value="Click the image to choose a pixel, enter its target coordinate, then click Add new.")
        try:
            bindings = list(parse_binding_points(card.params.get("bindings", "")))
        except ValueError:
            bindings = []

        header = ttk.Frame(dialog, padding=12)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Virtual Mask · image → coordinate → power", font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=status, foreground="#ef4444", wraplength=760).grid(row=1, column=0, sticky="ew", pady=(5, 0))

        image_frame = ttk.LabelFrame(dialog, text="1 · Click image points", padding=8)
        image_frame.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 8))
        image_frame.columnconfigure(0, weight=1)
        image_frame.rowconfigure(1, weight=1)
        path_bar = ttk.Frame(image_frame)
        path_bar.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        path_bar.columnconfigure(0, weight=1)
        ttk.Entry(path_bar, textvariable=path_var, state="readonly").grid(row=0, column=0, sticky="ew")
        mask_canvas = tk.Canvas(image_frame, bg="#111827", highlightthickness=1, highlightbackground="#334155", cursor="crosshair")
        mask_canvas.grid(row=1, column=0, sticky="nsew")
        image_state: dict[str, object] = {}

        controls = ttk.Frame(dialog, padding=(6, 0, 12, 8))
        controls.grid(row=1, column=1, sticky="nsew")
        controls.columnconfigure(0, weight=1)
        controls.rowconfigure(3, weight=1)

        settings_box = ttk.LabelFrame(controls, text="2 · Mapping & laser power", padding=8)
        settings_box.grid(row=0, column=0, sticky="ew")
        settings_box.columnconfigure(1, weight=1)
        mode_row = ttk.Frame(settings_box)
        mode_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(mode_row, text="Coordinate mode").pack(side="left", padx=(0, 12))
        ttk.Radiobutton(mode_row, text="Stage XY", variable=frame_var, value="xy").pack(side="left", padx=(0, 10))
        ttk.Radiobutton(mode_row, text="GDS UV", variable=frame_var, value="uv").pack(side="left")
        fields = (
            ("Black pixel electrical power (mW)", black_var, ()),
            ("White pixel electrical power (mW)", white_var, ()),
        )
        for index, (label, variable, choices) in enumerate(fields):
            ttk.Label(settings_box, text=label).grid(row=index + 1, column=0, sticky="w", padx=(0, 8), pady=3)
            widget = ttk.Combobox(settings_box, textvariable=variable, values=choices, state="readonly") if choices else ttk.Entry(settings_box, textvariable=variable)
            widget.grid(row=index + 1, column=1, sticky="ew", pady=3)
        ttk.Checkbutton(
            settings_box, text="Invert grayscale intensity (black ↔ white)", variable=invert_var,
            command=lambda: dialog.after_idle(render_image),
        ).grid(row=len(fields) + 1, column=0, columnspan=2, sticky="w", pady=(5, 0))
        mode_help = ttk.Label(
            settings_box,
            text="",
            wraplength=330,
        )
        mode_help.grid(row=len(fields) + 2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        point_box = ttk.LabelFrame(controls, text="3 · Selected binding point", padding=8)
        point_box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        point_box.columnconfigure((1, 3), weight=1)
        coordinate_labels = ("Stage X (µm)", "Stage Y (µm)") if frame_var.get() == "xy" else ("GDS U (µm)", "GDS V (µm)")
        point_labels = []
        for column, (label, variable) in enumerate((("Pixel X", pixel_x_var), ("Pixel Y", pixel_y_var), (coordinate_labels[0], target_a_var), (coordinate_labels[1], target_b_var))):
            pair, side = divmod(column, 2)
            grid_column = side * 2
            label_widget = ttk.Label(point_box, text=label)
            label_widget.grid(row=pair, column=grid_column, sticky="w", padx=(0, 4), pady=3)
            point_labels.append(label_widget)
            ttk.Entry(point_box, textvariable=variable, width=12).grid(row=pair, column=grid_column + 1, sticky="ew", padx=(0, 8), pady=3)

        point_buttons = ttk.Frame(controls)
        point_buttons.grid(row=2, column=0, sticky="ew", pady=6)
        point_buttons.columnconfigure((0, 1, 2, 3), weight=1)

        table = ttk.Treeview(controls, columns=("px", "py", "a", "b"), show="headings", height=9, selectmode="browse")
        table.grid(row=3, column=0, sticky="nsew")
        for key, label, width in (("px", "Pixel X", 70), ("py", "Pixel Y", 70), ("a", coordinate_labels[0], 100), ("b", coordinate_labels[1], 100)):
            table.heading(key, text=label)
            table.column(key, width=width, anchor="center")

        def refresh_table() -> None:
            table.delete(*table.get_children())
            for index, point in enumerate(bindings):
                table.insert("", "end", iid=str(index), values=(f"{point.pixel_x:g}", f"{point.pixel_y:g}", f"{point.coordinate_a:g}", f"{point.coordinate_b:g}"))

        def refresh_mode_labels(*_args) -> None:
            if frame_var.get() == "uv":
                labels = ("GDS U (µm)", "GDS V (µm)")
                mode_help.configure(text="UV mode: target coordinates are GDS layout U/V and require a fitted LayoutMap before Preview/Start.")
            else:
                labels = ("Stage X (µm)", "Stage Y (µm)")
                mode_help.configure(text="XY mode: target coordinates directly specify logical sample-stage X/Y positions.")
            point_labels[2].configure(text=labels[0])
            point_labels[3].configure(text=labels[1])
            table.heading("a", text=labels[0])
            table.heading("b", text=labels[1])
            status.set(f"Coordinate mode switched to {frame_var.get().upper()}; verify all target coordinates in the table.")

        frame_var.trace_add("write", refresh_mode_labels)

        def render_image() -> None:
            try:
                import cv2
                image = load_grayscale(path_var.get())
                canvas_w, canvas_h = max(100, mask_canvas.winfo_width()), max(100, mask_canvas.winfo_height())
                scale = min((canvas_w - 24) / image.shape[1], (canvas_h - 24) / image.shape[0])
                render_w, render_h = max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))
                display_image = 255 - image if invert_var.get() else image
                resized = cv2.resize(display_image, (render_w, render_h), interpolation=cv2.INTER_NEAREST)
                rgb = cv2.cvtColor(resized, cv2.COLOR_GRAY2RGB)
                photo = tk.PhotoImage(data=f"P6 {render_w} {render_h} 255\n".encode("ascii") + rgb.tobytes(), format="PPM")
                x0, y0 = (canvas_w - render_w) / 2, (canvas_h - render_h) / 2
                mask_canvas.delete("all")
                mask_canvas.create_image(x0, y0, image=photo, anchor="nw")
                for index, point in enumerate(bindings, 1):
                    x, y = x0 + point.pixel_x * scale, y0 + point.pixel_y * scale
                    mask_canvas.create_oval(x - 6, y - 6, x + 6, y + 6, outline="#22d3ee", width=2)
                    mask_canvas.create_text(x + 9, y - 9, text=str(index), fill="#f8fafc", anchor="sw", font=("Segoe UI", 9, "bold"))
                image_state.update(image=image, photo=photo, scale=scale, x0=x0, y0=y0)
            except Exception as exc:
                mask_canvas.delete("all")
                mask_canvas.create_text(20, 20, text=str(exc), fill="#ef4444", anchor="nw", width=440)
                image_state.clear()

        def choose_image() -> None:
            path = filedialog.askopenfilename(
                parent=dialog, title="Choose grayscale virtual mask",
                filetypes=(("Images", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.pgm"), ("All files", "*.*")),
            )
            if path:
                path_var.set(path)
                dialog.after_idle(render_image)

        ttk.Button(path_bar, text="Browse…", command=choose_image).grid(row=0, column=1, padx=(6, 0))

        def canvas_click(event: tk.Event) -> None:
            image = image_state.get("image")
            if image is None:
                return
            scale, x0, y0 = float(image_state["scale"]), float(image_state["x0"]), float(image_state["y0"])
            height, width = image.shape
            px = min(width - 1, max(0, round((event.x - x0) / scale)))
            py = min(height - 1, max(0, round((event.y - y0) / scale)))
            pixel_x_var.set(str(px))
            pixel_y_var.set(str(py))
            selected = table.selection()
            if selected:
                status.set(f"Moved selected binding point to image pixel ({px}, {py}); edit its target coordinate or click Update selected.")
            else:
                target_a_var.set("")
                target_b_var.set("")
                status.set(f"Selected image pixel ({px}, {py}); enter its target coordinate, then click Add point.")

        mask_canvas.bind("<Button-1>", canvas_click)
        mask_canvas.bind("<Configure>", lambda _event: dialog.after_idle(render_image))

        def select_row(_event=None) -> None:
            selected = table.selection()
            if not selected:
                return
            point = bindings[int(selected[0])]
            pixel_x_var.set(f"{point.pixel_x:g}")
            pixel_y_var.set(f"{point.pixel_y:g}")
            target_a_var.set(f"{point.coordinate_a:g}")
            target_b_var.set(f"{point.coordinate_b:g}")

        table.bind("<<TreeviewSelect>>", select_row)

        def add_or_update(*, add_new: bool = False) -> None:
            try:
                point = MaskBindingPoint(*(float(variable.get()) for variable in (pixel_x_var, pixel_y_var, target_a_var, target_b_var)))
            except ValueError:
                status.set("Pixel and target coordinates must all be numeric.")
                return
            selected = table.selection()
            if not add_new and not selected:
                status.set("Select a binding row to update, or click Add new to create another point.")
                return
            if not add_new:
                selected_index = int(selected[0])
                bindings[selected_index] = point
                action = "Updated"
            else:
                bindings.append(point)
                selected_index = len(bindings) - 1
                action = "Added"
            refresh_table()
            render_image()
            table.selection_set(str(selected_index))
            table.focus(str(selected_index))
            status.set(f"{action} binding point {selected_index + 1}; {len(bindings)} points total.")

        def delete_point() -> None:
            selected = table.selection()
            if selected:
                bindings.pop(int(selected[0]))
                refresh_table()
                render_image()

        def reset_corners() -> None:
            image = image_state.get("image")
            if image is None:
                return
            height, width = image.shape
            bindings[:] = [
                MaskBindingPoint(0, height - 1, 0, 0), MaskBindingPoint(width - 1, height - 1, 100, 0),
                MaskBindingPoint(0, 0, 0, 50), MaskBindingPoint(width - 1, 0, 100, 50),
            ]
            refresh_table()
            render_image()
            status.set("Reset to four image corners mapped to a 100 × 50 target rectangle.")

        ttk.Button(point_buttons, text="Add new", command=lambda: add_or_update(add_new=True)).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(point_buttons, text="Update selected", command=add_or_update).grid(row=0, column=1, sticky="ew", padx=3)
        ttk.Button(point_buttons, text="Delete", command=delete_point).grid(row=0, column=2, sticky="ew", padx=3)
        ttk.Button(point_buttons, text="Reset corners", command=reset_corners).grid(row=0, column=3, sticky="ew", padx=(3, 0))

        footer = ttk.Frame(dialog, padding=(12, 0, 12, 12))
        footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        footer.columnconfigure(0, weight=1)

        def save() -> None:
            binding_text = ";".join(
                f"{point.pixel_x:g},{point.pixel_y:g},{point.coordinate_a:g},{point.coordinate_b:g}" for point in bindings
            )
            try:
                load_grayscale(path_var.get())
                parse_binding_points(binding_text)
                black, white = float(black_var.get()), float(white_var.get())
                if black < 0 or white < black:
                    raise ValueError("White power must be greater than or equal to nonnegative black power.")
            except ValueError as exc:
                status.set(str(exc))
                return
            card.params.update(
                image_path=path_var.get(), coordinate_frame=frame_var.get(), bindings=binding_text,
                black_power_mw=f"{black:g}", white_power_mw=f"{white:g}",
                invert_intensity="true" if invert_var.get() else "false",
            )
            for obsolete in ("sample_pitch_px", "pattern", "power_min_mw", "power_max_mw", "drive_voltage_v", "drive_current_min_a", "drive_current_max_a", "ovp_v", "ocp_a", "preset_index"):
                card.params.pop(obsolete, None)
            self._changed(redraw=True)
            self._show_inspector(card)
            dialog.destroy()

        ttk.Button(footer, text="Cancel", command=dialog.destroy).grid(row=0, column=1, padx=(5, 0))
        ttk.Button(footer, text="Save configuration", style="Accent.TButton", command=save).grid(row=0, column=2, padx=(5, 0))
        refresh_table()
        refresh_mode_labels()
        dialog.after_idle(render_image)

    def _sync_plot_slot_tabs(self, card):
        notebook = getattr(self, "_plot_slot_notebook", None)
        if (getattr(self, "_inspected_id", None) != card.card_id
                or notebook is None or not notebook.winfo_exists()):
            return
        count = len(input_ports(card))
        for index, page in enumerate(self._plot_slot_pages):
            if index < count:
                notebook.add(page, text=str(index + 1))
            else:
                notebook.hide(page)

    def _param_changed(self, card: AutoTestFlowCard, key: str, value: str) -> None:
        canonical, error = self._validate_parameter(card, key, value)
        self._field_feedback(card, key, value, error)
        if error:
            return
        value = canonical
        card.params[key] = value
        if card.type_id == 'iv' and key == 'measure_range':
            from .smu_settings import reduce_workflow_limit
            adjustment = reduce_workflow_limit(card.params)
            if adjustment:
                limit_key, limit = adjustment
                card.params[limit_key] = limit
                if getattr(self, '_inspected_id', None) == card.card_id and limit_key in self.param_vars:
                    self.param_vars[limit_key].set(limit)
                self.status_var.set(f'Protection reduced to {limit} for the selected measurement range.')
        if card.type_id == "plot_panel" and key == "layout":
            self._sync_plot_slot_tabs(card)
        if card.type_id == "math_formula":
            from .workflow_formula import parse_expression, evaluate_expression, Curve
            import json
            try:
                ports = registered_input_ports(card)
                parse_expression(card.params.get("expression", "a+b"), dict(ports))
                values = json.loads(card.params.get("constants", "{}"))
                for name, dtype in ports:
                    values.setdefault(name, Curve((0., 1., 2.), (0., 1., 4.)) if dtype == "curve" else True if dtype == "bool" else 1.)
                preview = evaluate_expression(card.params.get("expression", "a+b"), values, card.params.get("output_type", "number"))
                self.status_var.set(f"Example preview (synthetic inputs): {preview}")
            except (ValueError, SyntaxError, TypeError) as exc:
                self.status_var.set(f"Formula: {exc}")
        if card.type_id == "laser_pulse" and hasattr(self, "wave_canvas"):
            self._draw_waveform(card)
        self._changed(redraw=True, group=(card.card_id, key))

    def _draw_waveform(self, card: AutoTestFlowCard) -> None:
        canvas = self.wave_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 280)
        left, right, top, bottom = 24, width - 16, 18, 112
        points = parse_waveform_points(card.params)
        period = max(0.001, points[-1].time_ms)
        peak = max(0.01, max(point.voltage_v for point in points))
        canvas.create_line(left, bottom, right, bottom, fill=self.colors["border"])
        canvas.create_line(left, bottom, left, top, fill=self.colors["border"])
        coordinates: list[float] = []
        for point in points:
            coordinates.extend((left + (right - left) * point.time_ms / period, bottom - (bottom - top - 14) * point.voltage_v / peak))
        canvas.create_line(*coordinates, fill="#f43f5e", width=3)
        canvas.create_text(left, 4, anchor="nw", text=f"{peak:g} V / {max(point.current_a for point in points):g} A", fill=self.colors["text"], font=("Segoe UI Semibold", 8))
        canvas.create_text(right, bottom + 12, anchor="ne", text=f"period {period:g} ms", fill=self.colors["muted"], font=("Segoe UI", 8))

    def _open_waveform_editor(self, card: AutoTestFlowCard) -> None:
        if self._waveform_editor_process is not None and self._waveform_editor_process.poll() is None:
            self.status_var.set("A waveform editor is already open.")
            return
        exchange_dir = Path.cwd() / ".runtime" / "waveform_editor"
        exchange_dir.mkdir(parents=True, exist_ok=True)
        exchange_path = exchange_dir / f"{uuid.uuid4().hex}.json"
        exchange_path.write_text(json.dumps({"accepted": False, "params": card.params}, indent=2) + "\n", encoding="utf-8")
        try:
            process = subprocess.Popen([sys.executable, "-m", "semi_auto_probe.qt_waveform_editor", str(exchange_path)], cwd=Path.cwd())
            self._waveform_editor_process = process
        except OSError as exc:
            messagebox.showerror("Waveform editor", f"Could not start the Qt waveform editor:\n{exc}", parent=self.window)
            exchange_path.unlink(missing_ok=True)
            return
        self.status_var.set("Qt waveform editor opened in a separate window.")
        self._poll_waveform_editor(process, exchange_path, card)

    def _poll_waveform_editor(self, process: subprocess.Popen[bytes], exchange_path: Path, card: AutoTestFlowCard) -> None:
        if process.poll() is None:
            self.window.after(120, lambda: self._poll_waveform_editor(process, exchange_path, card))
            return
        if self._waveform_editor_process is process:
            self._waveform_editor_process = None
        try:
            payload = json.loads(exchange_path.read_text(encoding="utf-8"))
            if payload.get("accepted") and isinstance(payload.get("params"), dict):
                card.params.update({str(key): str(value) for key, value in payload["params"].items()})
                self._waveform_applied(card)
                self.status_var.set("Waveform updated from the Qt editor.")
            elif process.returncode:
                messagebox.showerror("Waveform editor", f"Qt waveform editor exited with code {process.returncode}.", parent=self.window)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Waveform editor", f"Could not read waveform result:\n{exc}", parent=self.window)
        finally:
            exchange_path.unlink(missing_ok=True)

    def _waveform_applied(self, card: AutoTestFlowCard) -> None:
        for key in ("peak_voltage_v", "peak_current_a", "period_ms"):
            if key in self.param_vars:
                self.param_vars[key].set(card.params[key])
        self._draw_waveform(card)
        self._changed()

    def _wave_press(self, _event: tk.Event) -> str:
        current = self.wave_canvas.find_withtag("current")
        tags = self.wave_canvas.gettags(current[0]) if current else ()
        marker = next((tag for tag in tags if tag.startswith("wave:")), None)
        self.wave_drag = marker.split(":", 1)[1] if marker else None
        return "break"

    def _wave_move(self, event: tk.Event) -> str:
        card = self._card(getattr(self, "_inspected_id", None))
        if card is None or self.wave_drag is None:
            return "break"
        width = max(self.wave_canvas.winfo_width(), 280)
        ratio = max(0.0, min(1.0, (event.x - 24) / max(1, width - 40)))
        period = max(1.0, self._float(card, "period_ms", 500))
        value = ratio * period
        if self.wave_drag == "delay":
            card.params["delay_ms"] = f"{min(value, period - self._float(card, 'width_ms', 100)):.6g}"
        elif self.wave_drag == "width":
            card.params["width_ms"] = f"{max(1, value - self._float(card, 'delay_ms', 100)):.6g}"
        else:
            card.params["period_ms"] = f"{max(value, self._float(card, 'delay_ms', 100) + self._float(card, 'width_ms', 100)):.6g}"
        for key in ("delay_ms", "width_ms", "period_ms"):
            if key in self.param_vars:
                self.param_vars[key].set(card.params[key])
        self._draw_waveform(card)
        return "break"

    @staticmethod
    def _float(card: AutoTestFlowCard, key: str, default: float) -> float:
        try:
            return float(card.params.get(key, default))
        except ValueError:
            return default

    def _important_summary(self, card: AutoTestFlowCard) -> str:
        p = card.params
        if card.type_id == "laser_iv_calibration":
            return (f"{float(p.get('calibration_current_min_a', '.001')) * 1000:g} to "
                    f"{float(p.get('calibration_current_max_a', '.025')) * 1000:g} mA | "
                    f"retry {p.get('readback_timeout_s', '2.0')} s")
        if card.type_id == "photo_power_steps":
            return (f"{p.get('power_min_mw', 'auto')} to {p.get('power_max_mw', 'auto')} mW | "
                    f"{p.get('power_levels', '25')} equal levels | fixed bias I-t")
        if card.type_id == "photo_width_steps":
            return (f"{p.get('width_power_mw', 'auto')} mW | "
                    f"{p.get('width_min_ms', '100')}...{p.get('width_max_ms', '500')} ms")
        if card.type_id == "mapping_heatmap":
            return "Connected features to spatial heatmaps"
        if card.type_id == "laser_output":
            return f"{p.get('peak_voltage_v', DEFAULT_LASER_PARAMS['peak_voltage_v'])} V  limit {p.get('peak_current_a', DEFAULT_LASER_PARAMS['peak_current_a'])} A  {p.get('output_state', 'on').upper()}"
        if card.type_id == "laser_pulse":
            if p.get("program") == "calibrated_response_suite":
                return (f"Calibrated 1→25→1 mA | 15 mA widths 100…500 ms | "
                        f"dark gap {p.get('pulse_gap_ms', '1000')} ms")
            points = parse_waveform_points(p)
            return f"{max(point.voltage_v for point in points):g} V / {max(point.current_a for point in points):g} A | {points[-1].time_ms:g} ms x {p.get('count', '1')}"
        if is_condition_flow_type(card.type_id):
            if card.type_id == "condition_metric":
                return f"{p.get('metric', 'metric')} {p.get('operator', '>')} {p.get('threshold', '0')}"
            return p.get("state", p.get("expected", "true"))
        definition = self.definitions[card.type_id]
        return " | ".join(f"{param.label}: {p.get(param.key, param.default)}" for param in definition.parameters[:2]) or definition.description

    def _node_height(self, card: AutoTestFlowCard) -> int:
        return self._parameter_top(card) + len(self._inline_params(card)) * 38 + (54 if card.type_id == "laser_pulse" else 10)

    @staticmethod
    def _node_width(card: AutoTestFlowCard) -> int:
        return CAPSULE_WIDTH if card.type_id in BOUNDARY_TYPES else 272 if card.type_id == "plot_panel" else NODE_WIDTH

    def _card(self, card_id: str | None) -> AutoTestFlowCard | None:
        return next((card for card in self.cards if card.card_id == card_id), None)

    def _delete_inspected(self):
        self.selected_edge = None
        self.selected_id = getattr(self, "_inspected_id", None)
        return self._delete_selected()

    def _delete_selected(self, _event: tk.Event | None = None) -> str:
        if _event is not None and _event.widget.winfo_class() in {"Entry", "TEntry", "TCombobox", "Spinbox", "TSpinbox", "Text"}:
            return "break"
        if self.selected_edge is not None:
            removed = self._disconnect_edge(self.selected_edge)
            self.selected_edge = None
            if removed:
                self._changed()
                self.status_var.set("Connection deleted. Undo restores it.")
            return "break"
        if self.selected_id is None:
            return "break"
        deleted = self.selected_id
        selected = self._card(deleted)
        if selected is not None and selected.type_id in BOUNDARY_TYPES:
            self.status_var.set("START and END are fixed workflow boundaries.")
            return "break"
        self.cards[:] = [card for card in self.cards if card.card_id != deleted]
        self._remove_node_references(deleted)
        self.selected_id = None
        self._hide_inspector()
        self.status_var.set(f"Deleted {deleted}.")
        self._changed()
        return "break"

    def _clear(self) -> None:
        self._reset_edit_feedback()
        self.cards.clear()
        self._ensure_boundary_nodes()
        self.selected_id = None
        self._hide_inspector()
        self.status_var.set("Flow cleared.")
        self._changed()

    def _validate(self) -> None:
        self._finish_inline_edit()
        self._validation_issues = self._collect_issues()
        self._show_issues()
        self.redraw()
        self.status_var.set("Flow valid." if not self._validation_issues else f"{len(self._validation_issues)} issues — select one below the canvas to locate it.")

    def _fit_nodes(self) -> None:
        if not self.cards:
            return
        min_x = min(card.x for card in self.cards)
        min_y = min(card.y for card in self.cards)
        max_x = max(card.x + self._node_width(card) for card in self.cards)
        max_y = max(card.y + self._node_height(card) for card in self.cards)
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        span_x, span_y = max(1, max_x - min_x + 120), max(1, max_y - min_y + 120)
        self.zoom = max(0.45, min(1.5, min(width / span_x, height / span_y)))
        self.redraw()
        center_x = (min_x + max_x) / 2 * self.zoom
        center_y = (min_y + max_y) / 2 * self.zoom
        extent = WORLD_EXTENT * self.zoom
        self.canvas.xview_moveto((center_x - width / 2 + extent) / (2 * extent))
        self.canvas.yview_moveto((center_y - height / 2 + extent) / (2 * extent))
        self.redraw()

    def _changed(self, redraw: bool = True, group=None) -> None:
        if getattr(self, "_history_suspended", False):
            return
        if getattr(self, "_validation_issues", None):
            self._validation_issues = self._collect_issues()
            self._refresh_issue_list()
        if redraw:
            self.redraw()
        self._record_history(group)
        self.on_change()

    def close(self) -> None:
        self._finish_inline_edit()
        self.on_change()
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        self.window.destroy()
