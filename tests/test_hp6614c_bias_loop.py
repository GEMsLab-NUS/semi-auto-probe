import json
import queue
import tempfile
import threading
import tkinter as tk
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.auto_test import AutoTestFlowStep, create_autotest_flow_card, measurement_flow_steps_from_cards
from semi_auto_probe.flow_editor import MeasurementFlowEditor
from semi_auto_probe.flow_graph import validate_flow_graph
from semi_auto_probe.workflow_runtime import NodeCapture


class HP6614CBiasLoopTests(unittest.TestCase):
    @staticmethod
    def cards():
        start = create_autotest_flow_card("workflow_start", "start")
        start.params.update(_workflow_version="2", _next="loop")
        loop = create_autotest_flow_card("hp6614c_bias_loop", "loop")
        loop.params.update(_child="drain_iv", _next="end")
        drain = create_autotest_flow_card("iv", "drain_iv")
        drain.params.update(_parent="loop")
        end = create_autotest_flow_card("workflow_end", "end")
        return [start, loop, drain, end]

    def test_nested_iv_is_not_a_second_control_root(self):
        self.assertEqual(validate_flow_graph(measurement_flow_steps_from_cards(self.cards())), ())

    def test_container_requires_one_linked_iv_child(self):
        cards = self.cards()
        cards[1].params.pop("_child")
        errors = validate_flow_graph(measurement_flow_steps_from_cards(cards))
        self.assertTrue(any("drop one iv node" in error for error in errors))

    def test_dragging_iv_into_and_out_of_container_updates_links(self):
        editor = object.__new__(MeasurementFlowEditor)
        loop = create_autotest_flow_card("hp6614c_bias_loop", "loop")
        drain = create_autotest_flow_card("iv", "drain")
        loop.x = loop.y = 0
        editor.cards = [loop, drain]
        left, top, _right, _bottom = editor._container_slot_bounds(loop)
        drain.x, drain.y = left + 20, top + 20
        self.assertTrue(editor._nest_card_at(drain))
        self.assertEqual(loop.params["_child"], "drain")
        self.assertEqual(drain.params["_parent"], "loop")
        drain.x, drain.y = 1000, 1000
        self.assertFalse(editor._nest_card_at(drain))
        self.assertNotIn("_child", loop.params)
        self.assertNotIn("_parent", drain.params)

    def test_container_uses_open_c_wrap_and_encloses_nested_iv(self):
        editor = object.__new__(MeasurementFlowEditor)
        loop = create_autotest_flow_card("hp6614c_bias_loop", "loop")
        drain = create_autotest_flow_card("iv", "drain")
        loop.x, loop.y = 100, 50
        loop.params["_child"] = drain.card_id
        drain.params["_parent"] = loop.card_id
        editor.cards = [loop, drain]

        editor._layout_nested_cards()
        left, top, right, bottom = editor._container_slot_bounds(loop)
        self.assertEqual(
            editor._container_wrap_points(loop),
            ((right, top), (left, top), (left, bottom), (right, bottom)),
        )
        self.assertGreaterEqual(drain.x, left)
        self.assertGreaterEqual(drain.y, top)
        self.assertLessEqual(drain.x + editor._node_width(drain), right)
        self.assertLessEqual(drain.y + editor._node_height(drain), bottom)

    def test_canvas_redraw_snaps_stale_child_into_open_wrap(self):
        root = tk.Tk()
        root.withdraw()
        loop = create_autotest_flow_card("hp6614c_bias_loop", "loop")
        drain = create_autotest_flow_card("iv", "drain")
        loop.x, loop.y = 100, 50
        loop.params["_child"] = drain.card_id
        drain.params["_parent"] = loop.card_id
        drain.x, drain.y = 900, 900
        colors = defaultdict(
            lambda: "#334155",
            text="#ffffff",
            muted="#94a3b8",
            surface="#111827",
            surface_2="#111827",
        )
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "semi_auto_probe.workflow_library.default_workflow_root", return_value=Path(tmp)
            ):
                editor = MeasurementFlowEditor(root, [loop, drain], colors, lambda: "new", lambda: None)
                editor.window.withdraw()
                drain.x, drain.y = 900, 900
                editor.redraw()
                left, top, right, bottom = editor._container_slot_bounds(loop)
                self.assertEqual((drain.x, drain.y), (left + 8, top + 8))
                wrap = editor.canvas.find_withtag("container_wrap:loop")
                self.assertEqual(len(wrap), 1)
                self.assertEqual(
                    tuple(editor.canvas.coords(wrap[0])),
                    (right, top, left, top, left, bottom, right, bottom),
                )
                editor.close()
        finally:
            root.update_idletasks()
            root.destroy()

    def test_nested_iv_curve_routes_through_container_opening_to_plot(self):
        root = tk.Tk()
        root.withdraw()
        loop = create_autotest_flow_card("hp6614c_bias_loop", "loop")
        drain = create_autotest_flow_card("iv", "drain")
        plot = create_autotest_flow_card("plot_panel", "plot")
        loop.x, loop.y = 100, 50
        loop.params["_child"] = drain.card_id
        drain.params.update(_parent=loop.card_id, _outputs="curve")
        plot.x, plot.y = 620, 220
        plot.params.update(
            layout="1x1",
            _input_slot1_source=drain.card_id,
            _input_slot1_output="curve",
            _input_slot1_type="curve",
            _input_slot1_unit="A",
        )
        colors = defaultdict(
            lambda: "#334155",
            text="#ffffff",
            muted="#94a3b8",
            surface="#111827",
            surface_2="#111827",
        )
        try:
            with tempfile.TemporaryDirectory() as tmp, patch(
                "semi_auto_probe.workflow_library.default_workflow_root", return_value=Path(tmp)
            ):
                editor = MeasurementFlowEditor(root, [loop, drain, plot], colors, lambda: "new", lambda: None)
                editor.window.withdraw()
                editor.redraw()
                self.assertNotIn("drain → plot", editor._blocked_routes)
                edge = editor.canvas.find_withtag("edge:drain:_input_slot1_source:plot")
                self.assertTrue(edge)
                coordinates = editor.canvas.coords(edge[-1])
                self.assertEqual(
                    tuple(coordinates[-2:]),
                    editor._socket_position(plot, "plot@slot1", incoming=True),
                )
                editor.close()
        finally:
            root.update_idletasks()
            root.destroy()

    def test_executor_applies_each_gate_bias_and_runs_nested_native_iv(self):
        wrapper = AutoTestFlowStep("hp6614c_bias_loop", {
            "_node_id": "loop", "_child": "drain_iv", "bias_values_v": "0,2,5",
            "hp_resource": "GPIB0::5::INSTR", "gate_current_limit_a": "1e-6",
            "gate_settle_s": "0", "readback": "true", "skip_open_clear": "true",
        })
        child = AutoTestFlowStep("iv", {
            "_node_id": "drain_iv", "_parent": "loop", "resource": "GPIB0::18::INSTR",
            "output_terminal": "rear", "sweep_mode": "voltage", "start": "0", "stop": "10",
            "step": "0.2", "voltage_limit_v": "15", "current_limit_a": "1e-5",
            "source_range": "20", "measure_range": "1e-5", "source_delay_s": "0.02", "nplc": "1",
        })
        settings = SimpleNamespace(measurement_flow=(wrapper, child))
        point = SimpleNamespace(name="D1", row=0, col=0, order=1)
        app = object.__new__(ProbeApp)
        app.autotest_stop_event = threading.Event()
        app.result_queue = queue.Queue()
        app.autotest_grid_rows = app.autotest_grid_cols = 1
        app._workflow_run_id = "run"
        app._workflow_local = threading.local()
        parent_capture = NodeCapture("loop", "hp6614c_bias_loop", "run", point, app.result_queue, False)
        app._workflow_local.capture = parent_capture
        labels = []
        applied = []

        class FakeHP:
            def __init__(self, _instrument=None):
                self.instrument = _instrument

            def _open_resource(self, *_args, **_kwargs):
                return SimpleNamespace(close=lambda: None)

            def apply_voltage(self, config):
                applied.append(config)
                return SimpleNamespace(voltage_v=config.voltage_v)

            def measure_output(self):
                return SimpleNamespace(voltage_v=applied[-1].voltage_v, current_a=1e-9)

        def run_iv(_point, _settings, configs, folder, _params):
            labels.append((app._workflow_local.curve_label, configs[0].start, configs[0].stop, folder.name))
            for voltage in (0.0, 10.0):
                app._workflow_sample({"source_value": voltage, "voltage_v": voltage, "current_a": voltage * 1e-7})
            return {"statistics": {"max_abs_current_a": 1e-6}}

        app._run_autotest_iv_sweep = run_iv
        with tempfile.TemporaryDirectory() as tmp, patch("semi_auto_probe.app.HP6614CRunner", FakeHP):
            result = app._run_autotest_hp6614c_bias_loop(point, settings, wrapper, Path(tmp))
            metadata = json.loads((Path(tmp) / "hp6614c_gate_loop.json").read_text(encoding="utf-8"))

        self.assertEqual([entry[0] for entry in labels], ["Vg=0 V", "Vg=2 V", "Vg=5 V"])
        self.assertTrue(all(entry[1:3] == (0.0, 10.0) for entry in labels))
        self.assertEqual(result["gate_biases_v"], [0.0, 2.0, 5.0])
        child_result = result["_nested_results"]["drain_iv"]
        self.assertEqual(set(child_result["curve"]), {"Vg=0 V", "Vg=2 V", "Vg=5 V"})
        self.assertEqual(set(parent_capture.series), {"Vg=0 V", "Vg=2 V", "Vg=5 V"})
        self.assertTrue(all(series["current"] == [1e-9, 1e-9] for series in parent_capture.series.values()))
        self.assertEqual(metadata["nested_node"], "drain_iv")
        self.assertEqual([config.voltage_v for config in applied], [0.0, 2.0, 5.0, 0.0])
        self.assertFalse(applied[-1].output_on)


if __name__ == "__main__":
    unittest.main()
