from __future__ import annotations

import unittest
from types import SimpleNamespace

from matplotlib.figure import Figure

from semi_auto_probe.app import HP6614CPlotWindow, ProbeApp
from semi_auto_probe.hp6614c import HP6614CSweepConfig, HP6614C_TEST_OUTPUT, HP6614C_TEST_TRANSFER


COLORS = {
    "bg": "#0b0f14",
    "surface": "#111827",
    "surface_2": "#172033",
    "surface_3": "#202a3d",
    "text": "#f8fafc",
    "muted": "#94a3b8",
    "border": "#334155",
}


class FakeWindow:
    def __init__(self) -> None:
        self.last_geometry = ""

    def geometry(self, value: str) -> None:
        self.last_geometry = value


class FakeVar:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


def make_plot() -> HP6614CPlotWindow:
    plot = object.__new__(HP6614CPlotWindow)
    plot.colors = COLORS
    plot.figure = Figure(figsize=(13.0, 5.2), dpi=100, facecolor=COLORS["surface"])
    plot.window = FakeWindow()
    plot.combined_plot = None
    plot.heatmap_colorbar = None
    plot.records_by_test = {HP6614C_TEST_TRANSFER: [], HP6614C_TEST_OUTPUT: []}
    plot.records = plot.records_by_test[HP6614C_TEST_TRANSFER]
    plot.test_type = HP6614C_TEST_TRANSFER
    plot.current_point_name = None
    plot.curve_colors = {}
    plot.next_curve_color_index = 0
    plot.heatmap_values = {}
    plot.heatmap_values_by_key = {}
    plot.heatmap_rows = 1
    plot.heatmap_cols = 1
    plot.heatmap_label = "Vth (V)"
    plot.heatmap_metric = "vth"
    plot.heatmap_key = (HP6614C_TEST_TRANSFER, "vth")
    plot.show_heatmap_values = True
    plot.active_cell = None
    plot.title_var = FakeVar()
    plot.status_var = FakeVar()
    plot._draw = lambda: None
    plot.show = lambda: None
    return plot


class HP6614CPlotTests(unittest.TestCase):
    def test_workflow_requires_both_6614c_cards_for_combined_plot(self) -> None:
        combined = SimpleNamespace(
            measurement_flow=(
                SimpleNamespace(type_id="hp6614c_transfer"),
                SimpleNamespace(type_id="wait"),
                SimpleNamespace(type_id="hp6614c_output"),
            )
        )
        transfer_only = SimpleNamespace(measurement_flow=(SimpleNamespace(type_id="hp6614c_transfer"),))

        self.assertTrue(ProbeApp._autotest_uses_combined_hp6614c_plot(combined))
        self.assertFalse(ProbeApp._autotest_uses_combined_hp6614c_plot(transfer_only))

    def test_combined_layout_places_transfer_output_and_heatmap_left_to_right(self) -> None:
        plot = make_plot()

        plot._configure_plot_layout(True)

        self.assertIsNotNone(plot.transfer_axes)
        self.assertIsNotNone(plot.output_axes)
        transfer_x = plot.transfer_axes.get_position().x0
        output_x = plot.output_axes.get_position().x0
        heatmap_x = plot.heatmap_axes.get_position().x0
        self.assertLess(transfer_x, output_x)
        self.assertLess(output_x, heatmap_x)
        self.assertEqual(plot.window.last_geometry, "1320x620")

    def test_combined_layout_preserves_other_test_records_until_next_point(self) -> None:
        plot = make_plot()
        transfer_config = HP6614CSweepConfig(test_type=HP6614C_TEST_TRANSFER)
        output_config = HP6614CSweepConfig(test_type=HP6614C_TEST_OUTPUT, bias_values_v=(0.0,))

        plot.start("P1", transfer_config, combined_plot=True, reset_heatmap=True)
        plot.records.append({"drain_voltage_v": -1.0})
        plot.heatmap_values[(0, 0)] = 0.75
        plot.start("P1", output_config, combined_plot=True, reset_heatmap=False, combined_heatmap_metric="vth")

        self.assertEqual(plot.records_by_test[HP6614C_TEST_TRANSFER], [{"drain_voltage_v": -1.0}])
        self.assertEqual(plot.records, plot.records_by_test[HP6614C_TEST_OUTPUT])
        self.assertEqual(plot.heatmap_values[(0, 0)], 0.75)

        plot.records.append({"gate_voltage_v": 1.0})
        plot.start("P2", transfer_config, combined_plot=True)

        self.assertEqual(plot.records_by_test[HP6614C_TEST_TRANSFER], [])
        self.assertEqual(plot.records_by_test[HP6614C_TEST_OUTPUT], [])

    def test_combined_output_keeps_transfer_heatmap_and_does_not_update_it(self) -> None:
        plot = make_plot()
        output_config = HP6614CSweepConfig(test_type=HP6614C_TEST_OUTPUT, bias_values_v=(0.0,))

        plot.start(
            "P1",
            output_config,
            row=0,
            col=0,
            combined_plot=True,
            combined_heatmap_metric="ss",
            combined_heatmap_values=False,
        )
        plot.records.append({"drain_current_a": 2e-5})
        plot.heatmap_values[(0, 0)] = 123.0

        parameter_text = plot._update_heatmap_from_completed_point()

        self.assertEqual(plot.heatmap_key, (HP6614C_TEST_TRANSFER, "ss"))
        self.assertEqual(plot.heatmap_label, "SS (mV/dec)")
        self.assertFalse(plot.show_heatmap_values)
        self.assertEqual(plot.heatmap_values[(0, 0)], 123.0)
        self.assertEqual(parameter_text, "")

    def test_output_only_updates_max_id_heatmap(self) -> None:
        plot = make_plot()
        output_config = HP6614CSweepConfig(test_type=HP6614C_TEST_OUTPUT, bias_values_v=(0.0,))

        plot.start("P1", output_config, row=0, col=0, combined_plot=False)
        plot.records.extend(({"drain_current_a": -2e-5}, {"drain_current_a": 1e-5}))
        plot._update_heatmap_from_completed_point()

        self.assertEqual(plot.heatmap_key, (HP6614C_TEST_OUTPUT, "max_abs_id"))
        self.assertEqual(plot.heatmap_label, "Max |Id| (A)")
        self.assertEqual(plot.heatmap_values[(0, 0)], 2e-5)


if __name__ == "__main__":
    unittest.main()
