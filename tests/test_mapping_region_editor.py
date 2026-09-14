"""Region construction, editing and persistence exercised without hardware."""
import tempfile
import tkinter as tk
import unittest
from tkinter import ttk
from pathlib import Path
from types import SimpleNamespace

from semi_auto_probe.mapping import MappingSettings, MappingSession, generate_mapping_plan, load_mapping
from semi_auto_probe.mapping_panel import MappingPanel
from semi_auto_probe.mapping_region import region_geometry


class ConstructionTests(unittest.TestCase):
    def test_rectangle_center_and_opposite_corners_agree(self):
        self.assertEqual(region_geometry('rectangle','center_corner',[(20,30),(10,5)]),(10,5,30,55))
        self.assertEqual(region_geometry('rectangle','two_corners',[(30,55),(10,5)]),(10,5,30,55))

    def test_circumcircle_is_translation_invariant(self):
        for offset in (0,1000000):
            bounds=region_geometry('circle','three_points',[(offset,5),(offset+5,10),(offset+10,5)])
            self.assertEqual(bounds,(offset,0,offset+10,10))
        self.assertEqual(region_geometry('circle','center_corner',[(0,0),(3,4)]),(-5,-5,5,5))

    def test_invalid_circle_points_rejected(self):
        for points in ([(0,0),(1,1),(2,2)],[(1,1),(1,1),(3,4)]):
            with self.assertRaisesRegex(ValueError,'collinear'):
                region_geometry('circle','three_points',points)

    def test_construction_saved_and_loaded_with_scan(self):
        settings=MappingSettings(region_shape='circle',region_method='three_points',region_control_points=((0,5),(5,10),(10,5)),step_x_um=1,step_y_um=1,measurement_flow=())
        plan=generate_mapping_plan(settings,(1,1,1))
        with tempfile.TemporaryDirectory() as directory:
            session=MappingSession(Path(directory),plan,0)
            session.finish('stopped','');session.close()
            loaded,_,_=load_mapping(Path(directory))
            self.assertEqual(loaded.settings.region_method,'three_points')
            self.assertEqual(loaded.settings.region_control_points,settings.region_control_points)
            self.assertEqual(loaded.points,plan.points)


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.geometry('1280x800')
        self.callback_errors=[]
        self.root.report_callback_exception=lambda *args:self.callback_errors.append(args)
        self.starts=[]
        self.panel=MappingPanel(self.root,get_scales=lambda:(1,1,1),record_corner=lambda c:None,start_run=self.starts.append,stop_run=lambda:None)
        self.panel.pack(fill='both',expand=True);self.root.update()
        self.editor=self.panel.region_editor
        self.canvas=self.panel.scan_canvas

    def tearDown(self):
        self.root.update_idletasks();self.root.destroy()
        self.assertEqual(self.callback_errors,[])

    def event(self,x,y,state=0):
        sx,sy=self.canvas.screen(x,y)
        return SimpleNamespace(x=sx,y=sy,state=state)

    def test_modes_have_only_their_point_rows_and_restore_values(self):
        self.editor.method_var.set('center_corner')
        self.editor.set_points([(20,30),(10,5)])
        self.assertEqual(self.editor.geometry()[0],(10,5,30,55))
        self.editor.select_shape('circle');self.editor.method_var.set('three_points')
        self.assertEqual(len(self.editor.rows),3)
        self.editor.select_shape('rectangle');self.editor.method_var.set('center_corner')
        self.assertEqual(self.editor.points(),[(20,30),(10,5)])
        self.editor.select_shape('polygon');self.editor.add_vertex()
        self.assertEqual(len(self.editor.rows),4)
        self.assertEqual(int(self.editor.polygon_tree.cget('height')),5)
        self.editor.remove_vertex(1)
        self.assertEqual(len(self.editor.rows),3)

    def test_polygon_is_fixed_list_with_insert_add_remove(self):
        self.editor.select_shape('polygon')
        self.editor.polygon_tree.selection_set('1')
        self.editor.insert_vertex()
        self.assertEqual(len(self.editor.rows),4)
        self.assertEqual(self.editor.points()[1],(100,0))
        self.editor.polygon_tree.selection_set('1')
        self.editor.remove_selected_vertex()
        self.assertEqual(self.editor.points(),[(0,0),(100,0),(50,100)])
        self.editor.add_vertex()
        self.assertEqual(len(self.editor.polygon_tree.get_children()),4)
        descendants=[]
        stack=[self.editor.editor_host]
        while stack:
            widget=stack.pop();descendants.extend(widget.winfo_children());stack.extend(widget.winfo_children())
        self.assertFalse(any(isinstance(widget,ttk.Entry) for widget in descendants))

    def test_canvas_three_point_circle_commits_only_after_third_point(self):
        self.editor.select_shape('circle');self.editor.method_var.set('three_points')
        self.canvas.begin()
        for point in ((0,50),(50,100)):
            self.canvas.click(self.event(*point))
        self.assertTrue(self.canvas.editing)
        self.canvas.click(self.event(100,50))
        self.assertFalse(self.canvas.editing)
        self.assertEqual(self.panel.plan.settings.region_control_points,((0,50),(50,100),(100,50)))
        self.assertTrue(self.canvas.find_withtag('trajectory'))

    def test_handle_drag_and_region_translation_keep_viewport(self):
        self.panel.preview();bounds=self.canvas.bounds
        self.canvas.click(self.event(100,100))
        self.canvas.drag(self.event(120,130));self.canvas.release(self.event(120,130))
        self.assertEqual(self.editor.points()[1],(120,130))
        self.assertEqual(self.canvas.bounds,bounds)
        self.canvas.click(self.event(50,50,state=1))
        self.canvas.drag(self.event(60,70,state=1));self.canvas.release(self.event(60,70,state=1))
        self.assertEqual(self.editor.points(),[(10,20),(130,150)])
        self.assertEqual(self.panel.plan.settings.x_a_um,10)

    def test_escape_rolls_back_drag(self):
        self.panel.preview()
        self.canvas.click(self.event(100,100));self.canvas.drag(self.event(130,120));self.canvas.cancel()
        self.assertEqual(self.editor.points(),[(0,0),(100,100)])

    def test_invalid_form_does_not_start_and_running_locks_edits(self):
        self.editor.select_shape('circle');self.editor.method_var.set('three_points')
        self.editor.set_points([(0,0),(10,10),(20,20)])
        self.panel.start();self.assertEqual(self.starts,[])
        self.assertIn('collinear',self.panel.status.get())
        self.panel.set_running(True)
        self.assertEqual(self.panel.stop_button.cget('background'),'#dc2626')
        self.editor.select_shape('polygon');self.assertEqual(self.editor.shape_var.get(),'circle')
        self.panel.set_running(False)
        self.assertEqual(self.panel.stop_button.cget('background'),'#49383d')

    def test_form_change_marks_path_stale_and_preview_restores_it(self):
        self.panel.preview()
        self.panel.variables['step_x_um'].set('20')
        self.canvas.draw()
        self.assertTrue(self.panel.region_dirty)
        self.assertFalse(self.canvas.find_withtag('trajectory'))
        self.panel.preview()
        self.assertFalse(self.panel.region_dirty)
        self.assertTrue(self.canvas.find_withtag('trajectory'))

    def test_canvas_has_major_minor_grid_and_live_xy_marker(self):
        self.panel.preview()
        self.canvas.set_stage_position(25,35)
        self.canvas.draw()
        self.assertGreater(len(self.canvas.find_withtag('minor_grid')),0)
        self.assertGreater(len(self.canvas.find_withtag('major_grid')),0)
        self.assertGreater(len(self.canvas.find_withtag('stage_position')),2)

    def test_left_status_has_no_scrollbar_and_uses_full_height(self):
        self.root.geometry('1280x720');self.root.update()
        descendants=[]
        stack=[self.panel.left_status]
        while stack:
            widget=stack.pop();descendants.extend(widget.winfo_children());stack.extend(widget.winfo_children())
        self.assertFalse(any(isinstance(widget,ttk.Scrollbar) for widget in descendants))
        self.assertGreaterEqual(self.panel.left_status.winfo_width(),280)
        self.assertGreaterEqual(self.panel.left_status.winfo_height(),680)

    def test_region_sidebar_is_wide_and_keeps_dark_scroll_background(self):
        self.root.geometry('1920x1000');self.root.update()
        self.assertEqual(self.panel.REGION_SIDEBAR_WIDTH,410)
        self.assertGreaterEqual(self.panel.region_scroll_canvas.winfo_width(),410)
        self.assertGreaterEqual(self.panel.region_sidebar.winfo_width(),425)
        self.assertEqual(self.panel.region_scroll_canvas.cget('background'),'#0b0f14')
        self.assertGreater(self.panel.scan_canvas.winfo_width(),900)

    def test_measurement_flow_is_in_sidebar_and_uses_probe_accent(self):
        self.assertEqual(self.panel.workflow_button.master.master.master, self.panel.region_scroll_canvas)
        self.assertEqual(self.panel.workflow_button.cget('background'),'#0f5f91')
        self.assertEqual(self.panel.workflow_button.cget('text'),'Config Measure Flow')

    def test_cursor_mode_snaps_shift_double_click_move_target(self):
        moves=[]
        self.panel.move_to_xy=lambda x,y:moves.append((x,y))
        self.panel.variables['step_x_um'].set('10')
        self.panel.variables['step_y_um'].set('10')
        self.panel.set_canvas_tool('cursor')
        event=self.event(21,39,state=1)
        self.canvas.double_click(event)
        self.assertEqual(moves,[(20,40)])
        self.assertEqual(self.canvas.selected_target,(20,40))
        self.panel.set_canvas_tool('hand')
        self.assertEqual(self.canvas.cget('cursor'),'fleur')

    def test_draw_hint_only_appears_in_center_footer_while_drawing(self):
        self.assertFalse(self.panel.draw_hint_label.winfo_ismapped())
        original_status=self.panel.status.get()
        self.canvas.begin();self.root.update()
        self.assertTrue(self.panel.draw_hint_label.winfo_ismapped())
        self.assertEqual(self.panel.draw_hint_label.master.master, self.panel.scan_canvas.master)
        self.assertEqual(self.panel.status.get(),original_status)
        self.canvas.cancel();self.root.update()
        self.assertFalse(self.panel.draw_hint_label.winfo_ismapped())

    def test_unfinished_canvas_selection_does_not_start(self):
        self.panel.preview()
        self.canvas.begin()
        self.canvas.click(self.event(10,10))
        self.panel.start()
        self.assertEqual(self.starts,[])
        self.assertIn('Finish or cancel',self.panel.status.get())

    def test_record_stage_updates_third_circle_point(self):
        self.editor.select_shape('circle')
        self.editor.method_var.set('three_points')
        self.panel.set_corner('2',75,25)
        self.assertEqual(self.editor.points()[2],(75,25))

    def test_restore_circle_rebuilds_correct_method_and_rows(self):
        settings=MappingSettings(region_shape='circle',region_method='three_points',region_control_points=((0,5),(5,10),(10,5))).normalized()
        self.editor.restore(settings)
        self.assertEqual(self.editor.method_var.get(),'three_points')
        self.assertEqual(len(self.editor.rows),3)
        self.assertEqual(self.editor.geometry()[0],(0,0,10,10))

if __name__=='__main__':unittest.main()
