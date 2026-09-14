import json
import tempfile
import tkinter as tk
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from semi_auto_probe.flow_editor import MeasurementFlowEditor
from semi_auto_probe.workflow_library import cards_from_template
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
