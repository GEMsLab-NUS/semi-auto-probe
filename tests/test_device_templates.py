import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from semi_auto_probe.auto_test import measurement_flow_steps_from_cards
from semi_auto_probe.device_templates import device_templates
from semi_auto_probe.flow_graph import validate_flow_graph, resolve_data_output, evaluate_condition
from semi_auto_probe.workflow_formula import Curve, InvalidValue
from semi_auto_probe.workflow_library import cards_from_template, load_workflow, save_workflow, workflow_payload
from semi_auto_probe.workflow_runtime import NodeCapture


def steps_for(name):
    return measurement_flow_steps_from_cards(cards_from_template(name))


def feature(steps, identity, results):
    return resolve_data_output(identity, "result", {s.params["_node_id"]: s for s in steps}, results, {})


class DeviceTemplateTests(unittest.TestCase):
    def test_catalog_is_categorized_complete_and_has_no_mode_or_version_aliases(self):
        from semi_auto_probe.workflow_library import builtin_templates, template_tree
        tree = template_tree()
        self.assertTrue({"FET", "PD", "MR", "Cap", "General"}.issubset(tree))
        self.assertEqual(set(tree["FET"]), {"B1500", "HP6614C"})
        leaves = []
        def collect(children):
            for value in children.values():
                collect(value) if isinstance(value, dict) else leaves.append(value)
        collect(tree)
        self.assertEqual(set(leaves), set(builtin_templates()))
        self.assertEqual(len(leaves), len(set(leaves)))
        for name in leaves:
            self.assertNotIn("v2", name.lower())
            self.assertNotIn("Bonding", name)
            self.assertEqual(validate_flow_graph(steps_for(name)), ())
            self.assertFalse(any(s.type_id == "mapping_heatmap" for s in steps_for(name)))

    def test_mr_bipolar_path_and_read_resistance_window(self):
        from semi_auto_probe.keithley2450 import iv_sweep_configs_from_params
        sweep = next(s for s in steps_for("MR / Bipolar Switching IV") if s.type_id == "iv")
        values = iv_sweep_configs_from_params(sweep.params)[0].sweep_values()
        self.assertEqual((values[0], max(values), min(values), values[-1]), (0, 1, -1, 0))
        steps = steps_for("MR / Read SET Read RESET Read")
        results = {}
        for identity, current in (("read_initial", 1e-8), ("read_set", 1e-6), ("read_reset", 1e-8)):
            results[identity] = {"curve": {"Current": Curve((0, 1), (current, current))},
                                 "curve_voltage": {"Current": Curve((0, 1), (.1, .1), y_unit="V")}}
        self.assertAlmostEqual(feature(steps, "window", results), 100)
        self.assertTrue(feature(steps, "acceptance", results))
        self.assertIsInstance(feature(steps, "window", {"read_set": results["read_set"]}), InvalidValue)

    def test_cap_insulation_uses_measured_voltage_and_does_not_claim_capacitance(self):
        steps = steps_for("Cap / DC Leakage and Insulation")
        results = {"leakage": {"curve": {"Current": Curve((0, 1), (1e-9, 1e-9))},
                                "curve_voltage": {"Current": Curve((0, 1), (.09, .09), y_unit="V")},
                                "statistics": {"mean_current_a": 1e-9}}}
        self.assertAlmostEqual(feature(steps, "insulation", results), 9e7)
        self.assertTrue(feature(steps, "acceptance", results))

    def test_device_graphs_have_same_execution_and_features_in_probe_and_bond_modes(self):
        from semi_auto_probe.app import ProbeApp
        from semi_auto_probe.mapping import MappingSettings
        from semi_auto_probe.workflow_ports import CURVE_NODES
        for name in device_templates():
            observed = []
            for mode in ("probe", "bond"):
                with self.subTest(template=name, mode=mode), tempfile.TemporaryDirectory() as tmp:
                    app = ProbeApp.__new__(ProbeApp)
                    app.autotest_stop_event = threading.Event()
                    app.result_queue = queue.Queue()
                    app.autotest_grid_rows = app.autotest_grid_cols = 1
                    executed = []
                    def simulated_acquisition(point, settings, step, folder, cv2):
                        executed.append(step.params["_node_id"])
                        if step.type_id in CURVE_NODES:
                            for i in range(3):
                                app._workflow_sample({"elapsed_s": i, "source_value": i * .1,
                                    "gate_voltage_v": i * .1, "voltage_v": .1, "current_a": (i + 1) * 1e-9})
                        return {"calibration": [[.001, 2]], "on_off_ratio": 1000,
                                "statistics": {"max_pulse_response_a": 1e-9, "width_sensitivity_a_per_s": 1e-9,
                                               "power_sensitivity_a_per_mw": 1e-9, "response_amplitude_a": 2e-9}}
                    app._execute_autotest_flow_step_scoped = simulated_acquisition
                    app._execute_autotest_step_with_light = lambda p, s, step, light, folder, cv: app._execute_autotest_flow_step(p, s, step, folder, cv)
                    flow = steps_for(name)
                    settings = MappingSettings(measurement_flow=flow) if mode == "bond" else SimpleNamespace(measurement_flow=flow)
                    point = SimpleNamespace(name="p", order=1, row=0, col=0)
                    app._execute_autotest_measurement_graph(point, settings, Path(tmp), None)
                    saved = json.loads((Path(tmp) / "workflow_plots" / "0_0.json").read_text())
                    self.assertEqual(saved["status"], "done")
                    observed.append((executed, saved["values"]))
            self.assertEqual(observed[0], observed[1], name)

    def test_all_templates_pass_mapping_and_fet_instrument_preflight(self):
        from semi_auto_probe.app import ProbeApp
        from semi_auto_probe.b1500 import b1500_config_from_params
        from semi_auto_probe.hp6614c import hp6614c_sweep_config_from_params
        for name in device_templates():
            with self.subTest(template=name):
                steps = steps_for(name)
                ProbeApp._validate_mapping_workflow(SimpleNamespace(measurement_flow=steps))
                for step in steps:
                    if step.type_id.startswith("b1500_"):
                        b1500_config_from_params(step.params, test_type=step.type_id.removeprefix("b1500_"))
                    elif step.type_id.startswith("hp6614c_"):
                        hp6614c_sweep_config_from_params(step.params, test_type=step.type_id.removeprefix("hp6614c_"))

    def test_bond_stability_mapping_saves_curves_and_features_per_point(self):
        from test_mapping_workflow import MappingWorkflowTests
        cards = cards_from_template("General / Current Stability")
        measurement = next(c for c in cards if c.type_id == "constant_current")
        measurement.params.update(sample_count="5", settle_s="0")
        with tempfile.TemporaryDirectory() as tmp:
            app, events, (_, metadata, results) = MappingWorkflowTests().run_map(
                Path(tmp), measurement_flow_steps_from_cards(cards), x_b_um=1, y_b_um=0)
            self.assertEqual(metadata["status"], "completed", metadata.get("error"))
            self.assertEqual(len(results), 2)
            self.assertEqual(events.count(":READ?"), 10)
            self.assertEqual(events.count(":OUTP OFF"), 2)
            for col in (0, 1):
                saved = json.loads((Path(tmp) / "workflow_plots" / f"0_{col}.json").read_text())
                self.assertEqual(saved["status"], "done")
                self.assertEqual(len(saved["curves"]["stability"]["live"]["Current"]["y"]), 5)
                self.assertAlmostEqual(saved["values"]["2"], -3e-9)
                self.assertIsInstance(saved["values"]["3"], float)
            self.assertTrue(all(move[0].name != "Z" for move in app.serial_client.moves))

    def test_every_recipe_validates_and_saves_without_changing_its_wiring(self):
        for name in device_templates():
            with self.subTest(template=name), tempfile.TemporaryDirectory() as tmp:
                cards = cards_from_template(name)
                steps = measurement_flow_steps_from_cards(cards)
                self.assertEqual(validate_flow_graph(steps), ())
                panel = next(s for s in steps if s.type_id == "plot_panel")
                rows, cols = map(int, panel.params["layout"].split("x"))
                for i in range(1, rows * cols + 1):
                    self.assertIn(f"_input_slot{i}_source", panel.params)
                path = save_workflow(Path(tmp) / "example.json", cards)
                self.assertEqual(workflow_payload(load_workflow(path)), workflow_payload(cards))

    def test_fet_screen_skips_output_on_fail_or_missing_transfer(self):
        for instrument in ("B1500", "HP6614C"):
            steps = steps_for(f"FET / {instrument} / Screen and Characterize")
            decision = next(s for s in steps if s.type_id == "condition_boolean")
            for value, expected in ((1000, True), (10, False)):
                results = {"transfer": {"on_off_ratio": value}}
                self.assertEqual(evaluate_condition(decision, results, steps=steps), expected)
                self.assertEqual(feature(steps, "acceptance_value", results), int(expected))
            self.assertFalse(evaluate_condition(decision, {}, steps=steps))
            self.assertIsInstance(feature(steps, "acceptance_value", {}), InvalidValue)
            self.assertEqual(decision.params["_true"], "output")
            self.assertEqual(decision.params["_false"], "fail")

    def test_photocurrent_uses_this_points_dark_and_light_measurements(self):
        steps = steps_for("PD / Dark Light Current")
        results = {"dark": {"statistics": {"mean_current_a": -1e-9}},
                   "bright": {"statistics": {"mean_current_a": -21e-9}}}
        self.assertAlmostEqual(feature(steps, "photocurrent", results), -20e-9)
        self.assertAlmostEqual(feature(steps, "ratio", results), 21)
        self.assertIsInstance(feature(steps, "photocurrent", {"dark": results["dark"]}), InvalidValue)
        results["dark"]["statistics"]["mean_current_a"] = 0
        self.assertIsInstance(feature(steps, "ratio", results), InvalidValue)
        self.assertEqual(feature(steps, "photocurrent", results), -21e-9)

    def test_responsivity_requires_positive_incident_optical_power(self):
        steps = steps_for("PD / Responsivity and EQE")
        results = {"dark": {"statistics": {"mean_current_a": 1e-9}},
                   "bright": {"statistics": {"mean_current_a": 2.001e-6}}}
        self.assertIsInstance(feature(steps, "responsivity", results), InvalidValue)
        r = next(s for s in steps if s.params["_node_id"] == "responsivity")
        r.params["constants"] = json.dumps({"incident_power_w": 1e-5})
        self.assertAlmostEqual(feature(steps, "responsivity", results), 0.2)
        self.assertAlmostEqual(feature(steps, "eqe", results), 0.2 * 1239.841984 / 650 * 100)
        r.params["constants"] = json.dumps({"incident_power_w": -1})
        self.assertIsInstance(feature(steps, "responsivity", results), InvalidValue)

    def test_pulse_charge_uses_actual_time_samples(self):
        steps = steps_for("PD / Pulse Response")
        results = {"response": {"curve": {"Current": Curve((0, 0.1, 0.5), (1e-6, 3e-6, 1e-6))}}}
        self.assertAlmostEqual(feature(steps, "charge", results), 1e-6)
        self.assertGreater(feature(steps, "rms_noise", results), 0)

    def test_calibration_executes_before_both_gradient_consumers(self):
        steps = steps_for("PD / Calibrated Power and Width")
        by_id = {s.params["_node_id"]: s for s in steps}
        self.assertEqual(by_id["start"].params["_next"], "calibration")
        for identity in ("power", "width"):
            self.assertEqual(by_id[identity].params["_input_calibration_source"], "calibration")
        by_id["start"].params["_next"] = "power"
        self.assertTrue(any("calibration must execute" in error for error in validate_flow_graph(steps)))

    def test_bond_resistance_uses_measured_current_and_rejects_voltage_limit(self):
        steps = steps_for("General / Continuity and Resistance")
        capture = NodeCapture("continuity", "iv", "test", SimpleNamespace(name="p", row=0, col=0),
                              queue.Queue(), sweep_mode="current")
        for current in (-9e-5, 0, 9e-5):
            capture.sample({"source_value": current * 1.1, "current_a": current, "voltage_v": 50 * current})
        results = {"continuity": capture.result({}, "done")}
        voltage = results["continuity"]["curve_voltage"]["Current"]
        self.assertEqual((voltage.x_unit, voltage.y_unit), ("A", "V"))
        self.assertAlmostEqual(feature(steps, "resistance", results), 50)
        self.assertTrue(feature(steps, "acceptance", results))
        # Low slope on a clipped curve must not masquerade as a good bond.
        results["continuity"]["curve_voltage"] = {"Current": Curve((-1e-4, 0, 1e-4), (.1, .1, .1), x_unit="A", y_unit="V")}
        self.assertFalse(feature(steps, "acceptance", results))

    def test_bond_leakage_and_stability_failures(self):
        steps = steps_for("General / Bipolar Leakage")
        results = {"negative": {"statistics": {"mean_current_a": -1e-9}},
                   "positive": {"statistics": {"mean_current_a": 1e-9}}}
        self.assertTrue(feature(steps, "acceptance", results))
        results["positive"]["statistics"]["mean_current_a"] = 1e-6
        self.assertFalse(feature(steps, "acceptance", results))
        steps = steps_for("General / Current Stability")
        results = {"stability": {"curve": {"Current": Curve((0, 1, 3), (2e-6, 2e-6, 2e-6))}}}
        self.assertEqual(feature(steps, "variation", results), 0)
        self.assertEqual(feature(steps, "drift", results), 0)
        self.assertTrue(feature(steps, "acceptance", results))

    def test_legacy_device_names_are_removed(self):
        for name in ("FET Screen + Characterize", "Photo Transient", "Photo Pulse Dynamics"):
            with self.assertRaises(KeyError):
                cards_from_template(name)


if __name__ == "__main__":
    unittest.main()
