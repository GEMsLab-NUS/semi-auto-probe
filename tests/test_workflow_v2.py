import json
import math
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

from semi_auto_probe.auto_test import create_autotest_flow_card, measurement_flow_steps_from_cards, AutoTestSettings
from semi_auto_probe.flow_graph import control_node_runs_on_every_path, resolve_data_input, evaluate_condition, validate_flow_graph
from semi_auto_probe.workflow_formula import Curve, InvalidValue, evaluate_expression
from semi_auto_probe.workflow_library import cards_from_template, cards_from_workflow_payload, overwrite_workflow, save_workflow, workflow_payload
from semi_auto_probe.workflow_plot import PlotSlot, LAYOUTS, validate_panel
from semi_auto_probe.workflow_runtime import NodeCapture, DisplayMailbox
from semi_auto_probe.app import ProbeApp


def card(kind, name, **params):
    node = create_autotest_flow_card(kind, name)
    node.params.update(params)
    return node


def wire(target, name, source, output, dtype):
    target.params.update({f"_input_{name}_source": source, f"_input_{name}_output": output, f"_input_{name}_type": dtype})


class FormulaTests(unittest.TestCase):
    def test_scalar_and_typed_boolean(self):
        self.assertEqual(evaluate_expression("sqrt(a**2) + log10(b)", {"a": -3, "b": 100}), 5)
        self.assertIs(evaluate_expression("a > 1 and b < 4", {"a": 2, "b": 3}, "bool"), True)
        self.assertIsInstance(evaluate_expression("a > 1", {"a": 2}), InvalidValue)
        self.assertIsInstance(evaluate_expression("a", {"a": 2}, "bool"), InvalidValue)

    def test_curve_coordinates_and_acquisition_order(self):
        curve = Curve((0., 1., 3.), (1., 3., 7.))
        self.assertAlmostEqual(evaluate_expression("slope(c)", {"c": curve}), 2)
        self.assertAlmostEqual(evaluate_expression("integral(c)", {"c": curve}), 12)
        reversed_curve = Curve(tuple(reversed(curve.x)), tuple(reversed(curve.y)))
        self.assertEqual(evaluate_expression("integral(c)", {"c": reversed_curve}), -12)
        self.assertEqual(evaluate_expression("mean(segment(c, 1, 3))", {"c": curve}), 5)

    def test_invalid_and_disallowed_expressions(self):
        for expression in ("1/0", "sqrt(-1)", "exp(1000)", "2**999999", "__import__('os')", "a.__class__", "[x for x in a]", "open('file')", "slope(c)"):
            with self.subTest(expression=expression):
                self.assertIsInstance(evaluate_expression(expression, {"a": 1, "c": Curve((1., 1.), (2., 3.))}), InvalidValue)
        self.assertIsInstance(evaluate_expression("a", {"a": float("nan")}), InvalidValue)
        with self.assertRaises(ValueError):
            evaluate_expression("1/0", {}, policy="stop")

    def test_unexecuted_branch_never_uses_constant_fallback(self):
        source = card("iv", "read")
        formula = card("math_add", "calc", fallback_a="100", fallback_b="3")
        wire(formula, "a", "read", "statistics.resistance_ohm", "number")
        branch = card("condition_metric", "branch", threshold="0", operator=">")
        wire(branch, "value", "calc", "result", "number")
        steps = measurement_flow_steps_from_cards([source, formula, branch])
        self.assertIsInstance(resolve_data_input(steps[-1], "value", steps, {}), InvalidValue)
        self.assertFalse(evaluate_condition(steps[-1], {}, steps=steps))
        self.assertTrue(evaluate_condition(steps[-1], {"read": {"statistics": {"resistance_ohm": 2}}}, steps=steps))

    def test_curve_family_explicit_selection(self):
        source = card("iv", "read")
        formula = card("math_formula", "calc", variables="signal:curve", expression="mean(signal)", curve_series="first")
        wire(formula, "signal", "read", "curve", "curve")
        sink = card("mapping_heatmap", "map")
        wire(sink, "primary", "calc", "result", "number")
        steps = measurement_flow_steps_from_cards([source, formula, sink])
        results = {"read": {"curve": {"first": Curve((0, 1), (2, 4)), "second": Curve((0, 1), (20, 40))}}}
        self.assertEqual(resolve_data_input(steps[-1], "primary", steps, results), 3)


class GraphAndPersistenceTests(unittest.TestCase):
    def test_templates_validate_and_round_trip(self):
        for name in ("General / Examples / Current and Heatmap", "General / Examples / Curve Feature Branch"):
            cards = cards_from_template(name)
            self.assertEqual(validate_flow_graph(measurement_flow_steps_from_cards(cards)), ())
            payload = workflow_payload(cards)
            self.assertEqual(payload["version"], 2)
            self.assertEqual(workflow_payload(cards_from_workflow_payload(payload)), payload)

    def test_legacy_workflow_format_is_rejected(self):
        payload = {"version": 1, "nodes": [{"type_id": "math_formula", "params": {"_node_id": "calc", "operator": "/", "fallback_a": "20", "fallback_b": "4"}}]}
        for legacy in (payload, {"nodes": []}, []):
            with self.assertRaisesRegex(ValueError, "version 2"):
                cards_from_workflow_payload(legacy)

    def test_overwrite_keeps_existing_file_when_new_workflow_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = save_workflow(Path(tmp) / "old.json",
                                 cards_from_template("General / Examples / Current and Heatmap"), name="Old custom")
            previous = path.read_bytes()
            invalid = card("math_formula", "invalid", operator="+")
            with self.assertRaises(ValueError):
                overwrite_workflow(path, [invalid])
            self.assertEqual(path.read_bytes(), previous)

    def test_missing_end_wrong_source_type_and_control_cycles(self):
        nodes = cards_from_template("General / Examples / Current and Heatmap")
        nodes[-1].params["_input_slot1_output"] = "statistics.mean_current_a"
        self.assertTrue(any("expects trace" in error for error in validate_flow_graph(measurement_flow_steps_from_cards(nodes))))
        nodes = cards_from_template("General / Examples / Current and Heatmap")
        nodes[1].params["_next"] = "read"
        self.assertTrue(any("cycle" in error for error in validate_flow_graph(measurement_flow_steps_from_cards(nodes))))
        self.assertTrue(any("End" in error for error in validate_flow_graph(measurement_flow_steps_from_cards(nodes[:2] + nodes[3:]))))

    def test_calibration_must_precede_consumer(self):
        nodes = [card("workflow_start", "start", _next="mask", _workflow_version="2"),
                 card("virtual_mask", "mask", _next="cal"), card("laser_iv_calibration", "cal", _next="end"), card("workflow_end", "end")]
        wire(nodes[1], "calibration", "cal", "calibration", "calibration")
        errors = validate_flow_graph(measurement_flow_steps_from_cards(nodes))
        self.assertTrue(any("every incoming path" in error for error in errors), errors)


class PlotTests(unittest.TestCase):
    def test_layouts_ranges_and_log_validation(self):
        node = card("plot_panel", "plots")
        for layout in LAYOUTS:
            node.params["layout"] = layout
            validate_panel(node)
        node.params.update(slot1_xscale="log", slot1_xlim="0,10")
        with self.assertRaises(ValueError):
            validate_panel(node)

    def test_multiple_plot_panels_are_valid(self):
        start, read, end = card("workflow_start", "start"), card("iv", "read"), card("workflow_end", "end")
        first, second = card("plot_panel", "first_plot"), card("plot_panel", "second_plot")
        start.params["_next"] = "read"
        read.params["_next"] = "end"
        wire(first, "slot1", "read", "live", "trace")
        wire(second, "slot1", "read", "curve", "curve")
        steps = measurement_flow_steps_from_cards([start, read, end, first, second])
        self.assertEqual(validate_flow_graph(steps), ())

    def test_conditional_fine_source_is_optional(self):
        start, coarse = card("workflow_start", "start"), card("iv", "coarse")
        condition, fine, end = card("condition_metric", "condition"), card("iv", "fine"), card("workflow_end", "end")
        start.params["_next"] = "coarse"
        coarse.params["_next"] = "condition"
        condition.params.update(_true="fine", _false="end")
        fine.params["_next"] = "end"
        steps = measurement_flow_steps_from_cards([start, coarse, condition, fine, end])
        self.assertTrue(control_node_runs_on_every_path(steps, "coarse"))
        self.assertFalse(control_node_runs_on_every_path(steps, "fine"))

    def test_persistent_artists_and_same_source_family(self):
        fig = Figure(figsize=(4, 3))
        FigureCanvasAgg(fig)
        slot = PlotSlot(fig, {}, 1, (2, 3), "trace")
        curves = {"bias1": Curve((0, 1, 2), (1, 2, 3)), "bias2": Curve((0, 1, 2), (2, 4, 6))}
        slot.curves(curves)
        first = slot.lines["bias1"]
        slot.curves(curves)
        self.assertIs(first, slot.lines["bias1"])
        self.assertEqual(len(slot.axes.lines), 2)
        slot.curves({})
        self.assertEqual(len(slot.axes.lines), 0)

    def test_curve_and_trace_slots_use_subtle_grid_but_heatmap_does_not(self):
        for mode in ("curve", "trace"):
            slot = PlotSlot(Figure(figsize=(4, 3)), {}, 1, (2, 3), mode)
            slot.figure.canvas.draw()
            gridlines = slot.axes.get_xgridlines() + slot.axes.get_ygridlines()
            self.assertTrue(gridlines)
            self.assertTrue(all(line.get_visible() for line in gridlines))
            self.assertTrue(all(line.get_color() == "#808080" for line in gridlines))
            self.assertTrue(all(line.get_alpha() == 0.28 for line in gridlines))
            self.assertEqual(slot.axes.get_axisbelow(), True)

        heatmap = PlotSlot(Figure(figsize=(4, 3)), {}, 1, (2, 3), "number")
        heatmap.figure.canvas.draw()
        self.assertFalse(any(line.get_visible() for line in
                             heatmap.axes.get_xgridlines() + heatmap.axes.get_ygridlines()))

    def test_workflow_plot_ticks_use_engineering_units(self):
        fig = Figure(figsize=(4, 3))
        FigureCanvasAgg(fig)
        slot = PlotSlot(fig, {"_input_slot1_x_label": "V set"}, 1, (2, 3), "trace")
        slot.curves({"current": Curve((0, 2e-6), (0, 70e-9), x_unit="V", y_unit="A")})
        self.assertEqual(slot.axes.get_xlabel(), "V set (V)")
        self.assertEqual(slot.axes.xaxis.get_major_formatter()(2e-6), "2 µV")
        self.assertEqual(slot.axes.yaxis.get_major_formatter()(70e-9), "70 nA")

        heatmap = PlotSlot(Figure(figsize=(4, 3)), {"_input_slot1_unit": "A"}, 1, (2, 3), "number")
        self.assertEqual(heatmap.colorbar.formatter(3e-3), "3 mA")

    def test_iv_plot_slot_switches_voltage_x_axis(self):
        curve = Curve((0., 1.), (2e-9, 3e-9), x_unit="V", y_unit="A", x_measured=(.02, 1.03))
        for source, expected_x, expected_label in (
            ("V set", (0., 1.), "V set (V)"),
            ("V measure", (.02, 1.03), "V measure (V)"),
        ):
            fig = Figure(figsize=(4, 3))
            FigureCanvasAgg(fig)
            slot = PlotSlot(fig, {"slot1_iv_x_axis": source, "_input_slot1_x_label": source}, 1, (1, 1), "trace")
            slot.curves({"current": curve})
            self.assertEqual(tuple(slot.lines["current"].get_xdata()), expected_x)
            self.assertEqual(slot.axes.get_xlabel(), expected_label)
        self.assertEqual(curve.x, (0., 1.))

    def test_log_axes_display_absolute_values_without_changing_raw_curves(self):
        fig = Figure(figsize=(4, 3))
        FigureCanvasAgg(fig)
        slot = PlotSlot(fig, {"slot1_xscale": "log", "slot1_yscale": "log"}, 1, (2, 3), "trace")
        curve = Curve((-2., 0., 3.), (-70e-9, 0., 90e-9), x_unit="V", y_unit="A")
        slot.curves({"sweep": curve})
        x, y = slot.lines["sweep"].get_data()
        self.assertEqual(x[0], 2.)
        self.assertAlmostEqual(y[0], 70e-9)
        self.assertTrue(np.isnan(x[1]) and np.isnan(y[1]))
        self.assertEqual((curve.x[0], curve.y[0]), (-2., -70e-9))
        self.assertIn("|Current|", slot.axes.get_ylabel())

    def test_log_heatmap_displays_magnitude_and_hides_zero(self):
        slot = PlotSlot(Figure(figsize=(4, 3)), {"slot1_cscale": "log"}, 1, (1, 3), "number")
        values = np.array([[-2., 0., 3.]])
        slot.heatmap(values, np.full((1, 3), -1), None)
        shown = slot.image.get_array()
        self.assertEqual(shown[0, 0], 2.)
        self.assertTrue(np.ma.is_masked(shown[0, 1]))
        self.assertEqual(values[0, 0], -2.)

    def test_heatmap_reuses_axes_colorbar_and_masks_log_values(self):
        fig = Figure(figsize=(4, 3))
        FigureCanvasAgg(fig)
        slot = PlotSlot(fig, {"slot1_cscale": "log"}, 1, (2, 3), "number")
        identity = slot.image, slot.colorbar, slot.axes
        data = np.array([[1., -1., 3.], [np.nan, 0., 4.]])
        for _ in range(3):
            slot.heatmap(data, np.full((2, 3), -1), (1, 2))
        self.assertEqual(identity, (slot.image, slot.colorbar, slot.axes))
        self.assertEqual(len(fig.axes), 2)
        self.assertTrue(slot.masked)
        self.assertEqual(slot.marker.get_xdata(), [2])
        fig.canvas.draw()

    def test_heatmap_current_position_uses_square_crosshair_marker(self):
        fig = Figure(figsize=(4, 3))
        FigureCanvasAgg(fig)
        slot = PlotSlot(fig, {}, 1, (2, 3), "number")
        marker = slot.marker.get_marker()
        vertices, codes = marker.vertices, marker.codes
        self.assertEqual(len(vertices), 9)
        self.assertEqual(sum(codes == marker.MOVETO), 3)
        self.assertTrue(any(np.allclose(vertex, (-1, 0)) for vertex in vertices))
        self.assertTrue(any(np.allclose(vertex, (1, 0)) for vertex in vertices))
        self.assertTrue(any(np.allclose(vertex, (0, -1)) for vertex in vertices))
        self.assertTrue(any(np.allclose(vertex, (0, 1)) for vertex in vertices))
        self.assertEqual(slot.marker.get_markerfacecolor(), "none")
        slot.heatmap(np.ones((2, 3)), np.full((2, 3), -1), (1, 2))
        fig.canvas.draw()

class RuntimeTests(unittest.TestCase):
    def test_mapping_panel_history_survives_point_archival(self):
        from test_mapping_workflow import MappingWorkflowTests
        harness = MappingWorkflowTests()
        nodes = cards_from_template("General / Examples / Current and Heatmap")
        nodes[1].params.update(sample_count="2", settle_s="0")
        flow = measurement_flow_steps_from_cards(nodes)
        with tempfile.TemporaryDirectory() as tmp:
            app, events, (_, metadata, results) = harness.run_map(Path(tmp), flow, x_b_um=0, y_b_um=0)
            self.assertEqual(metadata["status"], "completed", metadata.get("error"))
            self.assertEqual(len(results), 1)
            path = Path(tmp) / "workflow_plots" / "0_0.json"
            self.assertTrue(path.is_file())
            payload = json.loads(path.read_text())
            self.assertEqual(len(payload["curves"]["read"]["live"]["Current"]["y"]), 2)
            self.assertIsInstance(payload["values"]["2"], float)
            self.assertFalse((Path(tmp) / "points").exists())

    def test_failed_mapping_keeps_partial_curve_and_failure_status(self):
        from test_mapping_workflow import MappingWorkflowTests
        from test_mapping import FakeInstrument
        harness = MappingWorkflowTests()
        nodes = cards_from_template("General / Examples / Current and Heatmap")
        nodes[1].params.update(sample_count="3", settle_s="0")
        flow = measurement_flow_steps_from_cards(nodes)
        with tempfile.TemporaryDirectory() as tmp:
            app, events, (_, metadata, results) = harness.run_map(Path(tmp), flow,
                lambda: FakeInstrument([], read_fail=2), x_b_um=0, y_b_um=0)
            self.assertEqual(metadata["status"], "failed")
            payload = json.loads((Path(tmp) / "workflow_plots" / "0_0.json").read_text())
            self.assertEqual(payload["status"], "failed")
            self.assertIsNone(payload["values"]["2"])
            self.assertEqual(len(payload["curves"]["read"]["live"]["Current"]["y"]), 1)

    def test_stop_policy_propagates_through_dependent_formula(self):
        broken = card("math_divide", "broken", fallback_a="1", fallback_b="0", on_invalid="stop")
        dependent = card("math_add", "dependent")
        wire(dependent, "a", "broken", "result", "number")
        sink = card("mapping_heatmap", "map")
        wire(sink, "primary", "dependent", "result", "number")
        steps = measurement_flow_steps_from_cards([broken, dependent, sink])
        with self.assertRaises(RuntimeError):
            resolve_data_input(steps[-1], "primary", steps, {})

    def test_display_mailbox_is_bounded(self):
        mailbox, events = DisplayMailbox(), queue.Queue()
        for i in range(1000):
            mailbox.publish(events, i)
        self.assertEqual(events.qsize(), 1)
        self.assertEqual(events.get()[1].take(), 999)
        mailbox.publish(events, 1001)
        self.assertEqual(events.qsize(), 1)

    def test_capture_keeps_raw_curves_and_metadata(self):
        events = queue.Queue()
        capture = NodeCapture("read", "iv", "run1", SimpleNamespace(name="p", row=0, col=1), events, True)
        for i in range(100):
            capture.sample({"source_value": i, "voltage_v": i, "current_a": i * .1}, "Vtop1")
        live_snapshot = events.get()[1].take()
        self.assertIn("live", live_snapshot[2])
        self.assertNotIn("curve", live_snapshot[2])
        result = capture.result({"statistics": {"resistance_ohm": 10}}, "done")
        self.assertEqual(result["node_id"], "read")
        self.assertEqual(result["point"]["col"], 1)
        self.assertEqual(len(result["curve"]["Vtop1"].x), 100)
        self.assertEqual(events.qsize(), 1)
        completed_snapshot = events.get()[1].take()
        self.assertEqual(completed_snapshot[2]["curve"]["Vtop1"], result["curve"]["Vtop1"])
        self.assertEqual(result["outputs"]["statistics.resistance_ohm"]["unit"], "Ω")

    def test_running_iv_live_plot_displays_every_acquired_point(self):
        events = queue.Queue()
        capture = NodeCapture("fine", "iv", "run", SimpleNamespace(name="p", row=0, col=0), events, True)
        count = 2500
        for i in range(count):
            capture.sample({"source_value": float(i), "voltage_v": float(i), "current_a": float(i) * 1e-9})
        capture.publish(force=True)
        snapshot = events.get()[1].take()[2]
        self.assertEqual(len(snapshot["live"]["Current"].x), count)
        fig = Figure(figsize=(4, 3))
        FigureCanvasAgg(fig)
        slot = PlotSlot(fig, {"_input_slot1_x_label": "V set"}, 1, (1, 1), "trace")
        slot.curves(snapshot["live"])
        self.assertEqual(len(slot.lines["Current"].get_xdata()), count)

    def test_completed_iv_live_plot_displays_every_acquired_point(self):
        events = queue.Queue()
        capture = NodeCapture("fine", "iv", "run", SimpleNamespace(name="p", row=0, col=0), events, True)
        count = 2500
        for i in range(count):
            capture.sample({"source_value": float(i % 50), "voltage_v": float(i % 50),
                            "current_a": float((i % 60) - 30) * 1e-9})
        result = capture.result({}, "done")
        self.assertEqual(len(result["curve"]["Current"].x), count)
        snapshot = events.get()[1].take()[2]
        self.assertEqual(len(snapshot["live"]["Current"].x), count)
        fig = Figure(figsize=(4, 3))
        FigureCanvasAgg(fig)
        slot = PlotSlot(fig, {"_input_slot1_x_label": "V set"}, 1, (1, 1), "trace")
        slot.curves(snapshot["live"])
        self.assertEqual(len(slot.lines["Current"].get_xdata()), count)

    def test_resume_restores_saved_plot_cells_before_new_point(self):
        app = ProbeApp.__new__(ProbeApp)
        app.autotest_stop_event = threading.Event()
        app.result_queue = queue.Queue()
        app.autotest_grid_rows, app.autotest_grid_cols = 1, 3
        start, read, end, panel = (card("workflow_start", "start"), card("iv", "read"),
                                   card("workflow_end", "end"), card("plot_panel", "plots"))
        start.params["_next"] = "read"
        read.params["_next"] = "end"
        panel.params["layout"] = "1x1"
        wire(panel, "slot1", "read", "live", "trace")
        settings = SimpleNamespace(measurement_flow=measurement_flow_steps_from_cards([start, read, end, panel]))
        app._execute_autotest_measurement_graph_impl = lambda *args: None
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "workflow_plots"
            folder.mkdir()
            paths = []
            for col in (0, 1):
                path = folder / f"0_{col}.json"
                path.write_text(json.dumps({"point": {"row": 0, "col": col}, "status": "done",
                                            "values": {"1": col + 1}, "details": {}, "curves": {}}))
                paths.append(path)
            app._autotest_resume_history = tuple(paths)
            point = SimpleNamespace(name="new", row=0, col=2, order=3)
            app._execute_autotest_measurement_graph(point, settings, Path(tmp), None)
            events = list(app.result_queue.queue)
            start_index = next(i for i, item in enumerate(events) if item[0] == "workflow_point_start")
            restored = [item for item in events[:start_index] if item[0] == "workflow_point_done"]
            self.assertEqual([(item[2], item[3]) for item in restored], [(0, 0), (0, 1)])
            self.assertEqual(restored[1][4]["plots"]["1"], 2)
            self.assertEqual(events[start_index][2:], (0, 2))

    def test_multiple_panels_receive_separate_point_values(self):
        app = ProbeApp.__new__(ProbeApp)
        app.autotest_stop_event = threading.Event()
        app.result_queue = queue.Queue()
        app.autotest_grid_rows = app.autotest_grid_cols = 1
        start, coarse, fine, end = (card("workflow_start", "start"), card("iv", "coarse"),
                                    card("iv", "fine"), card("workflow_end", "end"))
        first, second = card("plot_panel", "coarse_plot"), card("plot_panel", "fine_plot")
        start.params["_next"] = "coarse"
        coarse.params["_next"] = "fine"
        fine.params["_next"] = "end"
        wire(first, "slot1", "coarse", "statistics.max_abs_current_a", "number")
        wire(first, "slot2", "coarse", "live", "trace")
        wire(second, "slot1", "fine", "statistics.max_abs_current_a", "number")
        wire(second, "slot2", "fine", "live", "trace")
        second.params["slot2_iv_x_axis"] = "V measure"
        settings = SimpleNamespace(measurement_flow=measurement_flow_steps_from_cards(
            [start, coarse, fine, end, first, second]))
        def execute(point, settings, step, directory, cv):
            if step.type_id == "iv":
                app._workflow_sample({"source_value": 1., "voltage_v": 1.,
                                      "current_a": 1e-9 if step.params["_node_id"] == "coarse" else 2e-9})
            return {}
        app._execute_autotest_flow_step_impl = execute
        with tempfile.TemporaryDirectory() as tmp:
            point = SimpleNamespace(name="p", row=0, col=0, order=1)
            app._execute_autotest_measurement_flow(point, settings, Path(tmp), None)
            events = list(app.result_queue.queue)
            starts = [event for event in events if event[0] == "workflow_plot_start"]
            self.assertEqual({event[2] for event in starts}, {"coarse_plot", "fine_plot"})
            coarse_window = next(event[3] for event in starts if event[2] == "coarse_plot")
            self.assertEqual(coarse_window.params["_input_slot2_x_label"], "V set")
            self.assertEqual(coarse_window.params["slot2_iv_x_axis"], "V set")
            fine_window = next(event[3] for event in starts if event[2] == "fine_plot")
            self.assertEqual(fine_window.params["_input_slot2_x_label"], "V measure")
            done = next(event for event in events if event[0] == "workflow_point_done")
            self.assertAlmostEqual(done[4]["coarse_plot"]["1"], 1e-9)
            self.assertAlmostEqual(done[4]["fine_plot"]["1"], 2e-9)
            history = json.loads((Path(tmp) / "workflow_plots" / "0_0.json").read_text())
            self.assertAlmostEqual(history["panels"]["coarse_plot"]["values"]["1"], 1e-9)
            self.assertAlmostEqual(history["panels"]["fine_plot"]["values"]["1"], 2e-9)

    def test_simulated_desktop_graph_isolates_points(self):
        app = ProbeApp.__new__(ProbeApp)
        app.autotest_stop_event = threading.Event()
        app.result_queue = queue.Queue()
        app.autotest_grid_rows, app.autotest_grid_cols = 1, 2
        nodes = cards_from_template("General / Examples / Current and Heatmap")
        settings = SimpleNamespace(measurement_flow=measurement_flow_steps_from_cards(nodes))
        def execute(point, settings, step, directory, cv):
            if step.type_id == "constant_current":
                for i in range(3):
                    app._workflow_sample({"elapsed_s": i, "current_a": point.col + 1, "voltage_v": .1})
                return {"statistics": {"mean_current_a": point.col + 1}}
            return {}
        app._execute_autotest_flow_step_impl = execute
        with tempfile.TemporaryDirectory() as tmp:
            for col in range(2):
                point = SimpleNamespace(name=f"p{col}", row=0, col=col, order=col+1)
                app._execute_autotest_measurement_flow(point, settings, Path(tmp), None)
            saved = [json.loads((Path(tmp) / "workflow_plots" / f"0_{col}.json").read_text()) for col in range(2)]
            self.assertEqual([item["values"]["2"] for item in saved], [1, 2])
            self.assertEqual(saved[0]["curves"]["read"]["live"]["Current"]["y"], [1., 1., 1.])
            self.assertEqual(saved[1]["curves"]["read"]["live"]["Current"]["y"], [2., 2., 2.])


if __name__ == "__main__":
    unittest.main()
