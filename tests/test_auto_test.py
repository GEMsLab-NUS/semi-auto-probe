from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from semi_auto_probe.auto_test import (
    AutoTestFlowStep,
    AutoTestPanel,
    AutoTestPoint,
    AutoTestPointSpec,
    AutoTestSettings,
    PROBE_ASSIST_PROBES,
    autotest_point_specs_from_json_payload,
    autotest_point_specs_payload,
    compile_autotest_point_name,
    compile_nested_autotest_point_name,
    contact_wobble_offsets_um,
    create_autotest_flow_card,
    generate_autotest_points,
    generate_autotest_points_from_specs,
    generate_nested_autotest_point_specs,
    index_to_letters,
    legacy_measurement_steps_from_flow,
    measurement_flow_steps_from_cards,
    summarize_autotest_flow,
    wobbtest_xy_offsets_um,
    wobbtest_z_offsets_um,
)
from semi_auto_probe.app import ProbeApp
from semi_auto_probe.gds_stage_mapper import AffineCoordinateMapper, CalibrationPoint
from semi_auto_probe.keithley2450 import IVSweepConfig, IVSweepSample, IVSweepStatistics


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

    def test_autotest_contact_defaults_are_normalized(self) -> None:
        settings = AutoTestSettings(0, 0, 1, 0, 0, 1, 1, 1, 10, 10, 100, 50, 50, 2, "Dev{i}{j}").normalized()

        self.assertEqual(settings.z_wobble_um, 0.0)
        self.assertEqual(settings.z_wobble_cycles, 0)
        self.assertEqual(settings.z_offset_um, 0.0)

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
        with self.assertRaisesRegex(ValueError, "wobble"):
            AutoTestSettings(0, 0, 1, 0, 0, 1, 1, 1, 10, 10, 1, 50, 50, 2, "Dev{i}{j}", z_wobble_um=-1).normalized()
        with self.assertRaisesRegex(ValueError, "cycles"):
            AutoTestSettings(0, 0, 1, 0, 0, 1, 1, 1, 10, 10, 1, 50, 50, 2, "Dev{i}{j}", z_wobble_cycles=-1).normalized()

    def test_autotest_contact_z_targets_include_wobble_and_offset(self) -> None:
        self.assertEqual(
            ProbeApp._autotest_contact_z_targets(focus_z=1000, wobble_pulses=5, wobble_cycles=2, offset_pulses=3),
            [1005, 995, 1000, 1005, 995, 1000, 1003],
        )
        self.assertEqual(ProbeApp._autotest_contact_z_targets(focus_z=1000, wobble_pulses=0, wobble_cycles=2, offset_pulses=3), [1003])
        self.assertEqual(ProbeApp._autotest_contact_z_targets(focus_z=1000, wobble_pulses=5, wobble_cycles=0, offset_pulses=0), [])

    def test_contact_wobble_offsets_drive_curve_preview(self) -> None:
        self.assertEqual(contact_wobble_offsets_um(2, 2, 0.5), (2.0, -2.0, 0.0, 2.0, -2.0, 0.0, 0.5))

    def test_wobbtest_z_offsets_scan_pairs_then_longer_side(self) -> None:
        self.assertEqual(wobbtest_z_offsets_um(-2, 5, 1), (0.0, 1.0, -1.0, 2.0, -2.0, 3.0, 4.0, 5.0))
        self.assertEqual(wobbtest_z_offsets_um(-3, 1, 1), (0.0, 1.0, -1.0, -2.0, -3.0))

    def test_wobbtest_xy_offsets_include_square_path_and_return(self) -> None:
        self.assertEqual(wobbtest_xy_offsets_um(1, 1, "corners")[-1], (0.0, 0.0))
        self.assertIn((1.0, 1.0), wobbtest_xy_offsets_um(1, 1, "square"))

    def test_name_pattern_uses_i_letters_and_j_numbers(self) -> None:
        self.assertEqual(index_to_letters(0), "A")
        self.assertEqual(index_to_letters(25), "Z")
        self.assertEqual(index_to_letters(26), "AA")
        self.assertEqual(compile_autotest_point_name("Dev{i}{j}", i_index=27, j_index=2), "DevAB3")
        self.assertEqual(compile_nested_autotest_point_name("D{bi}{bj}_{i}{j}_{n}", block_i_index=1, block_j_index=2, i_index=0, j_index=3, order=5), "DB3_A4_5")

    def test_imported_point_specs_generate_autotest_points(self) -> None:
        payload = {
            "format": "semi_auto_probe.autotest_point_list",
            "version": 1,
            "points": [
                {"name": "A", "u": 1.0, "v": 2.0, "row": 0, "col": 0},
                {"name": "B", "u": 3.0, "v": 4.0, "row": 0, "col": 1},
            ],
        }
        specs = autotest_point_specs_from_json_payload(payload)
        settings = AutoTestSettings(0, 0, 1, 0, 0, 1, 1, 1, 10, 10, 100, 50, 50, 2, "Dev{i}{j}")
        points = generate_autotest_points_from_specs(specs, settings, self.mapper())

        self.assertEqual([point.name for point in points], ["A", "B"])
        self.assertEqual([(point.row, point.col) for point in points], [(0, 0), (0, 1)])
        self.assertAlmostEqual(points[1].stage_x_um, 106.0)
        self.assertAlmostEqual(points[1].stage_y_um, 212.0)

    def test_nested_point_generator_payload_round_trips(self) -> None:
        specs = generate_nested_autotest_point_specs(
            origin_u=0,
            origin_v=0,
            inner_u_vector_u=1,
            inner_u_vector_v=0,
            inner_v_vector_u=0,
            inner_v_vector_v=1,
            inner_cols=2,
            inner_rows=2,
            outer_u_vector_u=10,
            outer_u_vector_v=0,
            outer_v_vector_u=0,
            outer_v_vector_v=20,
            outer_cols=2,
            outer_rows=1,
            name_pattern="Dev{bi}{bj}_{i}{j}",
        )
        payload = autotest_point_specs_payload(specs, source="test")
        round_trip = autotest_point_specs_from_json_payload(payload)

        self.assertEqual(len(round_trip), 8)
        self.assertEqual(round_trip[0], AutoTestPointSpec("DevA1_A1", 0.0, 0.0, 0, 0))
        self.assertEqual(round_trip[-1].name, "DevB1_B2")

    def test_autotest_grid_shape_uses_actual_secondary_array_points(self) -> None:
        settings = AutoTestSettings(0, 0, 1, 0, 0, 1, 1, 1, 10, 10, 100, 50, 50, 2, "Dev{i}{j}")
        points = (
            AutoTestPoint(0, 0, 1, "A", 0, 0, 0, 0, ()),
            AutoTestPoint(3, 4, 2, "B", 0, 0, 0, 0, ()),
        )

        self.assertEqual(ProbeApp._autotest_grid_shape(points, settings), (4, 5))

    def test_iv_csv_has_json_sidecar_metadata(self) -> None:
        point = generate_autotest_points(
            AutoTestSettings(1, 2, 1, 0, 0, 1, 1, 1, 10, 10, 100, 50, 50, 2, "Dev{i}{j}"),
            self.mapper(),
        )[0]
        config = IVSweepConfig(resource_name="GPIB0::18::INSTR", start=0, stop=1, step=1)
        samples = [
            IVSweepSample(index=1, total=1, elapsed_s=0.1, source_value=0.0, voltage_v=0.0, current_a=1e-6, resistance_ohm=0.0, raw="0,1e-6")
        ]
        statistics = IVSweepStatistics(sample_count=1, resistance_ohm=1000.0, resistance_method="linear_fit")
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "DevA1_iv.csv"
            ProbeApp._write_iv_samples_csv(output_path, point, config, samples, statistics)
            metadata = json.loads(output_path.with_suffix(".json").read_text(encoding="utf-8"))
            with output_path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.reader(file))

        self.assertEqual(rows[0], ["index", "total", "elapsed_s", "source_value", "voltage_v", "current_a", "resistance_ohm", "raw"])
        self.assertEqual(metadata["device"]["name"], point.name)
        self.assertEqual(metadata["coordinates"]["gds"], {"u": point.u, "v": point.v})
        self.assertEqual(metadata["csv_file"], "DevA1_iv.csv")

    def test_iv_output_paths_keep_multiple_vtops_and_repeated_cards(self) -> None:
        point = generate_autotest_points(
            AutoTestSettings(1, 2, 1, 0, 0, 1, 1, 1, 10, 10, 100, 50, 50, 2, "Dev{i}{j}"),
            self.mapper(),
        )[0]
        with tempfile.TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir)
            (session_dir / "iv").mkdir()
            first = ProbeApp._autotest_iv_output_path(session_dir, point, IVSweepConfig(stop=1), multi_vtop=True)
            first.touch()
            second = ProbeApp._autotest_iv_output_path(session_dir, point, IVSweepConfig(stop=1), multi_vtop=True)

        self.assertEqual(first.name, "DevA1_iv_vtop_1.csv")
        self.assertEqual(second.name, "DevA1_iv_vtop_1_2.csv")

    def test_flow_summary_and_legacy_steps(self) -> None:
        cards = [
            create_autotest_flow_card("wait", "card_1"),
            create_autotest_flow_card("wobb_test", "card_wobb"),
            create_autotest_flow_card("iv", "card_2"),
            create_autotest_flow_card("b1500_transfer", "card_b1500_transfer"),
            create_autotest_flow_card("photo", "card_3"),
        ]

        self.assertEqual(
            summarize_autotest_flow(cards),
            "Measurement flow: Entity Pause -> WobbTest -> Keithley IV -> B1500 Transfer -> Capture Photo",
        )
        self.assertEqual(legacy_measurement_steps_from_flow(cards), ("pause", "photo"))

        steps = measurement_flow_steps_from_cards(cards)
        self.assertEqual([step.type_id for step in steps], ["wait", "wobb_test", "iv", "b1500_transfer", "photo"])
        self.assertEqual(steps[1].params["mode"], "Z")
        self.assertEqual(steps[1].params["bias_v"], "0.1")
        self.assertEqual(steps[1].params["current_limit_a"], "1e-5")
        self.assertEqual(steps[1].params["nplc"], "10")
        self.assertEqual(steps[2].params["resource"], "GPIB0::18::INSTR")
        self.assertEqual(steps[2].params["output_terminal"], "rear")
        self.assertEqual(steps[2].params["sweep_mode"], "voltage")
        self.assertEqual(steps[2].params["scan_type"], "single")
        self.assertEqual(steps[2].params["measure_range"], "auto")
        self.assertEqual(steps[2].params["output_statistics"], "true")
        self.assertEqual(steps[2].params["resistance_method"], "linear_fit")
        self.assertEqual(steps[2].params["plot_layout"], "horizontal")
        self.assertEqual(steps[2].params["heatmap_values"], "true")
        self.assertEqual(steps[3].params["resource"], "GPIB0::17::INSTR")
        self.assertEqual(steps[3].params["drain_smu"], "smu3")
        self.assertEqual(steps[3].params["gate_smu"], "smu4")
        self.assertEqual(steps[3].params["sweep_start_v"], "-40")
        self.assertEqual(steps[3].params["scan_type"], "single")
        self.assertEqual(steps[3].params["bias_values_v"], "0:1:5")
        self.assertEqual(steps[3].params["abort_on_compliance"], "false")
        self.assertEqual(steps[3].params["staircase_nplc"], "8")
        self.assertEqual(steps[3].params["step_delay_s"], "0")
        self.assertEqual(steps[3].params["measurement_adc"], "high_resolution")
        self.assertNotIn("avg_coefficient", steps[3].params)

    def test_hp6614c_cards_include_id_range_and_transfer_heatmap_metric(self) -> None:
        cards = [
            create_autotest_flow_card("hp6614c_transfer", "card_6614c_transfer"),
            create_autotest_flow_card("hp6614c_output", "card_6614c_output"),
        ]

        steps = measurement_flow_steps_from_cards(cards)

        self.assertEqual(steps[0].params["drain_measure_range"], "auto")
        self.assertEqual(steps[0].params["output_terminal"], "front")
        self.assertEqual(steps[0].params["heatmap_metric"], "vth")
        self.assertEqual(steps[0].params["heatmap_values"], "true")
        self.assertEqual(steps[1].params["drain_measure_range"], "auto")
        self.assertEqual(steps[1].params["output_terminal"], "front")
        self.assertEqual(steps[1].params["heatmap_values"], "true")
        self.assertNotIn("heatmap_metric", steps[1].params)

    def test_wobbtest_requires_session_dir_and_best_z_uses_median_current(self) -> None:
        settings = AutoTestSettings(
            0,
            0,
            1,
            0,
            0,
            1,
            1,
            1,
            10,
            10,
            100,
            50,
            50,
            2,
            "Dev{i}{j}",
            measurement_flow=(AutoTestFlowStep("wobb_test", {}),),
        )
        records = [
            {"target_z_pulses": 100, "z_um": 0.0, "current_a": 1e-9},
            {"target_z_pulses": 100, "z_um": 0.0, "current_a": 2e-9},
            {"target_z_pulses": 110, "z_um": 1.0, "current_a": -5e-9},
            {"target_z_pulses": 110, "z_um": 1.0, "current_a": -7e-9},
        ]

        self.assertTrue(ProbeApp._autotest_requires_session_dir(settings))
        best = ProbeApp._autotest_wobb_best_z_record(records, "max_abs")
        self.assertIsNotNone(best)
        self.assertEqual(best["target_z_pulses"], 110)
        self.assertAlmostEqual(best["score_current_a"], -6e-9)

    def test_wobbtest_mode_normalization_accepts_new_and_legacy_values(self) -> None:
        self.assertEqual(ProbeApp._normalize_wobb_test_mode("Z"), "z")
        self.assertEqual(ProbeApp._normalize_wobb_test_mode("Z-XY"), "z-xy")
        self.assertEqual(ProbeApp._normalize_wobb_test_mode("xy"), "z-xy")
        with self.assertRaisesRegex(ValueError, "Z or Z-XY"):
            ProbeApp._normalize_wobb_test_mode("bad")

    def test_wobbtest_card_height_changes_when_xy_section_is_visible(self) -> None:
        panel = object.__new__(AutoTestPanel)
        panel._flow_zoom = 1.0
        card = create_autotest_flow_card("wobb_test", "card_wobb", expanded=True)
        z_height = panel._flow_card_height_for_card(card)
        card.params["mode"] = "Z-XY"
        zxy_height = panel._flow_card_height_for_card(card)

        self.assertGreaterEqual(z_height, 450)
        self.assertGreaterEqual(zxy_height, 530)
        self.assertGreater(zxy_height, z_height)

    def test_autotest_probe_assist_builds_three_named_relative_overlays(self) -> None:
        panel = object.__new__(AutoTestPanel)
        panel.probe_assist_enabled_var = type("Var", (), {"get": lambda self: True})()
        panel.probe_assist_vars = {
            "Source": {"du": type("Var", (), {"get": lambda self: "1"})(), "dv": type("Var", (), {"get": lambda self: "2"})()},
            "Drain": {"du": type("Var", (), {"get": lambda self: "-1"})(), "dv": type("Var", (), {"get": lambda self: "0"})()},
            "Gate": {"du": type("Var", (), {"get": lambda self: "0.5"})(), "dv": type("Var", (), {"get": lambda self: "-0.5"})()},
        }

        overlays = panel.probe_assist_overlays_for_center((10.0, 20.0))

        self.assertEqual([overlay.label for overlay in overlays], ["Source", "Drain", "Gate"])
        self.assertEqual([overlay.point for overlay in overlays], [(11.0, 22.0), (9.0, 20.0), (10.5, 19.5)])

    def test_autotest_probe_assist_default_offsets_match_probe_layout(self) -> None:
        self.assertEqual(
            [(name, du, dv) for name, _color, _style, du, dv in PROBE_ASSIST_PROBES],
            [("Source", "100", "0"), ("Drain", "-100", "0"), ("Gate", "0", "100")],
        )

    def test_expanded_iv_card_height_accounts_for_all_params(self) -> None:
        panel = object.__new__(AutoTestPanel)
        panel._flow_zoom = 1.0
        card = create_autotest_flow_card("iv", "card_1", expanded=True)

        self.assertGreaterEqual(panel._flow_card_height_for_card(card), 532)

    def test_expanded_b1500_card_height_accounts_for_grouped_params(self) -> None:
        panel = object.__new__(AutoTestPanel)
        panel._flow_zoom = 1.0
        card = create_autotest_flow_card("b1500_output", "card_b1500", expanded=True)

        self.assertGreaterEqual(panel._flow_card_height_for_card(card), 500)

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
