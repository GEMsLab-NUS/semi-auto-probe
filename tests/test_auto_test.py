from __future__ import annotations

import unittest

from semi_auto_probe.auto_test import (
    AutoTestPanel,
    AutoTestSettings,
    compile_autotest_point_name,
    create_autotest_flow_card,
    generate_autotest_points,
    index_to_letters,
    legacy_measurement_steps_from_flow,
    measurement_flow_steps_from_cards,
    summarize_autotest_flow,
)
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

    def test_flow_summary_and_legacy_steps(self) -> None:
        cards = [
            create_autotest_flow_card("wait", "card_1"),
            create_autotest_flow_card("iv", "card_2"),
            create_autotest_flow_card("photo", "card_3"),
        ]

        self.assertEqual(
            summarize_autotest_flow(cards),
            "Measurement flow: Entity Pause -> Keithley IV -> Capture Photo",
        )
        self.assertEqual(legacy_measurement_steps_from_flow(cards), ("pause", "photo"))

        steps = measurement_flow_steps_from_cards(cards)
        self.assertEqual([step.type_id for step in steps], ["wait", "iv", "photo"])
        self.assertEqual(steps[1].params["resource"], "GPIB0::18::INSTR")
        self.assertEqual(steps[1].params["output_terminal"], "rear")
        self.assertEqual(steps[1].params["sweep_mode"], "voltage")
        self.assertEqual(steps[1].params["output_statistics"], "true")
        self.assertEqual(steps[1].params["resistance_method"], "linear_fit")

    def test_expanded_iv_card_height_accounts_for_all_params(self) -> None:
        panel = object.__new__(AutoTestPanel)
        panel._flow_zoom = 1.0
        card = create_autotest_flow_card("iv", "card_1", expanded=True)

        self.assertGreaterEqual(panel._flow_card_height_for_card(card), 382)

    def test_discarding_flow_widget_cache_preserves_flow_cards_and_entry_vars(self) -> None:
        panel = object.__new__(AutoTestPanel)
        card = create_autotest_flow_card("iv", "card_1")
        panel.measurement_flow_cards = [card]
        panel._flow_entry_vars = {("card_1", "resource"): "kept"}
        panel._flow_card_widgets = {"card_1": (1, object())}
        panel._flow_card_render_state = {"card_1": "stale"}

        panel._discard_flow_widget_cache(destroy=False)

        self.assertEqual(panel.measurement_flow_cards, [card])
        self.assertEqual(panel._flow_entry_vars[("card_1", "resource")], "kept")
        self.assertEqual(panel._flow_card_widgets, {})
        self.assertEqual(panel._flow_card_render_state, {})


if __name__ == "__main__":
    unittest.main()
