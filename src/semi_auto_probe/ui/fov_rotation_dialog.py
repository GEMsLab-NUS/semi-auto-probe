from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

import numpy as np

from ..camera_stage_transform import normalize_camera_fov_rotation_deg


OverlayProvider = Callable[[float, int, int], list[list[tuple[float, float]]]]


class FOVRotationCalibrationDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        image_bgr,
        colors: dict[str, str],
        initial_angle_deg: float,
        overlay_provider: OverlayProvider,
    ) -> None:
        super().__init__(parent)
        self.title("Camera FOV Rotation")
        self.geometry("920x700")
        self.minsize(720, 520)
        self.configure(bg=colors["bg"])
        self.transient(parent)
        self.grab_set()

        self.colors = colors
        self.overlay_provider = overlay_provider
        self.result_angle_deg: float | None = None
        self.image_rgb = np.ascontiguousarray(image_bgr[:, :, ::-1])
        self.image_height, self.image_width = self.image_rgb.shape[:2]
        self.image_photo: tk.PhotoImage | None = None
        self.display_scale = 1.0
        self.display_offset = (0.0, 0.0)
        initial_angle = normalize_camera_fov_rotation_deg(initial_angle_deg)
        self.angle_var = tk.StringVar(value=f"{initial_angle:g}")
        self.slider_var = tk.DoubleVar(value=initial_angle)
        self.status_var = tk.StringVar(value="")
        self._syncing_angle = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, style="App.TFrame", padding=(14, 12, 14, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="CAMERA FOV ROTATION", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Entry(header, textvariable=self.angle_var, width=10).grid(row=0, column=1, sticky="w", padx=(0, 8), ipady=3)
        ttk.Label(header, text="deg", style="Muted.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 12))
        ttk.Button(header, text="Apply", style="Accent.TButton", command=self._apply).grid(row=0, column=3, sticky="e", padx=(0, 8))
        ttk.Button(header, text="Cancel", command=self.destroy).grid(row=0, column=4, sticky="e")

        body = ttk.Frame(self, style="App.TFrame", padding=(14, 0, 14, 14))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg="#05070a", highlightthickness=1, highlightbackground=colors["border"])
        self.canvas.grid(row=0, column=0, sticky="nsew")

        controls = ttk.Frame(body, style="App.TFrame")
        controls.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        controls.columnconfigure(2, weight=1)
        ttk.Button(controls, text="-5", command=lambda: self._nudge(-5.0)).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(controls, text="-1", command=lambda: self._nudge(-1.0)).grid(row=0, column=1, padx=(0, 8))
        scale = ttk.Scale(controls, from_=-180.0, to=180.0, variable=self.slider_var, command=self._on_slider)
        scale.grid(row=0, column=2, sticky="ew")
        ttk.Button(controls, text="+1", command=lambda: self._nudge(1.0)).grid(row=0, column=3, padx=(8, 6))
        ttk.Button(controls, text="+5", command=lambda: self._nudge(5.0)).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(controls, text="Reset 0", command=lambda: self._set_angle(0.0)).grid(row=0, column=5)
        ttk.Label(body, textvariable=self.status_var, style="Status.TLabel", padding=8).grid(row=2, column=0, sticky="ew", pady=(8, 0))

        self.angle_var.trace_add("write", self._on_angle_entry_changed)
        self.canvas.bind("<Configure>", lambda _event: self._render())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Return>", lambda _event: self._apply())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.after(50, self._render)

    def _current_angle(self) -> float:
        return normalize_camera_fov_rotation_deg(self.angle_var.get())

    def _set_angle(self, angle_deg: float) -> None:
        angle = normalize_camera_fov_rotation_deg(angle_deg)
        self._syncing_angle = True
        try:
            self.angle_var.set(f"{angle:g}")
            self.slider_var.set(angle)
        finally:
            self._syncing_angle = False
        self.status_var.set("")
        self._render()

    def _nudge(self, delta_deg: float) -> None:
        try:
            self._set_angle(self._current_angle() + delta_deg)
        except ValueError as exc:
            self.status_var.set(str(exc))

    def _on_slider(self, value: str) -> None:
        if self._syncing_angle:
            return
        self._set_angle(float(value))

    def _on_angle_entry_changed(self, *_args) -> None:
        if self._syncing_angle:
            return
        try:
            angle = self._current_angle()
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        self._syncing_angle = True
        try:
            self.slider_var.set(angle)
        finally:
            self._syncing_angle = False
        self.status_var.set("")
        self._render()

    def _render(self) -> None:
        if not self.canvas.winfo_exists():
            return
        canvas_w = max(1, self.canvas.winfo_width())
        canvas_h = max(1, self.canvas.winfo_height())
        scale = min(canvas_w / self.image_width, canvas_h / self.image_height)
        render_w = max(1, int(round(self.image_width * scale)))
        render_h = max(1, int(round(self.image_height * scale)))
        offset_x = (canvas_w - render_w) / 2.0
        offset_y = (canvas_h - render_h) / 2.0
        self.display_scale = scale
        self.display_offset = (offset_x, offset_y)

        if render_w != self.image_width or render_h != self.image_height:
            import cv2

            interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            image_rgb = cv2.resize(self.image_rgb, (render_w, render_h), interpolation=interpolation)
        else:
            image_rgb = self.image_rgb
        image_rgb = np.ascontiguousarray(image_rgb)
        header = f"P6 {render_w} {render_h} 255\n".encode("ascii")
        self.image_photo = tk.PhotoImage(data=header + image_rgb.tobytes(), format="PPM")

        self.canvas.delete("all")
        self.canvas.create_image(offset_x, offset_y, anchor="nw", image=self.image_photo)
        self._draw_overlay()

    def _draw_overlay(self) -> None:
        try:
            angle = self._current_angle()
            polygons = self.overlay_provider(angle, self.image_width, self.image_height)
        except Exception as exc:
            self.status_var.set(f"Overlay unavailable: {exc}")
            return
        offset_x, offset_y = self.display_offset
        scale = self.display_scale
        for polygon in polygons:
            if len(polygon) < 2:
                continue
            coords: list[float] = []
            for x, y in polygon:
                coords.extend((offset_x + x * scale, offset_y + y * scale))
            self.canvas.create_line(*(coords + coords[:2]), fill="#facc15", width=1.4, tags="gds")

        center_x = offset_x + self.image_width * scale / 2.0
        center_y = offset_y + self.image_height * scale / 2.0
        self.canvas.create_line(center_x - 18, center_y, center_x + 18, center_y, fill="#38bdf8", width=1)
        self.canvas.create_line(center_x, center_y - 18, center_x, center_y + 18, fill="#38bdf8", width=1)

    def _apply(self) -> None:
        try:
            self.result_angle_deg = self._current_angle()
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        self.destroy()
