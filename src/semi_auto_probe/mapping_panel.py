from __future__ import annotations

import csv
import io
import json
import re
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from .mapping_canvas import MappingCanvas
from .mapping_region_editor import MappingRegionEditor
from .mapping_status import MappingStatusPanel
from .mapping import MappingSettings, generate_mapping_plan, load_mapping, load_point_samples, read_mapping_artifact
from .mapping_plot import (
    draw_laser_calibration_figure,
    draw_mapping_figure,
    draw_temt6000_figure,
    draw_workflow_heatmap_figure,
)
from .temt6000 import TEMT6000Response, is_temt6000
from .flow_graph import step_node_id


LASER_CALIBRATION_LABEL = "Laser calibration I-V / P-V"


class MappingPanel(ttk.Frame):
    # Match the scrollable control panel used by LayoutMap/GDS mapping.
    REGION_SIDEBAR_WIDTH = 410

    def __init__(self, parent, *, get_scales, record_corner, start_run, stop_run, get_workflow=lambda: (), get_mapper=lambda: None, get_system_status=lambda: "Wire-bond mode · Fixed Z", open_measurement_flow=lambda: None, measurement_var=None, move_to_xy=lambda x, y: None, colors=None):
        super().__init__(parent, padding=8)
        self.colors = colors or {
            "bg": "#0b0f14",
            "surface": "#111821",
            "border": "#263545",
        }
        self.get_scales = get_scales
        self.record_corner_callback = record_corner
        self.start_run = start_run
        self.stop_run = stop_run
        self.get_workflow = get_workflow
        self.get_mapper = get_mapper
        self.get_system_status = get_system_status
        self.open_measurement_flow = open_measurement_flow
        self.move_to_xy = move_to_xy
        self.running = False
        self.plan = None
        self.directory = None
        self.results = {}
        self._redraw_id = None
        self._preview = True
        self._completed = 0
        self.inputs = []
        self.region_dirty = True
        self._draft_redraw_id = None
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.summary = tk.StringVar(value="Preview parameters or draw a region.")
        self.status = tk.StringVar(value="Ready — fixed-Z mapping")
        self.detail = tk.StringVar(value="Click a scan point to inspect its status.")
        self.eta_var = tk.StringVar(value="Expected time: preview a plan")
        self.system_var = tk.StringVar(value="Wire-bond mode · Fixed Z")
        self.viewport_var = tk.StringVar(value="XY viewport")
        self.measurement_var = measurement_var or tk.StringVar(value="Measurement flow: not configured")
        self.left_status = MappingStatusPanel(self, self, colors)
        self.left_status.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        center = ttk.Frame(self)
        center.grid(row=0,column=1,sticky="nsew")
        center.columnconfigure(0,weight=1); center.rowconfigure(1,weight=1)
        toolbar=ttk.Frame(center); toolbar.grid(row=0,column=0,sticky="ew",pady=(0,6))
        self.scan_canvas=MappingCanvas(center,self)
        self.scan_canvas.grid(row=1,column=0,sticky="nsew")
        ttk.Button(toolbar,text="Fit view",command=self.scan_canvas.fit).pack(side="left")
        ttk.Button(toolbar,text="Data plots…",command=self.open_data_plot).pack(side="left",padx=6)
        self.canvas_tool_var = tk.StringVar(value="cursor")
        self.hand_tool_button = tk.Button(toolbar,text="✋ Hand",command=lambda:self.set_canvas_tool('hand'),relief="flat",bd=0,padx=10,pady=4)
        self.hand_tool_button.pack(side="left",padx=(8,2))
        self.cursor_tool_button = tk.Button(toolbar,text="⌖ Cursor",command=lambda:self.set_canvas_tool('cursor'),relief="flat",bd=0,padx=10,pady=4)
        self.cursor_tool_button.pack(side="left",padx=2)
        self.canvas_help_var = tk.StringVar()
        ttk.Label(toolbar,textvariable=self.canvas_help_var).pack(side="left",padx=(8,0))
        self.set_canvas_tool('cursor')
        self.footer_title_var = tk.StringVar(value="No scan preview")
        self.footer_meta_var = tk.StringVar(value="Choose a region and preview the path")
        self.draw_hint_var = tk.StringVar()
        footer = tk.Frame(center, bg="#0b1624", highlightthickness=1, highlightbackground="#2b405b")
        footer.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        footer.columnconfigure(1, weight=1)
        self.footer_badge = tk.Label(
            footer, text="DRAFT", bg="#5b4512", fg="#fde68a",
            font=("Segoe UI", 8, "bold"), padx=8, pady=3,
        )
        self.footer_badge.grid(row=0, column=0, sticky="w", padx=7, pady=(6, 2))
        tk.Label(
            footer, textvariable=self.footer_title_var, bg="#0b1624", fg="#e5f0ff",
            font=("Segoe UI", 10, "bold"), anchor="w",
        ).grid(row=0, column=1, sticky="ew", padx=(2, 8), pady=(6, 2))
        tk.Label(
            footer, textvariable=self.footer_meta_var, bg="#0b1624", fg="#8fa9c7",
            font=("Cascadia Mono", 8), anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 6))
        self.draw_hint_label = tk.Label(
            footer, textvariable=self.draw_hint_var, bg="#3f3212", fg="#fde68a",
            font=("Segoe UI", 8, "bold"), anchor="w", padx=8, pady=5,
        )
        self.draw_hint_label.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.draw_hint_label.grid_remove()
        self.columnconfigure(2, minsize=self.REGION_SIDEBAR_WIDTH + 18)
        right=ttk.Frame(self); right.grid(row=0,column=2,sticky="nsew",padx=(10,0))
        right.columnconfigure(0,weight=1)
        right.rowconfigure(0,weight=1)
        self.region_sidebar = right
        scroll_canvas=tk.Canvas(
            right,
            width=self.REGION_SIDEBAR_WIDTH,
            bg=self.colors["bg"],
            bd=0,
            highlightthickness=0,
        )
        self.region_scroll_canvas = scroll_canvas
        scroll_canvas.grid(row=0,column=0,sticky="nsew")
        scrollbar=ttk.Scrollbar(right,orient="vertical",command=scroll_canvas.yview)
        scrollbar.grid(row=0,column=1,sticky="ns")
        scroll_canvas.configure(yscrollcommand=scrollbar.set)
        form=ttk.Frame(scroll_canvas,padding=8)
        window=scroll_canvas.create_window(0,0,window=form,anchor="nw")
        form.bind("<Configure>",lambda e:scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all")))
        scroll_canvas.bind("<Configure>",lambda e:scroll_canvas.itemconfigure(window,width=e.width))
        form.columnconfigure((0,1),weight=1)
        ttk.Label(form,text="Scan region",font=("Segoe UI",12,"bold")).grid(row=0,column=0,columnspan=2,sticky="w")
        self.shape_var=tk.StringVar(value="rectangle")
        self.polygon_var=tk.StringVar()
        defaults={"x_a_um":"0","y_a_um":"0","x_b_um":"100","y_b_um":"100","step_x_um":"10","step_y_um":"10","pattern":"serpentine","settle_s":"0.2"}
        self.variables={key:tk.StringVar(value=value) for key,value in defaults.items()}
        self.region_editor=MappingRegionEditor(form,self)
        self.region_editor.grid(row=1,column=0,columnspan=2,sticky="ew",pady=8)
        ttk.Separator(form).grid(row=4,column=0,columnspan=2,sticky="ew",pady=8)
        for i,(key,label) in enumerate((("step_x_um","ΔX (µm)"),("step_y_um","ΔY (µm)"),("pattern","Scan path"),("settle_s","Move settle (s)"))):
            cell=ttk.Frame(form)
            cell.grid(row=5+i//2,column=i%2,sticky="ew",padx=3,pady=3)
            ttk.Label(cell,text=label).pack(anchor="w")
            entry=ttk.Combobox(cell,textvariable=self.variables[key],values=("serpentine","raster"),state="readonly",width=15) if key=="pattern" else ttk.Entry(cell,textvariable=self.variables[key],width=15)
            entry.pack(fill="x")
            self.inputs.append((entry,"readonly" if key=="pattern" else "normal"))
        self.snap_var=tk.BooleanVar(value=True)
        snap=ttk.Checkbutton(form,text="Snap to XY grid / first vertex (12 px)",variable=self.snap_var)
        snap.grid(row=7,column=0,columnspan=2,sticky="w",pady=6); self.inputs.append((snap,"normal"))
        for col,(label,command) in enumerate((("Draw region",self.scan_canvas.begin),("Finish polygon",self.scan_canvas.commit))):
            button=ttk.Button(form,text=label,command=command); button.grid(row=8,column=col,sticky="ew",padx=3); self.inputs.append((button,"normal"))
            if label=="Finish polygon":
                self.finish_region_button=button
                button.grid_remove()
        measurement = ttk.LabelFrame(form, text="Measurement", padding=10)
        measurement.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        measurement.columnconfigure(0, weight=1)
        self.workflow_button = tk.Button(
            measurement,
            text="Config Measure Flow",
            command=self.open_measurement_flow,
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
        )
        self.workflow_button.grid(row=0, column=0, sticky="ew")
        self.inputs.append((self.workflow_button, "normal"))
        ttk.Label(
            measurement,
            textvariable=self.measurement_var,
            style="Value.TLabel",
            padding=8,
            wraplength=self.REGION_SIDEBAR_WIDTH - 52,
        ).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        controls=ttk.Frame(right,padding=8); controls.grid(row=1,column=0,columnspan=2,sticky="ew")
        self.progress=ttk.Progressbar(controls,maximum=1); self.progress.pack(fill="x",pady=6)
        ttk.Label(controls,textvariable=self.eta_var,wraplength=self.REGION_SIDEBAR_WIDTH - 24).pack(fill="x",pady=5)
        buttons=ttk.Frame(controls); buttons.pack(fill="x")
        for i,(label,command) in enumerate((("Preview",self.preview),("Load…",self.load),("Start mapping",self.start))):
            button=tk.Button(buttons,text=label,command=command,bg="#15803d",fg="white",activebackground="#16a34a",activeforeground="white",disabledforeground="#91a699",relief="flat",pady=6) if label=="Start mapping" else ttk.Button(buttons,text=label,command=command)
            if label=="Start mapping": self.start_button=button
            button.grid(row=i//2,column=i%2,sticky="ew",padx=2,pady=3); self.inputs.append((button,"normal"))
        buttons.columnconfigure((0,1),weight=1)
        self.stop_button=tk.Button(buttons,text="Stop",command=self.stop_run,state="disabled",bg="#49383d",fg="white",disabledforeground="#99777e",activebackground="#ef4444",activeforeground="white",relief="flat",pady=6)
        self.stop_button.grid(row=1,column=1,sticky="ew",padx=2,pady=3)
        self.data_window=tk.Toplevel(self); self.data_window.title("Mapping data plots"); self.data_window.geometry("1000x700"); self.data_window.withdraw()
        self.data_window.protocol("WM_DELETE_WINDOW",self.data_window.withdraw)
        self.figure=Figure(figsize=(10,6),dpi=100)
        self.plot=FigureCanvasTkAgg(self.figure,master=self.data_window)
        self.plot.get_tk_widget().pack(fill="both",expand=True)
        self.plot.mpl_connect("button_press_event",self._inspect_point)
        self.metric_var=tk.StringVar(value="Workflow completion")
        self.metric_picker=ttk.Combobox(self.data_window,textvariable=self.metric_var,state="readonly",values=("Workflow completion",))
        self.metric_picker.pack(fill="x"); self.metric_picker.bind("<<ComboboxSelected>>",self._select_metric)
        self._system_refresh_id = self.after(750, self._poll_system_status)
        for variable in self.variables.values():
            variable.trace_add("write", self.mark_region_dirty)

    def mark_region_dirty(self, *_):
        self.region_dirty = True
        if not self.running:
            self.summary.set("Region draft — Preview to update scan points and travel direction.")
            self.eta_var.set("Expected time: preview the updated region")
            self.set_footer(
                "DRAFT", "Region changed",
                "Preview to update scan points, trajectory direction and expected time.",
            )
        if self._draft_redraw_id is None:
            self._draft_redraw_id = self.after(100, self._draw_draft)

    def set_canvas_tool(self, mode, *, update_canvas=True):
        self.canvas_tool_var.set(mode)
        selected=("#1d4ed8","#eff6ff")
        idle=(self.colors.get("surface_3","#1b2735"),self.colors.get("text","#e5edf5"))
        for name,button in (("hand",self.hand_tool_button),("cursor",self.cursor_tool_button)):
            background,foreground=selected if name==mode else idle
            button.configure(bg=background,fg=foreground,activebackground=background,activeforeground=foreground)
        self.canvas_help_var.set(
            "Drag to pan · wheel to zoom" if mode=="hand"
            else "Snapped cursor · Shift+double-click to move XY"
        )
        if update_canvas and self.scan_canvas.tool_mode != mode:
            self.scan_canvas.set_tool_mode(mode)

    def move_to_canvas_position(self, x, y):
        self.status.set(f"Move requested · X {x:.3f}, Y {y:.3f} µm")
        self.set_footer("PREVIEW", "Moving to selected XY", f"Target X {x:.3f} µm  ·  Y {y:.3f} µm  ·  fixed Z")
        self.move_to_xy(x,y)

    def _draw_draft(self):
        self._draft_redraw_id = None
        if not self.running:
            self.scan_canvas.draw()

    def set_footer(self, state, title, meta):
        palette = {
            "DRAFT": ("#5b4512", "#fde68a"),
            "PREVIEW": ("#164e63", "#a5f3fc"),
            "DRAW": ("#6b4f12", "#fde68a"),
            "RUNNING": ("#14532d", "#bbf7d0"),
            "DONE": ("#14532d", "#bbf7d0"),
            "FAILED": ("#7f1d1d", "#fecaca"),
        }
        background, foreground = palette.get(state, ("#263449", "#dbeafe"))
        self.footer_badge.configure(text=state, bg=background, fg=foreground)
        self.footer_title_var.set(title)
        self.footer_meta_var.set(meta)

    def show_draw_hint(self, text):
        self.draw_hint_var.set(text)
        self.draw_hint_label.grid()
        self.set_footer("DRAW", "Drawing region on XY canvas", "Coordinates snap to the configured XY grid when enabled.")

    def hide_draw_hint(self):
        self.draw_hint_label.grid_remove()

    def _poll_system_status(self):
        if self.winfo_ismapped():
            self.refresh_system_status()
        self._system_refresh_id = self.after(750, self._poll_system_status)

    def destroy(self):
        for callback in (self._redraw_id, self._draft_redraw_id, getattr(self, "_system_refresh_id", None)):
            if callback is not None:
                self.after_cancel(callback)
        if getattr(self.plot, "_idle_draw_id", None) is not None:
            self.plot.get_tk_widget().after_cancel(self.plot._idle_draw_id)
            self.plot._idle_draw_id = None
        super().destroy()

    def refresh_system_status(self):
        self.left_status.refresh(self.get_system_status())

    def open_data_plot(self):
        self.data_window.deiconify(); self.data_window.lift(); self._draw()

    def set_current(self, point):
        self.scan_canvas.current=point
        self.scan_canvas.draw_current()

    def settings(self):
        values = {key: var.get().strip() for key, var in self.variables.items()}
        numeric = {key: float(values[key]) for key in ("step_x_um", "step_y_um", "settle_s")}
        bounds, points = self.region_editor.geometry()
        mapper = self.get_mapper()
        return MappingSettings(
            **numeric, **dict(zip(("x_a_um","y_a_um","x_b_um","y_b_um"),bounds)),
            pattern=values["pattern"], measurement_flow=self.get_workflow(),
            region_shape=self.shape_var.get(),
            region_method=self.region_editor.method_var.get(),
            region_control_points=tuple(points),
            polygon_vertices=tuple(points) if self.shape_var.get()=="polygon" else (),
            gds_mapper=mapper.to_dict() if mapper is not None else None,
        ).normalized()

    def set_corner(self, corner, x, y):
        index={"A":0,"B":1}.get(corner)
        if index is None:
            index=int(corner)
        points=self.region_editor._raw_points()
        while len(points)<=index:
            points.append(("0","0"))
        points[index]=(f"{x:.12g}",f"{y:.12g}")
        self.region_editor.set_points(points)
        self.status.set(f"Point {index+1} recorded: X={x:g} µm, Y={y:g} µm")

    def set_plan(self, plan, directory=None, *, fit=True):
        self.plan, self.directory = plan, directory
        self.region_dirty = False
        self.results = {}
        self._completed = 0
        self.values = np.full((len(plan.ys), len(plan.xs)), np.nan)
        self.states = np.zeros(self.values.shape, dtype=np.uint8)
        self.progress.configure(maximum=len(plan.points), value=0)
        s = plan.settings
        self._laser_calibration_records = []
        self._laser_calibration_curve = []
        self._temt_nodes = {
            f"TEMT6000 dual mapping: {step_node_id(step, index)}": (step_node_id(step, index), TEMT6000Response.from_params(step.params))
            for index, step in enumerate(s.measurement_flow or ())
            if step.type_id == "constant_current" and is_temt6000(step.params)
        }
        self._heatmap_nodes = {}
        virtual_mask_ids = {
            step_node_id(step, index) for index, step in enumerate(s.measurement_flow or ())
            if step.type_id == "virtual_mask"
        }
        for index, step in enumerate(s.measurement_flow or ()):
            if step.type_id != "mapping_heatmap":
                continue
            node_id = step_node_id(step, index)
            primary_label = str(step.params.get("primary_label", "Primary mapping feature"))
            secondary_label = str(step.params.get("secondary_label", "Secondary mapping feature"))
            has_secondary = bool(step.params.get("_input_secondary_source"))
            value_slot = "primary"
            # Migrate the first virtual-mask template in place for display: it
            # published target power as primary and measured current as
            # secondary. Only the measured-current heatmap is useful here.
            if (
                virtual_mask_ids and
                str(step.params.get("_input_primary_source", "")) in virtual_mask_ids and
                has_secondary
            ):
                primary_label = "Measured Current (A)"
                has_secondary = False
                value_slot = "secondary"
            display_label = (
                f"{primary_label} + {secondary_label}" if has_secondary else primary_label
            )
            self._heatmap_nodes[display_label] = (
                node_id, primary_label, secondary_label, has_secondary, value_slot,
            )
        mask_node = next((step for step in s.measurement_flow or () if step.type_id == "virtual_mask"), None)
        mx, my = max(s.x_a_um, s.x_b_um) - plan.xs[-1], max(s.y_a_um, s.y_b_um) - plan.ys[-1]
        coverage = (
            f"Virtual mask {Path(str(mask_node.params.get('image_path', ''))).name} · "
            f"{mask_node.params.get('coordinate_frame', 'auto').upper()} binding · "
            f"power {mask_node.params.get('black_power_mw', mask_node.params.get('power_min_mw', '0'))}…"
            f"{mask_node.params.get('white_power_mw', mask_node.params.get('power_max_mw', '5'))} mW\n"
            f"Main-page scan X={plan.xs[0]:g}…{plan.xs[-1]:g}, Y={plan.ys[0]:g}…{plan.ys[-1]:g} µm · "
            f"ΔX={s.step_x_um:g}, ΔY={s.step_y_um:g} µm · {s.pattern}"
            if mask_node is not None else
            f"Coverage X={plan.xs[0]:g}…{plan.xs[-1]:g}, Y={plan.ys[0]:g}…{plan.ys[-1]:g} µm; remainder X={mx:g}, Y={my:g} µm"
        )
        displayed_pattern = s.pattern
        self.summary.set(
            f"{len(plan.ys)} rows × {len(plan.xs)} columns = {len(plan.points)} points · {displayed_pattern}\n"
            f"{coverage}\n"
            f"Move settle={s.settle_s:g}s" + (" · fast reached-position scan" if s.settle_s == 0 else "") + " · " + (
                f"Shared workflow: {len(s.measurement_flow)} nodes; full results saved for each point."
                if s.measurement_flow is not None else s.measurement.summary()
            )
        )
        shape_label = {
            "rectangle": "Rectangle", "circle": "Circle", "polygon": "Polygon",
        }.get(s.region_shape, s.region_shape.title())
        self.set_footer(
            "PREVIEW",
            f"{len(plan.points):,} points · {shape_label} · {displayed_pattern}",
            f"X {plan.xs[0]:g}…{plan.xs[-1]:g} µm  ·  Y {plan.ys[0]:g}…{plan.ys[-1]:g} µm  ·  Δ {s.step_x_um:g} × {s.step_y_um:g} µm",
        )
        self.metric_var.set("Workflow completion" if s.measurement_flow is not None else "Mean current (A)")
        self.metric_picker.configure(values=(self.metric_var.get(),))
        self._metric_selected = False
        self._update_metric()
        self.scan_canvas.current = None
        if fit:
            self.scan_canvas.fit()
        else:
            self.scan_canvas.draw()
        self.refresh_system_status()
        self.eta_var.set(f"{len(plan.points)} points · settle-only lower bound: {len(plan.points)*s.settle_s:.1f}s\nMotion + workflow time estimated after first point")

    def _select_metric(self, _event=None):
        self._metric_selected = True
        self._update_metric()

    def _update_metric(self, *, schedule=True):
        if self.plan is None:
            return
        if self.plan.settings.measurement_flow is not None:
            keys = sorted({key for result in self.results.values() for key in result.workflow_data.get("metrics", {})})
            calibration_choices = (LASER_CALIBRATION_LABEL,) if self._laser_calibration_curve else ()
            choices = (*self._temt_nodes, *self._heatmap_nodes, *calibration_choices, "Workflow completion", *keys)
            self.metric_picker.configure(values=choices)
            if (self._temt_nodes or self._heatmap_nodes or keys) and not self._metric_selected:
                self.metric_var.set(
                    next(iter(self._temt_nodes)) if self._temt_nodes else
                    next(iter(self._heatmap_nodes)) if self._heatmap_nodes else next(
                    (key for suffix in ("response_amplitude_a", "mean_current_a") for key in keys if key.endswith(suffix)),
                    keys[0],
                ))
                self._metric_selected = True
            if self.metric_var.get() not in choices:
                self.metric_var.set("Workflow completion")
            self.values.fill(np.nan)
            self.secondary_values = np.full(self.values.shape, np.nan)
            self.irradiance_values = np.full(self.values.shape, np.nan)
            for key, result in self.results.items():
                metrics = result.workflow_data.get("metrics", {})
                if self.metric_var.get() in self._temt_nodes:
                    node_id, optical = self._temt_nodes[self.metric_var.get()]
                    safe_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", node_id.strip() or "point")
                    source = f"nodes/{safe_id}/current/point: statistics."
                    value = metrics.get(source + "mean_current_a")
                    if result.status == "done":
                        # Saved derived values take precedence over recomputation.
                        power = metrics.get(source + "estimated_irradiance_w_m2", optical.irradiance(value))
                        if power is not None:
                            self.irradiance_values[key] = power
                elif self.metric_var.get() in self._heatmap_nodes:
                    node_id, _primary_label, _secondary_label, has_secondary, value_slot = self._heatmap_nodes[self.metric_var.get()]
                    safe_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", node_id.strip() or "point")
                    source = f"nodes/{safe_id}/heatmap/point: statistics."
                    value = metrics.get(source + f"{value_slot}_value")
                    secondary = metrics.get(source + "secondary_value")
                    if result.status == "done" and has_secondary and isinstance(secondary, (float, int)):
                        self.secondary_values[key] = secondary
                else:
                    value = metrics.get(self.metric_var.get())
                if result.status == "done" and isinstance(value, (float, int)):
                    self.values[key] = value
        if schedule:
            self._schedule_draw()

    def set_laser_calibration(self, records, calibration, *, select=True):
        self._laser_calibration_records = list(records)
        self._laser_calibration_curve = [tuple(point) for point in calibration]
        if select:
            self.metric_var.set(LASER_CALIBRATION_LABEL)
            self._metric_selected = True
        self._update_metric()

    def preview(self, *, fit=True):
        if self.running:
            return
        if self.scan_canvas.editing:
            self.scan_canvas.commit()
            return
        try:
            plan = generate_mapping_plan(self.settings(), self.get_scales())
            self._preview = True
            self.set_plan(plan, fit=fit)
            self.status.set("Preview ready. Axes are stage coordinates; Z will be captured at start.")
        except Exception as exc:
            self.status.set(str(exc))

    def start(self):
        if self.running:
            return
        if self.scan_canvas.editing:
            self.status.set("Finish or cancel the region selection before starting.")
            return
        try:
            settings = self.settings()
            plan = generate_mapping_plan(settings, self.get_scales())
            self._preview = True
            self.set_plan(plan)
            self.start_run(settings)
        except Exception as exc:
            self.status.set(str(exc))

    def set_running(self, running):
        self.running = running
        self.region_editor.set_locked(running)
        self.refresh_system_status()
        if running:
            self.scan_canvas.cancel()
            if self.plan is not None:
                self.set_footer(
                    "RUNNING", f"Mapping 0 / {len(self.plan.points):,}",
                    "Current stage position and completed points update live.",
                )
        else:
            self.scan_canvas.current = None
            self.scan_canvas.draw()
        for widget, normal in self.inputs:
            widget.configure(state="disabled" if running else normal)
        self.stop_button.configure(state="normal" if running else "disabled", bg="#dc2626" if running else "#49383d")

    def on_result(self, result, elapsed):
        key = result.point.row, result.point.col
        # Raw data is flushed by the worker, and inspected from CSV on demand.
        self.results[key] = replace(result, samples=())
        self.states[key] = {"done": 1, "incomplete": 2, "failed": 3}[result.status]
        if result.status == "done":
            self.values[key] = result.mean_current_a
            self._completed += 1
        remaining = elapsed / self._completed * (len(self.plan.points) - self._completed) if self._completed else None
        eta = f"{remaining:.0f}s remaining" if remaining is not None else "ETA waiting for a completed point"
        self.progress.configure(value=self._completed)
        self.eta_var.set(f"{self._completed}/{len(self.plan.points)} · elapsed {elapsed:.0f}s\n{eta}")
        self.scan_canvas.update_point(result.point)
        self.status.set(f"{self._completed}/{len(self.plan.points)} complete · {eta} · {result.status}")
        self.set_footer(
            "RUNNING", f"Mapping {self._completed:,} / {len(self.plan.points):,}",
            f"Current: {result.point.stage_x_um:g}, {result.point.stage_y_um:g} µm  ·  elapsed {elapsed:.0f}s  ·  {eta}",
        )
        self._preview = False
        if self.metric_var.get() == LASER_CALIBRATION_LABEL and self._heatmap_nodes:
            self.metric_var.set(next(iter(self._heatmap_nodes)))
        self._schedule_draw()

    def _schedule_draw(self):
        if not self.data_window.winfo_viewable():
            return
        if self._redraw_id is None:
            self._redraw_id = self.after(200, self._draw)

    def _draw(self):
        self._redraw_id = None
        if self.plan is not None:
            self._update_metric(schedule=False)
            optical = self._temt_nodes.get(self.metric_var.get())
            workflow_heatmap = self._heatmap_nodes.get(self.metric_var.get())
            if self.metric_var.get() == LASER_CALIBRATION_LABEL and self._laser_calibration_curve:
                calibration_axes = draw_laser_calibration_figure(
                    self.figure, self._laser_calibration_records, self._laser_calibration_curve,
                )
                self.axes = calibration_axes[0]
                self.mapping_axes = ()
            elif optical is not None:
                node_id, response = optical
                self.mapping_axes = draw_temt6000_figure(self.figure, self.plan, self.values, self.irradiance_values, self.states,
                    wavelength_nm=response.wavelength_nm, preview=self._preview, node_id=node_id)
                self.axes = self.mapping_axes[0]
            elif workflow_heatmap is not None and workflow_heatmap[3]:
                node_id, primary_label, secondary_label, _has_secondary, _value_slot = workflow_heatmap
                self.mapping_axes = draw_workflow_heatmap_figure(
                    self.figure, self.plan, self.values, self.secondary_values, self.states,
                    primary_label=primary_label, secondary_label=secondary_label,
                    preview=self._preview, node_id=node_id,
                )
                self.axes = self.mapping_axes[0]
            else:
                self.axes = draw_mapping_figure(
                    self.figure, self.plan, self.values, self.states, preview=self._preview,
                    metric=self.metric_var.get() if self.plan.settings.measurement_flow is not None else None,
                )
                self.mapping_axes = (self.axes,)
            self.plot.draw_idle()

    def finish(self, status, error, directory):
        self.directory = directory
        self._preview = False
        if self._redraw_id is not None:
            self.after_cancel(self._redraw_id)
            self._redraw_id = None
        self._draw()
        try:
            self.figure.savefig(directory / "mapping.png", dpi=160)
            metadata_path = directory / "mapping.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["display_metric"] = self.metric_var.get()
            temporary = metadata_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            temporary.replace(metadata_path)
        except Exception as exc:
            error = f"{error}; PNG export failed: {exc}".strip("; ")
            status = "failed"
            try:
                metadata_path = directory / "mapping.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata.update(status=status, error=error)
                temporary = directory / "mapping.json.tmp"
                temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                temporary.replace(metadata_path)
            except Exception as metadata_exc:
                error += f"; metadata update failed: {metadata_exc}"
        self.scan_canvas.current = None
        self.scan_canvas.draw()
        self.eta_var.set(f"{status} · {self._completed}/{len(self.plan.points)} complete")
        self.status.set(f"{status}: {self._completed}/{len(self.plan.points)} complete · {directory}" + (f"\n{error}" if error else ""))
        self.set_footer(
            "DONE" if status == "completed" else "FAILED" if status == "failed" else status.upper(),
            f"{self._completed:,} / {len(self.plan.points):,} points complete",
            str(directory),
        )
        return status, error

    def load(self):
        path = filedialog.askopenfilename(parent=self, title="Open mapping.json", filetypes=[("Mapping metadata", "mapping.json")])
        if not path:
            return
        try:
            plan, metadata, results = load_mapping(Path(path).parent)
            self._preview = False
            for key,var in self.variables.items():
                var.set(str(getattr(plan.settings,key)))
            self.region_editor.restore(plan.settings)
            self.polygon_var.set('; '.join(f'{x:g},{y:g}' for x,y in plan.settings.polygon_vertices))
            self.set_plan(plan, Path(path).parent)
            for key, result in results.items():
                self.results[key] = result
                self.states[key] = {"done": 1, "incomplete": 2, "failed": 3}.get(result.status, 2)
                if result.status == "done":
                    self.values[key] = result.mean_current_a
                    self._completed += 1
            self.progress.configure(value=self._completed)
            self.status.set(f"Loaded {metadata['status']}: {self._completed}/{len(plan.points)} complete · {Path(path).parent}")
            calibration_path = Path(path).parent / "laser_iv_calibration.json"
            if calibration_path.is_file():
                calibration_payload = json.loads(calibration_path.read_text(encoding="utf-8"))
                records = calibration_payload.get("points", [])
                saved_curve = calibration_payload.get("calibration_curve", [])
                curve = [
                    (float(item["current_a"]), float(item["voltage_v"]))
                    for item in saved_curve
                ] or [
                    (float(item["effective_current_a"]), float(item["measured_voltage_v"]))
                    for item in records if float(item.get("effective_current_a", 0)) > 0
                ]
                if curve:
                    self.set_laser_calibration(records, curve, select=False)
            self.metric_var.set(metadata.get("display_metric", self.metric_var.get()))
            self._metric_selected = "display_metric" in metadata
            self._update_metric()
            self.scan_canvas.draw()
            self.eta_var.set(f"Loaded · {self._completed}/{len(plan.points)} complete")
            self.set_footer(
                "PREVIEW", f"Loaded {self._completed:,} / {len(plan.points):,} points",
                f"{metadata['status']}  ·  {Path(path).parent}",
            )
        except Exception as exc:
            self.status.set(f"Load failed: {exc}")

    def _inspect_point(self, event):
        if self.plan is None or event.inaxes not in getattr(self, "mapping_axes", ()) or event.xdata is None or event.ydata is None:
            return
        mask_points = [point for point in self.plan.points if point.normalized_intensity is not None]
        if mask_points:
            point = min(mask_points, key=lambda item: (item.stage_x_um - event.xdata) ** 2 + (item.stage_y_um - event.ydata) ** 2)
            row, col = point.row, point.col
        else:
            col = round((event.xdata - self.plan.xs[0]) / self.plan.settings.step_x_um)
            row = round((event.ydata - self.plan.ys[0]) / self.plan.settings.step_y_um)
        if not (0 <= col < len(self.plan.xs) and 0 <= row < len(self.plan.ys)):
            return
        result = self.results.get((row, col))
        if mask_points:
            label = (
                f"Mask pixel ({point.mask_pixel_x}, {point.mask_pixel_y}) · "
                f"X={point.stage_x_um:g}, Y={point.stage_y_um:g} µm · "
                f"gray={point.normalized_intensity * 100:.1f}% · target={point.target_power_mw:g} mW"
            )
        else:
            label = f"Row {row + 1}, column {col + 1} · X={self.plan.xs[col]:g}, Y={self.plan.ys[row]:g} µm"
        if result is None:
            self.detail.set(label + " · pending")
            return
        self.detail.set(label + f" · {result.status}" + (
            f" · {self.metric_var.get()}={result.workflow_data.get('metrics', {}).get(self.metric_var.get(), '—')}"
            if self.plan.settings.measurement_flow is not None else f" · mean={result.mean_current_a} A · std={result.std_current_a} A"
        ))
        if self.metric_var.get() in self._temt_nodes:
            self.detail.set(label + f" · {result.status} · current={self.values[row, col]:+.6g} A"
                            f" · estimated irradiance={self.irradiance_values[row, col] / 10:.6g} mW/cm²")
        if self.metric_var.get() in self._heatmap_nodes:
            _node_id, primary_label, secondary_label, has_secondary, _value_slot = self._heatmap_nodes[self.metric_var.get()]
            value = self.values[row, col]
            shown = f"{value:+.6g}" if np.isfinite(value) else "unavailable"
            detail = label + f" | {result.status} | {primary_label}={shown}"
            if has_secondary:
                secondary = self.secondary_values[row, col]
                secondary_shown = f"{secondary:+.6g}" if np.isfinite(secondary) else "unavailable"
                detail += f" | {secondary_label}={secondary_shown}"
            self.detail.set(detail)
        window = tk.Toplevel(self)
        window.title("Mapping point I-t response")
        it_series = self._it_curve_series(result)
        if it_series:
            curve_figure = Figure(figsize=(9, max(3.4, 3.2 * len(it_series))), dpi=100)
            curve_axes = np.atleast_1d(curve_figure.subplots(len(it_series), 1)).ravel()
            program_paths = sorted(self.directory.glob("laser_response_program_*.json"))
            legacy_program = self.directory / "laser_response_program.json"
            if not program_paths and legacy_program.is_file():
                program_paths = [legacy_program]
            programs = {}
            for program_path in program_paths:
                try:
                    program = json.loads(program_path.read_text(encoding="utf-8"))
                    programs[str(program.get("phase", "laser"))] = program
                except (OSError, ValueError, TypeError):
                    continue
            colors = ("#f97316", "#eab308")
            for index, ((source, rows), curve_ax) in enumerate(zip(it_series, curve_axes)):
                try:
                    times = [float(item["elapsed_s"]) for item in rows]
                    currents = [float(item["current_a"]) * 1e6 for item in rows]
                except (ValueError, KeyError):
                    continue
                curve_ax.plot(times, currents, linewidth=1, color="#38bdf8")
                curve_ax.set(
                    xlabel="Time from synchronized laser start (s)",
                    ylabel="Detector current (uA)",
                    title=source,
                )
                curve_ax.grid(True, alpha=.25)
                source_lower = source.lower()
                phase = "width" if "width" in source_lower else "power" if "power" in source_lower else ""
                program = programs.get(phase)
                if program is None and len(programs) == 1:
                    program = next(iter(programs.values()))
                if program is None:
                    continue
                pulse_t, pulse_i = [0.0], [0.0]
                for item in program.get("events", []):
                    start_s = float(item["start_ms"]) / 1000
                    end_s = start_s + float(item["width_ms"]) / 1000
                    power_mw = float(item["target_power_mw"])
                    pulse_t.extend((start_s, start_s, end_s, end_s))
                    pulse_i.extend((0, power_mw, power_mw, 0))
                light_ax = curve_ax.twinx()
                light_ax.step(
                    pulse_t, pulse_i, where="post", color=colors[index % len(colors)],
                    alpha=.65, linewidth=.9,
                )
                light_ax.set_ylabel("Laser drive power (mW)", color=colors[index % len(colors)])
                light_ax.tick_params(axis="y", colors=colors[index % len(colors)])
            curve_figure.suptitle(label)
            curve_figure.tight_layout()
            curve_canvas = FigureCanvasTkAgg(curve_figure, master=window)
            curve_canvas.get_tk_widget().pack(fill="both", expand=True)
            curve_canvas.draw_idle()
            window._it_curve_canvas = curve_canvas
        text = tk.Text(window, width=100, height=25, wrap="word")
        text.pack(fill="both", expand=True)
        text.insert("end", self.detail.get() + f"\nReadback XYZ (pulses): {result.readback_pulses}\n{result.timestamp}\n{result.error}\n\n")
        if result.workflow_data:
            text.insert("end", json.dumps(result.workflow_data, indent=2, ensure_ascii=False) + "\n\n")
        try:
            samples = load_point_samples(self.directory, result.point) if self.directory else []
            for sample in samples:
                text.insert("end", f"#{sample['sample_index']}  {sample['current_a']} A  t={sample['elapsed_s']} s  raw={sample['raw']}\n")
        except Exception as exc:
            text.insert("end", f"Could not read samples: {exc}")
        text.configure(state="disabled")

    def _it_curve_series(self, result):
        """Load transient CSV rows from loose legacy files or compact storage."""
        if self.directory is None:
            return []
        series = []
        for relative in result.workflow_data.get("artifacts", ()):
            relative = str(relative)
            parts = Path(relative).parts
            if Path(relative).suffix.lower() != ".csv" or "photo_transient" not in parts:
                continue
            try:
                payload = read_mapping_artifact(self.directory, relative).decode("utf-8")
                node = parts[parts.index("nodes") + 1] if "nodes" in parts else Path(relative).stem
                series.append((node, list(csv.DictReader(io.StringIO(payload)))))
            except (OSError, UnicodeError, ValueError):
                continue
        if series:
            return series
        for path in self._it_curve_paths(result):
            try:
                with path.open(newline="", encoding="utf-8") as stream:
                    series.append((path.parent.parent.name, list(csv.DictReader(stream))))
            except OSError:
                continue
        return series

    def _it_curve_paths(self, result):
        if self.directory is None:
            return []
        paths = []
        for relative in result.workflow_data.get("artifacts", ()):
            path = self.directory / str(relative)
            if path.suffix.lower() == ".csv" and "photo_transient" in path.parts and path.is_file():
                paths.append(path)
        if not paths:
            point_dir = self.directory / "points" / result.point.name
            paths = list(point_dir.glob("nodes/*/photo_transient/*.csv"))
        return sorted(set(paths))
