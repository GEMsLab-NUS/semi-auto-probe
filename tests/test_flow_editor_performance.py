"""Structural performance regressions; no wall-clock thresholds or hardware."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_flow_editor_blender as fixtures
from semi_auto_probe.auto_test import create_autotest_flow_card
from semi_auto_probe.workflow_library import cards_from_template


class EditorPerformanceTests(unittest.TestCase):
    setUp = fixtures.BlenderEditorTests.setUp
    tearDown = fixtures.BlenderEditorTests.tearDown

    def test_zoom_reuses_world_routes_and_preserves_display_hit_paths(self):
        e = self.editor
        e._connect("read", "trace@live", "plot", "plot@slot1")
        paths = dict(e._wire_cache)
        with patch.object(e, "_visibility_route", wraps=e._visibility_route) as route:
            e.redraw()
            e._zoom_canvas(SimpleNamespace(x=300, y=200, delta=120))
            e._zoom_canvas(SimpleNamespace(x=300, y=200, delta=-120))
            route.assert_not_called()
        self.assertEqual(e._wire_cache, paths)
        self.assertEqual([edge[0] for edge in e._routed_edges], list(paths.values()))

    def test_unconnected_obstacle_movement_invalidates_routes(self):
        e = self.editor
        e._connect("read", "trace@live", "plot", "plot@slot1")
        blocker = create_autotest_flow_card("constant_current", "blocker")
        blocker.x, blocker.y = 500, 800
        e.cards.append(blocker)
        e.redraw()
        before = dict(e._wire_cache)
        blocker.y = 80
        with patch.object(e, "_visibility_route", wraps=e._visibility_route) as route:
            e.redraw()
            self.assertGreater(route.call_count, 0)
        self.assertNotEqual(e._wire_cache, before)
        for points, *_ in e._routed_edges:
            self.assertFalse(e._route_hits_obstacle(points, [e._node_bounds(blocker, -.01)]))

    def test_layout_and_hidden_connections_invalidate_then_restore(self):
        e = self.editor
        self.plot.params["layout"] = "2x3"
        e._connect("read", "trace@live", "plot", "plot@slot6")
        before = dict(e._wire_cache)
        self.plot.params["layout"] = "1x2"
        e.redraw()
        self.assertFalse(any(key[3] == "plot@slot6" for key in e._wire_cache))
        self.plot.params["layout"] = "2x3"
        e.redraw()
        self.assertEqual(e._wire_cache, before)

    def test_hp_template_routes_only_use_orthogonal_spines(self):
        e = self.editor
        e.cards = cards_from_template("FET / HP6614C / Screen and Characterize")
        occupied_routes = []
        original = e._visibility_route
        def capture(start, end, obstacles, occupied=()):
            occupied_routes.extend(occupied)
            return original(start, end, obstacles, occupied)
        with patch.object(e, "_visibility_route", side_effect=capture):
            e.redraw()
        self.assertTrue(occupied_routes)
        for points in occupied_routes:
            self.assertLess(len(points), 20)
            self.assertTrue(all(a[0] == b[0] or a[1] == b[1]
                                for a, b in zip(points, points[1:])))
        self.assertEqual(len(e._routed_edges), 16)
        self.assertTrue(any(len(points) > 20 for points, *_ in e._routed_edges))
        for points, *_ in e._routed_edges:
            self.assertFalse(e._route_hits_obstacle(points,
                [e._node_bounds(card, -.01) for card in e.cards]))

    def test_bounds_cache_does_not_survive_next_movement(self):
        e = self.editor
        e._connect("read", "trace@live", "plot", "plot@slot1")
        self.plot.y += 80
        e.redraw()
        self.assertEqual(e._wire_cache["read", "plot", "trace@live", "plot@slot1"][-1],
                         e._socket_position(self.plot, "plot@slot1", incoming=True))
        self.plot.y += 80
        e.redraw()
        self.assertEqual(e._wire_cache["read", "plot", "trace@live", "plot@slot1"][-1],
                         e._socket_position(self.plot, "plot@slot1", incoming=True))

    def test_local_search_checks_distant_obstacles_and_falls_back(self):
        e = self.editor
        e._connect("read", "trace@live", "plot", "plot@slot1")
        blocker = create_autotest_flow_card("constant_current", "distant")
        blocker.x, blocker.y = 500, 900
        e.cards.append(blocker)
        original = e._visibility_route
        calls = []
        def detour(start, end, obstacles, occupied=()):
            calls.append(len(obstacles))
            if len(calls) == 1:
                return [start, (550, 950), end]
            return original(start, end, obstacles, occupied)
        e._route_cache = {}
        with patch.object(e, "_visibility_route", side_effect=detour):
            points = e._orthogonal_route(self.read, self.plot, "trace@live", target_kind="plot@slot1")
        self.assertEqual(len(calls), 2)
        self.assertLess(calls[0], calls[1])
        self.assertFalse(e._route_hits_obstacle(points, [e._node_bounds(blocker)]))

    def test_distant_node_movement_keeps_unaffected_paths(self):
        e = self.editor
        e._connect("read", "trace@live", "plot", "plot@slot1")
        blocker = create_autotest_flow_card("constant_current", "distant")
        blocker.x, blocker.y = 500, 900
        e.cards.append(blocker)
        e.redraw()
        before = dict(e._wire_cache)
        blocker.y += 100
        with patch.object(e, "_visibility_route", wraps=e._visibility_route) as route:
            e.redraw()
            route.assert_not_called()
        self.assertEqual(e._wire_cache, before)
