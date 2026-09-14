"""Grayscale virtual-mask sampling and scatter-point coordinate binding."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np


DEFAULT_MASK_PATH = Path(__file__).with_name("assets") / "nus_virtual_mask.png"


@dataclass(frozen=True)
class MaskBindingPoint:
    pixel_x: float
    pixel_y: float
    coordinate_a: float
    coordinate_b: float


def parse_binding_points(text: str) -> tuple[MaskBindingPoint, ...]:
    """Parse ``pixel_x,pixel_y,u_or_x,v_or_y`` rows separated by semicolons/newlines."""
    points: list[MaskBindingPoint] = []
    for row_number, raw in enumerate(str(text).replace(";", "\n").splitlines(), 1):
        row = raw.strip()
        if not row or row.startswith("#"):
            continue
        fields = [value.strip() for value in row.split(",")]
        if len(fields) != 4:
            raise ValueError(f"Mask binding row {row_number} needs four comma-separated values.")
        try:
            values = tuple(float(value) for value in fields)
        except ValueError as exc:
            raise ValueError(f"Mask binding row {row_number} contains a non-numeric value.") from exc
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"Mask binding row {row_number} must be finite.")
        points.append(MaskBindingPoint(*values))
    if len(points) < 3:
        raise ValueError("Virtual-mask binding needs at least three scatter points.")
    pixels = np.asarray([(point.pixel_x, point.pixel_y) for point in points], dtype=float)
    design = np.column_stack((np.ones(len(points)), pixels))
    if np.linalg.matrix_rank(design) < 3:
        raise ValueError("Virtual-mask image scatter points must not be collinear.")
    return tuple(points)


def fit_pixel_coordinate_affine(points: tuple[MaskBindingPoint, ...]) -> np.ndarray:
    design = np.asarray([[1.0, point.pixel_x, point.pixel_y] for point in points], dtype=float)
    targets = np.asarray([[point.coordinate_a, point.coordinate_b] for point in points], dtype=float)
    coefficients, *_ = np.linalg.lstsq(design, targets, rcond=None)
    matrix = coefficients.T
    if abs(float(np.linalg.det(matrix[:, 1:3]))) <= 1e-12:
        raise ValueError("Virtual-mask target scatter binding must span a two-dimensional area.")
    return matrix


def load_grayscale(path: str | Path) -> np.ndarray:
    import cv2

    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"Virtual-mask image does not exist: {resolved}")
    image = cv2.imread(str(resolved), cv2.IMREAD_GRAYSCALE)
    if image is None or image.ndim != 2 or not image.size:
        raise ValueError(f"Could not read virtual-mask image as grayscale: {resolved}")
    return image


def sampled_pixels(image: np.ndarray, pitch_px: int) -> tuple[tuple[int, int, float], ...]:
    pitch = int(pitch_px)
    if pitch <= 0:
        raise ValueError("Virtual-mask sample pitch must be a positive integer.")
    height, width = image.shape
    xs = list(range(0, width, pitch))
    ys = list(range(0, height, pitch))
    if xs[-1] != width - 1:
        xs.append(width - 1)
    if ys[-1] != height - 1:
        ys.append(height - 1)
    return tuple((x, y, float(image[y, x]) / 255.0) for y in ys for x in xs)


def sample_grayscale(image: np.ndarray, pixel_x: float, pixel_y: float) -> float:
    """Bilinearly sample normalized gray; positions outside the mask are black."""
    height, width = image.shape
    x, y = float(pixel_x), float(pixel_y)
    if abs(x - round(x)) < 1e-9:
        x = float(round(x))
    if abs(y - round(y)) < 1e-9:
        y = float(round(y))
    if x < 0 or y < 0 or x > width - 1 or y > height - 1:
        return 0.0
    x0, y0 = int(math.floor(x)), int(math.floor(y))
    x1, y1 = min(width - 1, x0 + 1), min(height - 1, y0 + 1)
    fx, fy = x - x0, y - y0
    value = (
        (1 - fx) * (1 - fy) * float(image[y0, x0]) +
        fx * (1 - fy) * float(image[y0, x1]) +
        (1 - fx) * fy * float(image[y1, x0]) +
        fx * fy * float(image[y1, x1])
    )
    # Least-squares affine inversion can put an edge coordinate a few ULPs
    # beyond an integer pixel. Keep normalized intensity physically bounded.
    normalized = min(1.0, max(0.0, value / 255.0))
    if normalized < 1e-12:
        return 0.0
    if normalized > 1.0 - 1e-12:
        return 1.0
    return normalized


def default_binding_text() -> str:
    # Matches the packaged 64 x 32 mask. Image Y grows downward; target Y grows upward.
    return "0,31,0,0;63,31,100,0;0,0,0,50;63,0,100,50"
