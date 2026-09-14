"""Compact mode-specific editor for mapping-region construction."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from .mapping_region import region_geometry


METHODS = {
    "rectangle": (("two_corners", "Two corners"), ("center_corner", "Center + corner")),
    "circle": (("center_corner", "Center + rim"), ("three_points", "Three points")),
    "polygon": (("vertices", "Closed polygon"),),
}


def _draw_icon(master, kind, *, width=20, height=16):
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = "#dcecff"
    outline = (3, 3, width - 4, height - 4)
    if kind.startswith("circle") or kind == "three_points":
        draw.ellipse(outline, outline=color, width=2)
    elif kind.startswith("polygon") or kind == "vertices":
        draw.polygon(((width // 2, 2), (width - 3, height - 4), (3, height - 4)), outline=color, width=2)
    else:
        draw.rectangle(outline, outline=color, width=2)
    if kind in {"two_corners", "center_corner", "circle_center", "three_points", "vertices"}:
        points = {
            "two_corners": ((3, height - 4), (width - 4, 3)),
            "center_corner": ((width // 2, height // 2), (width - 4, 3)),
            "circle_center": ((width // 2, height // 2), (width - 4, height // 2)),
            "three_points": ((width // 2, 2), (3, height // 2), (width - 4, height // 2)),
            "vertices": ((width // 2, 2), (3, height - 4), (width - 3, height - 4)),
        }[kind]
        for x, y in points:
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill="#67e8f9")
    return ImageTk.PhotoImage(image, master=master)


class MappingRegionEditor(ttk.Frame):
    def __init__(self, parent, panel):
        super().__init__(parent)
        self.panel = panel
        self.shape_var = panel.shape_var
        self.method_var = tk.StringVar(value="two_corners")
        self.rows = []
        self.locked = False
        self._updating = False
        self._snapshots = {}
        self._active = ("rectangle", "two_corners")
        self.icons = {
            name: _draw_icon(self, name) for name in (
                "rectangle", "circle", "polygon", "two_corners",
                "center_corner", "circle_center", "three_points", "vertices",
            )
        }
        self.columnconfigure(0, weight=1)

        shapes = ttk.Frame(self)
        shapes.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.shape_buttons = {}
        for column, (shape, label) in enumerate((
            ("rectangle", "Rectangle"),
            ("circle", "Circle"),
            ("polygon", "Polygon"),
        )):
            shapes.columnconfigure(column, weight=1, uniform="region_shapes")
            button = tk.Button(
                shapes, text=label, image=self.icons[shape], compound="left",
                relief="flat", bd=0, padx=4, pady=6, font=("Segoe UI", 9),
                command=lambda value=shape: self.select_shape(value),
            )
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 3, 0))
            self.shape_buttons[shape] = button

        self.method_frame = ttk.Frame(self)
        self.method_frame.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self.editor_host = ttk.Frame(self)
        self.editor_host.grid(row=2, column=0, sticky="ew")
        self.hint_var = tk.StringVar()

        self.shape_var.trace_add("write", self._shape_changed)
        self.method_var.trace_add("write", self._method_changed)
        self._rebuild([(0, 0), (100, 100)])

    def _raw_points(self):
        return [(x.get(), y.get()) for x, y in self.rows]

    def points(self):
        return [(float(x.get()), float(y.get())) for x, y in self.rows]

    def select_shape(self, shape):
        if not self.locked:
            self.shape_var.set(shape)

    def _shape_changed(self, *_):
        if self._updating:
            return
        self._snapshots[self._active] = self._raw_points()
        shape = self.shape_var.get()
        self._updating = True
        self.method_var.set(METHODS[shape][0][0])
        self._updating = False
        self._switch()

    def _method_changed(self, *_):
        if not self._updating:
            self._snapshots[self._active] = self._raw_points()
            self._switch()

    def _switch(self):
        self.panel.scan_canvas.cancel(redraw=False)
        self._active = self.shape_var.get(), self.method_var.get()
        defaults = [(0, 0), (100, 100)]
        if self._active == ("circle", "three_points"):
            defaults = [(0, 50), (50, 100), (100, 50)]
        elif self._active[0] == "polygon":
            defaults = [(0, 0), (100, 0), (50, 100)]
        self._rebuild(self._snapshots.get(self._active, defaults))
        self.panel.mark_region_dirty()
        self.panel.status.set("Region changed — preview to regenerate the scan path.")
        self.panel.scan_canvas.draw()

    def _rebuild(self, points):
        self._updating = True
        for host in (self.method_frame, self.editor_host):
            for child in host.winfo_children():
                child.destroy()
        shape, method = self._active
        self.edit_widgets = []
        self.method_buttons = {}
        for column, (value, label) in enumerate(METHODS[shape]):
            self.method_frame.columnconfigure(column, weight=1, uniform="region_methods")
            icon_name = "circle_center" if shape == "circle" and value == "center_corner" else value
            button = tk.Button(
                self.method_frame, text=label, image=self.icons[icon_name], compound="left",
                relief="flat", bd=0, padx=4, pady=4, font=("Segoe UI", 8),
                command=lambda selected=value: self.method_var.set(selected),
            )
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 3, 0))
            self.method_buttons[value] = button
            self.edit_widgets.append((button, "normal"))

        self.rows = []
        for x, y in points:
            xv, yv = tk.StringVar(value=str(x)), tk.StringVar(value=str(y))
            xv.trace_add("write", self._point_changed)
            yv.trace_add("write", self._point_changed)
            self.rows.append((xv, yv))
        if shape == "polygon":
            self._build_polygon_list()
        else:
            self._build_fixed_points(shape, method)

        self.hint_var.set({
            "two_corners": "Click opposite corners A → B",
            "center_corner": "Click center → " + ("corner" if shape == "rectangle" else "point on rim"),
            "three_points": "Click three points on the circumference",
            "vertices": "Click vertices in order, then click P1 or press Enter",
        }[method])
        for value, button in self.shape_buttons.items():
            selected = value == shape
            button.configure(
                bg="#2563eb" if selected else "#263449",
                fg="#ffffff" if selected else "#b9c9df",
                activebackground="#3478ea", activeforeground="white",
            )
        for value, button in self.method_buttons.items():
            selected = value == method
            button.configure(
                bg="#31506f" if selected else "#1b293b",
                fg="#dcecff" if selected else "#9fb0c7",
                activebackground="#3b668e", activeforeground="white",
            )
        self._updating = False
        self.set_locked(self.locked)
        if hasattr(self.panel, "finish_region_button"):
            self.panel.finish_region_button.grid() if shape == "polygon" else self.panel.finish_region_button.grid_remove()

    def _build_fixed_points(self, shape, method):
        table = ttk.Frame(self.editor_host)
        table.pack(fill="x")
        table.columnconfigure((1, 2), weight=1, uniform="region_xy")
        for column, label in enumerate(("Point", "X (µm)", "Y (µm)", "Stage")):
            ttk.Label(table, text=label, font=("Segoe UI", 8)).grid(row=0, column=column, sticky="w", padx=2)
        for index, (xv, yv) in enumerate(self.rows):
            label = (
                f"P{index + 1}" if method == "three_points"
                else ("Center", "Corner" if shape == "rectangle" else "Rim")[index]
                if method == "center_corner" else ("A", "B")[index]
            )
            ttk.Label(table, text=label).grid(row=index + 1, column=0, sticky="w", padx=2)
            for column, variable in enumerate((xv, yv), 1):
                entry = ttk.Entry(table, textvariable=variable, width=9)
                entry.grid(row=index + 1, column=column, sticky="ew", padx=2, pady=2)
                self.edit_widgets.append((entry, "normal"))
            use = ttk.Button(
                table, text="Use", width=4,
                command=lambda selected=index: self.panel.record_corner_callback(str(selected)),
            )
            use.grid(row=index + 1, column=3, padx=(2, 0))
            self.edit_widgets.append((use, "normal"))

    def _build_polygon_list(self):
        shell = ttk.Frame(self.editor_host)
        shell.pack(fill="x")
        self.polygon_tree = ttk.Treeview(
            shell, columns=("x", "y"), show="tree headings", height=5, selectmode="browse",
        )
        self.polygon_tree.heading("#0", text="Point")
        self.polygon_tree.column("#0", width=52, minwidth=46, anchor="center", stretch=False)
        self.polygon_tree.heading("x", text="X (µm)")
        self.polygon_tree.heading("y", text="Y (µm)")
        self.polygon_tree.column("x", width=120, anchor="e", stretch=True)
        self.polygon_tree.column("y", width=120, anchor="e", stretch=True)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.polygon_tree.yview)
        self.polygon_tree.configure(yscrollcommand=scrollbar.set)
        self.polygon_tree.grid(row=0, column=0, sticky="ew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        shell.columnconfigure(0, weight=1)
        self.polygon_tree.bind("<Double-1>", self._begin_tree_edit)
        self.polygon_tree.bind("<<TreeviewSelect>>", self._polygon_selection_changed)
        self._sync_polygon_tree()
        if self.rows:
            self.polygon_tree.selection_set("0")

        actions = ttk.Frame(self.editor_host)
        actions.pack(fill="x", pady=(4, 0))
        self.insert_button = ttk.Button(actions, text="Insert", command=self.insert_vertex)
        self.add_button = ttk.Button(actions, text="Add", command=self.add_vertex)
        self.remove_button = ttk.Button(actions, text="Remove", command=self.remove_selected_vertex)
        self.use_stage_button = ttk.Button(actions, text="Use stage", command=self.use_stage_for_selected)
        for button in (self.insert_button, self.add_button, self.remove_button, self.use_stage_button):
            button.pack(side="left", padx=(0, 3))
            self.edit_widgets.append((button, "normal"))

    def _sync_polygon_tree(self):
        if not hasattr(self, "polygon_tree") or not self.polygon_tree.winfo_exists():
            return
        selection = self.polygon_tree.selection()
        for item in self.polygon_tree.get_children():
            self.polygon_tree.delete(item)
        for index, (xv, yv) in enumerate(self.rows):
            self.polygon_tree.insert(
                "", "end", iid=str(index), text=f"P{index + 1}", values=(xv.get(), yv.get()),
            )
        if selection and selection[0] in self.polygon_tree.get_children():
            self.polygon_tree.selection_set(selection[0])

    def _selected_polygon_index(self):
        selection = getattr(self, "polygon_tree", None)
        selected = selection.selection() if selection is not None else ()
        return int(selected[0]) if selected else max(0, len(self.rows) - 1)

    def _polygon_selection_changed(self, *_):
        return

    def _begin_tree_edit(self, event):
        if self.locked:
            return
        item = self.polygon_tree.identify_row(event.y)
        column = self.polygon_tree.identify_column(event.x)
        if not item or column not in ("#1", "#2"):
            return
        box = self.polygon_tree.bbox(item, column)
        if not box:
            return
        variable = self.rows[int(item)][0 if column == "#1" else 1]
        editor = ttk.Entry(self.polygon_tree, textvariable=variable)
        editor.place(x=box[0], y=box[1], width=box[2], height=box[3])
        editor.focus_set()
        editor.select_range(0, "end")
        editor.bind("<Return>", lambda _e: self._finish_tree_edit(editor))
        editor.bind("<Escape>", lambda _e: editor.destroy())
        editor.bind("<FocusOut>", lambda _e: self._finish_tree_edit(editor))

    def _finish_tree_edit(self, editor):
        if editor.winfo_exists():
            editor.destroy()
        self._sync_polygon_tree()

    def _point_changed(self, *_):
        if not self._updating:
            self._sync_polygon_tree()
            self.panel.mark_region_dirty()

    def insert_vertex(self):
        if self.locked:
            return
        index = self._selected_polygon_index()
        points = self._raw_points()
        points.insert(index, points[index] if points else ("0", "0"))
        self._rebuild(points)
        self.polygon_tree.selection_set(str(index))
        self.panel.mark_region_dirty()

    def add_vertex(self):
        if self.locked:
            return
        points = self._raw_points()
        points.append(points[-1] if points else ("0", "0"))
        self._rebuild(points)
        self.polygon_tree.selection_set(str(len(points) - 1))
        self.polygon_tree.see(str(len(points) - 1))
        self.panel.mark_region_dirty()

    def remove_selected_vertex(self):
        self.remove_vertex(self._selected_polygon_index())

    def remove_vertex(self, index):
        if self.locked or not (0 <= index < len(self.rows)):
            return
        points = self._raw_points()
        points.pop(index)
        self._rebuild(points)
        if points:
            self.polygon_tree.selection_set(str(min(index, len(points) - 1)))
        self.panel.mark_region_dirty()

    def use_stage_for_selected(self):
        if not self.locked:
            self.panel.record_corner_callback(str(self._selected_polygon_index()))

    def set_points(self, points):
        if len(points) == len(self.rows):
            self._updating = True
            for (xv, yv), (x, y) in zip(self.rows, points):
                xv.set(str(x))
                yv.set(str(y))
            self._updating = False
            self._sync_polygon_tree()
        else:
            self._rebuild(points)
        self.panel.mark_region_dirty()
        if self.shape_var.get() == "polygon":
            self.panel.polygon_var.set("; ".join(f"{float(x):.12g},{float(y):.12g}" for x, y in points))

    def geometry(self):
        points = self.points()
        return region_geometry(self.shape_var.get(), self.method_var.get(), points), points

    def restore(self, settings):
        shape = settings.region_shape
        method = getattr(settings, "region_method", "") or METHODS[shape][0][0]
        points = getattr(settings, "region_control_points", ())
        if not points:
            if shape == "polygon":
                points = settings.polygon_vertices
            elif shape == "circle":
                cx = (settings.x_a_um + settings.x_b_um) / 2
                cy = (settings.y_a_um + settings.y_b_um) / 2
                radius = min(abs(settings.x_b_um - settings.x_a_um), abs(settings.y_b_um - settings.y_a_um)) / 2
                method, points = "center_corner", [(cx, cy), (cx + radius, cy)]
            else:
                method, points = "two_corners", [
                    (settings.x_a_um, settings.y_a_um),
                    (settings.x_b_um, settings.y_b_um),
                ]
        self._updating = True
        self.shape_var.set(shape)
        self.method_var.set(method)
        self._updating = False
        self._active = shape, method
        self._rebuild(points)

    def set_locked(self, locked):
        self.locked = locked
        for widget, state in getattr(self, "edit_widgets", []):
            widget.configure(state="disabled" if locked else state)
        for widget in self.shape_buttons.values():
            widget.configure(state="disabled" if locked else "normal")
        if hasattr(self, "polygon_tree") and self.polygon_tree.winfo_exists():
            self.polygon_tree.configure(selectmode="none" if locked else "browse")
