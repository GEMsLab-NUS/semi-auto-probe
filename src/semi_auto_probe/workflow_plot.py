"""Incremental workflow display shared by AutoTest and Mapping."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

from .workflow_formula import Curve, InvalidValue

LAYOUTS = {"1x1": (1, 1), "1x2": (1, 2), "2x2": (2, 2), "2x3": (2, 3)}


def current_position_marker():
    """Return a square crosshair whose guides extend beyond the box."""
    from matplotlib.path import Path as MarkerPath

    move, line = MarkerPath.MOVETO, MarkerPath.LINETO
    return MarkerPath(
        [
            (-0.42, -0.42), (0.42, -0.42), (0.42, 0.42),
            (-0.42, 0.42), (-0.42, -0.42),
            (-1.0, 0.0), (1.0, 0.0),
            (0.0, -1.0), (0.0, 1.0),
        ],
        [move, line, line, line, line, move, line, move, line],
    )


def limits(text, scale="linear"):
    if text == "auto":
        return None
    parts = tuple(float(v.strip()) for v in text.split(","))
    if len(parts) != 2 or not all(math.isfinite(v) for v in parts) or parts[0] >= parts[1]:
        raise ValueError("Range must be auto or finite min,max with min < max")
    if scale == "log" and parts[0] <= 0:
        raise ValueError("Logarithmic ranges must be positive")
    return parts


def validate_panel(step):
    p = step.params
    if p.get("layout", "1x2") not in LAYOUTS:
        raise ValueError("Plot layout must be 1x1, 1x2, 2x2 or 2x3")
    for i in range(1, 7):
        for axis in "xyc":
            scale = p.get(f"slot{i}_{axis}scale", "linear")
            if scale not in {"linear", "log"}:
                raise ValueError("Scale must be linear or log")
            limits(p.get(f"slot{i}_{axis}lim", "auto"), scale)


def display_sample_indices(y, pixels):
    """Preserve extrema and their acquisition order in each pixel bucket."""
    y = np.asarray(y, dtype=float)
    buckets = max(1, int(pixels))
    if len(y) <= buckets * 2:
        return np.arange(len(y))
    width = math.ceil(len(y) / buckets)
    count = math.ceil(len(y) / width)
    padded = np.full(count * width, np.nan)
    padded[:len(y)] = y
    blocks = padded.reshape(count, width)
    valid = np.isfinite(blocks)
    offsets = np.arange(count) * width
    low = offsets + np.argmin(np.where(valid, blocks, np.inf), axis=1)
    high = offsets + np.argmax(np.where(valid, blocks, -np.inf), axis=1)
    indices = np.unique(np.concatenate(([0], low, high, [len(y)-1])))
    return indices[indices < len(y)]


class PlotSlot:
    """Persistent artists; can be exercised using the Agg canvas without Tk."""

    def __init__(self, figure, params, index, shape, mode):
        from matplotlib.colors import Normalize, LogNorm
        self.figure, self.params, self.index, self.mode = figure, params, index, mode
        self.axes = figure.add_subplot(111)
        self.lines = {}
        self.background = None
        self.render_signature = None
        self.legend_signature = None
        self.rendering = False
        self.auto_initialized = set()
        self.masked = False
        self.image = self.colorbar = self.marker = self.state_image = None
        self.axes.set_title(self.get("title", "") or f"Plot {index}")
        for axis in "xy":
            scale = self.get(axis + "scale", "linear")
            getattr(self.axes, "set_" + axis + "scale")(scale)
            bound = limits(self.get(axis + "lim", "auto"), scale)
            if bound:
                getattr(self.axes, "set_" + axis + "lim")(bound)
        if mode == "number":
            from matplotlib.colors import ListedColormap
            bound = limits(self.get("clim", "auto"), self.get("cscale", "linear"))
            norm = LogNorm(*(bound or (1, 10))) if self.get("cscale", "linear") == "log" else Normalize(*(bound or (0, 1)))
            self.image = self.axes.imshow(np.full(shape, np.nan), origin="lower", norm=norm, interpolation="nearest", aspect="auto")
            self.colorbar = figure.colorbar(self.image, ax=self.axes)
            from .workflow_ports import metric_unit
            self.colorbar.set_label(params.get(f"_input_slot{index}_unit") or metric_unit(params.get(f"_input_slot{index}_output", "")))
            self.state_image = self.axes.imshow(np.ma.masked_all(shape), origin="lower", aspect="auto", interpolation="nearest",
                cmap=ListedColormap(["#d1d5db", "#ef4444", "#f59e0b", "#64748b"]), vmin=0, vmax=3, alpha=.6)
            self.marker, = self.axes.plot(
                [], [], marker=current_position_marker(), markersize=18,
                markerfacecolor="none", markeredgecolor="#00e5ff",
                markeredgewidth=1.6, linestyle="none", zorder=5,
            )
            self.axes.set_xlabel("Mapping column (0-based)")
            self.axes.set_ylabel("Mapping row (0-based)")
        figure.subplots_adjust(left=.19, right=.94, bottom=.2, top=.85)
        # imshow changes data limits, so apply configured bounds afterwards.
        for axis in "xy":
            bound = limits(self.get(axis + "lim", "auto"), self.get(axis + "scale", "linear"))
            if bound:
                getattr(self.axes, "set_" + axis + "lim")(bound)

    def get(self, key, default):
        return self.params.get(f"slot{self.index}_{key}", default)

    def curves(self, curves):
        selected = self.get("series", "all")
        active = set()
        self.masked = False
        for label, curve in curves.items():
            if selected != "all" and label != selected:
                continue
            active.add(label)
            if label not in self.lines:
                self.lines[label], = self.axes.plot([], [], label=label, linewidth=1)
            x, y = np.asarray(curve.x, dtype=float), np.asarray(curve.y, dtype=float)
            mask = ~np.isfinite(x) | ~np.isfinite(y)
            if self.get("xscale", "linear") == "log":
                mask |= x <= 0
            if self.get("yscale", "linear") == "log":
                mask |= y <= 0
            self.masked |= bool(mask.any())
            y = np.where(mask, np.nan, y)
            indices = display_sample_indices(y, self.axes.bbox.width)
            self.lines[label].set_data(x[indices], y[indices])
            self.axes.set_xlabel(f"{ {'s': 'Time', 'V': 'Voltage', 'A': 'Current'}.get(curve.x_unit, 'X')} ({curve.x_unit})")
            self.axes.set_ylabel(f"{ {'A': 'Current', 'V': 'Voltage', 'Ω': 'Resistance'}.get(curve.y_unit, 'Y')} ({curve.y_unit})")
        for label in set(self.lines) - active:
            self.lines.pop(label).remove()
        self.axes.relim()
        if not active:
            self.auto_initialized.clear()
        # Expand only when actual data reaches an edge. Comparing autoscale's
        # padded limits would invalidate the background on every new sample.
        for axis in "xy":
            if self.get(axis + "lim", "auto") != "auto" or not active:
                continue
            low, high = getattr(self.axes.dataLim, "interval" + axis)
            if not np.isfinite([low, high]).all():
                continue
            old_low, old_high = getattr(self.axes, "get_" + axis + "lim")()
            if axis in self.auto_initialized and old_low <= low and high <= old_high:
                continue
            if self.get(axis + "scale", "linear") == "log":
                if low <= 0:
                    continue
                margin = max((math.log(high) - math.log(low)) * .15, .1)
                bound = low * math.exp(-margin), high * math.exp(margin)
            else:
                margin = max((high - low) * .15, abs(low) * .05, 1e-12)
                bound = low - margin, high + margin
            getattr(self.axes, "set_" + axis + "lim")(bound)
            self.auto_initialized.add(axis)
        signature = tuple(sorted(active))
        if signature != self.legend_signature:
            if active:
                self.axes.legend(fontsize=7, loc="upper right")
            elif self.axes.get_legend():
                self.axes.get_legend().remove()
            self.legend_signature = signature

    def heatmap(self, values, states, current):
        data = np.asarray(values, dtype=float).copy()
        if self.get("cscale", "linear") == "log":
            data[data <= 0] = np.nan
        self.masked = bool(np.any(np.isfinite(values) & ~np.isfinite(data)))
        self.image.set_data(np.ma.masked_invalid(data))
        finite = data[np.isfinite(data)]
        if finite.size and self.get("clim", "auto") == "auto":
            low, high = float(finite.min()), float(finite.max())
            if low == high:
                low, high = (low / 1.1, high * 1.1) if self.get("cscale", "linear") == "log" else (low - max(abs(low) * .01, 1e-12), high + max(abs(high) * .01, 1e-12))
            self.image.set_clim(low, high)
        self.state_image.set_data(np.ma.masked_where(states < 0, states))
        self.marker.set_data([] if current is None else [current[1]], [] if current is None else [current[0]])

    def render(self, canvas):
        """Redraw axes only when geometry/ranges/legend change, otherwise blit."""
        artists = list(self.lines.values()) if self.mode != "number" else [self.image, self.state_image, self.marker]
        signature = (tuple(self.figure.bbox.bounds), self.axes.get_xlim(), self.axes.get_ylim(),
                     self.axes.get_xlabel(), self.axes.get_ylabel(), self.legend_signature,
                     self.image.get_clim() if self.image is not None else None)
        if not getattr(canvas, "supports_blit", False):
            canvas.draw_idle()
            return
        for artist in artists:
            artist.set_animated(True)
        if self.background is None or signature != self.render_signature:
            self.rendering = True
            try:
                canvas.draw()
            finally:
                self.rendering = False
            self.background = canvas.copy_from_bbox(self.axes.bbox)
            self.render_signature = signature
        else:
            canvas.restore_region(self.background)
        for artist in artists:
            self.axes.draw_artist(artist)
        if self.axes.get_legend() is not None:
            self.axes.draw_artist(self.axes.get_legend())
        canvas.blit(self.axes.bbox)

    def export(self, path):
        artists = list(self.lines.values()) if self.mode != "number" else [self.image, self.state_image, self.marker]
        for artist in artists:
            artist.set_animated(False)
        self.figure.savefig(path)
        self.background = None


class WorkflowPlotPanel:
    def __init__(self, parent, step, shape):
        import tkinter as tk
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        self.window = tk.Toplevel(parent)
        self.window.title("Measurement Workflow — Plot Panel")
        self.window.geometry("1200x760")
        self.window.protocol("WM_DELETE_WINDOW", self.window.withdraw)
        self.step, self.shape = step, shape
        self.slots, self.canvases, self.values, self.states = {}, {}, {}, {}
        self.live, self.history, self.dirty = {}, {}, set()
        self.current = None
        self.last_heatmap = 0.0
        self.finished = False
        self.job = None
        rows, cols = LAYOUTS[step.params.get("layout", "1x2")]
        for i in range(1, rows * cols + 1):
            figure = Figure(figsize=(4, 3), dpi=90)
            mode = step.params.get(f"_input_slot{i}_type", "trace")
            self.slots[i] = PlotSlot(figure, step.params, i, shape, mode)
            canvas = FigureCanvasTkAgg(figure, master=self.window)
            canvas.get_tk_widget().grid(row=(i-1)//cols, column=(i-1)%cols, sticky="nsew")
            canvas.mpl_connect("button_press_event", self.inspect)
            canvas.mpl_connect("draw_event", lambda event, index=i: self.canvas_drawn(index))
            self.canvases[i] = canvas
            self.values[i] = np.full(shape, np.nan)
            self.states[i] = np.full(shape, -1)
            self.dirty.add(i)
        for i in range(rows):
            self.window.rowconfigure(i, weight=1)
        for i in range(cols):
            self.window.columnconfigure(i, weight=1)
        self.status = tk.StringVar(value="Waiting for measurement")
        tk.Label(self.window, textvariable=self.status).grid(row=rows, column=0, columnspan=cols, sticky="ew")
        self.job = self.window.after(50, self.tick)

    def canvas_drawn(self, index):
        # Tk redraws on resize/expose. Animated artists must be restored even
        # after acquisition has ended and the regular timer has stopped.
        if self.slots[index].rendering:
            return
        self.slots[index].background = None
        self.dirty.add(index)
        if self.finished and self.job is None:
            self.job = self.window.after_idle(lambda: self.tick(force=True))

    def point_started(self, row, col):
        self.current = (row, col)
        self.live.clear()
        for slot in self.slots.values():
            if slot.mode != "number":
                slot.auto_initialized.clear()
        self.dirty.update(self.slots)
        self.status.set(f"Running row {row}, column {col} · gray: missing · slate: skipped · red: failed · amber: incomplete")

    def curves_updated(self, node_id, output, curves):
        self.live[node_id, output] = curves
        for i, slot in self.slots.items():
            if self.step.params.get(f"_input_slot{i}_source") == node_id and self.step.params.get(f"_input_slot{i}_output") == output:
                self.dirty.add(i)

    def point_finished(self, row, col, values, status, history_path, details=None):
        self.history[row, col] = history_path
        for i, slot in self.slots.items():
            if slot.mode != "number":
                continue
            value = values.get(str(i))
            valid = isinstance(value, (int, float)) and math.isfinite(value) and status == "done"
            self.values[i][row, col] = value if valid else np.nan
            self.states[i][row, col] = -1 if valid else 1 if status == "failed" else 2 if status == "incomplete" else 3 if (details or {}).get(str(i), {}).get("skipped") else 0
            self.dirty.add(i)
        self.current = None

    def inspect(self, event):
        if self.current is not None or event.xdata is None or event.ydata is None:
            return
        if not any(slot.mode == "number" and event.inaxes is slot.axes for slot in self.slots.values()):
            return
        path = self.history.get((int(round(event.ydata)), int(round(event.xdata))))
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.live.clear()
            self.dirty.update(i for i, slot in self.slots.items() if slot.mode != "number")
            for node_id, outputs in payload["curves"].items():
                for output, family in outputs.items():
                    self.curves_updated(node_id, output, {label: Curve(**curve) for label, curve in family.items()})
            reasons = [d["reason"] for d in payload.get("details", {}).values() if d.get("reason")]
            self.status.set(f"Saved point: {payload.get('point')} · {payload.get('status')}" + (" · " + "; ".join(reasons) if reasons else ""))
            self.tick(force=True)
        except (OSError, ValueError) as exc:
            self.status.set(f"Cannot read saved curves: {exc}")

    def tick(self, force=False):
        if force and self.job is not None:
            self.window.after_cancel(self.job)
        now = time.monotonic()
        heatmap_due = force or now - self.last_heatmap >= .2
        for i in tuple(self.dirty):
            slot = self.slots[i]
            if slot.mode == "number":
                if not heatmap_due:
                    continue
                slot.heatmap(self.values[i], self.states[i], self.current)
            else:
                key = (self.step.params.get(f"_input_slot{i}_source"), self.step.params.get(f"_input_slot{i}_output"))
                slot.curves(self.live.get(key, {}))
            slot.render(self.canvases[i])
            self.dirty.discard(i)
        if heatmap_due:
            self.last_heatmap = now
        if any(slot.masked for slot in self.slots.values()):
            self.status.set("Non-finite or non-positive log values are hidden; raw data is unchanged.")
        delay = max(10, 50 - int((time.monotonic() - now) * 1000))
        self.job = None if self.finished else self.window.after(delay, self.tick)

    def finish(self):
        self.finished = True
        self.current = None
        self.dirty.update(self.slots)
        self.tick(force=True)

    def close(self):
        if self.job is not None:
            self.window.after_cancel(self.job)
            self.job = None
        for canvas in self.canvases.values():
            job = getattr(canvas, "_idle_draw_id", None)
            if job is not None:
                canvas.get_tk_widget().after_cancel(job)
                canvas._idle_draw_id = None
        self.window.destroy()
