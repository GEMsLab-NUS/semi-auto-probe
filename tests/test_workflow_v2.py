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
from semi_auto_probe.flow_graph import resolve_data_input, evaluate_condition, validate_flow_graph
from semi_auto_probe.workflow_formula import Curve, InvalidValue, evaluate_expression
from semi_auto_probe.workflow_library import cards_from_template, cards_from_workflow_payload, workflow_payload
from semi_auto_probe.workflow_plot import PlotSlot, LAYOUTS, validate_panel, display_sample_indices
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

    def test_downsampling_keeps_narrow_extrema_and_order(self):
        data = np.zeros(100_000)
        data[12345], data[12346] = 999, -999
        indices = display_sample_indices(data, 500)
        self.assertIn(12345, indices)
        self.assertIn(12346, indices)
        self.assertTrue(np.all(np.diff(indices) > 0))
        self.assertLessEqual(len(indices), 1002)


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
        result = capture.result({"statistics": {"resistance_ohm": 10}}, "done")
        self.assertEqual(result["node_id"], "read")
        self.assertEqual(result["point"]["col"], 1)
        self.assertEqual(len(result["curve"]["Vtop1"].x), 100)
        self.assertEqual(events.qsize(), 1)
        self.assertEqual(result["outputs"]["statistics.resistance_ohm"]["unit"], "Ω")

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
