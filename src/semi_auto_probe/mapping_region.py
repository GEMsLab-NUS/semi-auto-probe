"""Geometry used by the mapping region form and canvas."""
from __future__ import annotations

import math


def region_geometry(shape, method, points):
    points = tuple((float(x), float(y)) for x, y in points)
    if not all(math.isfinite(v) for point in points for v in point):
        raise ValueError("Region coordinates must be finite.")
    expected = 3 if method == "three_points" else 2
    if shape == "polygon":
        if len(set(points)) < 3:
            raise ValueError("Add at least three distinct polygon vertices.")
        xs, ys = zip(*points)
        return min(xs), min(ys), max(xs), max(ys)
    if len(points) != expected:
        raise ValueError(f"This construction needs {expected} points.")
    (ax, ay), (bx, by) = points[:2]
    if shape == "rectangle":
        if method == "center_corner":
            dx, dy = abs(bx-ax), abs(by-ay)
            bounds = ax-dx, ay-dy, ax+dx, ay+dy
        elif method == "two_corners":
            bounds = min(ax,bx), min(ay,by), max(ax,bx), max(ay,by)
        else:
            raise ValueError("Choose two corners or center + corner.")
        if bounds[0] == bounds[2] or bounds[1] == bounds[3]:
            raise ValueError("Rectangle must have positive width and height.")
        return bounds
    if shape != "circle":
        raise ValueError("Unknown region shape.")
    if method == "three_points":
        # Translate to A to avoid cancellation for large stage offsets.
        (cx, cy) = points[2]
        ux, uy, vx, vy = bx-ax, by-ay, cx-ax, cy-ay
        determinant = 2*(ux*vy-uy*vx)
        size = max(math.hypot(ux,uy), math.hypot(vx,vy))
        if size == 0 or abs(determinant) <= 1e-10*size*size:
            raise ValueError("Three circle points must be distinct and not collinear.")
        u2, v2 = ux*ux+uy*uy, vx*vx+vy*vy
        ox, oy = (u2*vy-v2*uy)/determinant, (ux*v2-vx*u2)/determinant
        cx, cy, radius = ax+ox, ay+oy, math.hypot(ox,oy)
    elif method == "center_corner":
        cx, cy, radius = ax, ay, math.hypot(bx-ax,by-ay)
    else:
        raise ValueError("Choose center + rim point or three circle points.")
    if radius <= 0:
        raise ValueError("Circle radius must be positive.")
    return cx-radius, cy-radius, cx+radius, cy+radius
