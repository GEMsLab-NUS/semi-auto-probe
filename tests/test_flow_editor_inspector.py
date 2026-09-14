from __future__ import annotations

import tkinter as tk
import tkinter.filedialog
import unittest
from unittest.mock import patch

from semi_auto_probe.auto_test import create_autotest_flow_card
from semi_auto_probe.flow_editor import MeasurementFlowEditor
from semi_auto_probe.virtual_mask import parse_binding_points


class FlowEditorInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")
        self.root.withdraw()
        self.panes = tk.PanedWindow(self.root)
        self.inspector = tk.Frame(self.panes)
        self.editor = object.__new__(MeasurementFlowEditor)
        self.editor.body_pane = self.panes
        self.editor.inspector = self.inspector

    def tearDown(self) -> None:
        if hasattr(self, "root"):
            self.root.destroy()

    def test_close_removes_visible_inspector_and_allows_reopen(self) -> None:
        self.panes.add(self.inspector)
        self.assertTrue(self.editor._inspector_is_visible())

        self.editor._hide_inspector()

        self.assertFalse(self.editor._inspector_is_visible())
        self.assertEqual(self.panes.panes(), ())

        self.panes.add(self.inspector)
        self.assertTrue(self.editor._inspector_is_visible())

    def test_virtual_mask_browse_button_opens_file_chooser(self) -> None:
        self.editor.window = self.root
        self.editor.colors = {
            "surface_2": "#111827", "text": "#f8fafc", "muted": "#94a3b8",
        }
        callback_errors = []
        self.root.report_callback_exception = lambda *args: callback_errors.append(args)
        card = create_autotest_flow_card("virtual_mask", "mask")

        with patch("tkinter.filedialog.askopenfilename", return_value="") as chooser:
            self.editor._open_virtual_mask_config(card)
            dialog = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))

            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)

            browse = next(widget for widget in descendants(dialog) if widget.winfo_class() == "TButton" and widget.cget("text") == "Browse…")
            browse.invoke()
            self.root.update_idletasks()
            dialog.destroy()

        self.assertEqual(callback_errors, [])
        chooser.assert_called_once()

    def test_virtual_mask_edits_are_saved_back_to_node(self) -> None:
        self.editor.window = self.root
        self.editor.colors = {
            "surface_2": "#111827", "text": "#f8fafc", "muted": "#94a3b8",
        }
        self.editor._changed = lambda redraw=True: None
        self.editor._show_inspector = lambda card: None
        card = create_autotest_flow_card("virtual_mask", "mask")
        self.editor._open_virtual_mask_config(card)
        dialog = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
        self.root.deiconify()
        self.root.update()

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        widgets = list(descendants(dialog))
        next(widget for widget in widgets if widget.winfo_class() == "TRadiobutton" and widget.cget("text") == "GDS UV").invoke()
        next(widget for widget in widgets if widget.winfo_class() == "TCheckbutton" and widget.cget("text").startswith("Invert grayscale")).invoke()
        labels = {widget.cget("text"): widget for widget in widgets if widget.winfo_class() == "TLabel"}

        def adjacent_entry(label_text):
            label = labels[label_text]
            row = int(label.grid_info()["row"])
            return next(
                child for child in label.master.winfo_children()
                if child.winfo_class() == "TEntry" and int(child.grid_info()["row"]) == row
            )

        black = adjacent_entry("Black pixel electrical power (mW)")
        white = adjacent_entry("White pixel electrical power (mW)")
        black.delete(0, "end")
        black.insert(0, "1")
        white.delete(0, "end")
        white.insert(0, "12")
        table = next(widget for widget in widgets if widget.winfo_class() == "Treeview")
        table.selection_set("0")
        table.event_generate("<<TreeviewSelect>>")
        self.root.update_idletasks()
        canvas = next(widget for widget in widgets if widget.winfo_class() == "Canvas")
        canvas.event_generate("<Button-1>", x=20, y=20)
        self.root.update()
        self.assertEqual(table.selection(), ("0",))
        widgets = list(descendants(dialog))
        labels = {widget.cget("text"): widget for widget in widgets if widget.winfo_class() == "TLabel"}
        target_u = adjacent_entry("GDS U (µm)")
        target_u.delete(0, "end")
        target_u.insert(0, "12")
        next(widget for widget in widgets if widget.winfo_class() == "TButton" and widget.cget("text") == "Update selected").invoke()
        next(widget for widget in widgets if widget.winfo_class() == "TButton" and widget.cget("text") == "Save configuration").invoke()

        self.assertEqual(card.params["coordinate_frame"], "uv")
        self.assertEqual(card.params["black_power_mw"], "1")
        self.assertEqual(card.params["white_power_mw"], "12")
        self.assertEqual(card.params["invert_intensity"], "true")
        self.assertEqual(parse_binding_points(card.params["bindings"])[0].coordinate_a, 12)


if __name__ == "__main__":
    unittest.main()
