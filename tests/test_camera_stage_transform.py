from __future__ import annotations

import unittest

from semi_auto_probe.camera_stage_transform import (
    fov_stage_corners_from_image_frame,
    image_delta_px_to_stage_delta_um,
    normalize_camera_fov_rotation_deg,
    stage_delta_um_to_image_delta_px,
)


class CameraStageTransformTests(unittest.TestCase):
    def test_stage_to_image_and_image_to_stage_are_inverse(self) -> None:
        for angle in (-135.0, -45.0, 0.0, 33.0, 90.0, 180.0):
            image_delta = stage_delta_um_to_image_delta_px(12.0, -8.0, 0.5, angle)
            stage_delta = image_delta_px_to_stage_delta_um(*image_delta, 0.5, angle)

            self.assertAlmostEqual(stage_delta[0], 12.0)
            self.assertAlmostEqual(stage_delta[1], -8.0)

    def test_zero_rotation_uses_image_y_down_stage_y_up_convention(self) -> None:
        self.assertEqual(stage_delta_um_to_image_delta_px(10.0, 20.0, 2.0), (-5.0, -10.0))
        self.assertEqual(image_delta_px_to_stage_delta_um(-5.0, -10.0, 2.0), (10.0, 20.0))

    def test_positive_rotation_is_clockwise_in_image_coordinates(self) -> None:
        image_delta = stage_delta_um_to_image_delta_px(10.0, 0.0, 2.0, 90.0)

        self.assertAlmostEqual(image_delta[0], 0.0)
        self.assertAlmostEqual(image_delta[1], -5.0)

    def test_fov_stage_corners_follow_camera_image_frame(self) -> None:
        corners = fov_stage_corners_from_image_frame(100.0, 200.0, 20.0, 10.0)

        self.assertEqual(corners, ((110.0, 205.0), (90.0, 205.0), (90.0, 195.0), (110.0, 195.0)))

    def test_rotation_normalizes_to_signed_half_turn(self) -> None:
        self.assertEqual(normalize_camera_fov_rotation_deg(270.0), -90.0)
        self.assertEqual(normalize_camera_fov_rotation_deg(-180.0), 180.0)


if __name__ == "__main__":
    unittest.main()
