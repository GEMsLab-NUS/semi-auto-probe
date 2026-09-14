from __future__ import annotations

import tempfile
import queue
import threading
import unittest
from pathlib import Path

import cv2
import numpy as np
from matplotlib.collections import PathCollection, QuadMesh
from matplotlib.figure import Figure

from semi_auto_probe.auto_test import autotest_flow_definitions_by_type
from semi_auto_probe.app import ProbeApp, is_high_frequency_dp100_status
from semi_auto_probe.dp100 import SimulatedDP100Driver
from semi_auto_probe.gds_stage_mapper import AffineCoordinateMapper
from semi_auto_probe.mapping import MappingPoint, MappingSettings, generate_mapping_plan
from semi_auto_probe.mapping_plot import draw_workflow_heatmap_figure
from semi_auto_probe.measurement_workflow import AutoTestFlowStep
from semi_auto_probe.virtual_mask import DEFAULT_MASK_PATH, default_binding_text, parse_binding_points
from semi_auto_probe.waveform import calibrated_power_setpoint
from semi_auto_probe.workflow_library import VIRTUAL_MASK_MAPPING_TEMPLATE, cards_from_template, cards_from_workflow_payload


class VirtualMaskMappingTests(unittest.TestCase):
    def mask_step(self, **overrides) -> AutoTestFlowStep:
        params = {
            "image_path": str(DEFAULT_MASK_PATH),
            "coordinate_frame": "auto",
            "bindings": default_binding_text(),
            "sample_pitch_px": "16",
            "pattern": "serpentine",
            "power_min_mw": "0",
            "power_max_mw": "5",
        }
        params.update(overrides)
        return AutoTestFlowStep("virtual_mask", params)

    def test_default_nus_mask_builds_xy_scatter_plan_without_layoutmap(self) -> None:
        plan = generate_mapping_plan(
            MappingSettings(
                x_a_um=20, y_a_um=10, x_b_um=60, y_b_um=30,
                step_x_um=20, step_y_um=10,
                measurement_flow=(self.mask_step(sample_pitch_px="1", pattern="raster"),),
            ),
            (0.1, 0.1, 0.1),
        )
        # Main-page bounds/steps/path own the scan. Legacy image pitch/path are ignored.
        self.assertEqual(plan.xs, (20.0, 40.0, 60.0))
        self.assertEqual(plan.ys, (10.0, 20.0, 30.0))
        self.assertEqual(len(plan.points), 9)
        self.assertAlmostEqual(plan.points[0].stage_x_um, 20.0, places=6)
        self.assertAlmostEqual(plan.points[0].stage_y_um, 10.0, places=6)
        self.assertIsNone(plan.points[0].layout_u)
        self.assertTrue(all(0 <= point.normalized_intensity <= 1 for point in plan.points))
        self.assertTrue(all(0 <= point.target_power_mw <= 5 for point in plan.points))
        self.assertGreater(plan.points[3].col, plan.points[4].col)  # main-page serpentine path

    def test_auto_uses_uv_when_layoutmap_snapshot_exists(self) -> None:
        mapper = AffineCoordinateMapper(np.array([[10.0, 2.0, 0.0], [-5.0, 0.0, 3.0]]))
        plan = generate_mapping_plan(
            MappingSettings(measurement_flow=(self.mask_step(),), gds_mapper=mapper.to_dict()),
            (0.1, 0.1, 0.1),
        )
        first = plan.points[0]
        # LayoutMap changes only how the image is sampled; the stage grid still
        # begins at the main-page Corner A (default 0, 0).
        self.assertAlmostEqual(first.layout_u, -5.0, places=6)
        self.assertAlmostEqual(first.layout_v, 5.0 / 3.0, places=6)
        self.assertAlmostEqual(first.stage_x_um, 0.0, places=6)
        self.assertAlmostEqual(first.stage_y_um, 0.0, places=6)

    def test_explicit_uv_rejects_missing_layoutmap(self) -> None:
        with self.assertRaisesRegex(ValueError, "LayoutMap"):
            generate_mapping_plan(
                MappingSettings(measurement_flow=(self.mask_step(coordinate_frame="uv"),)),
                (0.1, 0.1, 0.1),
            )

    def test_grayscale_controls_target_power(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mask.png"
            cv2.imwrite(str(path), np.array([[0, 255], [128, 64]], dtype=np.uint8))
            step = self.mask_step(
                image_path=str(path), sample_pitch_px="1",
                bindings="0,0,0,0;1,0,10,0;0,1,0,10;1,1,10,10",
                power_min_mw="1", power_max_mw="9",
            )
            plan = generate_mapping_plan(MappingSettings(
                x_a_um=0, y_a_um=0, x_b_um=10, y_b_um=10,
                step_x_um=10, step_y_um=10,
                measurement_flow=(step,),
            ), (1, 1, 1))
        powers = {(point.mask_pixel_x, point.mask_pixel_y): point.target_power_mw for point in plan.points}
        self.assertEqual(powers[(0, 0)], 1.0)
        self.assertEqual(powers[(1, 0)], 9.0)
        self.assertAlmostEqual(powers[(0, 1)], 1 + 128 / 255 * 8)

    def test_inverse_grayscale_swaps_black_and_white_power(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mask.png"
            cv2.imwrite(str(path), np.array([[0, 255], [128, 64]], dtype=np.uint8))
            step = self.mask_step(
                image_path=str(path), invert_intensity="true",
                bindings="0,0,0,0;1,0,10,0;0,1,0,10;1,1,10,10",
                power_min_mw="1", power_max_mw="9",
            )
            plan = generate_mapping_plan(MappingSettings(
                x_a_um=0, y_a_um=0, x_b_um=10, y_b_um=10,
                step_x_um=10, step_y_um=10, measurement_flow=(step,),
            ), (1, 1, 1))
        powers = {(point.mask_pixel_x, point.mask_pixel_y): point.target_power_mw for point in plan.points}
        self.assertEqual(powers[(0, 0)], 9.0)
        self.assertEqual(powers[(1, 0)], 1.0)

    def test_binding_parser_rejects_collinear_pixels(self) -> None:
        with self.assertRaisesRegex(ValueError, "collinear"):
            parse_binding_points("0,0,0,0;1,1,5,5;2,2,10,10")

    def test_virtual_mask_workflow_plot_is_cell_heatmap_not_scatter(self) -> None:
        plan = generate_mapping_plan(
            MappingSettings(measurement_flow=(self.mask_step(),)),
            (0.1, 0.1, 0.1),
        )
        primary = np.full((len(plan.ys), len(plan.xs)), np.nan)
        secondary = np.full_like(primary, np.nan)
        states = np.ones(primary.shape, dtype=np.uint8)
        for point in plan.points:
            primary[point.row, point.col] = point.target_power_mw
            secondary[point.row, point.col] = point.normalized_intensity
        figure = Figure()
        axes = draw_workflow_heatmap_figure(
            figure, plan, primary, secondary, states,
            primary_label="Target power", secondary_label="Measured current",
        )
        for axis in axes:
            self.assertTrue(any(isinstance(item, QuadMesh) for item in axis.collections))
            self.assertFalse(any(isinstance(item, PathCollection) for item in axis.collections))

    def test_virtual_mask_exposes_only_black_and_white_laser_power(self) -> None:
        definition = autotest_flow_definitions_by_type()["virtual_mask"]
        keys = {parameter.key for parameter in definition.parameters}
        defaults = {parameter.key: parameter.default for parameter in definition.parameters}
        self.assertIn("black_power_mw", keys)
        self.assertIn("white_power_mw", keys)
        self.assertEqual(defaults["invert_intensity"], "false")
        self.assertNotIn("sample_pitch_px", keys)
        self.assertNotIn("pattern", keys)
        self.assertFalse(keys & {
            "drive_voltage_v", "drive_current_min_a", "drive_current_max_a",
            "ovp_v", "ocp_a", "preset_index", "power_min_mw", "power_max_mw",
        })
        self.assertEqual(defaults["coordinate_frame"], "xy")

    def test_default_template_displays_only_measured_current(self) -> None:
        cards = cards_from_template(VIRTUAL_MASK_MAPPING_TEMPLATE)
        calibration = next(card for card in cards if card.type_id == "laser_iv_calibration")
        mask = next(card for card in cards if card.type_id == "virtual_mask")
        heatmap = next(card for card in cards if card.type_id == "plot_panel")
        self.assertEqual(mask.params["coordinate_frame"], "xy")
        self.assertEqual(mask.params["invert_intensity"], "false")
        self.assertEqual(calibration.params["_next"], mask.card_id)
        self.assertEqual(mask.params["_input_calibration_source"], calibration.card_id)
        self.assertNotIn("sample_pitch_px", mask.params)
        self.assertNotIn("pattern", mask.params)
        self.assertNotIn("_outputs", mask.params)
        self.assertEqual(heatmap.params["slot1_title"], "Measured Current (A)")
        self.assertEqual(heatmap.params["_input_slot1_source"], "mask_current")
        self.assertEqual(heatmap.params["_input_slot1_output"], "statistics.mean_current_a")
        self.assertNotIn("_input_slot2_source", heatmap.params)

    def test_calibrated_power_uses_measured_power_voltage_curve(self) -> None:
        setpoint = calibrated_power_setpoint(6.0, [(.001, 2.0), (.003, 3.0)])
        self.assertAlmostEqual(setpoint.voltage_v, 2.0 + 4.0 / 7.0)
        self.assertEqual(setpoint.current_a, .003)
        self.assertEqual(calibrated_power_setpoint(.5, [(.001, 2.0), (.003, 3.0)]).voltage_v, 0)

    def test_target_power_uses_cached_measured_power_voltage_curve(self) -> None:
        driver = SimulatedDP100Driver()
        driver.connect()
        app = ProbeApp.__new__(ProbeApp)
        app._get_dp100_driver = lambda: driver
        app._mapping_laser_used = False
        app._mapping_laser_calibrations = {
            "virtual_mask_power_calibration": [(.001, 2.0), (.003, 3.0)],
        }
        app.autotest_stop_event = threading.Event()
        app.result_queue = queue.Queue()
        point = MappingPoint(
            0, 0, 1, 0, 0, 0, 0,
            mask_pixel_x=0, mask_pixel_y=0, normalized_intensity=.5, target_power_mw=6,
        )
        step = AutoTestFlowStep("virtual_mask", {"black_power_mw": "0", "white_power_mw": "12", "settle_s": "0"})
        settings = MappingSettings(measurement_flow=(step,))
        with tempfile.TemporaryDirectory() as directory:
            payload = ProbeApp._execute_autotest_flow_step_impl(
                app, point, settings, step, Path(directory), None,
            )
        self.assertAlmostEqual(driver.last_setpoint.voltage_v, 2.0 + 4.0 / 7.0)
        self.assertAlmostEqual(driver.last_setpoint.current_a, .003)
        self.assertTrue(driver.output_enabled)
        self.assertEqual(payload["target_power_mw"], 6)

    def test_sub_resolution_mask_power_turns_output_off_instead_of_failing(self) -> None:
        driver = SimulatedDP100Driver()
        driver.connect()
        app = ProbeApp.__new__(ProbeApp)
        app._get_dp100_driver = lambda: driver
        app._mapping_laser_used = False
        app._mapping_laser_calibrations = {
            "virtual_mask_power_calibration": [(.001, 2.0), (.003, 3.0)],
        }
        app.autotest_stop_event = threading.Event()
        app.result_queue = queue.Queue()
        point = MappingPoint(
            0, 0, 1, 0, 0, 0, 0,
            mask_pixel_x=0, mask_pixel_y=0, normalized_intensity=.01, target_power_mw=.05,
        )
        step = AutoTestFlowStep("virtual_mask", {"black_power_mw": "0", "white_power_mw": "5", "settle_s": "0"})

        payload = ProbeApp._execute_autotest_flow_step_impl(
            app, point, MappingSettings(measurement_flow=(step,)), step, None, None,
        )

        self.assertFalse(driver.output_enabled)
        self.assertEqual(driver.last_setpoint.current_ma, 0)
        self.assertEqual(payload["applied_power_mw"], 0)

    def test_same_quantized_mask_level_does_not_repeat_dp100_command(self) -> None:
        driver = SimulatedDP100Driver()
        driver.connect()
        app = ProbeApp.__new__(ProbeApp)
        app._get_dp100_driver = lambda: driver
        app._mapping_laser_used = False
        app._mapping_laser_calibrations = {
            "virtual_mask_power_calibration": [(.001, 2.0), (.003, 3.0)],
        }
        app.autotest_stop_event = threading.Event()
        app.result_queue = queue.Queue()
        step = AutoTestFlowStep("virtual_mask", {"settle_s": "0"})
        settings = MappingSettings(measurement_flow=(step,))
        for target in (6.0, 6.0001):  # Both interpolate to the same DP100 millivolt.
            point = MappingPoint(
                0, 0, 1, 0, 0, 0, 0,
                mask_pixel_x=0, mask_pixel_y=0, normalized_intensity=.5,
                target_power_mw=target,
            )
            ProbeApp._execute_autotest_flow_step_impl(app, point, settings, step, None, None)

        self.assertEqual(len(driver.commands), 1)

    def test_unversioned_virtual_mask_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "version 2"):
            cards_from_workflow_payload({"nodes": [{"type_id": "virtual_mask", "params": {}}]})

    def test_normal_dp100_point_updates_are_hidden_from_info_console(self) -> None:
        self.assertTrue(is_high_frequency_dp100_status("Virtual mask: 5 mW target at 100% gray"))
        self.assertTrue(is_high_frequency_dp100_status("DP100 ON: 3 V / 0.01 A"))
        self.assertFalse(is_high_frequency_dp100_status("DP100 output cleanup failed"))
        self.assertFalse(is_high_frequency_dp100_status("Mapping failed: DP100 readback timeout"))


if __name__ == "__main__":
    unittest.main()
