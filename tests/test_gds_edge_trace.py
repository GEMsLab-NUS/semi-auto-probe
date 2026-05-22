from __future__ import annotations

import queue
import unittest
from pathlib import Path
from unittest import mock

from semi_auto_probe.af_plane import SamplePlaneModel, clear_sample_plane_model, set_sample_plane_model
from semi_auto_probe.app import ProbeApp
from semi_auto_probe.edge_trace_panel import (
    EDGE_TRACE_ACTION_CONTACT,
    EDGE_TRACE_ACTION_SAFE,
    EDGE_TRACE_ACTION_START,
)
from semi_auto_probe.gds_edge_trace import build_edge_trace_plan, extract_edge_trace_polylines
from semi_auto_probe.gds_stage_mapper import AffineCoordinateMapper, CalibrationPoint, GDSLayoutModel, GDSShape
from semi_auto_probe.protocol import Axis, AxisPosition


def rectangle(min_u: float, min_v: float, max_u: float, max_v: float, *, layer: int = 1, datatype: int = 0) -> GDSShape:
    points = ((min_u, min_v), (max_u, min_v), (max_u, max_v), (min_u, max_v))
    return GDSShape(points=points, layer=layer, datatype=datatype, bbox=(min_u, min_v, max_u, max_v))


def model_for(shapes: list[GDSShape]) -> GDSLayoutModel:
    min_u = min(shape.bbox[0] for shape in shapes)
    min_v = min(shape.bbox[1] for shape in shapes)
    max_u = max(shape.bbox[2] for shape in shapes)
    max_v = max(shape.bbox[3] for shape in shapes)
    return GDSLayoutModel(
        path=Path("test.gds"),
        top_cell_name="TOP",
        top_cell_names=("TOP",),
        shapes=shapes,
        labels=[],
        bounds=(min_u, min_v, max_u, max_v),
    )


class EdgeTracePlannerTests(unittest.TestCase):
    def mapper(self) -> AffineCoordinateMapper:
        return AffineCoordinateMapper.fit(
            [
                CalibrationPoint("P1", 0.0, 0.0, 0.0, 0.0),
                CalibrationPoint("P2", 10.0, 0.0, 10.0, 0.0),
                CalibrationPoint("P3", 0.0, 10.0, 0.0, 10.0),
                CalibrationPoint("P4", 10.0, 10.0, 10.0, 10.0),
            ]
        )

    def test_overlapping_polygons_are_union_boundary_not_internal_edges(self) -> None:
        model = model_for([rectangle(0, 0, 10, 10), rectangle(5, 0, 15, 10)])

        polylines = extract_edge_trace_polylines(model, (1, 0), (-1, -1, 16, 11), max_step_um=100, min_segment_um=0)

        self.assertEqual(len(polylines), 1)
        self.assertAlmostEqual(polylines[0].length_um, 50.0)
        points = {point.gds for point in polylines[0].points}
        self.assertNotIn((5.0, 0.0), points)
        self.assertNotIn((5.0, 10.0), points)

    def test_hole_bridge_edges_are_removed(self) -> None:
        bridged_hole = GDSShape(
            points=((10, 10), (0, 10), (0, 3), (3, 3), (3, 7), (7, 7), (7, 3), (3, 3), (0, 3), (0, 0), (10, 0)),
            layer=1,
            datatype=0,
            bbox=(0, 0, 10, 10),
        )
        model = model_for([bridged_hole])

        polylines = extract_edge_trace_polylines(model, (1, 0), (-1, -1, 11, 11), max_step_um=100, min_segment_um=0)

        self.assertEqual(sorted(round(polyline.length_um, 6) for polyline in polylines), [16.0, 40.0])
        all_edges = [
            (start.gds, end.gds)
            for polyline in polylines
            for start, end in zip(polyline.points, polyline.points[1:])
        ]
        self.assertNotIn(((0.0, 3.0), (3.0, 3.0)), all_edges)
        self.assertNotIn(((3.0, 3.0), (0.0, 3.0)), all_edges)

    def test_roi_clips_true_edges_without_adding_roi_border(self) -> None:
        model = model_for([rectangle(0, 0, 10, 10)])

        polylines = extract_edge_trace_polylines(model, (1, 0), (-1, -1, 5, 5), max_step_um=100, min_segment_um=0)

        self.assertEqual(sum(round(polyline.length_um, 6) for polyline in polylines), 10.0)
        all_points = [point.gds for polyline in polylines for point in polyline.points]
        self.assertIn((5.0, 0.0), all_points)
        self.assertIn((0.0, 5.0), all_points)
        self.assertNotIn((5.0, 5.0), all_points)

    def test_offset_changes_boundary_size(self) -> None:
        model = model_for([rectangle(0, 0, 10, 10)])

        base = build_edge_trace_plan(model, (1, 0), (-5, -5, 15, 15), max_step_um=100, min_segment_um=0)
        expanded = build_edge_trace_plan(model, (1, 0), (-5, -5, 15, 15), offset_um=1, max_step_um=100, min_segment_um=0)
        eroded = build_edge_trace_plan(model, (1, 0), (-5, -5, 15, 15), offset_um=-1, max_step_um=100, min_segment_um=0)

        self.assertAlmostEqual(base.total_scratch_length_um, 40.0)
        self.assertGreater(expanded.total_scratch_length_um, base.total_scratch_length_um)
        self.assertLess(eroded.total_scratch_length_um, base.total_scratch_length_um)

    def test_negative_offset_that_erodes_geometry_skips_trace(self) -> None:
        model = model_for([rectangle(0, 0, 10, 10)])

        plan = build_edge_trace_plan(model, (1, 0), (-5, -5, 15, 15), offset_um=-6, max_step_um=100, min_segment_um=0)

        self.assertEqual(plan.polylines, ())
        self.assertIn("removed all geometry", " ".join(plan.warnings))

    def test_mapper_and_focusmap_populate_xyz_and_z_offsets(self) -> None:
        model = model_for([rectangle(0, 0, 10, 10)])

        plan = build_edge_trace_plan(
            model,
            (1, 0),
            (-1, -1, 11, 11),
            max_step_um=100,
            min_segment_um=0,
            scratch_depth_um=3,
            lift_height_um=5,
            mapper=self.mapper(),
            focus_z_at_stage_um=lambda x, y: x + 2 * y,
            current_gds=(-0.5, -0.5),
        )

        first = plan.polylines[0].points[0]
        self.assertIsNotNone(first.stage_x_um)
        self.assertIsNotNone(first.stage_y_um)
        self.assertAlmostEqual(first.surface_z_um, first.stage_x_um + 2 * first.stage_y_um)
        self.assertAlmostEqual(first.scratch_z_um, first.surface_z_um + 3)
        self.assertAlmostEqual(first.travel_z_um, first.surface_z_um - 5)
        self.assertTrue(any(segment.kind == "travel" and segment.dashed for segment in plan.segments))

    def test_nearest_order_can_reverse_open_path(self) -> None:
        model = model_for([rectangle(0, 0, 10, 10)])

        polylines = extract_edge_trace_polylines(model, (1, 0), (-1, -1, 5, 5), max_step_um=100, min_segment_um=0, current_gds=(4.9, 0.0))

        self.assertTrue(polylines)
        first = polylines[0]
        self.assertLessEqual(
            ((first.start.u - 4.9) ** 2 + (first.start.v - 0.0) ** 2) ** 0.5,
            ((first.end.u - 4.9) ** 2 + (first.end.v - 0.0) ** 2) ** 0.5,
        )


class DummyVar:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class DummyEdgeTracePanel:
    def __init__(self) -> None:
        self.status = ""
        self.running = False
        self.plan = None
        self.actions_done: list[tuple[str, int]] = []

    def set_plan(self, plan) -> None:
        self.plan = plan

    def set_status(self, message: str) -> None:
        self.status = message

    def set_running(self, running: bool) -> None:
        self.running = running

    def set_action_done(self, action: str, polyline_offset: int) -> None:
        self.actions_done.append((action, polyline_offset))

    def set_action_failed(self, _action: str | None = None) -> None:
        return None


class DummyMapperPanel:
    def __init__(self, mapper: AffineCoordinateMapper) -> None:
        self.mapper = mapper


class DummyProbeConfig:
    def __init__(self, *, acceleration_units: int = 10) -> None:
        self.acceleration_units = acceleration_units

    def um_per_pulse(self, _axis: str) -> float:
        return 1.0

    def motor_speed_percent(self, _profile: str | None = None) -> int:
        return 100

    def cc_acceleration_units(self) -> int:
        return self.acceleration_units


class FakeEdgeTraceSerial:
    def __init__(self, *, x: int, y: int, z: int, frozen_move_indices: set[int] | None = None) -> None:
        self.positions = {Axis.X: x, Axis.Y: y, Axis.Z: z}
        self.moves: list[dict[Axis, tuple[bool, int, int, int]]] = []
        self.relative_moves: list[tuple[Axis, bool, int, int]] = []
        self.frozen_move_indices = set(frozen_move_indices or set())

    def read_stable_xyz_positions(self):
        return [
            (b"", b"", AxisPosition(axis, False, self.positions[axis], b""))
            for axis in (Axis.X, Axis.Y, Axis.Z)
        ]

    def read_xyz_positions(self):
        return self.read_stable_xyz_positions()

    def move_multi_axis_relative_and_wait(self, axis_params, timeout: float = 10.0):
        self.moves.append(dict(axis_params))
        if len(self.moves) in self.frozen_move_indices:
            return b"move", b"ack"
        for axis, (reverse, pulses, _speed, _acceleration) in axis_params.items():
            self.positions[axis] += -pulses if reverse else pulses
        return b"move", b"done"

    def move_relative(self, axis: Axis, reverse: bool, pulses: int, speed_percent: int = 100):
        self.relative_moves.append((axis, reverse, pulses, speed_percent))
        self.positions[axis] += -pulses if reverse else pulses
        return b"relative"

    def wait_axis_reached(self, axis: Axis, timeout: float = 5.0):
        return b"reached"


class EdgeTraceAppBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_sample_plane_model()

    def tearDown(self) -> None:
        clear_sample_plane_model()

    def _plan(self):
        mapper = EdgeTracePlannerTests().mapper()
        model = model_for([rectangle(0, 0, 10, 10)])
        plan = build_edge_trace_plan(
            model,
            (1, 0),
            (-1, -1, 11, 11),
            max_step_um=100,
            min_segment_um=0,
            mapper=mapper,
            focus_z_at_stage_um=lambda _x, _y: 42.0,
            current_gds=(0, 0),
        )
        return plan, mapper

    def _app_shell(self, mapper: AffineCoordinateMapper) -> ProbeApp:
        app = ProbeApp.__new__(ProbeApp)
        app.edge_trace_panel = DummyEdgeTracePanel()
        app.status_var = DummyVar()
        app.edge_trace_running = False
        app.motion_busy = False
        app.keyboard_motion_busy = False
        app.autofocus_running = False
        app.af_plane_running = False
        app.imgstitch_running = False
        app.imgmatrix_running = False
        app.autotest_running = False
        app.position_read_pending = False
        app.gds_stage_mapper_panel = DummyMapperPanel(mapper)
        app.serial_client = object()
        app.realtime_enabled = False
        app.home_signal_enabled = False
        app.edge_trace_restore_realtime = False
        app.edge_trace_restore_home_signal = False
        import threading

        app.edge_trace_stop_event = threading.Event()
        return app

    def test_safe_action_sets_running_state_and_starts_worker(self) -> None:
        plan, mapper = self._plan()
        app = self._app_shell(mapper)
        set_sample_plane_model(
            SamplePlaneModel(
                enabled=True,
                type="plane",
                a=0.0,
                b=0.0,
                c=42.0,
                rms_residual=0.0,
                pv_residual=0.0,
                max_abs_residual=0.0,
                tilt_x_deg=0.0,
                tilt_y_deg=0.0,
                valid_points=3,
                failed_points=0,
            )
        )

        with mock.patch("semi_auto_probe.app.threading.Thread") as thread_cls:
            thread_instance = thread_cls.return_value
            ProbeApp.start_edge_trace(app, plan, EDGE_TRACE_ACTION_SAFE, 0)

        self.assertTrue(app.edge_trace_running)
        self.assertTrue(app.motion_busy)
        self.assertTrue(app.edge_trace_panel.running)
        thread_instance.start.assert_called_once()

    def test_step_actions_lower_material_then_move_to_start(self) -> None:
        mapper = EdgeTracePlannerTests().mapper()
        model = model_for([rectangle(0, 0, 10, 10)])
        plan = build_edge_trace_plan(
            model,
            (1, 0),
            (-1, -1, 11, 11),
            max_step_um=100,
            min_segment_um=0,
            lift_height_um=5,
            mapper=mapper,
            focus_z_at_stage_um=lambda _x, _y: 100.0,
            current_gds=(0, 0),
        )
        app = self._app_shell(mapper)
        app.serial_client = FakeEdgeTraceSerial(x=100, y=100, z=120)
        app.probe_config = DummyProbeConfig(acceleration_units=17)
        app.current_position_values = {"X": 100, "Y": 100, "Z": 120}
        app.result_queue = queue.Queue()
        app._gds_mapper_focus_z_um = lambda _x, _y: 100.0

        ProbeApp._edge_trace_action_worker(app, plan, EDGE_TRACE_ACTION_SAFE, 0)

        self.assertTrue(app.serial_client.moves)
        first_move = app.serial_client.moves[0]
        self.assertEqual(set(first_move), {Axis.Z})
        reverse, pulses, _speed, _acceleration = first_move[Axis.Z]
        self.assertTrue(reverse)
        self.assertEqual(pulses, 25)
        self.assertEqual(_acceleration, 17)
        self.assertIn((EDGE_TRACE_ACTION_SAFE, 0), [event[1:] for event in app.result_queue.queue if event[0] == "edge_trace_action_done"])

        ProbeApp._edge_trace_action_worker(app, plan, EDGE_TRACE_ACTION_START, 0)

        self.assertGreaterEqual(len(app.serial_client.moves), 2)
        second_move = app.serial_client.moves[1]
        self.assertIn(Axis.X, second_move)
        self.assertIn(Axis.Y, second_move)
        self.assertNotIn(Axis.Z, second_move)
        self.assertEqual(second_move[Axis.X][1], 100)
        self.assertEqual(second_move[Axis.Y][1], 100)
        self.assertEqual(second_move[Axis.X][3], 17)
        self.assertEqual(second_move[Axis.Y][3], 17)

    def test_contact_uses_fa_relative_z_and_b5_wait_not_cc(self) -> None:
        mapper = EdgeTracePlannerTests().mapper()
        model = model_for([rectangle(0, 0, 10, 10)])
        plan = build_edge_trace_plan(
            model,
            (1, 0),
            (-1, -1, 11, 11),
            max_step_um=100,
            min_segment_um=0,
            lift_height_um=5,
            mapper=mapper,
            focus_z_at_stage_um=lambda _x, _y: 100.0,
            current_gds=(0, 0),
        )
        app = self._app_shell(mapper)
        app.serial_client = FakeEdgeTraceSerial(x=0, y=0, z=95)
        app.probe_config = DummyProbeConfig()
        app.current_position_values = {"X": 0, "Y": 0, "Z": 95}
        app.result_queue = queue.Queue()

        ProbeApp._edge_trace_action_worker(app, plan, EDGE_TRACE_ACTION_CONTACT, 0)

        self.assertEqual(app.serial_client.moves, [])
        self.assertTrue(app.serial_client.relative_moves)
        self.assertTrue(all(move[0] == Axis.Z for move in app.serial_client.relative_moves))
        self.assertTrue(all(move[1] is False for move in app.serial_client.relative_moves))
        self.assertEqual(sum(move[2] for move in app.serial_client.relative_moves), 5)
        events = list(app.result_queue.queue)
        self.assertTrue(any(event[0] == "axis_done" for event in events))
        self.assertTrue(any(event[0] == "edge_trace_action_done" and event[1] == EDGE_TRACE_ACTION_CONTACT for event in events))

    def test_worker_aborts_before_contact_if_travel_target_is_not_verified(self) -> None:
        mapper = EdgeTracePlannerTests().mapper()
        model = model_for([rectangle(0, 0, 10, 10)])
        plan = build_edge_trace_plan(
            model,
            (1, 0),
            (-1, -1, 11, 11),
            max_step_um=100,
            min_segment_um=0,
            lift_height_um=5,
            mapper=mapper,
            focus_z_at_stage_um=lambda _x, _y: 100.0,
            current_gds=(0, 0),
        )
        app = self._app_shell(mapper)
        app.serial_client = FakeEdgeTraceSerial(x=100, y=100, z=120, frozen_move_indices={2})
        app.probe_config = DummyProbeConfig()
        app.current_position_values = {"X": 100, "Y": 100, "Z": 120}
        app.result_queue = queue.Queue()
        app._gds_mapper_focus_z_um = lambda _x, _y: 100.0
        app._cc_move_timeout = lambda _axis_params: 0.1

        ProbeApp._edge_trace_action_worker(app, plan, EDGE_TRACE_ACTION_SAFE, 0)
        ProbeApp._edge_trace_action_worker(app, plan, EDGE_TRACE_ACTION_START, 0)

        self.assertEqual(len(app.serial_client.moves), 2)
        events = list(app.result_queue.queue)
        self.assertTrue(any(event[0] == "edge_trace_error" for event in events))
        self.assertFalse(any(event[0] == "edge_trace_action_done" and event[1] == EDGE_TRACE_ACTION_START for event in events))


if __name__ == "__main__":
    unittest.main()
