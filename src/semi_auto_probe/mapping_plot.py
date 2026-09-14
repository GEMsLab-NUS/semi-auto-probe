"""Shared matrix rendering for the live view and standalone PNG export."""
from __future__ import annotations

import numpy as np
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch, Rectangle

from .mapping import MappingPlan


STATUS_COLORS = (("#dbe2e8", "Pending"), ("#86efac", "Done / no scalar"),
                 ("#fbbf24", "Incomplete"), ("#ef4444", "Failed"))


def current_unit(values: np.ndarray) -> tuple[float, str]:
    valid = np.abs(values[np.isfinite(values)])
    peak = float(valid.max()) if valid.size else 0.0
    for scale, label in ((1.0, "A"), (1e-3, "mA"), (1e-6, "µA")):
        if peak >= scale:
            return scale, label
    return 1e-9, "nA"


def draw_mapping_figure(figure, plan: MappingPlan, values: np.ndarray, states: np.ndarray, *, preview: bool = False, metric: str | None = None):
    figure.clear()
    ax = figure.add_subplot(111)
    _draw_mapping_axis(figure, ax, plan, values, states, preview=preview, metric=metric)
    figure.subplots_adjust(left=0.12, right=0.9, bottom=0.22, top=0.91)
    return ax


def draw_temt6000_figure(figure, plan, current, irradiance, states, *, wavelength_nm=650, preview=False, node_id="TEMT6000"):
    """Both arrays use the same node and spatial indices, with independent scales."""
    figure.clear()
    axes = figure.subplots(1, 2)
    _draw_mapping_axis(figure, axes[0], plan, current, states, preview=preview,
                       title="TEMT6000 total current", compact=True)
    _draw_mapping_axis(figure, axes[1], plan, irradiance, states, preview=preview,
                       title=f"Estimated irradiance · {wavelength_nm:g} nm", compact=True,
                       metric="Estimated irradiance", unit_override=(10., "mW/cm²"))
    figure.suptitle(f"TEMT6000 · {node_id}", fontsize=10)
    figure.legend(handles=[Patch(color=color, label=label) for color, label in STATUS_COLORS],
                  loc="lower center", bbox_to_anchor=(.5, .10), ncol=4, fontsize=8)
    figure.text(.5, .015, "Datasheet estimate + user correction; monochromatic, linear response.\n"
                "Sensor-area average; focused spots require calibration. Negative net current: no power estimate.",
                ha="center", va="bottom", fontsize=8)
    figure.subplots_adjust(left=.08, right=.98, bottom=.22, top=.87, wspace=.3)
    return tuple(axes)


def draw_workflow_heatmap_figure(
    figure, plan, primary, secondary, states, *, primary_label, secondary_label, preview=False, node_id="Heatmap"
):
    """Render both connected feature ports with the same spatial geometry."""
    figure.clear()
    axes = figure.subplots(1, 2)
    _draw_mapping_axis(
        figure, axes[0], plan, primary, states, preview=preview,
        title=primary_label, metric=primary_label, compact=True,
    )
    _draw_mapping_axis(
        figure, axes[1], plan, secondary, states, preview=preview,
        title=secondary_label, metric=secondary_label, compact=True,
    )
    figure.suptitle(f"Workflow heatmaps - {node_id}", fontsize=10)
    figure.legend(
        handles=[Patch(color=color, label=label) for color, label in STATUS_COLORS],
        loc="lower center", bbox_to_anchor=(.5, .04), ncol=4, fontsize=8,
    )
    figure.subplots_adjust(left=.08, right=.98, bottom=.16, top=.88, wspace=.3)
    return tuple(axes)


def draw_laser_calibration_figure(figure, records, calibration):
    """Draw measured laser I-V and derived electrical P-V calibration curves."""
    figure.clear()
    axes = figure.subplots(1, 2)
    modes = {
        "current_limit": ("Current-limit points", "o", "#22c55e"),
        "voltage_limit": ("Voltage-limit readback", "s", "#f59e0b"),
    }
    for mode, (label, marker, color) in modes.items():
        selected = [record for record in records if record.get("regulation_mode") == mode]
        if not selected:
            continue
        voltages = [float(record["measured_voltage_v"]) for record in selected]
        currents = [float(record["effective_current_a"]) * 1000 for record in selected]
        powers = [voltage * current for voltage, current in zip(voltages, currents)]
        axes[0].scatter(voltages, currents, label=label, marker=marker, color=color, s=24)
        axes[1].scatter(voltages, powers, label=label, marker=marker, color=color, s=24)
    if calibration:
        currents_a, voltages = zip(*calibration)
        currents_ma = np.asarray(currents_a, dtype=float) * 1000
        voltages = np.asarray(voltages, dtype=float)
        order = np.argsort(voltages)
        axes[0].plot(voltages[order], currents_ma[order], color="#38bdf8", linewidth=1.5, label="Monotonic interpolation")
        axes[1].plot(
            voltages[order], (voltages * currents_ma)[order],
            color="#c084fc", linewidth=1.5, label="V x I",
        )
    axes[0].set(xlabel="Laser voltage (V)", ylabel="Laser current (mA)", title="Laser I-V calibration")
    axes[1].set(xlabel="Laser voltage (V)", ylabel="Electrical power (mW)", title="Laser P-V calibration")
    for ax in axes:
        ax.grid(True, alpha=.25)
        if ax.lines or ax.collections:
            ax.legend(fontsize=8)
    figure.tight_layout()
    return tuple(axes)


def _draw_mapping_axis(figure, ax, plan, values, states, *, preview=False, metric=None,
                       title=None, unit_override=None, compact=False):
    # Only completed, finite measurements contribute to the color scale.
    measured = np.where((states == 1) & np.isfinite(values), values, np.nan)
    scale, unit = current_unit(measured)
    is_current_metric = bool(metric) and (metric.endswith("_a") or "(A)" in metric)
    if metric is not None and not is_current_metric:
        scale, unit = 1.0, ("Ω" if metric.endswith("_ohm") else "V" if metric.endswith("_v") else "")
    if metric is not None and metric.endswith("_w_m2"):
        scale, unit = 10., "mW/cm²"
    if unit_override is not None:
        scale, unit = unit_override
    mask_points = [point for point in plan.points if point.normalized_intensity is not None]
    if mask_points:
        rows, cols = len(plan.ys), len(plan.xs)
        # Region clipping removes scan points, not the underlying stage grid.
        # pcolormesh requires finite geometry even for hidden cells.
        edge_x, edge_y = np.meshgrid(
            _axis_cell_edges(plan.xs, plan.settings.step_x_um),
            _axis_cell_edges(plan.ys, plan.settings.step_y_um),
        )
        selected = np.zeros((rows, cols), dtype=bool)
        plotted = np.full((rows, cols), np.nan)
        for point in mask_points:
            selected[point.row, point.col] = True
            plotted[point.row, point.col] = point.target_power_mw if preview else measured[point.row, point.col]
        valid = np.isfinite(plotted)
        if preview:
            display_metric, display_unit = "Virtual-mask target power", "mW"
        else:
            display_metric, display_unit = metric or "Mean total current", unit
            plotted = plotted / scale
        finite = plotted[valid]
        vmin, vmax = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 1.0)
        if vmin == vmax:
            vmax = vmin + (abs(vmin) * .05 or 1.0)
        image = ax.pcolormesh(
            edge_x, edge_y, np.ma.masked_invalid(plotted),
            cmap="inferno", vmin=vmin, vmax=vmax, shading="flat",
        )
        overlay = np.ma.masked_where(valid | ~selected, states)
        ax.pcolormesh(
            edge_x, edge_y, overlay,
            cmap=ListedColormap([color for color, _label in STATUS_COLORS]),
            vmin=-0.5, vmax=3.5, shading="flat",
        )
        if metric != "Workflow completion" or preview:
            figure.colorbar(image, ax=ax, label=_metric_colorbar_label(display_metric, display_unit), **(
                {"orientation": "horizontal", "pad": .18, "fraction": .06} if compact else {"pad": .02}
            ))
        if preview and len(mask_points) <= 2500:
            ax.plot([point.stage_x_um for point in mask_points], [point.stage_y_um for point in mask_points],
                    color="#475569", linewidth=.45, alpha=.65)
            for point, label, offset in ((mask_points[0], "Start", 8), (mask_points[-1], "End", -15)):
                ax.annotate(label, (point.stage_x_um, point.stage_y_um), xytext=(5, offset), textcoords="offset points", fontsize=9)
        ax.set(xlabel="Stage X (µm)", ylabel="Stage Y (µm)", title=title or "Virtual-mask mapping")
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(True, alpha=.18)
        if not compact:
            ax.legend(handles=[Patch(color=color, label=label) for color, label in STATUS_COLORS],
                      loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=4, fontsize=8)
        return ax
    dx, dy = plan.settings.step_x_um, plan.settings.step_y_um
    extent = (plan.xs[0] - dx / 2, plan.xs[-1] + dx / 2, plan.ys[0] - dy / 2, plan.ys[-1] + dy / 2)
    finite = measured[np.isfinite(measured)] / scale
    vmin, vmax = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 1.0)
    if vmin == vmax:
        # A single point / constant map needs a nonzero range for normalization.
        padding = abs(vmin) * 0.05 if vmin else 1.0
        vmin, vmax = (max(0., vmin - padding) if vmin >= 0 else vmin - padding), vmax + padding
    image = ax.imshow(np.ma.masked_invalid(measured / scale), origin="lower", extent=extent,
                      interpolation="nearest", aspect="equal", cmap="inferno", vmin=vmin, vmax=vmax)
    # 0=pending, 1=done, 2=incomplete, 3=failed. Missing data is never zero.
    overlay = np.ma.masked_where((states == 1) & np.isfinite(values), states)
    ax.imshow(overlay, origin="lower", extent=extent, interpolation="nearest", aspect="equal",
              cmap=ListedColormap(["#dbe2e8", "#86efac", "#fbbf24", "#ef4444"]), vmin=-0.5, vmax=3.5)
    if metric != "Workflow completion":
        figure.colorbar(image, ax=ax, label=(_metric_colorbar_label(metric, unit) if metric else f"Mean total current ({unit})"),
                        **({"orientation": "horizontal", "pad": .18, "fraction": .06} if compact else {"pad": .02}))
    ax.set(xlabel="Stage X (µm)", ylabel="Stage Y (µm)", title=title or ("Workflow mapping" if metric else "Total current under constant illumination"))
    if compact:
        ax.title.set_fontsize(10)
        ax.tick_params(labelsize=8)
    settings = plan.settings
    xlo, xhi = sorted((settings.x_a_um, settings.x_b_um))
    ylo, yhi = sorted((settings.y_a_um, settings.y_b_um))
    ax.add_patch(Rectangle((xlo, ylo), xhi - xlo, yhi - ylo, fill=False, edgecolor="#334155", linestyle="--"))
    if preview:
        if len(plan.points) <= 2500:
            ax.plot([p.stage_x_um for p in plan.points], [p.stage_y_um for p in plan.points], ".-", color="#475569", markersize=3, linewidth=0.6)
        for row in range(0, len(plan.ys), max(1, len(plan.ys) // 20)):
            reverse = settings.pattern == "serpentine" and row % 2
            start, end = (plan.xs[-1], plan.xs[0]) if reverse else (plan.xs[0], plan.xs[-1])
            ax.annotate("", xy=(end, plan.ys[row]), xytext=(start, plan.ys[row]),
                        arrowprops={"arrowstyle": "->", "color": "#475569", "lw": 0.8})
        for point, label, offset in ((plan.points[0], "Start", 8), (plan.points[-1], "End", -15)):
            ax.annotate(label, (point.stage_x_um, point.stage_y_um), xytext=(5, offset), textcoords="offset points", fontsize=9)
    ax.set_xlim(min(extent[0], xlo - dx / 2), max(extent[1], xhi + dx / 2))
    ax.set_ylim(min(extent[2], ylo - dy / 2), max(extent[3], yhi + dy / 2))
    if not compact:
        ax.legend(handles=[Patch(color=color, label=label) for color, label in STATUS_COLORS],
                  loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=4, fontsize=8)
    return ax


def _axis_cell_edges(centers, step: float) -> np.ndarray:
    """Cell boundaries for a complete stage axis, including single-point scans."""
    centers = np.asarray(centers, dtype=float)
    if centers.size == 1:
        return np.array([centers[0] - step / 2, centers[0] + step / 2])
    return np.concatenate((
        [centers[0] - (centers[1] - centers[0]) / 2],
        (centers[:-1] + centers[1:]) / 2,
        [centers[-1] + (centers[-1] - centers[-2]) / 2],
    ))


def _metric_colorbar_label(metric: str, unit: str) -> str:
    return metric if not unit or f"({unit})" in metric else f"{metric} ({unit})"
