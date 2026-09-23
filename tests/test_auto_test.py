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
from semi_auto_probe.dp100 import DP100Setpoint, SimulatedDP100Driver
from semi_auto_probe.flow_graph import evaluate_condition, graph_start_node, resolve_data_input, validate_flow_graph
from semi_auto_probe.flow_editor import MeasurementFlowEditor
from semi_auto_probe.waveform import WaveformPoint, parse_waveform_points, run_dp100_waveform, serialize_waveform_points
from semi_auto_probe.workflow_library import DEFAULT_USERS, builtin_templates, cards_from_template, ensure_workflow_library, load_workflow, save_workflow


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

    def test_sac_coordinate_list_uses_current_grid_basis(self) -> None:
        payload = {
            "schema_version": 1,
            "coordinate_list": [[0, 1], [2, 3]],
            "devices": [
                {"device_name": "A2", "row": 0, "col": 1},
                {"device_name": "C4", "row": 2, "col": 3},
            ],
        }
        settings = AutoTestSettings(
            origin_u=100.0,
            origin_v=200.0,
            u_vector_u=10.0,
            u_vector_v=1.0,
            v_vector_u=-2.0,
            v_vector_v=20.0,
            rows=4,
            cols=5,
            fov_width_um=10.0,
            fov_height_um=10.0,
            z_down_margin_um=100.0,
            z_up_fast_percent=50.0,
            z_fast_speed_percent=50,
            z_slow_speed_percent=2,
            name_pattern="Dev{i}{j}",
        )

        specs = autotest_point_specs_from_json_payload(payload, grid_settings=settings)

        self.assertEqual(
            specs,
            (
                AutoTestPointSpec("A2", 110.0, 201.0, 0, 1),
                AutoTestPointSpec("C4", 126.0, 243.0, 2, 3),
            ),
        )

    def test_native_points_take_priority_over_legacy_sac_coordinate_list(self) -> None:
        payload = {
            "format": "semi_auto_probe.autotest_point_list",
            "version": 1,
            "points": [{"name": "A2", "u": 123.5, "v": -45.25, "row": 0, "col": 1}],
            "coordinate_list": [[0, 1]],
        }

        specs = autotest_point_specs_from_json_payload(payload)

        self.assertEqual(specs, (AutoTestPointSpec("A2", 123.5, -45.25, 0, 1),))

    def test_native_point_import_does_not_require_valid_base_array_settings(self) -> None:
        panel = object.__new__(AutoTestPanel)
        panel.settings_from_ui = lambda: self.fail("native u/v points must not read base array settings")
        payload = {
            "format": "semi_auto_probe.autotest_point_list",
            "version": 1,
            "points": [{"name": "A2", "u": 123.5, "v": -45.25, "row": 0, "col": 1}],
            "coordinate_list": [[0, 1]],
        }

        specs = panel._point_specs_from_import_payload(payload)

        self.assertEqual(specs, (AutoTestPointSpec("A2", 123.5, -45.25, 0, 1),))

    def test_legacy_coordinate_only_import_uses_base_array_settings(self) -> None:
        panel = object.__new__(AutoTestPanel)
        settings = AutoTestSettings(100, 200, 10, 1, -2, 20, 4, 5, 10, 10, 100, 50, 50, 2, "Dev{i}{j}")
        panel.settings_from_ui = lambda **_kwargs: settings

        specs = panel._point_specs_from_import_payload({"coordinate_list": [[2, 3]]})

        self.assertEqual(specs, (AutoTestPointSpec("DevD3", 126.0, 243.0, 2, 3),))

    def test_importing_custom_points_clears_previous_completion_colors(self) -> None:
        class Variable:
            def __init__(self) -> None:
                self.value = ""

            def get(self):
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        panel = object.__new__(AutoTestPanel)
        panel.custom_point_specs = None
        panel.advanced_point_list_var = Variable()
        panel._preview_cache_key = "old"
        panel._preview_cache_points = (object(),)
        panel.point_overlay_states = {(0, 1): "done"}
        panel.point_source_var = Variable()
        panel.status_var = Variable()
        redraws: list[bool] = []
        panel.redraw_preview = lambda: redraws.append(True)
        specs = (AutoTestPointSpec("A2", 10.0, 20.0, 0, 1),)

        panel._set_custom_point_specs(specs, "Imported")

        self.assertEqual(panel.point_overlay_states, {})
        self.assertTrue(panel.advanced_point_list_var.get())
        self.assertEqual(redraws, [True])

    def test_advanced_list_origin_does_not_require_basic_array_origin(self) -> None:
        class Variable:
            def __init__(self, value) -> None:
                self.value = value

            def get(self):
                return self.value

        panel = object.__new__(AutoTestPanel)
        panel.advanced_point_list_var = Variable(True)
        panel.custom_point_specs = (AutoTestPointSpec("A", -2680.0, -2780.0, 2, 2),)
        panel.origin_u_var = Variable("")
        panel.origin_v_var = Variable("")
        panel.u_vector_u_var = Variable("1000")
        panel.u_vector_v_var = Variable("0")
        panel.v_vector_u_var = Variable("0")
        panel.v_vector_v_var = Variable("1000")
        panel.rows_var = Variable("3")
        panel.cols_var = Variable("3")
        panel.fov_width_var = Variable("100")
        panel.fov_height_var = Variable("100")
        panel.z_down_margin_var = Variable("100")
        panel.z_up_fast_percent_var = Variable("50")
        panel.z_fast_speed_percent_var = Variable("50")
        panel.z_slow_speed_percent_var = Variable("2")
        panel.name_pattern_var = Variable("Dev{i}{j}")
        panel.z_wobble_um_var = Variable("4")
        panel.z_wobble_cycles_var = Variable("1")
        panel.z_offset_um_var = Variable("10")
        panel.measurement_steps = lambda: ()
        panel.measurement_flow = lambda: ()

        settings = panel.settings_from_ui()

        self.assertEqual((settings.origin_u, settings.origin_v), (-2680.0, -2780.0))

    def test_point_source_switch_selects_basic_or_advanced_points(self) -> None:
        class Variable:
            def __init__(self, value) -> None:
                self.value = value

            def get(self):
                return self.value

            def set(self, value) -> None:
                self.value = value

        panel = object.__new__(AutoTestPanel)
        panel.advanced_point_list_var = Variable(False)
        panel.custom_point_specs = (
            AutoTestPointSpec("A", 1.0, 2.0, 0, 0),
            AutoTestPointSpec("B", 3.0, 4.0, 0, 1),
        )
        panel._preview_cache_key = None
        panel._preview_cache_points = None
        panel.get_camera_fov_rotation_deg = lambda: 0.0
        settings = AutoTestSettings(10, 20, 1, 0, 0, 1, 1, 1, 10, 10, 100, 50, 50, 2, "Dev{i}{j}")

        basic_points = panel._points_for_settings(settings, self.mapper())
        panel.advanced_point_list_var.set(True)
        advanced_points = panel._points_for_settings(settings, self.mapper())

        self.assertEqual([point.name for point in basic_points], ["DevA1"])
        self.assertEqual([point.name for point in advanced_points], ["A", "B"])

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
        self.assertEqual(steps[2].params["segments"], "1")
        self.assertEqual(steps[2].params["initial_direction"], "up")
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

    def test_flow_cards_serialize_graph_position_and_edges(self) -> None:
        coarse = create_autotest_flow_card("iv", "coarse")
        coarse.x, coarse.y = 120, 240
        coarse.params["_next"] = "check"
        check = create_autotest_flow_card("condition_metric", "check")
        check.params.update({"source_node": "coarse", "metric": "statistics.resistance_ohm", "operator": ">", "threshold": "1e9", "_true": "skip"})
        skip = create_autotest_flow_card("photo", "skip")

        steps = measurement_flow_steps_from_cards([coarse, check, skip])

        self.assertEqual(steps[0].params["_node_id"], "coarse")
        self.assertEqual(steps[0].params["_x"], "120")
        self.assertEqual(graph_start_node(steps), "coarse")
        self.assertEqual(validate_flow_graph(steps), ())

    def test_metric_condition_reads_upstream_nested_statistics(self) -> None:
        condition = AutoTestFlowStep(
            "condition_metric",
            {"source_node": "coarse", "metric": "statistics.resistance_ohm", "operator": ">=", "threshold": "1e9"},
        )
        self.assertTrue(evaluate_condition(condition, {"coarse": {"statistics": {"resistance_ohm": 2e9}}}))
        self.assertFalse(evaluate_condition(condition, {"coarse": {"statistics": {"resistance_ohm": 2e6}}}))

    def test_math_and_boolean_data_chain_drives_boolean_branch(self) -> None:
        iv = create_autotest_flow_card("iv", "coarse")
        iv.params["_outputs"] = "statistics.resistance_ohm"
        iv.params["_next"] = "branch"
        math_node = create_autotest_flow_card("math_formula", "scaled")
        math_node.params.update(
            {
                "operator": "/",
                "fallback_b": "1000",
                "_input_a_source": "coarse",
                "_input_a_output": "statistics.resistance_ohm",
                "_input_a_type": "number",
            }
        )
        compare = create_autotest_flow_card("boolean_compare", "is_open")
        compare.params.update(
            {
                "operator": ">=",
                "threshold": "1000000",
                "_input_value_source": "scaled",
                "_input_value_output": "result",
                "_input_value_type": "number",
            }
        )
        branch = create_autotest_flow_card("condition_boolean", "branch")
        branch.params.update(
            {
                "_input_predicate_source": "is_open",
                "_input_predicate_output": "result",
                "_input_predicate_type": "bool",
                "_true": "end",
                "_false": "end",
            }
        )
        end = create_autotest_flow_card("workflow_end", "end")
        steps = measurement_flow_steps_from_cards([iv, math_node, compare, branch, end])
        results = {"coarse": {"statistics": {"resistance_ohm": 2e9}}}

        self.assertEqual(validate_flow_graph(steps), ())
        self.assertEqual(graph_start_node(steps), "coarse")
        self.assertEqual(resolve_data_input(steps[3], "predicate", steps, results), True)
        self.assertTrue(evaluate_condition(steps[3], results, "coarse", steps))

    def test_validation_rejects_incompatible_typed_data_link(self) -> None:
        math_node = create_autotest_flow_card("math_formula", "number_source")
        logic = create_autotest_flow_card("boolean_logic", "logic")
        logic.params.update(
            {
                "_input_a_source": "number_source",
                "_input_a_output": "result",
                "_input_a_type": "bool",
            }
        )
        start = create_autotest_flow_card("workflow_start", "start")
        end = create_autotest_flow_card("workflow_end", "end")
        start.params["_next"] = "end"

        errors = validate_flow_graph(measurement_flow_steps_from_cards([start, end, math_node, logic]))

        self.assertTrue(any("expects bool" in error and "outputs number" in error for error in errors))

    def test_light_waveform_is_dependency_not_second_graph_start(self) -> None:
        waveform = create_autotest_flow_card("laser_pulse", "waveform")
        photo_iv = create_autotest_flow_card("photo_iv", "photo_iv")
        photo_iv.params["_light_source"] = waveform.card_id

        steps = measurement_flow_steps_from_cards([waveform, photo_iv])

        self.assertEqual(validate_flow_graph(steps), ())
        self.assertEqual(graph_start_node(steps), "photo_iv")

    def test_workflow_library_round_trip_preserves_graph_and_positions(self) -> None:
        cards = cards_from_template("PD / Pulse Response")
        with tempfile.TemporaryDirectory() as directory:
            path = save_workflow(Path(directory) / "photo_test", cards)
            restored = load_workflow(path)

        self.assertEqual([(c.type_id, c.params, c.x, c.y) for c in restored],
                         [(c.type_id, c.params, c.x, c.y) for c in cards])
        response = next(c for c in restored if c.type_id == "photo_transient")
        self.assertEqual(response.params["_light_source"], "pulse")

    def test_light_route_does_not_cross_an_intermediate_waveform_card(self) -> None:
        source = create_autotest_flow_card("laser_pulse", "source")
        source.x, source.y = 0, 350
        obstacle = create_autotest_flow_card("laser_pulse", "obstacle")
        obstacle.x, obstacle.y = 110, 0
        target = create_autotest_flow_card("photo_iv", "target")
        target.x, target.y = 230, 300
        editor = object.__new__(MeasurementFlowEditor)
        editor.cards = [source, obstacle, target]

        route = editor._orthogonal_route(source, target, "light", target_kind="light")

        self.assertFalse(editor._route_hits_obstacle(route, [editor._node_bounds(obstacle, 18)]))

    def test_workflow_library_creates_named_user_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = ensure_workflow_library(Path(directory))

            self.assertEqual({path.name for path in root.iterdir()}, set(DEFAULT_USERS))

    def test_custom_dp100_waveform_round_trip_and_execution(self) -> None:
        points = [WaveformPoint(0, 0, 0), WaveformPoint(0.001, 2.5, 0.2), WaveformPoint(0.002, 0, 0)]
        params = {
            "waveform_points": serialize_waveform_points(points),
            "count": "1",
            "preset_index": "1",
            "ovp_v": "3",
            "ocp_a": "0.3",
        }
        driver = SimulatedDP100Driver()
        driver.connect()

        run_dp100_waveform(driver, params)

        self.assertEqual(parse_waveform_points(params), points)
        self.assertTrue(any(enabled and point.voltage_v == 2.5 for point, enabled in driver.commands))
        self.assertFalse(driver.commands[-1][1])

    def test_empty_editor_flow_gets_fixed_connected_boundaries(self) -> None:
        editor = object.__new__(MeasurementFlowEditor)
        editor.cards = []

        editor._ensure_boundary_nodes()

        self.assertEqual([card.type_id for card in editor.cards], ["workflow_start", "workflow_end"])
        self.assertEqual(editor.cards[0].params["_next"], editor.cards[1].card_id)

    def test_node_drag_snaps_centers_to_nearby_nodes(self) -> None:
        moving = create_autotest_flow_card("iv", "moving")
        fixed = create_autotest_flow_card("photo_iv", "fixed")
        fixed.x, fixed.y = 320, 192
        editor = object.__new__(MeasurementFlowEditor)
        editor.cards = [moving, fixed]

        x, y, guides = editor._snap_card_position(moving, 315, 188)

        self.assertEqual((x, y), (320, 192))
        self.assertIn(("v", 320), guides)
        self.assertIn(("h", 192), guides)

    def test_routes_leave_and_enter_nodes_horizontally(self) -> None:
        source = create_autotest_flow_card("workflow_start", "start")
        source.x, source.y = 0, 0
        target = create_autotest_flow_card("wobb_test", "test")
        target.x, target.y = 260, 150
        editor = object.__new__(MeasurementFlowEditor)
        editor.cards = [source, target]

        route = editor._orthogonal_route(source, target, "next")

        self.assertEqual(route[0][1], route[1][1])
        self.assertGreaterEqual(route[1][0] - route[0][0], 30)
        self.assertEqual(route[-2][1], route[-1][1])
        self.assertGreaterEqual(route[-1][0] - route[-2][0], 30)

    def test_cut_gesture_removes_crossed_flow_connection(self) -> None:
        source = create_autotest_flow_card("workflow_start", "start")
        target = create_autotest_flow_card("iv", "test")
        source.x, source.y = 0, 0
        target.x, target.y = 300, 0
        source.params["_next"] = target.card_id
        editor = object.__new__(MeasurementFlowEditor)
        editor.cards = [source, target]

        removed = editor._cut_edges((220, -50), (220, 100))

        self.assertEqual(removed, 1)
        self.assertNotIn("_next", source.params)

    def test_result_output_connection_configures_condition_source(self) -> None:
        source = create_autotest_flow_card("iv", "coarse")
        source.params["_outputs"] = "statistics.resistance_ohm"
        condition = create_autotest_flow_card("condition_metric", "check")
        editor = object.__new__(MeasurementFlowEditor)
        editor.cards = [source, condition]
        editor.status_var = type("Status", (), {"set": lambda self, value: None})()
        editor._changed = lambda redraw=True: None

        editor._connect(source.card_id, "result@statistics.resistance_ohm", condition.card_id)

        self.assertEqual(condition.params["source_node"], source.card_id)
        self.assertEqual(condition.params["metric"], "statistics.resistance_ohm")
        self.assertEqual(condition.params["_result_source"], source.card_id)

    def test_all_builtin_workflow_templates_validate_with_boundaries(self) -> None:
        for name in builtin_templates():
            editor = object.__new__(MeasurementFlowEditor)
            editor.cards = cards_from_template(name)
            editor._ensure_boundary_nodes()

            with self.subTest(name=name):
                self.assertEqual(validate_flow_graph(measurement_flow_steps_from_cards(editor.cards)), ())

    def test_multiple_edges_enter_shared_target_at_separate_ports(self) -> None:
        first = create_autotest_flow_card("photo", "first")
        second = create_autotest_flow_card("photo", "second")
        target = create_autotest_flow_card("workflow_end", "end")
        first.x, first.y = 0, 0
        second.x, second.y = 0, 200
        target.x, target.y = 600, 100
        first.params["_next"] = target.card_id
        second.params["_next"] = target.card_id
        editor = object.__new__(MeasurementFlowEditor)
        editor.cards = [first, second, target]

        first_route = editor._orthogonal_route(first, target, "next")
        second_route = editor._orthogonal_route(second, target, "next")

        self.assertNotEqual(first_route[-1][1], second_route[-1][1])
        self.assertEqual(len(editor._incoming_flow_offsets(target)), 2)

    def test_dp100_setpoint_converts_si_units_to_vendor_units(self) -> None:
        point = DP100Setpoint(5.25, 0.125, 2)
        driver = SimulatedDP100Driver()
        self.assertTrue(driver.connect())
        driver.set_output(point, True)
        self.assertEqual(point.voltage_mv, 5250)
        self.assertEqual(point.current_ma, 125)
        self.assertEqual(driver.commands, [(point, True)])

    def test_dp100_rejects_zero_energy_enable(self) -> None:
        driver = SimulatedDP100Driver()
        driver.connect()
        with self.assertRaisesRegex(ValueError, "non-zero"):
            driver.set_output(DP100Setpoint(0, 0, 1), True)


if __name__ == "__main__":
    unittest.main()
