"""Reproducible, hardware-free workflow plot benchmark and visual fixture.

Run: .venv/Scripts/python.exe scripts/benchmark_workflow_plot.py
Outputs are placed in the ignored .runtime/workflow-v2 directory.
"""
from __future__ import annotations

import json
import platform
import queue
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

from semi_auto_probe.workflow_formula import Curve
from semi_auto_probe.workflow_plot import PlotSlot, display_sample_indices
from semi_auto_probe.workflow_runtime import NodeCapture


def main():
    output = Path(__file__).resolve().parents[1] / ".runtime" / "workflow-v2"
    output.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(15)
    x = np.linspace(0, 10, 100_000)
    y = np.sin(x * 3) * 1e-6 + 2e-6
    # The UI receives bounded min/max envelopes, never the raw history.
    display_indices = display_sample_indices(y, 1200)
    family = {"Vg=1 V": Curve(tuple(x[display_indices]), tuple(y[display_indices])), "Vg=2 V": Curve(tuple(x[display_indices]), tuple(y[display_indices] * 1.7))}
    params = {f"slot{i}_title": title for i, title in enumerate(("Current vs time", "Mean current", "I-V family", "Resistance", "Transient response", "Feature map"), 1)}
    params.update(slot1_xlim="0,10", slot1_ylim="0,0.000006", slot3_xlim="0,10", slot3_ylim="0,0.000006", slot5_xlim="0,10", slot5_ylim="0,0.000006")
    plots = []
    for index in range(1, 7):
        figure = Figure(figsize=(4.8, 3.5), dpi=100)
        FigureCanvasAgg(figure)
        slot = PlotSlot(figure, params, index, (100, 100), "trace" if index % 2 else "number")
        plots.append(slot)
    values = rng.uniform(1e-6, 5e-6, (100, 100))
    states = np.full((100, 100), -1)
    values[70:] = np.nan
    for index, slot in enumerate(plots):
        if slot.mode == "trace":
            slot.curves(family)
        else:
            slot.heatmap(values, states, (70, 40))
        slot.render(slot.figure.canvas)
    timings = {"three_curve_update_and_render_ms": [], "three_heatmap_update_and_render_ms": []}
    for _ in range(12):
        for mode, key in (("trace", "three_curve_update_and_render_ms"), ("number", "three_heatmap_update_and_render_ms")):
            start = time.perf_counter()
            for slot in plots:
                if slot.mode != mode:
                    continue
                if mode == "trace":
                    slot.curves(family)
                else:
                    slot.heatmap(values, states, (70, 40))
                slot.render(slot.figure.canvas)
            timings[key].append((time.perf_counter() - start) * 1000)
    events = queue.Queue()
    capture = NodeCapture("current", "constant_current", "bench", SimpleNamespace(name="P", row=0, col=0), events, True)
    start = time.perf_counter()
    for index in range(100_000):
        capture.sample({"elapsed_s": index * .001, "voltage_v": 1., "current_a": float(y[index])})
    capture.publish(force=True)
    acquisition_ms = (time.perf_counter() - start) * 1000
    summary = {key: {"median": float(np.median(samples)), "p95": float(np.percentile(samples, 95))} for key, samples in timings.items()}
    summary.update(python=sys.version.split()[0], platform=platform.platform(), backend="Matplotlib Agg",
                   curve_points=100_000, display_points_per_curve=len(display_indices), curves_per_live_subplot=2, mapping_cells=10_000,
                   capture_100k_samples_ms=acquisition_ms, pending_display_notifications=events.qsize(),
                   raw_samples_retained=len(capture.series["Current"]["x"]))
    (output / "benchmark.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    # Contact sheet from actual renderer outputs, for visual QA of the six cells.
    from PIL import Image
    sheet = Image.new("RGB", (1440, 700), "white")
    for index, slot in enumerate(plots):
        path = output / f"subplot-{index+1}.png"
        slot.export(path)
        with Image.open(path) as tile:
            sheet.paste(tile.convert("RGB"), ((index % 3) * 480, (index // 3) * 350))
    sheet.save(output / "six-panel-preview.png")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
