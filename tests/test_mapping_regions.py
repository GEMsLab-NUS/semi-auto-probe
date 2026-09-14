import tkinter as tk
import unittest
from dataclasses import replace
from types import SimpleNamespace
from semi_auto_probe.mapping import MappingSettings, generate_mapping_plan, region_contains
from semi_auto_probe.mapping_panel import MappingPanel

class RegionTests(unittest.TestCase):
    def test_circle_clips_grid_and_preserves_order(self):
        s=MappingSettings(x_b_um=4,y_b_um=4,step_x_um=1,step_y_um=1,region_shape='circle',measurement_flow=())
        p=generate_mapping_plan(s,(1,1,1))
        self.assertEqual(len(p.points),13)
        self.assertEqual([pt.order for pt in p.points],list(range(1,14)))
        self.assertTrue(all((pt.stage_x_um-2)**2+(pt.stage_y_um-2)**2<=4 for pt in p.points))
    def test_concave_polygon_boundary_and_serialized_vertices(self):
        s=MappingSettings(region_shape='polygon',polygon_vertices=[[0,0],[4,0],[4,1],[1,1],[1,4],[0,4]],step_x_um=1,step_y_um=1,measurement_flow=()).normalized()
        p=generate_mapping_plan(s,(1,1,1))
        self.assertEqual(len(p.points),16)
        self.assertFalse(region_contains(s,2,2))
        self.assertTrue(region_contains(s,1,3))
        self.assertEqual((s.x_b_um,s.y_b_um),(4,4))
    def test_empty_region_and_degenerate_polygon_rejected(self):
        with self.assertRaises(ValueError):
            generate_mapping_plan(MappingSettings(region_shape='circle',x_b_um=1,y_b_um=1,step_x_um=2,step_y_um=2),(1,1,1))
        with self.assertRaises(ValueError):
            MappingSettings(region_shape='polygon',polygon_vertices=((0,0),(1,1),(2,2))).normalized()
    def test_canvas_polygon_commit_and_run_lock(self):
        root=tk.Tk(); root.geometry('1280x800')
        try:
            panel=MappingPanel(root,get_scales=lambda:(1,1,1),record_corner=lambda c:None,start_run=lambda s:None,stop_run=lambda:None)
            panel.pack(fill='both',expand=True); root.update()
            panel.shape_var.set('polygon'); c=panel.scan_canvas; c.begin()
            c.vertices=[(0,0),(20,0),(0,20)]; c.commit(); root.update()
            self.assertEqual(len(panel.plan.points),6)
            self.assertGreater(c.winfo_width(),450)
            panel.set_running(True); c.begin(); self.assertFalse(c.editing)
            self.assertTrue(c.winfo_ismapped())
            self.assertFalse(panel.data_window.winfo_viewable())
            panel.set_current(panel.plan.points[0]); self.assertTrue(c.find_withtag('current'))
            panel.set_running(False)
        finally:
            root.update_idletasks(); root.destroy()

if __name__=='__main__': unittest.main()
