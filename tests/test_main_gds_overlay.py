from __future__ import annotations

import unittest
from pathlib import Path

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.config import ProbeConfig
from semi_auto_probe.gds_stage_mapper import AuxiliaryPointOverlay, GDSLayoutModel, GDSShape


class IdentityStageMapper:
    def gds_to_stage(self, u: float, v: float) -> tuple[float, float]:
        return float(u), float(v)

    def stage_to_gds(self, x_um: float, y_um: float) -> tuple[float, float]:
        return float(x_um), float(y_um)


class FakeViewer:
    layer_order = ((1, 0),)
    layer_visibility = {(1, 0): True}


class FakeLayoutPanel:
    def __init__(self) -> None:
        self.model = GDSLayoutModel(
            path=Path("fake.gds"),
            top_cell_name="TOP",
            top_cell_names=("TOP",),
            shapes=[
                GDSShape(
                    points=((10.0, -5.0), (20.0, -5.0), (20.0, 5.0), (10.0, 5.0)),
                    layer=1,
                    datatype=0,
                    bbox=(10.0, -5.0, 20.0, 5.0),
                )
            ],
            labels=[],
            bounds=(10.0, -5.0, 20.0, 5.0),
        )
        self.mapper = IdentityStageMapper()
        self.viewer = FakeViewer()


class FakeAutoTestPanel:
    def probe_assist_enabled(self) -> bool:
        return True

    def probe_assist_overlays_for_center(self, _center_gds: tuple[float, float]) -> list[AuxiliaryPointOverlay]:
        return [AuxiliaryPointOverlay(point=(10.0, 0.0), label="Drain", color="#ef4444")]


class MainGDSOverlayTests(unittest.TestCase):
    def make_app_shell(self) -> ProbeApp:
        app = ProbeApp.__new__(ProbeApp)
        app.probe_config = ProbeConfig()
        app.probe_config.set_calibration(10, 2.0, 1.0)
        app.current_position_values = {"X": 0, "Y": 0, "Z": 0}
        app.gds_stage_mapper_panel = FakeLayoutPanel()
        app.main_gds_overlay_cache_key = None
        app.main_gds_overlay_cache_polygons = []
        return app

    def test_gds_overlay_projects_positive_stage_x_to_image_right(self) -> None:
        app = self.make_app_shell()

        polygons = ProbeApp.main_view_gds_overlay_polygons(app, image_width=100, image_height=80)

        self.assertEqual(len(polygons), 1)
        xs = [point[0] for point in polygons[0]]
        self.assertEqual(xs, [60.0, 70.0, 70.0, 60.0])
        self.assertGreater(min(xs), 50.0)

    def test_stage_to_main_image_projection_is_inverse_of_centering_move(self) -> None:
        app = self.make_app_shell()

        image_x, image_y = app._stage_xy_um_to_main_image_point(
            stage_x_um=10.0,
            stage_y_um=20.0,
            current_x_um=0.0,
            current_y_um=0.0,
            image_width=100,
            image_height=80,
            um_per_px=2.0,
        )
        move = ProbeApp._image_centering_move(
            app,
            point_x=image_x,
            point_y=image_y,
            image_width=100,
            image_height=80,
            um_per_px=2.0,
        )

        self.assertEqual((image_x, image_y), (55.0, 30.0))
        self.assertEqual(move["X"][0], 10.0)
        self.assertEqual(move["Y"][0], 20.0)

    def test_stage_to_main_image_projection_uses_camera_fov_rotation(self) -> None:
        app = self.make_app_shell()
        app.probe_config.camera_fov_rotation_deg = 90.0

        image_x, image_y = app._stage_xy_um_to_main_image_point(
            stage_x_um=10.0,
            stage_y_um=0.0,
            current_x_um=0.0,
            current_y_um=0.0,
            image_width=100,
            image_height=80,
            um_per_px=2.0,
        )
        move = ProbeApp._image_centering_move(
            app,
            point_x=image_x,
            point_y=image_y,
            image_width=100,
            image_height=80,
            um_per_px=2.0,
        )

        self.assertAlmostEqual(image_x, 50.0)
        self.assertAlmostEqual(image_y, 45.0)
        self.assertAlmostEqual(move["X"][0], 10.0)
        self.assertAlmostEqual(move["Y"][0], 0.0)

    def test_gds_overlay_keeps_diagonal_rotation_sign_after_stage_projection(self) -> None:
        app = self.make_app_shell()
        app.gds_stage_mapper_panel.model = GDSLayoutModel(
            path=Path("fake.gds"),
            top_cell_name="TOP",
            top_cell_names=("TOP",),
            shapes=[
                GDSShape(
                    points=((0.0, -10.0), (10.0, 0.0), (10.0, 2.0), (0.0, -8.0)),
                    layer=1,
                    datatype=0,
                    bbox=(0.0, -10.0, 10.0, 2.0),
                )
            ],
            labels=[],
            bounds=(0.0, -10.0, 10.0, 2.0),
        )

        polygons = ProbeApp.main_view_gds_overlay_polygons(app, image_width=100, image_height=80)

        self.assertEqual(len(polygons), 1)
        first, second = polygons[0][0], polygons[0][1]
        left_point, right_point = sorted((first, second), key=lambda point: point[0])
        self.assertGreater(left_point[1], right_point[1])

    def test_probe_assist_uses_same_main_camera_projection_as_gds_overlay(self) -> None:
        app = self.make_app_shell()
        app.autotest_panel = FakeAutoTestPanel()
        app._imgmatrix_mapper = lambda: app.gds_stage_mapper_panel.mapper

        points = ProbeApp.main_view_probe_assist_points(app, image_width=100, image_height=80)

        self.assertEqual(points, [(60.0, 40.0, "Drain", "#ef4444")])


if __name__ == "__main__":
    unittest.main()
