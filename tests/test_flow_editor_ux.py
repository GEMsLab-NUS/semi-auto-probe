import unittest
from types import SimpleNamespace
import test_flow_editor_blender as fixtures
from semi_auto_probe.auto_test import create_autotest_flow_card


class EditorUXTests(unittest.TestCase):
    setUp = fixtures.BlenderEditorTests.setUp
    tearDown = fixtures.BlenderEditorTests.tearDown

    def event_at(self, position):
        e = self.editor
        return SimpleNamespace(x=position[0]*e.zoom-e.canvas.canvasx(0), y=position[1]*e.zoom-e.canvas.canvasy(0))

    def test_connection_undo_redo(self):
        e = self.editor
        self.assertTrue(e._connect("read", "trace@live", "plot", "plot@slot1"))
        e._history_move(-1)
        self.assertNotIn("_input_slot1_source", self.plot.params)
        e._history_move(1)
        self.assertEqual(self.plot.params["_input_slot1_source"], "read")
        self.assertTrue(e.canvas.find_withtag("port:read:trace@live"))

    def test_drag_is_one_history_step(self):
        e = self.editor
        index = e._history_index
        before = (self.read.x,self.read.y)
        e.drag_id = "read"
        e.drag_offset = (0,0)
        e._canvas_drag(self.event_at((300,250)))
        e._canvas_drag(self.event_at((350,270)))
        self.assertEqual(e._history_index,index)
        e._canvas_release(self.event_at((350,270)))
        self.assertEqual(e._history_index,index+1)
        e._history_move(-1)
        self.assertEqual((self.read.x,self.read.y),before)

    def test_delete_cleans_visible_hidden_and_legacy_references_and_undo_restores(self):
        e = self.editor
        e._param_changed(self.plot,"layout","2x3")
        e._connect("read","trace@live","plot","plot@slot6")
        e._param_changed(self.plot,"layout","1x2")
        self.plot.params.update(_result_source="read",_result_metric="live",source_node="read")
        e._changed()
        before=e._snapshot()
        e.selected_id="read"
        e._delete_selected()
        self.assertFalse(any(k.startswith("_input_slot6_") for k in self.plot.params))
        self.assertNotIn("_result_source", self.plot.params)
        self.assertNotIn("source_node", self.plot.params)
        e._history_move(-1)
        self.assertEqual(e._snapshot(),before)
        e._history_move(1)
        self.assertIsNone(e._card("read"))

    def test_parameter_keystrokes_coalesce_and_new_edit_discards_redo(self):
        e=self.editor
        old=self.read.params["voltage_v"]
        e._show_inspector(self.read)
        index=e._history_index
        for text in ("2","2.","2.5"):
            e.param_vars["voltage_v"].set(text)
        e._end_parameter_edit(self.read,"voltage_v")
        self.assertEqual(e._history_index,index+1)
        e._history_move(-1)
        self.assertEqual(self.read.params["voltage_v"],old)
        e._param_changed(self.read,"voltage_v","3")
        self.assertEqual(e._history_index,len(e._history)-1)
        e._history_move(1)
        self.assertEqual(self.read.params["voltage_v"],"3")

    def test_clear_and_output_unexpose_can_be_undone(self):
        e=self.editor
        e._connect("read","trace@live","plot","plot@slot1")
        e._show_inspector(self.read)
        e.result_output_widgets["live"].invoke()
        self.assertNotIn("_input_slot1_source",self.plot.params)
        e._history_move(-1)
        self.assertEqual(self.plot.params["_input_slot1_source"],"read")
        before=e._snapshot()
        e._clear()
        self.assertEqual(len(e.cards),2)
        e._history_move(-1)
        self.assertEqual(e._snapshot(),before)

    def test_inline_invalid_input_remains_editable_and_survives_navigation(self):
        e=self.editor
        original=self.read.params["voltage_v"]
        e._begin_inline_edit(self.read,"voltage_v")
        e._inline_editor[2].set("wrong")
        e._finish_inline_edit(redraw=True)
        self.assertIsNotNone(e._inline_editor)
        self.assertEqual(e._inline_editor[2].get(),"wrong")
        self.assertEqual(self.read.params["voltage_v"],original)
        e.redraw()
        e._show_inspector(self.read)
        self.assertEqual(e.param_vars["voltage_v"].get(),"wrong")
        e.param_vars["voltage_v"].set("250 mV")
        self.assertAlmostEqual(float(self.read.params["voltage_v"]),.25)
        self.assertNotIn(("read","voltage_v"),e._field_errors)

    def test_shared_numeric_units_and_ranges(self):
        e=self.editor
        for key,value,expected in (("voltage_v","-250 mV",-.25),("current_limit_a","10 uA",1e-5),("settle_s","20 ms",.02)):
            canonical,error=e._validate_parameter(self.read,key,value)
            self.assertFalse(error)
            self.assertAlmostEqual(float(canonical),expected)
        for key,value in (("voltage_v","5 mA"),("sample_count","2.5"),("sample_count","0"),("nplc","0"),("settle_s","-1"),("voltage_v","nan")):
            self.assertTrue(e._validate_parameter(self.read,key,value)[1],(key,value))

    def test_validation_shows_all_errors_and_locates_parameter(self):
        e=self.editor
        e._show_inspector(self.read)
        for key in ("voltage_v","current_limit_a","sample_count","nplc"):
            e.param_vars[key].set("bad")
        e._validate()
        self.assertGreaterEqual(len(e._validation_issues),4)
        index=next(i for i,item in enumerate(e._validation_issues) if item[:2]==("read","sample_count"))
        e.issue_tree.selection_set(str(index))
        e._locate_issue()
        self.assertEqual(e.selected_id,"read")
        self.assertEqual(e.param_vars["sample_count"].get(),"bad")
        self.assertTrue(e._error_labels["read","sample_count"].cget("text"))

    def test_plot_labels_follow_source_and_disconnect(self):
        e=self.editor
        self.assertIn("Unconnected",e._node_inputs(self.plot)[0][1])
        e._connect("read","trace@live","plot","plot@slot1")
        self.assertEqual(e._node_inputs(self.plot)[0][1],"1 · Live current (A)")
        e._disconnect_edge(("read","_input_slot1_source","plot"))
        e._changed()
        self.assertIn("Unconnected",e._node_inputs(self.plot)[0][1])

    def test_wire_hit_highlight_delete_and_undo(self):
        e=self.editor
        e._connect("read","trace@live","plot","plot@slot1")
        edge=("read","_input_slot1_source","plot")
        route=e._wire_route(self.read,self.plot,"trace@live",target_kind="plot@slot1")
        event=self.event_at(route[len(route)//2])
        self.assertEqual(e._edge_at(event),edge)
        e._hover_edge(event)
        self.assertEqual(len(e.canvas.find_withtag("edge_highlight")),3)
        e.selected_edge=edge
        e._delete_selected()
        self.assertNotIn("_input_slot1_source",self.plot.params)
        e._history_move(-1)
        self.assertEqual(self.plot.params["_input_slot1_source"],"read")

    def test_rewire_target_is_atomic_and_undo_restores_previous_slot(self):
        e=self.editor
        e._connect("read","trace@live","plot","plot@slot1")
        before=e._snapshot()
        index=e._history_index
        self.assertTrue(e._apply_rewire(("read","_input_slot1_source","plot"),"read","trace@live","plot","plot@slot2"))
        self.assertNotIn("_input_slot1_source",self.plot.params)
        self.assertEqual(self.plot.params["_input_slot2_source"],"read")
        self.assertEqual(e._history_index,index+1)
        e._history_move(-1)
        self.assertEqual(e._snapshot(),before)

    def test_invalid_rewire_retains_original_and_history(self):
        e=self.editor
        e._connect("read","trace@live","plot","plot@slot1")
        before=e._snapshot()
        index=e._history_index
        self.assertFalse(e._apply_rewire(("read","_input_slot1_source","plot"),"read","curve@curve","plot","plot@slot2"))
        self.assertEqual(e._snapshot(),before)
        self.assertEqual(e._history_index,index)

    def test_rewire_source_and_cancel_endpoint_drag(self):
        e=self.editor
        second=create_autotest_flow_card("constant_current","read2")
        second.params["_outputs"]="live"
        second.x,second.y=200,500
        e.cards.append(second)
        e._changed()
        e._connect("read","trace@live","plot","plot@slot1")
        edge=("read","_input_slot1_source","plot")
        e.selected_edge=edge
        self.assertTrue(e._begin_rewire_at(self.event_at(e._socket_position(self.read,"trace@live"))))
        e._cancel_connection()
        self.assertEqual(self.plot.params["_input_slot1_source"],"read")
        self.assertTrue(e._apply_rewire(edge,"read2","trace@live","plot","plot@slot1"))
        self.assertEqual(self.plot.params["_input_slot1_source"],"read2")
        e._history_move(-1)
        self.assertEqual(self.plot.params["_input_slot1_source"],"read")

    def test_endpoint_drag_events_reconnect_to_named_slot(self):
        e=self.editor
        e._connect("read","trace@live","plot","plot@slot1")
        e.selected_edge=("read","_input_slot1_source","plot")
        self.assertTrue(e._begin_rewire_at(self.event_at(e._socket_position(self.plot,"plot@slot1",incoming=True))))
        event=self.event_at(e._socket_position(self.plot,"plot@slot2",incoming=True))
        e._canvas_motion(event)
        e._canvas_release(event)
        self.assertNotIn("_input_slot1_source",self.plot.params)
        self.assertEqual(self.plot.params["_input_slot2_source"],"read")

    def test_separate_inline_commits_have_separate_undo_steps(self):
        e=self.editor
        old=self.read.params["voltage_v"]
        for value in ("2","3"):
            e._begin_inline_edit(self.read,"voltage_v")
            e._inline_editor[2].set(value)
            e._finish_inline_edit(redraw=True)
        e._history_move(-1)
        self.assertEqual(self.read.params["voltage_v"],"2")
        e._history_move(-1)
        self.assertEqual(self.read.params["voltage_v"],old)

    def test_control_z_from_inspector_and_inline_field(self):
        e=self.editor
        old=self.read.params["voltage_v"]
        e._show_inspector(self.read)
        e.param_vars["voltage_v"].set("2")
        widget=e._field_widgets["read","voltage_v"]
        e._history_move(-1,SimpleNamespace(widget=widget))
        self.assertEqual(self.read.params["voltage_v"],old)
        e._begin_inline_edit(self.read,"voltage_v")
        e._inline_editor[2].set("3")
        event=SimpleNamespace(widget=e._inline_editor[0])
        e._history_move(-1,event)
        self.assertEqual(e._inline_editor[2].get(),old)
        e._history_move(1,event)
        self.assertEqual(e._inline_editor[2].get(),"3")

    def test_invalid_draft_blocks_save_without_opening_dialog(self):
        from unittest.mock import patch
        e=self.editor
        e._param_changed(self.read,"voltage_v","bad")
        with patch("semi_auto_probe.flow_editor.simpledialog.askstring") as prompt:
            e._save_current_workflow()
            prompt.assert_not_called()
        self.assertIn("before saving",e.status_var.get())

    def test_all_builtin_template_parameters_remain_accepted(self):
        from semi_auto_probe.workflow_library import builtin_templates,cards_from_template
        for name in builtin_templates():
            for card in cards_from_template(name):
                for param in self.editor.definitions[card.type_id].parameters:
                    _,error=self.editor._validate_parameter(card,param.key,card.params.get(param.key,param.default))
                    self.assertFalse(error,(name,card.card_id,param.key,error))

    def test_replacing_graph_clears_stale_drafts(self):
        e=self.editor
        e._param_changed(self.read,"voltage_v","bad")
        e._load_template("General / Examples / Current and Heatmap")
        self.assertFalse(e._draft_values)
        self.assertFalse(e._field_errors)
        read=e._card("read")
        e._param_changed(read,"voltage_v","bad again")
        e._clear()
        self.assertFalse(e._draft_values)
        self.assertFalse(e._field_errors)

    def test_actual_wire_click_then_source_handle_drag(self):
        e=self.editor
        second=create_autotest_flow_card("constant_current","read2")
        second.params["_outputs"]="live"
        second.x,second.y=200,500
        e.cards[:]=[self.read,self.plot,second]
        e._changed()
        e._connect("read","trace@live","plot","plot@slot1")
        e.window.deiconify()
        e.window.update()
        e._fit_nodes()
        e.window.update()
        def mouse(sequence,point):
            event=self.event_at(point)
            e.canvas.event_generate(sequence,x=int(event.x),y=int(event.y))
        route=e._wire_route(self.read,self.plot,"trace@live",target_kind="plot@slot1")
        mid=route[len(route)//2]
        mouse("<Motion>",mid)
        mouse("<ButtonPress-1>",mid)
        mouse("<ButtonRelease-1>",mid)
        self.assertEqual(e.selected_edge,("read","_input_slot1_source","plot"))
        source=e._socket_position(self.read,"trace@live")
        destination=e._socket_position(second,"trace@live")
        mouse("<Motion>",source)
        mouse("<ButtonPress-1>",source)
        self.assertIsNotNone(e._rewire)
        mouse("<B1-Motion>",destination)
        mouse("<ButtonRelease-1>",destination)
        self.assertEqual(self.plot.params["_input_slot1_source"],"read2")


if __name__ == "__main__":
    unittest.main()
