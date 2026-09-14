import unittest
from dataclasses import replace
import numpy as np
from semi_auto_probe.img_stitch import StitchSession, StitchSettings, TileRecord, recompose_session, mosaic_pixel_to_stage_um

class StitchGeometryRegressionTest(unittest.TestCase):
    def session(self):
        keys=[(0,0),(0,1),(0,2),(0,3),(1,3),(1,2),(1,1),(1,0)]
        return StitchSession(2,4,80,70,1.,10,2.,'array',60.,50.,0,0,0,
            StitchSettings(20,20,max_correction_um=5.,registration_weight=1.,white_balance_correction=False),
            tuple(TileRecord(r,c,i,'',-60*c,-50*r,0,-60.*c,-50.*r) for i,(r,c) in enumerate(keys)))

    def test_accumulated_cross_axis_offset_is_not_clipped_to_stage_grid(self):
        s=self.session()
        rng=np.random.default_rng(321)
        source=rng.integers(0,256,(140,270,3),dtype=np.uint8)
        tiles={t.key:source[t.row*50+t.col*3:t.row*50+t.col*3+70,t.col*60:t.col*60+80].copy() for t in s.tiles}
        _,p,edges=recompose_session(s,tile_images=tiles)
        self.assertAlmostEqual(p[(0,3)][1],9.,delta=.8)
        self.assertLess(max(np.linalg.norm(np.subtract(e.applied_shift_px,e.measured_shift_px)) for e in edges),1.)

    def test_click_registered_tile_centers_returns_capture_coordinates(self):
        s=self.session()
        for angle in (0.,31.,90.):
            session=replace(s,camera_fov_rotation_deg=angle)
            positions={t.key:(t.col*60.-t.row*2.,t.row*50.+t.col*3.) for t in s.tiles}
            for t in s.tiles:
                x=positions[t.key][0]-min(v[0] for v in positions.values())+40
                y=positions[t.key][1]-min(v[1] for v in positions.values())+35
                actual=mosaic_pixel_to_stage_um(session,positions,x,y)
                np.testing.assert_allclose(actual,(t.stage_x_um,t.stage_y_um),atol=1e-8)

    def test_click_empty_canvas_region_is_rejected(self):
        s=self.session()
        with self.assertRaises(ValueError):
            mosaic_pixel_to_stage_um(s,{t.key:(t.col*100.,t.row*100.) for t in s.tiles},90.,90.)

    def test_layout_is_independent_of_capture_order(self):
        s = self.session()
        rng = np.random.default_rng(42)
        source = rng.integers(0, 256, (140, 270, 3), dtype=np.uint8)
        tiles = {t.key: source[t.row*50+t.col*3:t.row*50+t.col*3+70,
                               t.col*60:t.col*60+80].copy() for t in s.tiles}
        _, expected, _ = recompose_session(s, tile_images=tiles)
        reordered = replace(s, tiles=tuple(replace(t, order=99-t.order) for t in s.tiles))
        _, actual, _ = recompose_session(reordered, tile_images=tiles)
        for key in expected:
            np.testing.assert_allclose(actual[key], expected[key], atol=1e-8)

    def test_off_center_click_rotates_local_displacement(self):
        s = replace(self.session(), camera_fov_rotation_deg=90.)
        positions = {t.key: (t.col*60., t.row*50.) for t in s.tiles}
        actual = mosaic_pixel_to_stage_um(s, positions, 45., 38.)
        np.testing.assert_allclose(actual, (-3., 5.), atol=1e-8)
