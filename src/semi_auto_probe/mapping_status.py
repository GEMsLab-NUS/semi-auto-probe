"""Compact Probe-style status column for wire-bond mapping."""
from __future__ import annotations

import io
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk


class MappingStatusPanel(ttk.Frame):
    def __init__(self, parent, panel, colors=None):
        super().__init__(parent, width=290)
        self.panel = panel
        self.colors = colors or {
            "surface_2": "#182638", "border": "#34445a",
            "muted": "#99abc4", "accent": "#60a5fa",
        }
        self.grid_propagate(False)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=3, minsize=125)
        self.rowconfigure(4, weight=2, minsize=105)

        preview = self.section("Microscope Live", 0, stretch=True)
        preview.rowconfigure(0, weight=1)
        preview.columnconfigure(0, weight=1)
        self.microscope_label = ttk.Label(
            preview, text="No microscope frame", anchor="center", padding=4,
        )
        self.microscope_label.grid(row=0, column=0, sticky="nsew")

        readiness = self.section("Prerequisites", 1)
        self.chips = {}
        for column, (name, title) in enumerate((
            ("stage", "Stage"), ("focusmap", "Focus"), ("layoutmap", "Layout"),
        )):
            readiness.columnconfigure(column, weight=1, uniform="mapping_ready")
            chip = tk.Label(
                readiness, text=title, anchor="center", padx=4, pady=4,
                font=("Cascadia Mono", 8),
            )
            chip.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 2, 0))
            self.chips[name] = chip
        ttk.Label(
            readiness, text="Fixed Z · FocusMap / LayoutMap optional",
            wraplength=270, font=("Segoe UI", 8),
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        stage = self.section("Stage XYZ / Layout UV", 2)
        grid = ttk.Frame(stage)
        grid.pack(fill="x")
        self.metrics = {}
        for index, key in enumerate(("X", "Y", "Z", "U", "V")):
            column, row = index % 5, index // 5
            grid.columnconfigure(column, weight=1, uniform="mapping_metrics")
            tile = tk.Frame(
                grid, bg=self.colors["surface_2"], highlightthickness=1,
                highlightbackground=self.colors["border"],
            )
            tile.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else 2, 0))
            tk.Label(
                tile, text=key, anchor="w", bg=self.colors["surface_2"],
                fg=self.colors["muted"], font=("Segoe UI", 7),
            ).pack(fill="x", padx=4)
            label = tk.Label(
                tile, text="—", anchor="e", bg=self.colors["surface_2"],
                fg=self.colors["accent"], font=("Cascadia Mono", 8, "bold"),
            )
            label.pack(fill="x", padx=4, pady=(0, 3))
            self.metrics[key] = label
        ttk.Label(stage, text="XYZ µm · UV layout", font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 0))

        viewport = self.section("XY Viewport", 3)
        viewport.columnconfigure(0, weight=1)
        ttk.Label(
            viewport, textvariable=panel.viewport_var, wraplength=270,
            font=("Cascadia Mono", 8),
        ).grid(row=0, column=0, sticky="ew")
        ttk.Button(viewport, text="Fit to View", command=lambda: panel.scan_canvas.fit()).grid(
            row=1, column=0, sticky="ew", pady=(4, 0),
        )

        scan = self.section("Scan status", 4, stretch=True)
        scan.columnconfigure(0, weight=1)
        scan.rowconfigure(1, weight=1)
        ttk.Label(
            scan, textvariable=panel.system_var, wraplength=270,
            font=("Segoe UI", 8, "bold"),
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(
            scan, textvariable=panel.status, wraplength=270,
            font=("Segoe UI", 8), justify="left",
        ).grid(row=1, column=0, sticky="nsew", pady=3)
        legend = ttk.Frame(scan)
        legend.grid(row=2, column=0, sticky="ew")
        for index, (name, color) in enumerate((
            ("Pending", "#526985"), ("Running", "#fbbf24"),
            ("Done", "#34d399"), ("Failed", "#fb7185"),
        )):
            tk.Label(
                legend, text="● " + name, fg=color,
                bg=self.colors["surface_2"], font=("Segoe UI", 8), padx=4,
            ).grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 2, 0))
            legend.columnconfigure(index, weight=1)

    def section(self, title, row, *, stretch=False):
        frame = ttk.LabelFrame(self, text=title, padding=6)
        frame.grid(row=row, column=0, sticky="nsew" if stretch else "ew", pady=(0, 5))
        return frame

    def refresh(self, data):
        if not isinstance(data, dict):
            self.panel.system_var.set(str(data))
            data = {}
        else:
            self.panel.system_var.set("Motion · " + data.get("motion", "—"))
        for key, title in (("stage", "Stage"), ("focusmap", "Focus"), ("layoutmap", "Layout")):
            ready = bool(data.get(key))
            optional = key != "stage"
            self.chips[key].configure(
                text=f"{title} {'ON' if ready else 'OFF'}",
                fg="#bbf7d0" if ready else "#aab8ca" if optional else "#fecdd3",
                bg="#052e24" if ready else "#263449" if optional else "#4c0519",
            )
        coordinates = data.get("coordinates", {})
        for key, label in self.metrics.items():
            value = coordinates.get(key)
            label.configure(text=f"{value:.3f}" if value is not None else "—")
        self.panel.scan_canvas.set_stage_position(coordinates.get("X"), coordinates.get("Y"))

        payload = data.get("microscope")
        if payload:
            try:
                image = Image.open(io.BytesIO(payload))
                width = max(150, self.microscope_label.winfo_width() - 8)
                height = max(80, self.microscope_label.winfo_height() - 8)
                image.thumbnail((width, height))
                self.photo = ImageTk.PhotoImage(image, master=self)
                self.microscope_label.configure(image=self.photo, text="")
            except (OSError, ValueError):
                self.microscope_label.configure(image="", text="Microscope frame unavailable")
        else:
            self.microscope_label.configure(image="", text="No microscope frame")
