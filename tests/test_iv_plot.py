import unittest
from types import SimpleNamespace
from semi_auto_probe.iv_plot import IVPlotBuffer, spread_indices

class IVPlotTests(unittest.TestCase):
    def test_full_run_sampling_and_endpoints(self):
        indices = spread_indices(300, 100)
        self.assertEqual((indices[0], indices[-1]), (0, 299))
        self.assertEqual(len(set(indices)), 100)
        self.assertEqual(spread_indices(3, 100), [0, 1, 2])

    def test_four_leg_cycles_keep_turning_point(self):
        buffer = IVPlotBuffer()
        samples = [SimpleNamespace(sweep_leg=leg) for leg in [0, 0, 1, 1, 2, 2, 3, 4]]
        for sample in samples:
            buffer.append(sample)
        self.assertEqual(len(buffer.curves), 2)
        self.assertIs(buffer.curves[1][0], samples[6])
        self.assertIs(buffer.display_curves()[-1][-1], samples[-1])
        buffer.clear()
        buffer.append(samples[-1])
        self.assertEqual(buffer.curves, [[samples[-1]]])

    def test_dense_curve_display_is_bounded_and_keeps_endpoints(self):
        buffer = IVPlotBuffer()
        for i in range(20001):
            buffer.append(SimpleNamespace(sweep_leg=0, index=i))
        curve = buffer.display_curves()[0]
        self.assertEqual(len(curve), 2000)
        self.assertEqual((curve[0].index, curve[-1].index), (0, 20000))
        self.assertEqual(len(buffer), 20001)
