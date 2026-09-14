import tempfile
import tkinter as tk
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from semi_auto_probe.auto_test import create_autotest_flow_card
from semi_auto_probe.flow_editor import MeasurementFlowEditor
from semi_auto_probe.workflow_library import cards_from_template, VIRTUAL_MASK_MAPPING_TEMPLATE


class RoutingTests(unittest.TestCase):
    def test_virtual_mask_template_routes_have_no_backtracking_spurs(self):
        for gap in (70, 80, 96, 106, 150, 180):
            with self.subTest(gap=gap):
                editor = object.__new__(MeasurementFlowEditor)
                editor.cards = cards_from_template(VIRTUAL_MASK_MAPPING_TEMPLATE)
                calibration = next(c for c in editor.cards if c.card_id == "mask_power_calibration")
                mask = next(c for c in editor.cards if c.card_id == "nus_virtual_mask")
                current = next(c for c in editor.cards if c.card_id == "mask_current")
                mask.x = calibration.x + editor._node_width(calibration) + gap
                current.x = mask.x + editor._node_width(mask) + gap
                end = next(c for c in editor.cards if c.card_id == "end")
                end.x = current.x + editor._node_width(current) + 106
                editor.colors = {"border_focus": "#22d3ee"}
                editor.canvas = Mock()
                editor._routing_edges = True
                editor._routed_edges = []
                editor._blocked_routes = []
                editor._route_cache = {}
                for card in editor.cards:
                    editor._draw_edges(card)
                self.assertFalse(editor._blocked_routes)
                for route, _, _, tags in editor._routed_edges:
                    for a, b, c in zip(route, route[1:], route[2:]):
                        dot = (b[0]-a[0])*(c[0]-b[0]) + (b[1]-a[1])*(c[1]-b[1])
                        self.assertGreaterEqual(dot, 0, (tags, route))
                    self.assertFalse(editor._route_hits_obstacle(
                        route, [editor._node_bounds(c, -.01) for c in editor.cards]))
                    rounded = editor._rounded_route_points(route, 12)
                    self.assertEqual(tuple(rounded[:2]), route[0])
                    self.assertEqual(tuple(rounded[-2:]), route[-1])

    def test_simplification_removes_retraced_segments_and_duplicate_vertices(self):
        route = [(0, 0), (60, 0), (40, 0), (40, 20), (40, 20), (40, 0), (80, 0), (80, 40)]
        self.assertEqual(MeasurementFlowEditor._simplify_route(route), [(0, 0), (80, 0), (80, 40)])

    def test_data_wire_approaches_panel_from_left_without_crossing_its_body(self):
        source = create_autotest_flow_card("constant_current", "read")
        target = create_autotest_flow_card("plot_panel", "plots")
        source.x, source.y = 400, 0
        target.x, target.y = 330, 200
        target.params.update(_input_slot3_source="read", _input_slot3_output="statistics.mean_current_a", _input_slot3_type="number")
        editor = object.__new__(MeasurementFlowEditor)
        editor.cards = [source, target]
        route = editor._orthogonal_route(source, target, "number@statistics.mean_current_a", target_kind="plot@slot3")
        self.assertEqual(route[-1][0], target.x)
        self.assertLess(route[-2][0], target.x)
        self.assertFalse(editor._route_hits_obstacle(route, [editor._node_bounds(c, -.01) for c in editor.cards]))
        rounded = editor._rounded_route_points(route, 12)
        for x, y in zip(rounded[::2], rounded[1::2]):
            for card in editor.cards:
                left, top, right, bottom = editor._node_bounds(card, -.01)
                self.assertFalse(left < x < right and top < y < bottom)

    def test_parallel_wires_get_separate_corridors(self):
        occupied = [[(-30, 0), (190, 0)]]
        route = MeasurementFlowEditor._visibility_route((0, 0), (160, 0), [], occupied)
        horizontal = [(a, b) for a, b in zip(route, route[1:]) if a[1] == b[1]]
        self.assertTrue(horizontal)
        self.assertTrue(all(abs(a[1]) >= 12 for a, b in horizontal))

    def test_detour_avoids_third_card_and_keeps_real_endpoints(self):
        obstacle = (40, -40, 100, 40)
        route = MeasurementFlowEditor._visibility_route((0, 0), (160, 0), [obstacle])
        self.assertEqual((route[0], route[-1]), ((0, 0), (160, 0)))
        self.assertFalse(MeasurementFlowEditor._route_hits_obstacle(route, [obstacle]))

    def test_crossing_has_hop_but_shared_endpoint_does_not(self):
        editor = object.__new__(MeasurementFlowEditor)
        editor.canvas = Mock()
        editor.canvas.cget.return_value = "#111111"
        horizontal = ([(0, 50), (100, 50)], "#ff0000", 2, ("edge", "horizontal"))
        vertical = ([(50, 0), (50, 100)], "#00ff00", 2, ("edge", "vertical"))
        editor._routed_edges = [horizontal, vertical]
        editor._draw_crossing_bridges()
        self.assertEqual(editor.canvas.create_line.call_count, 3)
        arc = editor.canvas.create_line.call_args
        self.assertIn("edge_bridge", arc.kwargs["tags"])
        self.assertLess(min(arc.args[1::2]), 50)
        editor.canvas.reset_mock()
        editor._routed_edges = [horizontal, ([(100, 50), (100, 100)], "#00ff00", 2, ("edge",))]
        editor._draw_crossing_bridges()
        editor.canvas.create_line.assert_not_called()


class InspectorInteractionTests(unittest.TestCase):
    def test_single_click_and_drag_do_not_open_double_click_toggles(self):
        root = tk.Tk()
        root.withdraw()
        colors = defaultdict(lambda: "#334155", text="#ffffff", muted="#94a3b8", surface="#111827", surface_2="#111827")
        try:
            with tempfile.TemporaryDirectory() as tmp, patch("semi_auto_probe.workflow_library.default_workflow_root", return_value=Path(tmp)):
                editor = MeasurementFlowEditor(root, cards_from_template("General / Examples / Current and Heatmap"), colors, lambda: "new", lambda: None)
                editor.window.withdraw()
                original_find = editor.canvas.find_withtag
                current = "read"
                def find(tag):
                    return original_find("node:" + current)[:1] if tag == "current" else original_find(tag)
                event = SimpleNamespace(x=200, y=100)
                with patch.object(editor.canvas, "find_withtag", side_effect=find):
                    editor._canvas_press(event)
                    self.assertFalse(editor._inspector_is_visible())
                    editor._canvas_drag(SimpleNamespace(x=220, y=120))
                    editor._canvas_release(event)
                    self.assertFalse(editor._inspector_is_visible())
                    editor._canvas_double_click(event)
                    self.assertTrue(editor._inspector_is_visible())
                    self.assertEqual(editor._inspected_id, "read")
                    current = "plots"
                    editor._canvas_press(event)
                    editor._canvas_release(event)
                    self.assertEqual(editor._inspected_id, "read")
                    editor._canvas_double_click(event)
                    self.assertEqual(editor._inspected_id, "plots")
                    editor._canvas_double_click(event)
                    self.assertFalse(editor._inspector_is_visible())
                    editor._canvas_double_click(event)
                    editor._hide_inspector()
                    self.assertFalse(editor._inspector_is_visible())
                editor.close()
        finally:
            root.update_idletasks()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
