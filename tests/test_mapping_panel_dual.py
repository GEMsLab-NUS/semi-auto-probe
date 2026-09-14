from __future__ import annotations

import tkinter as tk
import unittest

from semi_auto_probe.auto_test import measurement_flow_steps_from_cards
from semi_auto_probe.mapping import MappingPointResult, MappingSettings, generate_mapping_plan
from semi_auto_probe.mapping_panel import LASER_CALIBRATION_LABEL, MappingPanel
from semi_auto_probe.workflow_library import TEMT6000_PULSED_MAPPING_TEMPLATE, cards_from_template


class MappingPanelDualTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk is unavailable: {exc}")
        self.root.withdraw()

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()

    def test_two_connected_heatmap_ports_render_together_and_calibration_is_selectable(self):
        flow = measurement_flow_steps_from_cards(cards_from_template(TEMT6000_PULSED_MAPPING_TEMPLATE))
        settings = MappingSettings(
            x_b_um=1, y_b_um=1, step_x_um=1, step_y_um=1,
            measurement_flow=flow,
        )
        plan = generate_mapping_plan(settings, (1, 1, 1))
        panel = MappingPanel(
            self.root,
            get_scales=lambda: (1, 1, 1),
            record_corner=lambda _corner: None,
            start_run=lambda _settings: None,
            stop_run=lambda: None,
            get_workflow=lambda: flow,
        )
        panel.set_plan(plan)
        from semi_auto_probe.workflow_plot import WorkflowPlotPanel
        plot_node = next(s for s in flow if s.type_id == "plot_panel")
        viewer = WorkflowPlotPanel(self.root, plot_node, (2, 2))
        viewer.window.withdraw()
        viewer.point_started(0, 0)
        viewer.point_finished(0, 0, {"3": 1e-6, "4": 2e-6}, "done", None)
        viewer.tick(force=True)
        self.assertEqual(len(viewer.slots), 4)
        self.assertAlmostEqual(viewer.values[3][0, 0], 1e-6)
        self.assertAlmostEqual(viewer.values[4][0, 0], 2e-6)
        viewer.close()

        panel.set_laser_calibration(
            [{"effective_current_a": .001, "measured_voltage_v": 1.9, "regulation_mode": "current_limit"}],
            [(.001, 1.9)],
        )
        panel._draw()
        self.assertEqual(panel.metric_var.get(), LASER_CALIBRATION_LABEL)
        self.assertEqual(panel.mapping_axes, ())


if __name__ == "__main__":
    unittest.main()
