import unittest
from io import BytesIO
import numpy as np
from matplotlib.figure import Figure
from semi_auto_probe.mapping import MappingSettings, generate_mapping_plan
from semi_auto_probe.mapping_plot import draw_mapping_figure
from semi_auto_probe.measurement_workflow import AutoTestFlowStep
from semi_auto_probe.virtual_mask import DEFAULT_MASK_PATH, default_binding_text


class VirtualMaskGeometryTests(unittest.TestCase):
    def make_plan(self, **kwargs):
        step = AutoTestFlowStep('virtual_mask', {
            'image_path': str(DEFAULT_MASK_PATH), 'bindings': default_binding_text(),
            'coordinate_frame': 'xy',
        })
        return generate_mapping_plan(MappingSettings(measurement_flow=(step,), **kwargs), (1, 1, 1))

    def test_clipped_regions_have_finite_edges_and_hide_excluded_cells(self):
        for region in ({'region_shape': 'circle'}, {
            'region_shape': 'polygon', 'polygon_vertices': ((0, 0), (100, 0), (0, 100)),
        }):
            for preview in (False, True):
                with self.subTest(region=region, preview=preview):
                    plan = self.make_plan(**region)
                    values = np.full((len(plan.ys), len(plan.xs)), np.nan)
                    states = np.zeros(values.shape, dtype=np.uint8)
                    selected = np.zeros(values.shape, dtype=bool)
                    for p in plan.points:
                        selected[p.row, p.col] = True
                    figure = Figure()
                    ax = draw_mapping_figure(figure, plan, values, states, preview=preview)
                    output = BytesIO()
                    figure.savefig(output, format='png')
                    self.assertTrue(output.getvalue().startswith(b'\x89PNG'))
                    self.assertLess(selected.sum(), selected.size)
                    for mesh in ax.collections:
                        self.assertTrue(np.isfinite(mesh.get_coordinates()).all())
                        self.assertTrue(np.ma.getmaskarray(mesh.get_array())[~selected].all())
                    overlay = ax.collections[1].get_array()
                    self.assertTrue(np.ma.getmaskarray(overlay)[selected].all() if preview
                                    else (~np.ma.getmaskarray(overlay)[selected]).all())

    def test_singleton_axes_use_configured_cell_size(self):
        for xmax, ymax in ((0, 20), (20, 0), (0, 0)):
            with self.subTest(xmax=xmax, ymax=ymax):
                plan = self.make_plan(x_b_um=xmax, y_b_um=ymax, step_x_um=10, step_y_um=20)
                values = np.full((len(plan.ys), len(plan.xs)), np.nan)
                ax = draw_mapping_figure(Figure(), plan, values, np.zeros_like(values), preview=True)
                coords = ax.collections[0].get_coordinates()
                self.assertTrue(np.isfinite(coords).all())
                np.testing.assert_allclose(coords[0, :, 0], np.arange(len(plan.xs)+1)*10-5)
                np.testing.assert_allclose(coords[:, 0, 1], np.arange(len(plan.ys)+1)*20-10)

    def test_only_completed_measurements_are_colored(self):
        plan = self.make_plan(x_b_um=10, y_b_um=10)
        values = np.array([[1e-6, 2e-6], [3e-6, 4e-6]])
        states = np.array([[1, 0], [2, 3]], dtype=np.uint8)
        ax = draw_mapping_figure(Figure(), plan, values, states)
        np.testing.assert_array_equal(np.ma.getmaskarray(ax.collections[0].get_array()), states != 1)
        np.testing.assert_array_equal(np.ma.getmaskarray(ax.collections[1].get_array()), states == 1)


if __name__ == '__main__':
    unittest.main()
