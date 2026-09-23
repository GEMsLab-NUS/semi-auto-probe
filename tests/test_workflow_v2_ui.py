import json
import tempfile
import tkinter as tk
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from types import SimpleNamespace
from unittest.mock import patch

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.flow_editor import MeasurementFlowEditor
from semi_auto_probe.workflow_library import cards_from_template, save_workflow, workflow_payload
from semi_auto_probe.workflow_formula import Curve
from semi_auto_probe.workflow_plot import WorkflowPlotPanel, LAYOUTS


class WorkflowUITests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        self.root.update_idletasks()
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def test_editor_formula_and_subplot_settings_and_connections(self):
        nodes = cards_from_template("General / Examples / Curve Feature Branch")
        colors = defaultdict(lambda: "#334155", text="#ffffff", muted="#94a3b8", surface="#111827", surface_2="#111827")
        with tempfile.TemporaryDirectory() as tmp, patch("semi_auto_probe.workflow_library.default_workflow_root", return_value=Path(tmp)):
            editor = MeasurementFlowEditor(self.root, nodes, colors, lambda: "new", lambda: None)
            editor.window.withdraw()
            formula, panel = nodes[2], nodes[-1]
            editor._show_inspector(formula)
            editor.param_vars["expression"].set("integral(signal)")
            self.assertIn("Example preview", editor.status_var.get())
            editor.param_vars["variables"].set("signal:")
            editor.param_vars["variables"].set("signal:curve")
            editor._show_inspector(panel)
            editor.param_vars["layout"].set("2x3")
            editor.param_vars["slot6_yscale"].set("log")
            editor._connect("read", "trace@live", "plots", "plot@slot6")
            self.assertEqual(panel.params["_input_slot6_source"], "read")
            self.assertEqual(panel.params["_input_slot6_type"], "trace")
            # Joining two branches must keep both incoming control links.
            editor._connect("accepted", "next", "end")
            self.assertEqual(nodes[3].params["_false"], "end")
            editor.redraw()
            self.root.update_idletasks()
            editor.close()

    def test_template_tree_categories_and_leaf_loading(self):
        from semi_auto_probe.workflow_library import builtin_templates
        colors = defaultdict(lambda: "#334155", text="#ffffff", muted="#94a3b8", surface="#111827", surface_2="#111827")
        with tempfile.TemporaryDirectory() as tmp, patch("semi_auto_probe.workflow_library.default_workflow_root", return_value=Path(tmp)):
            editor = MeasurementFlowEditor(self.root, [], colors, lambda: "new", lambda: None)
            editor.window.withdraw()
            tree = editor.workflow_tree
            root = tree.get_children()[0]
            categories = {tree.item(i, "text"): i for i in tree.get_children(root)}
            self.assertTrue({"FET", "PD", "MR", "Cap"}.issubset(categories))
            fet = categories["FET"]
            tree.selection_set(fet)
            editor._update_workflow_actions()
            self.assertEqual(str(editor.workflow_actions["Load"]["state"]), "disabled")
            self.assertEqual(str(editor.workflow_actions["Copy"]["state"]), "disabled")
            b1500 = tree.get_children(fet)[0]
            leaf = tree.get_children(b1500)[0]
            tree.selection_set(leaf)
            editor._update_workflow_actions()
            kind, reference = editor._selected_workflow_item()
            self.assertEqual(kind, "template")
            self.assertIn(reference, builtin_templates())
            self.assertEqual(str(editor.workflow_actions["Copy"]["state"]), "normal")
            editor._load_template(reference)
            self.assertTrue(any(c.type_id == "b1500_transfer" for c in editor.cards))
            self.assertEqual(sum(c.type_id == "plot_panel" for c in editor.cards), 1)
            editor.close()

    def test_overwrite_selected_custom_workflow_preserves_its_name(self):
        colors = defaultdict(lambda: "#334155", text="#ffffff", muted="#94a3b8", surface="#111827", surface_2="#111827")
        with tempfile.TemporaryDirectory() as tmp, patch("semi_auto_probe.workflow_library.default_workflow_root", return_value=Path(tmp)):
            target = save_workflow(Path(tmp) / "Xinchuan" / "old.json",
                                   cards_from_template("General / Examples / Current and Heatmap"), name="Old custom")
            updated = cards_from_template("General / Examples / Curve Feature Branch")
            editor = MeasurementFlowEditor(self.root, updated, colors, lambda: "new", lambda: None)
            editor.window.withdraw()
            tree = editor.workflow_tree
            user = next(item for item in tree.get_children() if tree.item(item, "text") == "Xinchuan")
            old = next(item for item in tree.get_children(user) if tree.item(item, "text") == "old")
            tree.selection_set(old)
            editor._update_workflow_actions()
            self.assertEqual(str(editor.workflow_actions["Overwrite"]["state"]), "normal")
            expected = workflow_payload(editor.cards, name="Old custom")
            previous = target.read_text(encoding="utf-8")
            with patch("semi_auto_probe.flow_editor.simpledialog.askstring", return_value="old"):
                editor.workflow_actions["Save"].invoke()
            self.assertEqual(target.read_text(encoding="utf-8"), previous)
            self.assertIn("use Overwrite", editor.status_var.get())
            editor.workflow_actions["Overwrite"].invoke()
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), expected)
            self.assertTrue(target.is_file())
            self.assertEqual(editor._selected_workflow_item(), ("workflow", str(target)))
            template = tree.get_children()[0]
            tree.selection_set(template)
            editor._update_workflow_actions()
            self.assertEqual(str(editor.workflow_actions["Overwrite"]["state"]), "disabled")
            editor.close()

    def test_plot_windows_start_compact_for_each_layout(self):
        node = cards_from_template("General / Examples / Current and Heatmap")[-1]
        expected = {"1x1": "420x400", "1x2": "704x400", "2x2": "704x714", "2x3": "1044x714"}
        for layout, size in expected.items():
            node.params["layout"] = layout
            panel = WorkflowPlotPanel(self.root, node, (2, 3))
            try:
                self.root.update_idletasks()
                self.assertEqual(panel.window.geometry().split("+", 1)[0], size)
            finally:
                panel.close()

    def test_all_layouts_preserve_final_data_and_history_click(self):
        node = cards_from_template("General / Examples / Current and Heatmap")[-1]
        family = {"Current": Curve((0., 1.), (2., 3.))}
        for layout, (rows, cols) in LAYOUTS.items():
            node.params["layout"] = layout
            panel = WorkflowPlotPanel(self.root, node, (2, 3))
            panel.window.withdraw()
            self.assertEqual(len(panel.slots), rows * cols)
            panel.point_started(0, 1)
            panel.curves_updated("read", "live", family)
            panel.point_finished(0, 1, {"2": 2.5}, "done", None)
            panel.finish()
            self.root.update_idletasks()
            self.assertIsNone(panel.job)
            self.assertIsNone(panel.current)
            if 2 in panel.slots:
                self.assertEqual(panel.values[2][0, 1], 2.5)
            panel.close()

    def test_coarse_and_optional_fine_panels_keep_independent_windows(self):
        coarse = SimpleNamespace(params={
            "_node_id": "coarse_plot", "layout": "1x1",
            "_input_slot1_source": "coarse", "_input_slot1_output": "live",
            "_input_slot1_type": "trace", "_input_slot1_retain_between_points": True,
        })
        fine = SimpleNamespace(params={
            "_node_id": "fine_plot", "layout": "1x1",
            "_input_slot1_source": "fine", "_input_slot1_output": "live",
            "_input_slot1_type": "trace", "_input_slot1_retain_between_points": False,
        })
        handle = ProbeApp._handle_worker_event_impl
        try:
            handle(self.root, ("workflow_plot_start", "run", "coarse_plot", coarse, (1, 1)))
            handle(self.root, ("workflow_plot_start", "run", "fine_plot", fine, (1, 1)))
            plots = self.root._workflow_plots
            self.assertEqual(set(plots), {"coarse_plot", "fine_plot"})
            self.assertIsNot(plots["coarse_plot"].window, plots["fine_plot"].window)
            self.assertIn("coarse_plot", plots["coarse_plot"].window.title())
            self.assertIn("fine_plot", plots["fine_plot"].window.title())
            family = {"Current": Curve((0., 1.), (2., 3.))}
            handle(self.root, ("workflow_point_start", "run", 0, 0))
            for node in ("coarse", "fine"):
                mailbox = SimpleNamespace(take=lambda node=node: ("run", node, {"live": family}))
                handle(self.root, ("workflow_display", mailbox))
            self.assertIn(("coarse", "live"), plots["coarse_plot"].live)
            self.assertNotIn(("fine", "live"), plots["coarse_plot"].live)
            self.assertIn(("fine", "live"), plots["fine_plot"].live)
            self.assertNotIn(("coarse", "live"), plots["fine_plot"].live)
            plots["fine_plot"].tick(force=True)
            self.assertEqual(list(plots["fine_plot"].slots[1].lines["Current"].get_ydata()), [2., 3.])
            updated = {"Current": Curve((0., 1.), (4., 5.))}
            handle(self.root, ("workflow_display", SimpleNamespace(take=lambda: ("run", "fine", {"live": updated}))))
            plots["fine_plot"].tick(force=True)
            self.assertEqual(list(plots["fine_plot"].slots[1].lines["Current"].get_ydata()), [4., 5.])
            handle(self.root, ("workflow_point_start", "run", 0, 1))
            self.assertIn(("coarse", "live"), plots["coarse_plot"].live)
            self.assertNotIn(("fine", "live"), plots["fine_plot"].live)
            handle(self.root, ("workflow_point_done", "run", 0, 1,
                               {"coarse_plot": {}, "fine_plot": {}}, "done", None,
                               {"coarse_plot": {}, "fine_plot": {}}))
            self.assertEqual(plots["coarse_plot"].history[0, 1], None)
            self.assertEqual(plots["fine_plot"].history[0, 1], None)
        finally:
            for plot in self.root.__dict__.get("_workflow_plots", {}).values():
                plot.close()

    def test_curve_updates_do_not_redraw_heatmap(self):
        node = cards_from_template("General / Examples / Current and Heatmap")[-1]
        panel = WorkflowPlotPanel(self.root, node, (2, 3))
        panel.window.withdraw()
        panel.tick(force=True)
        with patch.object(panel.slots[2], "render") as heatmap_render:
            panel.curves_updated("read", "live", {"Current": Curve((0., 1.), (2., 3.))})
            panel.tick(force=True)
            heatmap_render.assert_not_called()
        panel.finish()
        panel.close()


if __name__ == "__main__":
    unittest.main()
