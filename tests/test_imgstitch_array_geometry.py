from __future__ import annotations

import unittest

import numpy as np

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.config import ProbeConfig
from semi_auto_probe.img_stitch import StitchSession, StitchSettings, TileRecord, stage_positions_from_um


class ImgStitchArrayGeometryTests(unittest.TestCase):
    def test_array_targets_are_centered_on_current_position_for_3x3(self) -> None:
        targets = {
            (row, col): ProbeApp._imgstitch_tile_target(
                origin_x=1000,
                origin_y=2000,
                row=row,
                col=col,
                rows=3,
                cols=3,
                step_x=100,
                step_y=200,
                range_mode="Array",
            )
            for row in range(3)
            for col in range(3)
        }

        self.assertEqual(targets[(1, 1)], (1000, 2000))
        self.assertEqual(targets[(0, 0)], (1100, 2200))
        self.assertEqual(targets[(2, 2)], (900, 1800))

    def test_non_array_targets_still_start_from_origin_corner(self) -> None:
        self.assertEqual(
            ProbeApp._imgstitch_tile_target(
                origin_x=1000,
                origin_y=2000,
                row=1,
                col=2,
                rows=3,
                cols=3,
                step_x=100,
                step_y=200,
                range_mode="Space",
            ),
            (800, 1800),
        )

    def test_shift_click_preview_target_maps_mosaic_pixel_to_stage_center(self) -> None:
        app = ProbeApp.__new__(ProbeApp)
        app.probe_config = ProbeConfig()
        app.imgstitch_preview_bgr = np.zeros((40, 90, 3), dtype=np.uint8)
        app.imgstitch_preview_scale = 0.5
        app.imgstitch_preview_pan = [10.0, 20.0]
        app.current_position_values = {"X": 1111, "Y": 2222, "Z": 3333}
        app.imgstitch_session = StitchSession(
            rows=1,
            cols=2,
            tile_width=50,
            tile_height=40,
            um_per_px=2.0,
            objective=20,
            eyepiece=1.5,
            range_mode="array",
            step_x_um=80.0,
            step_y_um=80.0,
            origin_stage_x=1000,
            origin_stage_y=2000,
            origin_stage_z=0,
            settings=StitchSettings(overlap_x=20, overlap_y=20, registration_weight=0.0),
            tiles=(
                TileRecord(0, 0, 1, "left.png", 1000, 2000, 0, 0.0, 0.0),
                TileRecord(0, 1, 2, "right.png", 920, 2000, 0, -80.0, 0.0),
            ),
        )
        app.imgstitch_latest_positions = stage_positions_from_um(app.imgstitch_session.tiles, app.imgstitch_session.um_per_px)

        target = ProbeApp._imgstitch_stage_target_from_canvas_point(app, 30.0, 20.0)

        self.assertEqual(target, (920, 2000, 3333))


if __name__ == "__main__":
    unittest.main()
