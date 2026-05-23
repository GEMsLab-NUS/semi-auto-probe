from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk


class VisionPanel:
    def __init__(
        self,
        parent: ttk.Frame,
        colors: dict[str, str],
        get_um_per_px: Callable[[], float | None],
        move_point_to_center: Callable[[float, float, int, int], None],
        get_centering_preview: Callable[[float, float, int, int], str],
        get_gds_overlay_polygons: Callable[[int, int], list[list[tuple[float, float]]]] | None = None,
        get_probe_assist_points: Callable[[int, int], list[tuple[float, float, str, str]]] | None = None,
    ) -> None:
        self.colors = colors
        self.get_um_per_px = get_um_per_px
        self.move_point_to_center = move_point_to_center
        self.get_centering_preview = get_centering_preview
        self.get_gds_overlay_polygons = get_gds_overlay_polygons
        self.get_probe_assist_points = get_probe_assist_points
        self.photo: tk.PhotoImage | None = None
        self.source_image_bgr = None
        self.canvas_image_id: int | None = None
        self.canvas_message_id: int | None = None
        self.image_bounds: tuple[float, float, float, float] | None = None
        self.image_width = 0
        self.image_height = 0
        self.display_scale_x = 1.0
        self.display_scale_y = 1.0
        self.tool_var = tk.StringVar(value="idle")
        self.status_var = tk.StringVar(value="Select a vision tool.")
        self.cross_enabled = False
        self.gds_overlay_enabled = False
        self.probe_assist_enabled = False
        self.probe_assist_points: list[tuple[float, float]] = []
        self.probe_assist_labels: list[str] = []
        self.probe_assist_colors: list[str] = []
        self.probe_assist_fixed = False
        self.probe_assist_drag_index: int | None = None
        self.tool_buttons: dict[str, tk.Button] = {}
        self.measure_points: list[tuple[float, float]] = []
        self.polygon_closed = False
        self.configure_job: str | None = None
        self.hover_canvas_point: tuple[float, float] | None = None
        self.shift_down = False
        self.pre_shift_status: str | None = None

        self.frame = ttk.Frame(parent, style="Panel.TFrame")
        self.frame.grid(row=1, column=0, sticky="nsew")
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        self._build_toolbar(self.frame)
        self.canvas = tk.Canvas(
            self.frame,
            bg="#05070a",
            highlightthickness=1,
            highlightbackground=colors["border"],
            bd=0,
            cursor="crosshair",
        )
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<Double-Button-1>", self._on_canvas_double_click)
        self.canvas.bind("<Button-3>", self._on_canvas_right_click)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Leave>", self._on_canvas_leave)

    def _build_toolbar(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent, style="Panel.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(4, weight=1)

        tools = (
            ("point_distance", "Point-Point"),
            ("line_distance", "Point-Line"),
            ("polygon_area", "Polygon Area"),
        )
        for column, (tool, label) in enumerate(tools):
            button = self._toolbar_button(toolbar, label, lambda name=tool: self.set_tool(name))
            button.grid(row=0, column=column, sticky="w", padx=(0, 6))
            self.tool_buttons[tool] = button

        ttk.Button(toolbar, text="Clear", command=self.clear_measurement).grid(row=0, column=3, sticky="w", padx=(4, 8))
        ttk.Label(toolbar, textvariable=self.status_var, style="Muted.TLabel", wraplength=360).grid(row=0, column=4, sticky="ew", padx=(0, 8))

        right_tools = ttk.Frame(toolbar, style="Panel.TFrame")
        right_tools.grid(row=0, column=5, sticky="e")
        for column, (tool, label, command) in enumerate(
            (
                ("cross", "\u2295", lambda: self.set_tool("cross")),
                ("gds_overlay", "GDS Overlay", self.toggle_gds_overlay),
                ("probe_assist", "Probe Assist", self.toggle_probe_assist),
            )
        ):
            button = self._toolbar_button(right_tools, label, command)
            button.grid(row=0, column=column, sticky="e", padx=(0 if column == 0 else 6, 0))
            self.tool_buttons[tool] = button
        self._refresh_tool_buttons()

    def _toolbar_button(self, parent: tk.Widget, text: str, command: Callable[[], None], *, width: int | None = None) -> tk.Button:
        options = {
            "text": text,
            "command": command,
            "bg": self.colors["surface_3"],
            "fg": self.colors["text"],
            "activebackground": "#223144",
            "activeforeground": self.colors["text"],
            "relief": "flat",
            "bd": 0,
            "padx": 10,
            "pady": 6,
            "font": ("Segoe UI", 9),
            "cursor": "hand2",
        }
        if width is not None:
            options["width"] = width
        return tk.Button(parent, **options)

    def set_tool(self, tool: str) -> None:
        if tool == "cross":
            self.cross_enabled = not self.cross_enabled
            self.status_var.set("Center cross enabled." if self.cross_enabled else "Center cross disabled.")
        else:
            self.tool_var.set("idle" if self.tool_var.get() == tool else tool)
            self.clear_measurement(reset_status=False)
            status_by_tool = {
                "point_distance": "Click two points to measure distance.",
                "line_distance": "Click two line points, then a point to measure distance to line.",
                "polygon_area": "Click polygon vertices. Double-click or right-click to finish.",
                "idle": "Select a vision tool.",
            }
            self.status_var.set(status_by_tool.get(self.tool_var.get(), "Select a vision tool."))
        self._refresh_tool_buttons()
        self._refresh_canvas_cursor()
        self._clear_move_hover()
        self.draw_overlay()

    def toggle_gds_overlay(self) -> None:
        self.gds_overlay_enabled = not self.gds_overlay_enabled
        if not self.gds_overlay_enabled:
            self.status_var.set("GDS overlay disabled.")
        elif self.get_gds_overlay_polygons is None:
            self.status_var.set("GDS overlay unavailable.")
        elif self.image_width <= 0 or self.image_height <= 0:
            self.status_var.set("GDS overlay enabled; waiting for live image.")
        else:
            try:
                polygons = self.get_gds_overlay_polygons(self.image_width, self.image_height)
            except Exception as exc:
                self.status_var.set(str(exc))
            else:
                if polygons:
                    self.status_var.set("GDS overlay enabled.")
                else:
                    self.status_var.set("GDS overlay enabled; no visible GDS geometry in the current view.")
        self._refresh_tool_buttons()
        self.draw_overlay()

    def toggle_probe_assist(self) -> None:
        self.probe_assist_enabled = not self.probe_assist_enabled
        if self.probe_assist_enabled:
            configured = self._refresh_configured_probe_assist_points(update_status=False)
            if configured:
                self.status_var.set("Probe Assist enabled from AutoTest Probe settings.")
            elif self.image_width > 0 and self.image_height > 0 and not self.probe_assist_points:
                self._initialize_probe_assist_points()
                self.status_var.set("Probe Assist enabled. Drag the red boxes to set assist points.")
            else:
                self.status_var.set("Probe Assist enabled; waiting for live image.")
        else:
            self.probe_assist_drag_index = None
            self.status_var.set("Probe Assist disabled.")
        self._refresh_tool_buttons()
        self._refresh_canvas_cursor()
        self.draw_overlay()

    def set_image(self, photo: tk.PhotoImage) -> None:
        self.source_image_bgr = None
        self.photo = photo
        self.image_width = photo.width()
        self.image_height = photo.height()
        self.display_scale_x = 1.0
        self.display_scale_y = 1.0
        self.draw_image()
        self.draw_overlay()

    def set_image_bgr(self, image_bgr) -> None:
        self.source_image_bgr = image_bgr
        self.image_height, self.image_width = image_bgr.shape[:2]
        self.draw_image()
        self.draw_overlay()

    def show_message(self, message: str) -> None:
        try:
            self.canvas.delete("camera_image")
            self.canvas.delete("vision_overlay")
            self.canvas_image_id = None
            self.image_bounds = None
            self.source_image_bgr = None
            self.image_width = 0
            self.image_height = 0
            if self.canvas_message_id is not None:
                self.canvas.delete(self.canvas_message_id)
            self.canvas_message_id = self.canvas.create_text(
                max(1, self.canvas.winfo_width()) / 2,
                max(1, self.canvas.winfo_height()) / 2,
                text=message,
                fill=self.colors["muted"],
                font=("Segoe UI Semibold", 14),
            )
        except tk.TclError:
            return

    def clear_measurement(self, reset_status: bool = True) -> None:
        self.measure_points.clear()
        self.polygon_closed = False
        if reset_status:
            self.status_var.set("Measurement cleared.")
        self.draw_overlay()

    def draw_image(self) -> None:
        if self.source_image_bgr is not None:
            self._draw_fit_bgr_image()
            return
        if self.photo is None:
            return
        try:
            canvas_width = max(1, self.canvas.winfo_width())
            canvas_height = max(1, self.canvas.winfo_height())
            image_width = self.photo.width()
            image_height = self.photo.height()
            self.image_width = image_width
            self.image_height = image_height
            self.display_scale_x = 1.0
            self.display_scale_y = 1.0
            x = max(0, (canvas_width - image_width) / 2.0)
            y = max(0, (canvas_height - image_height) / 2.0)
            self.image_bounds = (x, y, x + image_width, y + image_height)
            if self.canvas_message_id is not None:
                self.canvas.delete(self.canvas_message_id)
                self.canvas_message_id = None
            if self.canvas_image_id is None:
                self.canvas_image_id = self.canvas.create_image(x, y, anchor="nw", image=self.photo, tags=("camera_image",))
            else:
                self.canvas.coords(self.canvas_image_id, x, y)
                self.canvas.itemconfigure(self.canvas_image_id, image=self.photo)
            self.canvas.tag_lower(self.canvas_image_id)
        except tk.TclError:
            return

    def _draw_fit_bgr_image(self) -> None:
        if self.source_image_bgr is None or self.image_width <= 0 or self.image_height <= 0:
            return
        try:
            import cv2

            canvas_width = max(1, self.canvas.winfo_width())
            canvas_height = max(1, self.canvas.winfo_height())
            scale = min(canvas_width / self.image_width, canvas_height / self.image_height)
            if not math.isfinite(scale) or scale <= 0:
                scale = 1.0
            display_width = max(1, min(canvas_width, int(self.image_width * scale)))
            display_height = max(1, min(canvas_height, int(self.image_height * scale)))
            self.display_scale_x = display_width / self.image_width
            self.display_scale_y = display_height / self.image_height
            interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            display_bgr = cv2.resize(self.source_image_bgr, (display_width, display_height), interpolation=interpolation)
            display_rgb = cv2.cvtColor(display_bgr, cv2.COLOR_BGR2RGB)
            header = f"P6 {display_width} {display_height} 255\n".encode("ascii")
            self.photo = tk.PhotoImage(data=header + display_rgb.tobytes(), format="PPM")

            x = max(0, (canvas_width - display_width) / 2.0)
            y = max(0, (canvas_height - display_height) / 2.0)
            self.image_bounds = (x, y, x + display_width, y + display_height)
            if self.canvas_message_id is not None:
                self.canvas.delete(self.canvas_message_id)
                self.canvas_message_id = None
            if self.canvas_image_id is None:
                self.canvas_image_id = self.canvas.create_image(x, y, anchor="nw", image=self.photo, tags=("camera_image",))
            else:
                self.canvas.coords(self.canvas_image_id, x, y)
                self.canvas.itemconfigure(self.canvas_image_id, image=self.photo)
            self.canvas.tag_lower(self.canvas_image_id)
        except tk.TclError:
            return

    def draw_overlay(self) -> None:
        try:
            self.canvas.delete("vision_overlay")
            if self.image_bounds is None:
                return
            left, top, right, bottom = self.image_bounds
            width = right - left
            height = bottom - top
            if width <= 0 or height <= 0:
                return

            if self.cross_enabled:
                cx = left + width / 2.0
                cy = top + height / 2.0
                self.canvas.create_line(left, cy, right, cy, fill="#ef4444", width=1, dash=(8, 5), tags="vision_overlay")
                self.canvas.create_line(cx, top, cx, bottom, fill="#ef4444", width=1, dash=(8, 5), tags="vision_overlay")
                self.canvas.create_line(cx - 10, cy, cx + 10, cy, fill="#fecaca", width=2, tags="vision_overlay")
                self.canvas.create_line(cx, cy - 10, cx, cy + 10, fill="#fecaca", width=2, tags="vision_overlay")

            self._draw_gds_overlay()
            if self.probe_assist_enabled:
                self._refresh_configured_probe_assist_points(update_status=False)
            self._draw_probe_assist_points()

            canvas_points = [self._image_to_canvas_point(point) for point in self.measure_points]
            tool = self.tool_var.get()
            if tool == "point_distance" and len(canvas_points) >= 2:
                self.canvas.create_line(*canvas_points[0], *canvas_points[1], fill="#fbbf24", width=2, tags="vision_overlay")
            elif tool == "line_distance" and len(canvas_points) >= 2:
                self.canvas.create_line(*canvas_points[0], *canvas_points[1], fill="#34d399", width=2, tags="vision_overlay")
                if len(canvas_points) >= 3:
                    foot = self._projection_foot(canvas_points[0], canvas_points[1], canvas_points[2])
                    self.canvas.create_line(*canvas_points[2], *foot, fill="#fbbf24", width=2, dash=(4, 3), tags="vision_overlay")
            elif tool == "polygon_area" and len(canvas_points) >= 2:
                flat_points = [coord for point in canvas_points for coord in point]
                if self.polygon_closed and len(canvas_points) >= 3:
                    self.canvas.create_polygon(*flat_points, fill="#34d399", stipple="gray25", outline="#34d399", width=2, tags="vision_overlay")
                else:
                    self.canvas.create_line(*flat_points, fill="#34d399", width=2, tags="vision_overlay")

            for index, (x, y) in enumerate(canvas_points, start=1):
                self.canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill="#60a5fa", outline="#dbeafe", width=1, tags="vision_overlay")
                self.canvas.create_text(x + 8, y - 8, text=str(index), fill="#e5edf5", anchor="sw", font=("Segoe UI Semibold", 10), tags="vision_overlay")
            self._draw_move_hover()
        except tk.TclError:
            return

    def _refresh_tool_buttons(self) -> None:
        active_tool = self.tool_var.get()
        for tool, button in self.tool_buttons.items():
            if tool == "cross":
                is_active = self.cross_enabled
            elif tool == "gds_overlay":
                is_active = self.gds_overlay_enabled
            elif tool == "probe_assist":
                is_active = self.probe_assist_enabled
            else:
                is_active = active_tool == tool
            button.configure(
                bg="#0f3b2d" if is_active else self.colors["surface_3"],
                fg="#d1fae5" if is_active else self.colors["text"],
                highlightthickness=1 if is_active else 0,
                highlightbackground="#2dd4bf" if is_active else self.colors["border"],
            )

    def _draw_gds_overlay(self) -> None:
        if not self.gds_overlay_enabled or self.get_gds_overlay_polygons is None:
            return
        try:
            polygons = self.get_gds_overlay_polygons(self.image_width, self.image_height)
        except Exception:
            return
        for polygon in polygons:
            if len(polygon) < 3:
                continue
            canvas_points = [self._image_to_canvas_point(point) for point in polygon]
            flat_points = [coord for point in canvas_points for coord in point]
            self.canvas.create_polygon(
                *flat_points,
                fill="#38bdf8",
                stipple="gray25",
                outline="#bae6fd",
                width=1,
                tags="vision_overlay",
            )

    def _initialize_probe_assist_points(self) -> None:
        center_x = float(self.image_width) / 2.0
        center_y = float(self.image_height) / 2.0
        offset = max(35.0, min(float(self.image_width) * 0.16, 120.0))
        self.probe_assist_points = [
            (max(0.0, center_x - offset), center_y),
            (min(float(self.image_width), center_x + offset), center_y),
        ]
        self.probe_assist_labels = ["P1", "P2"]
        self.probe_assist_colors = ["#ef4444", "#ef4444"]
        self.probe_assist_fixed = False

    def _refresh_configured_probe_assist_points(self, *, update_status: bool) -> bool:
        if self.get_probe_assist_points is None or self.image_width <= 0 or self.image_height <= 0:
            return False
        try:
            points = self.get_probe_assist_points(self.image_width, self.image_height)
        except Exception as exc:
            if update_status:
                self.status_var.set(str(exc))
            return False
        if not points:
            if self.probe_assist_fixed:
                self.probe_assist_points = []
                self.probe_assist_labels = []
                self.probe_assist_colors = []
                self.probe_assist_fixed = False
            return False
        self.probe_assist_points = [(float(point[0]), float(point[1])) for point in points]
        self.probe_assist_labels = [str(point[2]) for point in points]
        self.probe_assist_colors = [str(point[3]) for point in points]
        self.probe_assist_fixed = True
        self.probe_assist_drag_index = None
        return True

    def _draw_probe_assist_points(self) -> None:
        if not self.probe_assist_enabled:
            return
        if self.image_width <= 0 or self.image_height <= 0:
            return
        if not self.probe_assist_points and not self.probe_assist_fixed:
            self._initialize_probe_assist_points()
        half_size = 15.0
        cross_size = 9.0
        for index, point in enumerate(self.probe_assist_points):
            color = self.probe_assist_colors[index] if index < len(self.probe_assist_colors) else "#ef4444"
            label = self.probe_assist_labels[index] if index < len(self.probe_assist_labels) else f"P{index + 1}"
            cx, cy = self._image_to_canvas_point(point)
            self.canvas.create_rectangle(
                cx - half_size,
                cy - half_size,
                cx + half_size,
                cy + half_size,
                outline=color,
                width=2,
                tags="vision_overlay",
            )
            self.canvas.create_line(cx - cross_size, cy, cx + cross_size, cy, fill=color, width=2, tags="vision_overlay")
            self.canvas.create_line(cx, cy - cross_size, cx, cy + cross_size, fill=color, width=2, tags="vision_overlay")
            self.canvas.create_text(cx + half_size + 4, cy - half_size, text=label, fill=color, anchor="nw", font=("Segoe UI Semibold", 9), tags="vision_overlay")

    def _probe_assist_hit_index(self, canvas_x: float, canvas_y: float) -> int | None:
        if not self.probe_assist_enabled or self.image_bounds is None:
            return None
        hit_radius = 18.0
        for index, point in enumerate(self.probe_assist_points):
            cx, cy = self._image_to_canvas_point(point)
            if abs(canvas_x - cx) <= hit_radius and abs(canvas_y - cy) <= hit_radius:
                return index
        return None

    def _refresh_canvas_cursor(self) -> None:
        try:
            assist_hit = None
            if self.hover_canvas_point is not None:
                assist_hit = self._probe_assist_hit_index(*self.hover_canvas_point)
            if self.probe_assist_drag_index is not None or (assist_hit is not None and not self.probe_assist_fixed):
                cursor = "fleur"
            elif self._move_center_active():
                cursor = "tcross"
            else:
                cursor = "crosshair"
            self.canvas.configure(cursor=cursor)
        except tk.TclError:
            return

    def set_shift_down(self, is_down: bool) -> None:
        if self.shift_down == is_down:
            return
        self.shift_down = is_down
        if not is_down:
            if self.pre_shift_status is not None:
                self.status_var.set(self.pre_shift_status)
            self.pre_shift_status = None
        elif self._hover_point_in_image() is not None:
            self.pre_shift_status = self.status_var.get()
            self.status_var.set("Shift move center enabled. Click a point to move it to image center.")
        else:
            self.pre_shift_status = self.status_var.get()
        self._refresh_tool_buttons()
        self._refresh_canvas_cursor()
        if not is_down:
            self._clear_move_hover()
        else:
            self._draw_move_hover()

    def _move_center_active(self) -> bool:
        return self.shift_down and self._hover_point_in_image() is not None

    def _hover_point_in_image(self) -> tuple[float, float] | None:
        if self.hover_canvas_point is None:
            return None
        return self._canvas_to_image_point(*self.hover_canvas_point, update_status=False)

    def _on_canvas_configure(self, _event: tk.Event) -> None:
        if self.configure_job is not None:
            try:
                self.canvas.after_cancel(self.configure_job)
            except tk.TclError:
                pass
        try:
            self.configure_job = self.canvas.after(20, self._redraw_after_configure)
        except tk.TclError:
            self.configure_job = None

    def _redraw_after_configure(self) -> None:
        self.configure_job = None
        self.draw_image()
        self.draw_overlay()

    def _on_canvas_click(self, event: tk.Event) -> str | None:
        if self.probe_assist_enabled:
            hit_index = self._probe_assist_hit_index(event.x, event.y)
            if hit_index is not None:
                if self.probe_assist_fixed:
                    self.status_var.set("Probe Assist positions are controlled by AutoTest Probe settings.")
                    return "break"
                self.probe_assist_drag_index = hit_index
                self.status_var.set(f"Dragging Probe Assist point {hit_index + 1}.")
                self._refresh_canvas_cursor()
                return "break"

        if self.shift_down:
            point = self._canvas_to_image_point(event.x, event.y)
            if point is not None and self.image_width > 0 and self.image_height > 0:
                self.pre_shift_status = None
                self.move_point_to_center(point[0], point[1], self.image_width, self.image_height)
            return "break"

        tool = self.tool_var.get()
        if tool not in ("point_distance", "line_distance", "polygon_area"):
            return None
        point = self._canvas_to_image_point(event.x, event.y)
        if point is None:
            return "break"

        if tool == "point_distance":
            if len(self.measure_points) >= 2:
                self.measure_points.clear()
            self.measure_points.append(point)
            if len(self.measure_points) == 2:
                self._update_point_distance_result()
            else:
                self.status_var.set("Point 1 recorded. Click point 2.")
        elif tool == "line_distance":
            if len(self.measure_points) >= 3:
                self.measure_points.clear()
            self.measure_points.append(point)
            if len(self.measure_points) == 3:
                self._update_line_distance_result()
            else:
                remaining = 3 - len(self.measure_points)
                self.status_var.set(f"Point {len(self.measure_points)} recorded. Click {remaining} more.")
        elif tool == "polygon_area":
            if self.polygon_closed:
                self.measure_points.clear()
                self.polygon_closed = False
            self.measure_points.append(point)
            self.status_var.set(f"{len(self.measure_points)} polygon vertices. Double-click or right-click to finish.")

        self.draw_overlay()
        return "break"

    def _on_canvas_drag(self, event: tk.Event) -> str | None:
        if self.probe_assist_drag_index is None:
            return None
        point = self._canvas_to_clamped_image_point(event.x, event.y)
        if point is not None:
            self.probe_assist_points[self.probe_assist_drag_index] = point
            self.draw_overlay()
        return "break"

    def _on_canvas_release(self, _event: tk.Event) -> str | None:
        if self.probe_assist_drag_index is None:
            return None
        index = self.probe_assist_drag_index
        self.probe_assist_drag_index = None
        self.status_var.set(f"Probe Assist point {index + 1} set.")
        self._refresh_canvas_cursor()
        self.draw_overlay()
        return "break"

    def _on_canvas_double_click(self, event: tk.Event) -> str | None:
        if self.shift_down:
            return "break"
        if self.tool_var.get() == "polygon_area":
            self._finish_polygon_measurement()
            return "break"
        return None

    def _on_canvas_right_click(self, _event: tk.Event) -> str | None:
        if self.tool_var.get() == "polygon_area":
            self._finish_polygon_measurement()
            return "break"
        return None

    def _on_canvas_motion(self, event: tk.Event) -> None:
        self.hover_canvas_point = (float(event.x), float(event.y))
        if self.probe_assist_enabled and self._probe_assist_hit_index(event.x, event.y) is not None:
            if self.probe_assist_fixed:
                self.status_var.set("Probe Assist positions are controlled by AutoTest Probe settings.")
            else:
                self.status_var.set("Drag a Probe Assist point to reposition it.")
            self._refresh_canvas_cursor()
            return
        if self._canvas_to_image_point(event.x, event.y, update_status=False) is None:
            self._clear_move_hover()
            return
        if self.shift_down:
            self.status_var.set("Shift move center enabled. Click a point to move it to image center.")
            self._refresh_canvas_cursor()
            self._draw_move_hover()
        elif self.canvas.cget("cursor") != "crosshair":
            self._refresh_canvas_cursor()

    def _on_canvas_leave(self, _event: tk.Event) -> None:
        self._clear_move_hover()
        self._refresh_canvas_cursor()

    def _canvas_to_image_point(self, canvas_x: float, canvas_y: float, update_status: bool = True) -> tuple[float, float] | None:
        if self.image_bounds is None:
            if update_status:
                self.status_var.set("No camera image is available.")
            return None
        left, top, right, bottom = self.image_bounds
        if canvas_x < left or canvas_x > right or canvas_y < top or canvas_y > bottom:
            if update_status:
                self.status_var.set("Click inside the camera image.")
            return None
        image_x = (canvas_x - left) / self.display_scale_x
        image_y = (canvas_y - top) / self.display_scale_y
        return max(0.0, min(float(self.image_width), image_x)), max(0.0, min(float(self.image_height), image_y))

    def _canvas_to_clamped_image_point(self, canvas_x: float, canvas_y: float) -> tuple[float, float] | None:
        if self.image_bounds is None:
            return None
        left, top, right, bottom = self.image_bounds
        clamped_x = max(left, min(right, float(canvas_x)))
        clamped_y = max(top, min(bottom, float(canvas_y)))
        image_x = (clamped_x - left) / self.display_scale_x
        image_y = (clamped_y - top) / self.display_scale_y
        return max(0.0, min(float(self.image_width), image_x)), max(0.0, min(float(self.image_height), image_y))

    def _image_to_canvas_point(self, point: tuple[float, float]) -> tuple[float, float]:
        left, top, _right, _bottom = self.image_bounds or (0.0, 0.0, 0.0, 0.0)
        return left + point[0] * self.display_scale_x, top + point[1] * self.display_scale_y

    def _format_pixel_measure(self, pixels: float, unit_suffix: str = "") -> str:
        um_per_px = self.get_um_per_px()
        if um_per_px is None or um_per_px <= 0:
            return f"{pixels:.2f} px"
        if unit_suffix == "area":
            return f"{pixels:.2f} px^2, {pixels * um_per_px * um_per_px:.2f} um^2"
        return f"{pixels:.2f} px, {pixels * um_per_px:.2f} um"

    def _draw_move_hover(self) -> None:
        try:
            self.canvas.delete("move_hover")
            if not self.shift_down or self.hover_canvas_point is None or self.image_bounds is None or self.image_width <= 0 or self.image_height <= 0:
                return
            point = self._canvas_to_image_point(*self.hover_canvas_point, update_status=False)
            if point is None:
                return

            left, top, right, bottom = self.image_bounds
            center_x = left + (right - left) / 2.0
            center_y = top + (bottom - top) / 2.0
            mouse_x, mouse_y = self.hover_canvas_point
            self.canvas.create_line(center_x, center_y, mouse_x, mouse_y, fill="#fbbf24", width=1, dash=(5, 4), tags="move_hover")
            self.canvas.create_line(mouse_x - 6, mouse_y, mouse_x + 6, mouse_y, fill="#fef3c7", width=1, tags="move_hover")
            self.canvas.create_line(mouse_x, mouse_y - 6, mouse_x, mouse_y + 6, fill="#fef3c7", width=1, tags="move_hover")
            self.canvas.create_oval(center_x - 3, center_y - 3, center_x + 3, center_y + 3, outline="#fbbf24", width=1, tags="move_hover")

            text = self.get_centering_preview(point[0], point[1], self.image_width, self.image_height)
            text_x = mouse_x + 14
            text_y = mouse_y + 14
            text_item = self.canvas.create_text(
                text_x,
                text_y,
                text=text,
                fill="#f8fafc",
                anchor="nw",
                justify="left",
                font=("Cascadia Mono", 9),
                tags="move_hover",
            )
            bbox = self.canvas.bbox(text_item)
            if bbox is None:
                return
            x1, y1, x2, y2 = bbox
            canvas_width = max(1, self.canvas.winfo_width())
            canvas_height = max(1, self.canvas.winfo_height())
            dx = min(0, canvas_width - x2 - 8)
            dy = min(0, canvas_height - y2 - 8)
            if dx or dy:
                self.canvas.move(text_item, dx, dy)
                x1, y1, x2, y2 = self.canvas.bbox(text_item) or (x1, y1, x2, y2)
            bg = self.canvas.create_rectangle(x1 - 6, y1 - 4, x2 + 6, y2 + 4, fill="#05070a", outline="#334155", tags="move_hover")
            self.canvas.tag_lower(bg, text_item)
        except tk.TclError:
            return

    def _clear_move_hover(self) -> None:
        self.hover_canvas_point = None
        try:
            self.canvas.delete("move_hover")
            self._refresh_canvas_cursor()
        except tk.TclError:
            return

    def _update_point_distance_result(self) -> None:
        p1, p2 = self.measure_points[:2]
        distance = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        self.status_var.set(f"Point distance: {self._format_pixel_measure(distance)}")

    def _update_line_distance_result(self) -> None:
        p1, p2, p3 = self.measure_points[:3]
        distance = self._point_line_distance(p1, p2, p3)
        self.status_var.set(f"Point-line distance: {self._format_pixel_measure(distance)}")

    def _finish_polygon_measurement(self) -> None:
        if len(self.measure_points) >= 2:
            last = self.measure_points[-1]
            previous = self.measure_points[-2]
            if math.hypot(last[0] - previous[0], last[1] - previous[1]) <= 2.0:
                self.measure_points.pop()
        if len(self.measure_points) < 3:
            self.status_var.set("Polygon area requires at least three vertices.")
            return
        self.polygon_closed = True
        area = self._polygon_area(self.measure_points)
        self.status_var.set(f"Polygon area: {self._format_pixel_measure(area, 'area')}")
        self.draw_overlay()

    @staticmethod
    def _point_line_distance(p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float]) -> float:
        x1, y1 = p1
        x2, y2 = p2
        x3, y3 = p3
        dx = x2 - x1
        dy = y2 - y1
        baseline = math.hypot(dx, dy)
        if baseline <= 0:
            return 0.0
        return abs(dx * (y1 - y3) - (x1 - x3) * dy) / baseline

    @staticmethod
    def _projection_foot(p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float]) -> tuple[float, float]:
        x1, y1 = p1
        x2, y2 = p2
        x3, y3 = p3
        dx = x2 - x1
        dy = y2 - y1
        length_sq = dx * dx + dy * dy
        if length_sq <= 0:
            return p1
        t = ((x3 - x1) * dx + (y3 - y1) * dy) / length_sq
        return x1 + t * dx, y1 + t * dy

    @staticmethod
    def _polygon_area(points: list[tuple[float, float]]) -> float:
        total = 0.0
        for index, (x1, y1) in enumerate(points):
            x2, y2 = points[(index + 1) % len(points)]
            total += x1 * y2 - x2 * y1
        return abs(total) / 2.0
