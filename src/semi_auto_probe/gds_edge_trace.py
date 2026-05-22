from __future__ import annotations

import importlib.util
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable, Iterable

from .gds_stage_mapper import AffineCoordinateMapper, GDSLayoutModel, GDSShape


EDGE_TRACE_MISSING_GDSTK = "gdstk is required for EdgeTrace planning. Please install it with: pip install gdstk"


@dataclass(frozen=True)
class EdgeTracePathPoint:
    u: float
    v: float
    stage_x_um: float | None = None
    stage_y_um: float | None = None
    surface_z_um: float | None = None
    scratch_z_um: float | None = None
    travel_z_um: float | None = None

    @property
    def gds(self) -> tuple[float, float]:
        return self.u, self.v

    @property
    def stage_xy_um(self) -> tuple[float, float] | None:
        if self.stage_x_um is None or self.stage_y_um is None:
            return None
        return self.stage_x_um, self.stage_y_um


@dataclass(frozen=True)
class EdgeTracePolyline:
    index: int
    points: tuple[EdgeTracePathPoint, ...]
    length_um: float
    closed: bool = False

    @property
    def start(self) -> EdgeTracePathPoint:
        return self.points[0]

    @property
    def end(self) -> EdgeTracePathPoint:
        return self.points[-1]


@dataclass(frozen=True)
class EdgeTraceMotionSegment:
    kind: str
    polyline_index: int
    points: tuple[EdgeTracePathPoint, ...]
    dashed: bool
    length_um: float


@dataclass(frozen=True)
class EdgeTracePlan:
    layer: tuple[int, int]
    work_bounds: tuple[float, float, float, float]
    offset_um: float
    max_step_um: float
    min_segment_um: float
    scratch_depth_um: float
    lift_height_um: float
    polylines: tuple[EdgeTracePolyline, ...]
    segments: tuple[EdgeTraceMotionSegment, ...]
    total_scratch_length_um: float
    total_travel_length_um: float
    warnings: tuple[str, ...] = ()

    @property
    def point_count(self) -> int:
        return sum(len(polyline.points) for polyline in self.polylines)


def build_edge_trace_plan(
    model: GDSLayoutModel,
    layer: tuple[int, int],
    work_bounds: tuple[float, float, float, float],
    *,
    offset_um: float = 0.0,
    max_step_um: float = 10.0,
    min_segment_um: float = 2.0,
    scratch_depth_um: float = 0.0,
    lift_height_um: float = 100.0,
    mapper: AffineCoordinateMapper | None = None,
    focus_z_at_stage_um: Callable[[float, float], float | None] | None = None,
    current_gds: tuple[float, float] | None = None,
) -> EdgeTracePlan:
    normalized_bounds = normalize_work_bounds(work_bounds)
    max_step = _positive_float(max_step_um, "Max step")
    min_segment = _nonnegative_float(min_segment_um, "Min segment")
    offset = _finite_float(offset_um, "Offset")
    scratch_depth = _nonnegative_float(scratch_depth_um, "Scratch depth")
    lift_height = _nonnegative_float(lift_height_um, "Safe distance")

    raw_polylines, warnings = extract_edge_trace_polylines_with_warnings(
        model,
        layer,
        normalized_bounds,
        offset_um=offset,
        max_step_um=max_step,
        min_segment_um=min_segment,
        current_gds=current_gds,
    )
    enriched_polylines = tuple(
        EdgeTracePolyline(
            index=index,
            points=tuple(
                _enrich_point(
                    point,
                    mapper=mapper,
                    focus_z_at_stage_um=focus_z_at_stage_um,
                    scratch_depth_um=scratch_depth,
                    lift_height_um=lift_height,
                )
                for point in polyline.points
            ),
            length_um=polyline.length_um,
            closed=polyline.closed,
        )
        for index, polyline in enumerate(raw_polylines, start=1)
    )
    segments = _motion_segments(enriched_polylines, current_gds=current_gds)
    total_scratch = sum(polyline.length_um for polyline in enriched_polylines)
    total_travel = sum(segment.length_um for segment in segments if segment.kind == "travel")
    return EdgeTracePlan(
        layer=layer,
        work_bounds=normalized_bounds,
        offset_um=offset,
        max_step_um=max_step,
        min_segment_um=min_segment,
        scratch_depth_um=scratch_depth,
        lift_height_um=lift_height,
        polylines=enriched_polylines,
        segments=segments,
        total_scratch_length_um=total_scratch,
        total_travel_length_um=total_travel,
        warnings=tuple(warnings),
    )


def extract_edge_trace_polylines(
    model: GDSLayoutModel,
    layer: tuple[int, int],
    work_bounds: tuple[float, float, float, float],
    *,
    offset_um: float = 0.0,
    max_step_um: float = 10.0,
    min_segment_um: float = 2.0,
    current_gds: tuple[float, float] | None = None,
) -> tuple[EdgeTracePolyline, ...]:
    polylines, _warnings = extract_edge_trace_polylines_with_warnings(
        model,
        layer,
        work_bounds,
        offset_um=offset_um,
        max_step_um=max_step_um,
        min_segment_um=min_segment_um,
        current_gds=current_gds,
    )
    return polylines


def extract_edge_trace_polylines_with_warnings(
    model: GDSLayoutModel,
    layer: tuple[int, int],
    work_bounds: tuple[float, float, float, float],
    *,
    offset_um: float = 0.0,
    max_step_um: float = 10.0,
    min_segment_um: float = 2.0,
    current_gds: tuple[float, float] | None = None,
) -> tuple[tuple[EdgeTracePolyline, ...], list[str]]:
    if importlib.util.find_spec("gdstk") is None:
        raise RuntimeError(EDGE_TRACE_MISSING_GDSTK)
    import gdstk  # type: ignore[import-not-found]

    bounds = normalize_work_bounds(work_bounds)
    max_step = _positive_float(max_step_um, "Max step")
    min_segment = _nonnegative_float(min_segment_um, "Min segment")
    offset = _finite_float(offset_um, "Offset")

    source_shapes = [shape for shape in model.shapes if shape.layer_key == layer]
    if not source_shapes:
        return (), [f"No geometry found on layer L{layer[0]} / D{layer[1]}."]

    polygons = [gdstk.Polygon(shape.points, layer=shape.layer, datatype=shape.datatype) for shape in source_shapes]
    union_polygons = gdstk.boolean(polygons, [], "or", layer=layer[0], datatype=layer[1])
    if offset:
        union_polygons = gdstk.offset(
            union_polygons,
            offset,
            join="miter",
            tolerance=2,
            use_union=True,
            layer=layer[0],
            datatype=layer[1],
        )
    if not union_polygons:
        return (), [f"Offset {offset:g} um removed all geometry on L{layer[0]} / D{layer[1]}."]

    boundary_edges = _extract_true_boundary_edges(tuple(_shape_from_polygon(poly) for poly in union_polygons))
    clipped_edges = []
    for start, end in boundary_edges:
        clipped = clip_segment_to_bounds(start, end, bounds)
        if clipped is None:
            continue
        clipped_start, clipped_end = clipped
        if _distance(clipped_start, clipped_end) > 1e-9:
            clipped_edges.append((clipped_start, clipped_end))

    chains = _chain_directed_edges(clipped_edges)
    polylines: list[EdgeTracePolyline] = []
    for chain in chains:
        cleaned = _dedupe_consecutive(chain)
        if len(cleaned) < 2:
            continue
        length = polyline_length(cleaned)
        if length < min_segment:
            continue
        sampled = resample_polyline(cleaned, max_step)
        if len(sampled) < 2:
            continue
        polylines.append(
            EdgeTracePolyline(
                index=len(polylines) + 1,
                points=tuple(EdgeTracePathPoint(u, v) for u, v in sampled),
                length_um=polyline_length(sampled),
                closed=_same_point(sampled[0], sampled[-1]),
            )
        )

    if current_gds is not None and polylines:
        polylines = _nearest_neighbor_order(polylines, current_gds)
    warnings: list[str] = []
    if not polylines:
        warnings.append("No edge segments remain inside the selected work range.")
    return tuple(
        EdgeTracePolyline(index=index, points=polyline.points, length_um=polyline.length_um, closed=polyline.closed)
        for index, polyline in enumerate(polylines, start=1)
    ), warnings


def normalize_work_bounds(bounds: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    if len(bounds) != 4:
        raise ValueError("Work range must have four coordinates.")
    min_u, min_v, max_u, max_v = (_finite_float(value, "Work range") for value in bounds)
    left, right = sorted((min_u, max_u))
    bottom, top = sorted((min_v, max_v))
    if right - left <= 0 or top - bottom <= 0:
        raise ValueError("Work range must have non-zero width and height.")
    return left, bottom, right, top


def clip_segment_to_bounds(
    start: tuple[float, float],
    end: tuple[float, float],
    bounds: tuple[float, float, float, float],
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    min_x, min_y, max_x, max_y = bounds
    x0, y0 = start
    x1, y1 = end
    dx = x1 - x0
    dy = y1 - y0
    p = (-dx, dx, -dy, dy)
    q = (x0 - min_x, max_x - x0, y0 - min_y, max_y - y0)
    u1 = 0.0
    u2 = 1.0
    for pi, qi in zip(p, q):
        if abs(pi) <= 1e-15:
            if qi < 0:
                return None
            continue
        ratio = qi / pi
        if pi < 0:
            if ratio > u2:
                return None
            u1 = max(u1, ratio)
        else:
            if ratio < u1:
                return None
            u2 = min(u2, ratio)
    return (x0 + u1 * dx, y0 + u1 * dy), (x0 + u2 * dx, y0 + u2 * dy)


def resample_polyline(points: Iterable[tuple[float, float]], max_step_um: float) -> tuple[tuple[float, float], ...]:
    max_step = _positive_float(max_step_um, "Max step")
    source = _dedupe_consecutive(tuple(points))
    if len(source) <= 1:
        return tuple(source)
    sampled: list[tuple[float, float]] = [source[0]]
    for start, end in zip(source, source[1:]):
        length = _distance(start, end)
        if length <= 1e-12:
            continue
        step_count = max(1, int(math.ceil(length / max_step)))
        for index in range(1, step_count + 1):
            fraction = index / step_count
            sampled.append((start[0] + (end[0] - start[0]) * fraction, start[1] + (end[1] - start[1]) * fraction))
    return tuple(_dedupe_consecutive(sampled))


def polyline_length(points: Iterable[tuple[float, float]]) -> float:
    source = tuple(points)
    return sum(_distance(start, end) for start, end in zip(source, source[1:]))


def _extract_true_boundary_edges(shapes: tuple[GDSShape, ...]) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
    directed_edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
    undirected_counts: dict[tuple[tuple[int, int], tuple[int, int]], int] = defaultdict(int)
    for shape in shapes:
        points = _dedupe_consecutive(shape.points)
        if len(points) < 3:
            continue
        if _same_point(points[0], points[-1]):
            points = points[:-1]
        for start, end in zip(points, points[1:] + points[:1]):
            if _same_point(start, end):
                continue
            directed_edges.append((start, end))
            key = _undirected_key(start, end)
            undirected_counts[key] += 1

    return tuple((start, end) for start, end in directed_edges if undirected_counts[_undirected_key(start, end)] == 1)


def _chain_directed_edges(edges: list[tuple[tuple[float, float], tuple[float, float]]]) -> list[tuple[tuple[float, float], ...]]:
    if not edges:
        return []
    starts: dict[tuple[int, int], deque[int]] = defaultdict(deque)
    in_degree: dict[tuple[int, int], int] = defaultdict(int)
    out_degree: dict[tuple[int, int], int] = defaultdict(int)
    for index, (start, end) in enumerate(edges):
        start_key = _point_key(start)
        end_key = _point_key(end)
        starts[start_key].append(index)
        out_degree[start_key] += 1
        in_degree[end_key] += 1

    used: set[int] = set()
    chains: list[tuple[tuple[float, float], ...]] = []

    open_start_indices = [
        index
        for index, (start, _end) in enumerate(edges)
        if in_degree[_point_key(start)] == 0 or out_degree[_point_key(start)] != 1
    ]
    all_start_indices = open_start_indices + [index for index in range(len(edges)) if index not in open_start_indices]

    for first_index in all_start_indices:
        if first_index in used:
            continue
        start, end = edges[first_index]
        chain = [start, end]
        used.add(first_index)
        current_key = _point_key(end)
        while True:
            next_index = _next_unused_edge(starts[current_key], used)
            if next_index is None:
                break
            _next_start, next_end = edges[next_index]
            used.add(next_index)
            chain.append(next_end)
            current_key = _point_key(next_end)
            if current_key == _point_key(chain[0]):
                break
        chains.append(tuple(chain))
    return chains


def _next_unused_edge(candidates: deque[int], used: set[int]) -> int | None:
    while candidates and candidates[0] in used:
        candidates.popleft()
    return candidates[0] if candidates else None


def _nearest_neighbor_order(polylines: list[EdgeTracePolyline], current_gds: tuple[float, float]) -> list[EdgeTracePolyline]:
    remaining = list(polylines)
    ordered: list[EdgeTracePolyline] = []
    cursor = current_gds
    while remaining:
        best_index = 0
        best_reverse = False
        best_distance = float("inf")
        best_rotated: EdgeTracePolyline | None = None
        for index, polyline in enumerate(remaining):
            candidate = _rotate_closed_polyline_to_nearest(polyline, cursor) if polyline.closed else polyline
            start_distance = _distance(cursor, candidate.start.gds)
            end_distance = _distance(cursor, candidate.end.gds)
            reverse = not candidate.closed and end_distance < start_distance
            distance = min(start_distance, end_distance)
            if distance < best_distance:
                best_index = index
                best_reverse = reverse
                best_distance = distance
                best_rotated = candidate
        selected = best_rotated or remaining[best_index]
        del remaining[best_index]
        if best_reverse:
            selected = _reverse_polyline(selected)
        ordered.append(selected)
        cursor = selected.end.gds
    return ordered


def _rotate_closed_polyline_to_nearest(polyline: EdgeTracePolyline, point: tuple[float, float]) -> EdgeTracePolyline:
    if not polyline.closed or len(polyline.points) <= 3:
        return polyline
    points = list(polyline.points[:-1])
    nearest_index = min(range(len(points)), key=lambda index: _distance(point, points[index].gds))
    rotated = points[nearest_index:] + points[:nearest_index] + [points[nearest_index]]
    return EdgeTracePolyline(index=polyline.index, points=tuple(rotated), length_um=polyline.length_um, closed=True)


def _reverse_polyline(polyline: EdgeTracePolyline) -> EdgeTracePolyline:
    return EdgeTracePolyline(
        index=polyline.index,
        points=tuple(reversed(polyline.points)),
        length_um=polyline.length_um,
        closed=polyline.closed,
    )


def _enrich_point(
    point: EdgeTracePathPoint,
    *,
    mapper: AffineCoordinateMapper | None,
    focus_z_at_stage_um: Callable[[float, float], float | None] | None,
    scratch_depth_um: float,
    lift_height_um: float,
) -> EdgeTracePathPoint:
    if mapper is None:
        return point
    stage_x, stage_y = mapper.gds_to_stage(point.u, point.v)
    surface_z = focus_z_at_stage_um(stage_x, stage_y) if focus_z_at_stage_um is not None else None
    scratch_z = travel_z = None
    if surface_z is not None:
        scratch_z = surface_z + scratch_depth_um
        travel_z = surface_z - lift_height_um
    return EdgeTracePathPoint(
        u=point.u,
        v=point.v,
        stage_x_um=stage_x,
        stage_y_um=stage_y,
        surface_z_um=surface_z,
        scratch_z_um=scratch_z,
        travel_z_um=travel_z,
    )


def _motion_segments(
    polylines: tuple[EdgeTracePolyline, ...],
    *,
    current_gds: tuple[float, float] | None,
) -> tuple[EdgeTraceMotionSegment, ...]:
    segments: list[EdgeTraceMotionSegment] = []
    previous_point: EdgeTracePathPoint | None = None
    if current_gds is not None:
        previous_point = EdgeTracePathPoint(float(current_gds[0]), float(current_gds[1]))

    for polyline in polylines:
        if previous_point is not None and not _same_point(previous_point.gds, polyline.start.gds):
            segments.append(
                EdgeTraceMotionSegment(
                    kind="travel",
                    polyline_index=polyline.index,
                    points=(previous_point, polyline.start),
                    dashed=True,
                    length_um=_distance(previous_point.gds, polyline.start.gds),
                )
            )
        segments.append(
            EdgeTraceMotionSegment(
                kind="plunge",
                polyline_index=polyline.index,
                points=(polyline.start,),
                dashed=False,
                length_um=0.0,
            )
        )
        segments.append(
            EdgeTraceMotionSegment(
                kind="scratch",
                polyline_index=polyline.index,
                points=polyline.points,
                dashed=False,
                length_um=polyline.length_um,
            )
        )
        segments.append(
            EdgeTraceMotionSegment(
                kind="retract",
                polyline_index=polyline.index,
                points=(polyline.end,),
                dashed=False,
                length_um=0.0,
            )
        )
        previous_point = polyline.end
    return tuple(segments)


def _shape_from_polygon(polygon: object) -> GDSShape:
    points = tuple((float(point[0]), float(point[1])) for point in getattr(polygon, "points", ()))
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return GDSShape(
        points=points,
        layer=int(getattr(polygon, "layer", 0)),
        datatype=int(getattr(polygon, "datatype", 0)),
        bbox=(min(xs), min(ys), max(xs), max(ys)),
    )


def _dedupe_consecutive(points: Iterable[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    cleaned: list[tuple[float, float]] = []
    for point in points:
        normalized = (float(point[0]), float(point[1]))
        if cleaned and _same_point(cleaned[-1], normalized):
            continue
        cleaned.append(normalized)
    return tuple(cleaned)


def _same_point(first: tuple[float, float], second: tuple[float, float], *, tolerance: float = 1e-9) -> bool:
    return abs(first[0] - second[0]) <= tolerance and abs(first[1] - second[1]) <= tolerance


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _point_key(point: tuple[float, float], *, scale: float = 1e9) -> tuple[int, int]:
    return int(round(point[0] * scale)), int(round(point[1] * scale))


def _undirected_key(first: tuple[float, float], second: tuple[float, float]) -> tuple[tuple[int, int], tuple[int, int]]:
    key_first = _point_key(first)
    key_second = _point_key(second)
    return (key_first, key_second) if key_first <= key_second else (key_second, key_first)


def _finite_float(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite.")
    return number


def _positive_float(value: object, label: str) -> float:
    number = _finite_float(value, label)
    if number <= 0:
        raise ValueError(f"{label} must be positive.")
    return number


def _nonnegative_float(value: object, label: str) -> float:
    number = _finite_float(value, label)
    if number < 0:
        raise ValueError(f"{label} must be zero or positive.")
    return number
