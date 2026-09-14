"""Stage-coordinate planning and acquisition for wire-bond current mapping.

This module has no camera, layout, focus-plane or Tk dependencies. Motion is
injected by the application and must move XY only, at the captured fixed Z.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import sqlite3
import statistics
import threading
import time
import zlib
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Callable

import numpy as np

from .keithley2450 import ConstantVoltageCurrentConfig, IVSweepSample, Keithley2450IVRunner
from .measurement_workflow import AutoTestFlowStep
from .virtual_mask import fit_pixel_coordinate_affine, load_grayscale, parse_binding_points, sample_grayscale


@dataclass(frozen=True)
class MappingSettings:
    x_a_um: float = 0.0
    y_a_um: float = 0.0
    x_b_um: float = 100.0
    y_b_um: float = 100.0
    step_x_um: float = 10.0
    step_y_um: float = 10.0
    pattern: str = "serpentine"
    settle_s: float = 0.2
    bias_settle_s: float = 0.2
    measurement: ConstantVoltageCurrentConfig = field(
        default_factory=lambda: ConstantVoltageCurrentConfig(nplc=1.0)
    )
    # None identifies legacy fixed-bias files. New scans snapshot the shared editor.
    measurement_flow: tuple[AutoTestFlowStep, ...] | None = None
    measurement_steps: tuple[str, ...] = ()
    # Store successful per-point workflow outputs in one transactional database
    # instead of leaving thousands of small files and directories on disk.
    compact_storage: bool = True
    # Snapshot of the active LayoutMap transform. Virtual-mask frame=auto uses
    # UV when this is present and stage XY otherwise.
    gds_mapper: dict | None = None
    region_shape: str = "rectangle"
    region_method: str = ""
    region_control_points: tuple[tuple[float, float], ...] = ()
    polygon_vertices: tuple[tuple[float, float], ...] = ()

    def normalized(self) -> MappingSettings:
        names = ("x_a_um", "y_a_um", "x_b_um", "y_b_um", "step_x_um", "step_y_um", "settle_s", "bias_settle_s")
        values = {name: float(getattr(self, name)) for name in names}
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError("Mapping coordinates, steps and waits must be finite.")
        if values["step_x_um"] <= 0 or values["step_y_um"] <= 0:
            raise ValueError("Mapping steps must be positive.")
        if values["settle_s"] < 0 or values["bias_settle_s"] < 0:
            raise ValueError("Mapping waits cannot be negative.")
        if self.pattern not in {"serpentine", "raster"}:
            raise ValueError("Mapping pattern must be serpentine or raster.")
        if self.region_shape not in {"rectangle", "circle", "polygon"}:
            raise ValueError("Unknown mapping region.")
        controls = tuple((float(x),float(y)) for x,y in self.region_control_points)
        if controls:
            from .mapping_region import region_geometry
            bounds = region_geometry(self.region_shape, self.region_method, controls)
            values.update(zip(("x_a_um","y_a_um","x_b_um","y_b_um"), bounds))
        vertices = controls if controls and self.region_shape=="polygon" else tuple((float(x), float(y)) for x, y in self.polygon_vertices)
        if self.region_shape == "polygon":
            if len(set(vertices)) < 3 or not all(math.isfinite(v) for pt in vertices for v in pt):
                raise ValueError("Polygon needs three finite, distinct vertices.")
            area = sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(vertices, vertices[1:]+vertices[:1]))
            if abs(area) < 1e-12:
                raise ValueError("Polygon must have nonzero area.")
            values.update(x_a_um=min(x for x,y in vertices), x_b_um=max(x for x,y in vertices),
                          y_a_um=min(y for x,y in vertices), y_b_um=max(y for x,y in vertices))
        if self.region_shape == "circle" and min(abs(values["x_b_um"]-values["x_a_um"]), abs(values["y_b_um"]-values["y_a_um"])) <= 0:
            raise ValueError("Circle requires a positive diameter.")
        measurement = self.measurement
        if self.measurement_flow is None:
            count = float(measurement.sample_count)
            if not math.isfinite(count) or not count.is_integer():
                raise ValueError("Mapping sample count must be an integer.")
            measurement = replace(measurement.normalized(), output_off_after=True)
        flow = self.measurement_flow
        if flow is not None:
            if any(step.type_id == "wobb_test" for step in flow):
                raise ValueError("WobbTest moves the probes and is incompatible with fixed-Z Wire-bond Test. Remove it from the workflow before scanning.")
            flow = tuple(AutoTestFlowStep(step.type_id, dict(step.params)) for step in flow)
        return replace(self, **values, region_control_points=controls, polygon_vertices=vertices, measurement=measurement, measurement_flow=flow, measurement_steps=tuple(self.measurement_steps))


@dataclass(frozen=True)
class MappingPoint:
    row: int
    col: int
    order: int
    stage_x_um: float
    stage_y_um: float
    x_pulses: int
    y_pulses: int
    mask_pixel_x: int | None = None
    mask_pixel_y: int | None = None
    normalized_intensity: float | None = None
    target_power_mw: float | None = None
    layout_u: float | None = None
    layout_v: float | None = None

    @property
    def name(self) -> str:
        return f"mapping_r{self.row + 1:03d}_c{self.col + 1:03d}"


@dataclass(frozen=True)
class MappingPlan:
    settings: MappingSettings
    points: tuple[MappingPoint, ...]
    xs: tuple[float, ...]
    ys: tuple[float, ...]
    um_per_pulse: tuple[float, float, float]


@dataclass(frozen=True)
class MappingPointResult:
    point: MappingPoint
    readback_pulses: tuple[int, int, int] | None
    timestamp: str
    samples: tuple[IVSweepSample, ...]
    mean_current_a: float | None
    std_current_a: float | None
    status: str
    error: str = ""
    workflow_data: dict = field(default_factory=dict)


def _axis_points(a: float, b: float, step: float, scale: float) -> tuple[tuple[float, ...], tuple[int, ...]]:
    lo, hi = sorted((Decimal(str(a)), Decimal(str(b))))
    stride = Decimal(str(step))
    count = int(((hi - lo) / stride).to_integral_value(rounding=ROUND_FLOOR)) + 1
    if count > 500:
        raise ValueError("Mapping is limited to 500 points per axis; increase the step.")
    positions = tuple(float(lo + index * stride) for index in range(count))
    pulses = tuple(round(value / scale) for value in positions)
    if len(set(pulses)) != len(pulses):
        raise ValueError("Mapping step produces duplicate motor positions; increase the step.")
    return positions, pulses


def region_contains(settings, x, y):
    """Boundary-inclusive selection in logical stage coordinates."""
    if settings.region_shape == "rectangle":
        return True
    if settings.region_shape == "circle":
        cx, cy = (settings.x_a_um+settings.x_b_um)/2, (settings.y_a_um+settings.y_b_um)/2
        radius = min(abs(settings.x_b_um-settings.x_a_um), abs(settings.y_b_um-settings.y_a_um))/2
        return (x-cx)**2+(y-cy)**2 <= radius**2 + 1e-9
    inside = False
    vertices = settings.polygon_vertices
    for (ax,ay),(bx,by) in zip(vertices, vertices[1:]+vertices[:1]):
        cross = (x-ax)*(by-ay)-(y-ay)*(bx-ax)
        if abs(cross) <= 1e-9 and min(ax,bx)-1e-9 <= x <= max(ax,bx)+1e-9 and min(ay,by)-1e-9 <= y <= max(ay,by)+1e-9:
            return True
        if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside = not inside
    return inside


def generate_mapping_plan(settings: MappingSettings, um_per_pulse: tuple[float, float, float]) -> MappingPlan:
    settings = settings.normalized()
    scales = tuple(float(value) for value in um_per_pulse)
    if len(scales) != 3 or not all(math.isfinite(value) and value > 0 for value in scales):
        raise ValueError("XYZ motor calibration must be finite and positive.")
    mask_nodes = [step for step in settings.measurement_flow or () if step.type_id == "virtual_mask"]
    if mask_nodes:
        if len(mask_nodes) > 1:
            raise ValueError("Use only one Virtual Mask Illumination node per mapping workflow.")
        return _generate_virtual_mask_plan(settings, scales, mask_nodes[0])
    xs, xp = _axis_points(settings.x_a_um, settings.x_b_um, settings.step_x_um, scales[0])
    ys, yp = _axis_points(settings.y_a_um, settings.y_b_um, settings.step_y_um, scales[1])
    points = []
    for row, y in enumerate(ys):
        columns = range(len(xs) - 1, -1, -1) if settings.pattern == "serpentine" and row % 2 else range(len(xs))
        for col in columns:
            if not region_contains(settings, xs[col], y):
                continue
            points.append(MappingPoint(row, col, len(points) + 1, xs[col], y, xp[col], yp[row]))
    if not points:
        raise ValueError("Region contains no scan points; reduce the step.")
    return MappingPlan(settings, tuple(points), xs, ys, scales)


def _generate_virtual_mask_plan(settings: MappingSettings, scales: tuple[float, float, float], step: AutoTestFlowStep) -> MappingPlan:
    from .gds_stage_mapper import AffineCoordinateMapper

    params = step.params
    image = load_grayscale(params.get("image_path", ""))
    bindings = parse_binding_points(params.get("bindings", ""))
    pixel_to_coordinate = fit_pixel_coordinate_affine(bindings)
    coordinate_to_pixel = np.linalg.inv(pixel_to_coordinate[:, 1:3])
    requested_frame = str(params.get("coordinate_frame", "auto")).strip().lower()
    if requested_frame not in {"auto", "uv", "xy"}:
        raise ValueError("Virtual-mask coordinate frame must be auto, uv or xy.")
    mapper = AffineCoordinateMapper.from_dict(settings.gds_mapper) if settings.gds_mapper else None
    frame = ("uv" if mapper is not None else "xy") if requested_frame == "auto" else requested_frame
    if frame == "uv" and mapper is None:
        raise ValueError("Virtual-mask UV binding requires a fitted LayoutMap mapping.")
    power_min = float(params.get("black_power_mw", params.get("power_min_mw", "0")))
    power_max = float(params.get("white_power_mw", params.get("power_max_mw", "5")))
    invert_intensity = str(params.get("invert_intensity", "false")).strip().lower() in {"1", "true", "yes", "on"}
    if not all(math.isfinite(value) for value in (power_min, power_max)) or power_min < 0 or power_max < power_min:
        raise ValueError("Virtual-mask power range must be finite, nonnegative and increasing.")
    # The main Mapping page exclusively owns scan bounds, step and path. The
    # image binding is only queried to obtain gray/power at each stage point.
    xs, xp = _axis_points(settings.x_a_um, settings.x_b_um, settings.step_x_um, scales[0])
    ys, yp = _axis_points(settings.y_a_um, settings.y_b_um, settings.step_y_um, scales[1])
    points: list[MappingPoint] = []
    for row, stage_y in enumerate(ys):
        columns = range(len(xs) - 1, -1, -1) if settings.pattern == "serpentine" and row % 2 else range(len(xs))
        for col in columns:
            stage_x = xs[col]
            if not region_contains(settings, stage_x, stage_y):
                continue
            coordinate_a, coordinate_b = mapper.stage_to_gds(stage_x, stage_y) if mapper is not None and frame == "uv" else (stage_x, stage_y)
            pixel = coordinate_to_pixel @ (
                np.array([coordinate_a, coordinate_b], dtype=float) - pixel_to_coordinate[:, 0]
            )
            pixel_x, pixel_y = float(pixel[0]), float(pixel[1])
            intensity = sample_grayscale(image, pixel_x, pixel_y)
            if invert_intensity:
                intensity = 1.0 - intensity
            points.append(MappingPoint(
                row, col, len(points) + 1, stage_x, stage_y, xp[col], yp[row],
                round(pixel_x), round(pixel_y), intensity,
                power_min + intensity * (power_max - power_min),
                coordinate_a if frame == "uv" else None,
                coordinate_b if frame == "uv" else None,
            ))
    if not points:
        raise ValueError("Region contains no scan points; reduce the step.")
    return MappingPlan(settings, tuple(points), xs, ys, scales)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_point_result(point: MappingPoint, readback, samples, status: str, error: str = "") -> MappingPointResult:
    currents = [sample.current_a for sample in samples if math.isfinite(sample.current_a) and abs(sample.current_a) < 9e36]
    return MappingPointResult(
        point, readback, utc_now(), tuple(samples),
        statistics.fmean(currents) if currents else None,
        statistics.pstdev(currents) if currents else None, status, error,
    )


class MappingSession:
    """Append and flush each sample/point; atomically replace session metadata."""

    def __init__(self, directory: Path, plan: MappingPlan, fixed_z: int | None, *, motion: dict | None = None,
                 buffer_point_io: bool = False):
        self.directory = directory
        self.plan = plan
        self.metadata = {
            "format": "semi_auto_probe.mapping", "version": 2 if plan.settings.measurement_flow is not None else 1, "mode": "wire_bond_mapping",
            "quantity": "Workflow results" if plan.settings.measurement_flow is not None else "Total current under constant illumination", "coordinate_frame": "logical_stage_xy_um",
            "created_at": utc_now(), "status": "running", "completed_points": 0,
            "fixed_z_pulses": fixed_z, "fixed_z_um": fixed_z * plan.um_per_pulse[2] if fixed_z is not None else None,
            "um_per_pulse": plan.um_per_pulse, "motion": motion or {},
            "settings": asdict(plan.settings), "xs_um": plan.xs, "ys_um": plan.ys,
            "points": [asdict(point) for point in plan.points],
        }
        mask_step = next((step for step in plan.settings.measurement_flow or () if step.type_id == "virtual_mask"), None)
        if mask_step is not None:
            source = Path(str(mask_step.params.get("image_path", ""))).expanduser().resolve()
            suffix = source.suffix.lower() if source.suffix else ".img"
            snapshot = self.directory / f"virtual_mask_source{suffix}"
            shutil.copy2(source, snapshot)
            self.metadata["virtual_mask"] = {
                "source_image": snapshot.name,
                "source_path": str(source),
                "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                "coordinate_frame": str(mask_step.params.get("coordinate_frame", "auto")),
            }
        self.completed = 0
        self._buffer_point_io = buffer_point_io
        if plan.settings.measurement_flow is not None:
            for key in ("measurement", "bias_settle_s", "measurement_steps"):
                self.metadata["settings"].pop(key, None)
        self._files = []
        self._artifact_db = None
        try:
            if plan.settings.measurement_flow is not None and plan.settings.compact_storage:
                self.metadata["artifact_storage"] = {
                    "format": "sqlite-zlib", "path": "mapping_artifacts.sqlite3",
                    "successful_points_compacted": True,
                    "failed_points_retained": True,
                }
                self._artifact_db = sqlite3.connect(self.directory / "mapping_artifacts.sqlite3")
                self._artifact_db.execute("PRAGMA synchronous=FULL")
                self._artifact_db.execute("""
                    CREATE TABLE IF NOT EXISTS artifacts (
                        relative_path TEXT PRIMARY KEY,
                        point_order INTEGER NOT NULL,
                        row_index INTEGER NOT NULL,
                        col_index INTEGER NOT NULL,
                        original_size INTEGER NOT NULL,
                        compression TEXT NOT NULL,
                        content BLOB NOT NULL
                    )
                """)
                self._artifact_db.execute(
                    "CREATE INDEX IF NOT EXISTS artifacts_by_point ON artifacts(point_order)"
                )
                self._artifact_db.commit()
            self.save_metadata()
            self._sample_file, self._sample_writer = self._open_csv("samples.csv", [
                "row", "col", "order", "timestamp", "sample_index", "elapsed_s", "current_a", "voltage_v", "source_value", "raw",
                "readback_x_um", "readback_y_um", "readback_z_um", "workflow_node",
            ])
            self._result_file, self._result_writer = self._open_csv("mapping.csv", [
                "row", "col", "order", "target_x_um", "target_y_um", "target_x_pulses", "target_y_pulses",
                "mask_pixel_x", "mask_pixel_y", "normalized_intensity", "target_power_mw", "layout_u", "layout_v",
                "readback_x_um", "readback_y_um", "readback_z_um", "timestamp", "sample_count",
                "mean_current_a", "std_current_a", "status", "error", "workflow_data",
            ])
        except Exception:
            self.close()
            raise

    def _open_csv(self, name, fields):
        stream = (self.directory / name).open("x", newline="", encoding="utf-8")
        self._files.append(stream)
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        stream.flush()
        return stream, writer

    def save_metadata(self):
        temporary = self.directory / "mapping.json.tmp"
        temporary.write_text(json.dumps(self.metadata, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(self.directory / "mapping.json")

    def _coordinates(self, readback):
        return dict(zip(("readback_x_um", "readback_y_um", "readback_z_um"),
                        (value * scale for value, scale in zip(readback, self.plan.um_per_pulse)))) if readback else {}

    def sample(self, point, readback, sample, node_id=""):
        self._sample_writer.writerow({
            "row": point.row, "col": point.col, "order": point.order, "timestamp": utc_now(),
            "sample_index": sample.index, "elapsed_s": sample.elapsed_s,
            "current_a": sample.current_a, "voltage_v": sample.voltage_v,
            "source_value": sample.source_value, "raw": sample.raw, **self._coordinates(readback),
            "workflow_node": node_id,
        })
        if not self._buffer_point_io:
            self._sample_file.flush()

    def result(self, result: MappingPointResult):
        point = result.point
        self._result_writer.writerow({
            "row": point.row, "col": point.col, "order": point.order,
            "target_x_um": point.stage_x_um, "target_y_um": point.stage_y_um,
            "target_x_pulses": point.x_pulses, "target_y_pulses": point.y_pulses,
            "mask_pixel_x": point.mask_pixel_x, "mask_pixel_y": point.mask_pixel_y,
            "normalized_intensity": point.normalized_intensity, "target_power_mw": point.target_power_mw,
            "layout_u": point.layout_u, "layout_v": point.layout_v,
            **self._coordinates(result.readback_pulses), "timestamp": result.timestamp,
            "sample_count": len(result.samples), "mean_current_a": result.mean_current_a,
            "std_current_a": result.std_current_a, "status": result.status, "error": result.error,
            "workflow_data": json.dumps(result.workflow_data, allow_nan=False),
        })
        self._sample_file.flush()
        self._result_file.flush()
        if result.status == "done":
            self._compact_point_artifacts(result)
            self.completed += 1

    def _compact_point_artifacts(self, result: MappingPointResult) -> None:
        """Commit one successful point's files, then remove its temporary tree."""
        if self._artifact_db is None:
            return
        relative = result.workflow_data.get("directory")
        if not relative:
            return
        point_dir = (self.directory / str(relative)).resolve()
        points_root = (self.directory / "points").resolve()
        try:
            point_dir.relative_to(points_root)
        except ValueError as exc:
            raise ValueError(f"Mapping point directory is outside the session: {point_dir}") from exc
        if not point_dir.is_dir():
            return
        records = []
        for path in sorted(point_dir.rglob("*")):
            if path.is_file():
                payload = path.read_bytes()
                records.append((
                    (Path(str(relative)) / path.relative_to(point_dir)).as_posix(), result.point.order,
                    result.point.row, result.point.col, len(payload), "zlib",
                    sqlite3.Binary(zlib.compress(payload)),
                ))
        with self._artifact_db:
            self._artifact_db.executemany(
                """INSERT OR REPLACE INTO artifacts
                   (relative_path, point_order, row_index, col_index, original_size, compression, content)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                records,
            )
        # The database transaction is durable before source files are removed.
        shutil.rmtree(point_dir)
        try:
            points_root.rmdir()
        except OSError:
            pass

    def finish(self, status: str, error: str):
        self.metadata.update(status=status, error=error, completed_points=self.completed, finished_at=utc_now())
        self.save_metadata()

    def close(self):
        errors = []
        if self._artifact_db is not None:
            try:
                self._artifact_db.close()
            except Exception as exc:
                errors.append(str(exc))
            self._artifact_db = None
        for stream in self._files:
            try:
                stream.close()
            except Exception as exc:
                errors.append(str(exc))
        self._files.clear()
        if errors:
            raise OSError("; ".join(errors))


def run_mapping(
    plan: MappingPlan, session: MappingSession, *,
    move_xy: Callable[[MappingPoint], tuple[int, int, int]],
    stop_event: threading.Event, emit: Callable[..., None],
    runner: Keithley2450IVRunner | None = None,
    measure_point: Callable | None = None,
    cleanup: Callable[[], None] | None = None,
) -> tuple[str, str]:
    """Serialized XY/wait/workflow, with a legacy fixed-bias file adapter."""
    legacy = plan.settings.measurement_flow is None
    runner = (runner or Keithley2450IVRunner()) if legacy else None
    status, error = "completed", ""
    started = time.monotonic()
    try:
        if not legacy and measure_point is None:
            raise ValueError("A shared measurement workflow executor is required.")
        if legacy and not stop_event.is_set():
            runner.begin_constant_voltage_current(plan.settings.measurement)
            stop_event.wait(plan.settings.bias_settle_s)
        for point in plan.points:
            if stop_event.is_set():
                break
            samples: list[IVSweepSample] = []
            readback = None
            point_error = ""
            point_status = "incomplete"
            workflow_data = {}
            emit("mapping_current", point)
            try:
                readback = move_xy(point)
                if not stop_event.wait(plan.settings.settle_s):
                    def on_sample(sample, node_id=""):
                        samples.append(sample)
                        session.sample(point, readback, sample, node_id)
                        if not math.isfinite(sample.current_a) or abs(sample.current_a) >= 9e36:
                            raise ValueError("Invalid/overrange current reading.")
                    if legacy:
                        runner.sample_constant_voltage_current(stop_requested=stop_event.is_set, on_sample=on_sample)
                    else:
                        measure_point(point, readback, on_sample, workflow_data)
                    if not stop_event.is_set():
                        if legacy and len(samples) != plan.settings.measurement.sample_count:
                            raise RuntimeError("Instrument returned an incomplete sample batch.")
                        point_status = "done"
            except Exception as exc:
                point_status = "incomplete" if stop_event.is_set() else "failed"
                point_error = str(exc)
                raise
            finally:
                result = make_point_result(point, readback, samples, point_status, point_error)
                if not legacy:
                    # Never average samples from different nodes/biases into one current.
                    result = replace(result, mean_current_a=None, std_current_a=None, workflow_data=workflow_data)
                # Deliver acquired samples to the UI even if writing the point fails.
                emit("mapping_result", result, time.monotonic() - started)
                session.result(result)
        if stop_event.is_set():
            status = "stopped"
    except Exception as exc:
        # A stop request must not hide an instrument, write or cleanup failure.
        status, error = "failed", str(exc)
    finally:
        try:
            if runner is not None:
                runner.end_constant_voltage_current(strict=True)
            if cleanup is not None:
                cleanup()
        except Exception as exc:
            status, error = "failed", f"{error}; Output cleanup failed: {exc}".strip("; ")
        try:
            session.close()
        except Exception as exc:
            status, error = "failed", f"{error}; File close failed: {exc}".strip("; ")
        try:
            session.finish(status, error)
        except Exception as exc:
            status, error = "failed", f"{error}; Metadata save failed: {exc}".strip("; ")
    return status, error


def load_mapping(directory: Path) -> tuple[MappingPlan, dict, dict[tuple[int, int], MappingPointResult]]:
    metadata = json.loads((directory / "mapping.json").read_text(encoding="utf-8"))
    if metadata.get("format") != "semi_auto_probe.mapping" or metadata.get("version") not in {1, 2}:
        raise ValueError("Unsupported mapping file format/version.")
    settings = dict(metadata["settings"])
    if "measurement" in settings:
        settings["measurement"] = ConstantVoltageCurrentConfig(**settings["measurement"])
    if settings.get("measurement_flow") is not None:
        settings["measurement_flow"] = tuple(AutoTestFlowStep(**step) for step in settings["measurement_flow"])
    normalized_settings = MappingSettings(**settings).normalized()
    if any(step.type_id == "virtual_mask" for step in normalized_settings.measurement_flow or ()) and metadata.get("points"):
        plan = MappingPlan(
            normalized_settings,
            tuple(MappingPoint(**point) for point in metadata["points"]),
            tuple(metadata["xs_um"]), tuple(metadata["ys_um"]), tuple(metadata["um_per_pulse"]),
        )
    else:
        plan = generate_mapping_plan(normalized_settings, tuple(metadata["um_per_pulse"]))
    by_key = {(p.row, p.col): p for p in plan.points}
    results = {}
    if not (directory / "mapping.csv").exists():
        return plan, metadata, results
    with (directory / "mapping.csv").open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            key = int(row["row"]), int(row["col"])
            readback = tuple(round(float(row[f"readback_{axis}_um"]) / scale) for axis, scale in zip("xyz", plan.um_per_pulse)) if row["readback_x_um"] else None
            results[key] = MappingPointResult(
                by_key[key], readback, row["timestamp"], (),
                float(row["mean_current_a"]) if row["mean_current_a"] else None,
                float(row["std_current_a"]) if row["std_current_a"] else None,
                row["status"], row["error"], json.loads(row.get("workflow_data") or "{}"),
            )
    return plan, metadata, results


def load_point_samples(directory: Path, point: MappingPoint) -> list[dict[str, str]]:
    # Load lazily: large maps should not keep millions of raw samples in RAM.
    with (directory / "samples.csv").open(newline="", encoding="utf-8") as stream:
        return [row for row in csv.DictReader(stream) if row["order"] == str(point.order)]


def read_mapping_artifact(directory: Path, relative_path: str) -> bytes:
    """Read an artifact from a legacy loose file or the compact session store."""
    loose_path = directory / relative_path
    if loose_path.is_file():
        return loose_path.read_bytes()
    database = directory / "mapping_artifacts.sqlite3"
    if not database.is_file():
        raise FileNotFoundError(relative_path)
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT compression, content FROM artifacts WHERE relative_path = ?",
            (Path(relative_path).as_posix(),),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise FileNotFoundError(relative_path)
    compression, payload = row
    return zlib.decompress(payload) if compression == "zlib" else bytes(payload)


def collect_workflow_data(directory: Path, session_root: Path, data: dict) -> None:
    """Index node artifacts and numeric result metrics without averaging unlike tests."""
    data.update(directory=str(directory.relative_to(session_root)), artifacts=[], metrics={})

    def numeric_values(value, prefix):
        if isinstance(value, dict):
            for key, child in value.items():
                yield from numeric_values(child, f"{prefix}.{key}" if prefix else key)
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            yield prefix, value

    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        data["artifacts"].append(str(path.relative_to(session_root)))
        if path.suffix != ".json" or path.name == "execution.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        # The same node/metric must address every spatial cell, independent of
        # point-specific artifact filenames used by the instrument exporters.
        source = path.relative_to(directory).with_suffix("").as_posix().replace(directory.name, "point")
        for section in ("statistics", "metrics", "analysis"):
            for key, value in numeric_values(payload.get(section, {}), section):
                data["metrics"][f"{source}: {key}"] = value
