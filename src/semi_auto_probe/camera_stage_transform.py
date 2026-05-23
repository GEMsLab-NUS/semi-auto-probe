from __future__ import annotations

import math


def normalize_camera_fov_rotation_deg(value: object) -> float:
    try:
        angle = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Camera FOV rotation must be a finite number.") from exc
    if not math.isfinite(angle):
        raise ValueError("Camera FOV rotation must be a finite number.")
    angle = math.fmod(angle, 360.0)
    if angle <= -180.0:
        angle += 360.0
    elif angle > 180.0:
        angle -= 360.0
    return 0.0 if abs(angle) < 1e-12 else angle


def rotate_image_delta_px(dx_px: float, dy_px: float, rotation_deg: float) -> tuple[float, float]:
    angle_rad = math.radians(normalize_camera_fov_rotation_deg(rotation_deg))
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    return (
        float(dx_px) * cos_a - float(dy_px) * sin_a,
        float(dx_px) * sin_a + float(dy_px) * cos_a,
    )


def stage_delta_um_to_image_delta_px(
    stage_dx_um: float,
    stage_dy_um: float,
    um_per_px: float,
    rotation_deg: float = 0.0,
) -> tuple[float, float]:
    if um_per_px <= 0:
        raise ValueError("um_per_px must be positive.")
    base_dx_px = float(stage_dx_um) / float(um_per_px)
    base_dy_px = -float(stage_dy_um) / float(um_per_px)
    return rotate_image_delta_px(base_dx_px, base_dy_px, rotation_deg)


def image_delta_px_to_stage_delta_um(
    image_dx_px: float,
    image_dy_px: float,
    um_per_px: float,
    rotation_deg: float = 0.0,
) -> tuple[float, float]:
    if um_per_px <= 0:
        raise ValueError("um_per_px must be positive.")
    base_dx_px, base_dy_px = rotate_image_delta_px(image_dx_px, image_dy_px, -float(rotation_deg))
    return base_dx_px * float(um_per_px), -base_dy_px * float(um_per_px)
