from __future__ import annotations

import unittest

from semi_auto_probe.auto_test import AutoTestSettings, compile_autotest_point_name, generate_autotest_points, index_to_letters
from semi_auto_probe.gds_stage_mapper import AffineCoordinateMapper, CalibrationPoint


class AutoTestTests(unittest.TestCase):
    def mapper(self) -> AffineCoordinateMapper:
        return AffineCoordinateMapper.fit(
            [
                CalibrationPoint("P1", 0.0, 0.0, 100.0, 200.0),
                CalibrationPoint("P2", 10.0, 0.0, 120.0, 200.0),
                CalibrationPoint("P3", 0.0, 10.0, 100.0, 230.0),
                CalibrationPoint("P4", 10.0, 10.0, 120.0, 230.0),
            ]
        )

    def test_generate_points_from_gds_basis_vectors(self) -> None:
        points = generate_autotest_points(
            AutoTestSettings(
                origin_u=1.0,
                origin_v=2.0,
                u_vector_u=10.0,
                u_vector_v=0.5,
                v_vector_u=-1.0,
                v_vector_v=5.0,
                rows=2,
                cols=3,
                fov_width_um=20.0,
                fov_height_um=10.0,
                z_down_margin_um=100.0,
                z_up_fast_percent=80.0,
                z_fast_speed_percent=80,
                z_slow_speed_percent=20,
                name_pattern="Dev{i}{j}",
            ),
            self.mapper(),
        )

        self.assertEqual([(point.row, point.col, point.order) for point in points], [(0, 0, 1), (0, 1, 2), (0, 2, 3), (1, 0, 4), (1, 1, 5), (1, 2, 6)])
        self.assertEqual([point.name for point in points], ["DevA1", "DevB1", "DevC1", "DevA2", "DevB2", "DevC2"])
        self.assertAlmostEqual(points[0].stage_x_um, 102.0)
        self.assertAlmostEqual(points[0].stage_y_um, 206.0)
        self.assertEqual(len(points[0].fov_polygon_gds), 4)

    def test_invalid_settings_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            AutoTestSettings(0, 0, 1, 0, 0, 1, 0, 1, 10, 10, 100, 80, 80, 20, "Dev{i}{j}").normalized()
        with self.assertRaisesRegex(ValueError, "non-zero"):
            AutoTestSettings(0, 0, 0, 0, 0, 1, 1, 1, 10, 10, 100, 80, 80, 20, "Dev{i}{j}").normalized()
        with self.assertRaisesRegex(ValueError, "zero or positive"):
            AutoTestSettings(0, 0, 1, 0, 0, 1, 1, 1, 10, 10, -1, 80, 80, 20, "Dev{i}{j}").normalized()
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            AutoTestSettings(0, 0, 1, 0, 0, 1, 1, 1, 10, 10, 1, 101, 80, 20, "Dev{i}{j}").normalized()
        with self.assertRaisesRegex(ValueError, "fast Z speed"):
            AutoTestSettings(0, 0, 1, 0, 0, 1, 1, 1, 10, 10, 1, 80, 101, 20, "Dev{i}{j}").normalized()

    def test_name_pattern_uses_i_letters_and_j_numbers(self) -> None:
        self.assertEqual(index_to_letters(0), "A")
        self.assertEqual(index_to_letters(25), "Z")
        self.assertEqual(index_to_letters(26), "AA")
        self.assertEqual(compile_autotest_point_name("Dev{i}{j}", i_index=27, j_index=2), "DevAB3")


if __name__ == "__main__":
    unittest.main()
