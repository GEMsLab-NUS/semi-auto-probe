import tempfile
import tkinter as tk
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from semi_auto_probe.auto_test import create_autotest_flow_card, autotest_flow_definitions_by_type
from semi_auto_probe.flow_editor import MeasurementFlowEditor
from semi_auto_probe.workflow_library import save_workflow, load_workflow, cards_from_template


class BlenderEditorTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch("semi_auto_probe.workflow_library.default_workflow_root", return_value=Path(self.tmp.name))
        self.patch.start()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.read = create_autotest_flow_card("constant_current", "read")
        self.plot = create_autotest_flow_card("plot_panel", "plot")
        self.read.x, self.read.y = 200, 80
        self.plot.x, self.plot.y = 660, 180
        colors = defaultdict(lambda: "#334155", text="#ffffff", muted="#94a3b8", surface="#111827", surface_2="#111827")
        self.editor = MeasurementFlowEditor(self.root, [self.read, self.plot], colors, lambda: "new", lambda: None)
        self.editor.window.withdraw()

    def tearDown(self):
        self.editor.close()
        self.root.update_idletasks()
        self.root.destroy()
        self.patch.stop()
        self.tmp.cleanup()
        self.assertEqual(self.errors, [])

    def test_inline_commit_cancel_validation_and_inspector_sync(self):
        e = self.editor
        e._show_inspector(self.read)
        e._begin_inline_edit(self.read, "voltage_v")
        e._inline_editor[2].set("2.5")
        e._finish_inline_edit(redraw=True)
        self.assertEqual(self.read.params["voltage_v"], "2.5")
        self.assertEqual(e.param_vars["voltage_v"].get(), "2.5")
        for value, commit in (("9", False), ("nan", True), ("unfinished", True)):
            e._begin_inline_edit(self.read, "voltage_v")
            e._inline_editor[2].set(value)
            e._finish_inline_edit(commit=commit, redraw=True)
            self.assertEqual(self.read.params["voltage_v"], "2.5")

    def test_inline_release_does_not_destroy_editor_or_move_card(self):
        e = self.editor
        e.zoom = 1.6
        e.redraw()
        xy = (self.read.x, self.read.y)
        e._begin_inline_edit(self.read, "voltage_v")
        widget = e._inline_editor[0]
        e._canvas_release(SimpleNamespace(x=10, y=10))
        self.assertTrue(widget.winfo_exists())
        e._canvas_drag(SimpleNamespace(x=100, y=100))
        self.assertEqual((self.read.x, self.read.y), xy)
        e._finish_inline_edit(commit=False)
        self.assertFalse(widget.winfo_exists())

    def test_keithley_current_curve_connects_to_plot(self):
        iv = create_autotest_flow_card("iv", "keithley_iv")
        iv.params["_outputs"] = "curve"
        self.editor.cards = [iv, self.plot]
        self.assertEqual(self.editor._default_target_input(self.plot, "curve@curve"), "plot@slot1")
        self.assertTrue(self.editor._connect("keithley_iv", "curve@curve", "plot", "plot@slot1"))
        self.assertEqual(self.plot.params["_input_slot1_type"], "curve")
        self.assertEqual(self.plot.params["_input_slot1_output"], "curve")

    def test_choice_changes_preserve_existing_connection(self):
        e = self.editor
        e._connect("read", "trace@live", "plot", "plot@slot1")
        e._begin_inline_edit(self.plot, "layout")
        self.assertEqual(str(e._inline_editor[0].cget("state")), "readonly")
        e._inline_editor[2].set("2x3")
        e._finish_inline_edit(redraw=True)
        self.assertEqual(self.plot.params["layout"], "2x3")
        self.assertEqual(self.plot.params["_input_slot1_source"], "read")

    def test_every_node_keeps_sockets_and_fields_inside_its_bounds(self):
        e = self.editor
        for type_id in autotest_flow_definitions_by_type():
            card = create_autotest_flow_card(type_id, type_id)
            bottom = card.y + e._node_height(card)
            for incoming, ports in ((True, e._node_inputs(card)), (False, e._node_outputs(card))):
                for kind, _ in ports:
                    x, y = e._socket_position(card, kind, incoming=incoming)
                    self.assertLess(y + 6, bottom, (type_id, kind))
                    self.assertLess(y, card.y + e._parameter_top(card), (type_id, kind))
            e._draw_node(card)

    def test_socket_capture_is_screen_sized_and_excludes_self(self):
        e = self.editor
        for zoom in (.45, 1.0, 2.2):
            e.zoom = zoom
            e.redraw()
            e.pending_edge = ("read", "trace@live")
            x, y = e._socket_position(self.plot, "plot@slot1", incoming=True)
            event = SimpleNamespace(x=x*zoom-e.canvas.canvasx(0)-8, y=y*zoom-e.canvas.canvasy(0))
            self.assertEqual(e._connection_target_at(event, "trace@live"), ("plot", "plot@slot1"))
            self.assertIsNone(e._connection_target_at(event, "next"))
        e._cancel_connection()
        self.assertIsNone(e.pending_edge)

    def test_curve_obstacle_detection_and_cut_use_visible_path(self):
        e = self.editor
        e.cards = [self.read, self.plot]
        e._connect("read", "trace@live", "plot", "plot@slot1")
        route = e._wire_route(self.read, self.plot, "trace@live", target_kind="plot@slot1")
        self.assertTrue(any(a[0] != b[0] and a[1] != b[1] for a, b in zip(route, route[1:])))
        mid = route[len(route)//2]
        self.assertTrue(e._route_hits_obstacle(route, [(mid[0]-3, mid[1]-3, mid[0]+3, mid[1]+3)]))
        self.assertEqual(e._cut_edges((mid[0], mid[1]-3), (mid[0], mid[1]+3)), 1)
        self.assertNotIn("_input_slot1_source", self.plot.params)
        obstacle = create_autotest_flow_card("wait", "obstacle")
        obstacle.x, obstacle.y = 440, 100
        e.cards.append(obstacle)
        e._connect("read", "trace@live", "plot", "plot@slot1")
        route = e._wire_route(self.read, self.plot, "trace@live", target_kind="plot@slot1")
        self.assertFalse(e._route_hits_obstacle(route, [e._node_bounds(c, -.01) for c in e.cards]))

    def test_inline_edits_and_connections_round_trip(self):
        e = self.editor
        e._begin_inline_edit(self.read, "voltage_v")
        e._inline_editor[2].set("0.35")
        e._finish_inline_edit()
        e._connect("read", "trace@live", "plot", "plot@slot1")
        path = Path(self.tmp.name) / "edited.json"
        save_workflow(path, e.cards)
        restored = {c.card_id: c for c in load_workflow(path)}
        self.assertEqual(restored["read"].params["voltage_v"], "0.35")
        self.assertEqual(restored["plot"].params["_input_slot1_source"], "read")
        self.assertEqual((restored["read"].x, restored["read"].y), (self.read.x, self.read.y))

    def test_delete_inside_parameter_keeps_selected_node(self):
        e = self.editor
        e.selected_id = self.read.card_id
        e._begin_inline_edit(self.read, "voltage_v")
        e._delete_selected(SimpleNamespace(widget=e._inline_editor[0]))
        self.assertIs(e._card("read"), self.read)
        self.assertIsNotNone(e._inline_editor)

    def test_actual_canvas_click_opens_field_and_return_commits(self):
        e = self.editor
        e.window.deiconify()
        e.window.update()
        e._fit_nodes()
        e.window.update()
        x = (self.read.x + 50) * e.zoom - e.canvas.canvasx(0)
        y = (self.read.y + e._parameter_top(self.read) + 24) * e.zoom - e.canvas.canvasy(0)
        e.canvas.event_generate("<Motion>", x=int(x), y=int(y))
        e.canvas.event_generate("<ButtonPress-1>", x=int(x), y=int(y))
        e.canvas.event_generate("<ButtonRelease-1>", x=int(x), y=int(y))
        e.window.update()
        self.assertIsNotNone(e._inline_editor)
        widget = e._inline_editor[0]
        e._inline_editor[2].set("0.75")
        widget.focus_force()
        widget.event_generate("<Return>")
        e.window.update()
        self.assertEqual(self.read.params["voltage_v"], "0.75")
        self.assertIsNone(e._inline_editor)

    def test_drag_preview_reaches_snapped_input(self):
        e = self.editor
        e.cards = [self.read, self.plot]
        e.zoom = 1.3
        self.read.params["_outputs"] = "live"
        e.redraw()
        e.pending_edge = ("read", "trace@live")
        x, y = e._socket_position(self.plot, "plot@slot1", incoming=True)
        event = SimpleNamespace(x=x*e.zoom-e.canvas.canvasx(0)-6, y=y*e.zoom-e.canvas.canvasy(0))
        e._canvas_motion(event)
        line = e.canvas.find_withtag("edge_preview")[-1]
        coords = e.canvas.coords(line)
        self.assertAlmostEqual(coords[-2], x*e.zoom)
        self.assertAlmostEqual(coords[-1], y*e.zoom)
        e._edge_dragging = True
        e._canvas_release(event)
        self.assertEqual(self.plot.params["_input_slot1_source"], "read")

    def test_plot_layout_controls_socket_and_inspector_counts(self):
        e = self.editor
        e._show_inspector(self.plot)
        for layout, count in (("1x1", 1), ("1x2", 2), ("2x2", 4), ("2x3", 6), ("1x2", 2)):
            e.param_vars["layout"].set(layout)
            expected = [f"plot@slot{i}" for i in range(1, count + 1)]
            self.assertEqual([kind for kind, _ in e._node_inputs(self.plot)], expected)
            for i in range(1, 7):
                self.assertEqual(bool(e.canvas.find_withtag(f"input:plot:plot@slot{i}")), i <= count)
            visible = [page for page in e._plot_slot_pages if e._plot_slot_notebook.tab(page, "state") != "hidden"]
            self.assertEqual(len(visible), count)

    def test_shrinking_plot_hides_wires_and_restores_saved_configuration(self):
        e = self.editor
        e._param_changed(self.plot, "layout", "2x3")
        e._connect("read", "trace@live", "plot", "plot@slot6")
        self.plot.params["slot6_title"] = "Retained plot"
        wire = "edge:read:_input_slot6_source:plot"
        self.assertTrue(e.canvas.find_withtag(wire))
        large_height = e._node_height(self.plot)
        e._begin_inline_edit(self.plot, "layout")
        e._inline_editor[2].set("1x2")
        e._finish_inline_edit(redraw=True)
        self.assertFalse(e.canvas.find_withtag(wire))
        self.assertLess(e._node_height(self.plot), large_height)
        path = Path(self.tmp.name) / "hidden-slots.json"
        save_workflow(path, e.cards)
        restored = {c.card_id: c for c in load_workflow(path)}
        self.assertEqual(restored["plot"].params["_input_slot6_source"], "read")
        e._param_changed(self.plot, "layout", "2x3")
        self.assertTrue(e.canvas.find_withtag(wire))
        self.assertEqual(self.plot.params["slot6_title"], "Retained plot")

    def test_cannot_connect_to_inactive_plot_slot(self):
        e = self.editor
        e._connect("read", "trace@live", "plot", "plot@slot6")
        self.assertNotIn("_input_slot6_source", self.plot.params)
        self.assertIn("hidden", e.status_var.get())

    def test_b1500_template_results_have_distinct_exposed_sockets(self):
        e = self.editor
        e.cards = cards_from_template("FET / B1500 / Transfer")
        e.redraw()
        test = next(c for c in e.cards if c.type_id == "b1500_transfer")
        plot = next(c for c in e.cards if c.type_id == "plot_panel")
        positions = []
        for slot in range(1, 5):
            metric = plot.params[f"_input_slot{slot}_output"]
            dtype = plot.params[f"_input_slot{slot}_type"]
            kind = f"{dtype}@{metric}"
            self.assertTrue(e.canvas.find_withtag(f"port:{test.card_id}:{kind}"), kind)
            position = e._socket_position(test, kind)
            self.assertNotEqual(position, e._socket_position(test, "next"))
            route = e._wire_route(test, plot, kind, target_kind=f"plot@slot{slot}")
            self.assertEqual(route[0], position)
            positions.append(position)
        self.assertEqual(len(set(positions)), 4)

    def test_unexposing_result_removes_its_links_only(self):
        e = self.editor
        e._connect("read", "trace@live", "plot", "plot@slot1")
        e._connect("read", "number@statistics.mean_current_a", "plot", "plot@slot2")
        e._show_inspector(self.read)
        e.output_vars["live"].set(False)
        e._result_outputs_changed(self.read)
        self.assertNotIn("_input_slot1_source", self.plot.params)
        self.assertNotIn("_input_slot1_output", self.plot.params)
        self.assertEqual(self.plot.params["_input_slot2_output"], "statistics.mean_current_a")
        self.assertFalse(e.canvas.find_withtag("port:read:trace@live"))
        self.assertTrue(e.canvas.find_withtag("port:read:number@statistics.mean_current_a"))

    def test_unknown_output_does_not_use_flow_socket(self):
        e = self.editor
        self.assertIsNone(e._socket_position(self.read, "number@not_a_result"))

    def test_output_checkbox_creates_socket_and_slot_keeps_one_source(self):
        e = self.editor
        e._show_inspector(self.read)
        e.result_output_widgets["live"].invoke()
        self.assertTrue(e.canvas.find_withtag("port:read:trace@live"))
        e._connect("read", "trace@live", "plot", "plot@slot1")
        e.result_output_widgets["statistics.mean_current_a"].invoke()
        e._connect("read", "number@statistics.mean_current_a", "plot", "plot@slot1")
        self.assertEqual(self.plot.params["_input_slot1_output"], "statistics.mean_current_a")
        self.assertEqual(self.plot.params["_input_slot1_type"], "number")
        self.assertEqual(len([edge for edge in e._routed_edges if "edge:read:_input_slot1_source:plot" in edge[3]]), 1)
        path = Path(self.tmp.name) / "outputs.json"
        save_workflow(path, e.cards)
        restored = {c.card_id: c for c in load_workflow(path)}
        self.assertIn("live", restored["read"].params["_outputs"].split(","))
        self.assertIn("statistics.mean_current_a", restored["read"].params["_outputs"].split(","))

    def test_diagonal_segment_clipping(self):
        e = self.editor
        self.assertTrue(e._route_hits_obstacle([(0, 0), (100, 100)], [(40, 40, 60, 60)]))
        self.assertFalse(e._route_hits_obstacle([(0, 0), (100, 100)], [(40, 70, 60, 90)]))


if __name__ == "__main__":
    unittest.main()
