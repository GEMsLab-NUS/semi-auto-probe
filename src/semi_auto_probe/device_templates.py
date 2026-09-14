"""Workflow v2 recipes built exclusively from executable desktop cards.

These are examples, not device ratings. All limits and acceptance thresholds
remain ordinary editable card parameters. No instrument is contacted here.
"""
from __future__ import annotations

import json

from .dp100 import DEFAULT_LASER_PARAMS
from .waveform import preset_waveform, serialize_waveform_points


def node(kind, identity, **params):
    return {"type_id": kind, "id": identity, "params": params}


def link(name, source, output="result", dtype="number"):
    return {f"_input_{name}_source": source, f"_input_{name}_output": output,
            f"_input_{name}_type": dtype}


def formula(identity, expression, bindings, *, constants=None, unit="", boolean=False):
    constants = constants or {}
    params = {"expression": expression, "variables": ",".join(
        [f"{name}:{binding[2]}" for name, binding in bindings.items()]
        + [f"{name}:number" for name in constants]),
        "constants": json.dumps(constants), "output_unit": unit,
        "output_type": "bool" if boolean else "number", "on_invalid": "invalid"}
    for name, binding in bindings.items():
        params.update(link(name, *binding))
    return node("math_formula", identity, **params)


def panel(slots):
    layout = {1: "1x1", 2: "1x2", 4: "2x2", 6: "2x3"}[len(slots)]
    params = {"layout": layout}
    for i, (source, output, title, unit) in enumerate(slots, 1):
        params.update(link(f"slot{i}", source, output, "trace" if output.startswith("live") else "number"))
        params.update({f"slot{i}_title": title, f"_input_slot{i}_unit": unit})
    return node("plot_panel", "plots", **params)


def recipe(control, analysis, slots):
    """Wire the control spine; analysis cards remain lazy data dependencies."""
    cards = [node("workflow_start", "start", _workflow_version="2"), *control,
             node("workflow_end", "end")]
    for i, card in enumerate(cards[:-1]):
        if not card["type_id"].startswith("condition_"):
            card["params"].setdefault("_next", cards[i + 1]["id"])
    for i, card in enumerate(cards):
        card.update(x=i * 330, y=0)
    for i, card in enumerate(analysis):
        card.update(x=330 + i * 330, y=330)
    plots = panel(slots)
    plots.update(x=660, y=680)
    return cards + analysis + [plots]


def number(source, output="result"):
    return source, output, "number"


def curve(source):
    return source, "curve", "curve"


def light(identity, enabled):
    return node("laser_output", identity, **DEFAULT_LASER_PARAMS,
                output_state="on" if enabled else "off", settle_s="0.2")


def fixed(identity, bias="0.1", samples="20"):
    return node("constant_current", identity, voltage_v=bias, sample_count=samples,
                current_limit_a="1e-4", nplc="1", hold_during_mapping="false",
                verify_bias="true", optical_model="none")


def verdict(source="acceptance", passed="pass", failed="fail"):
    return [node("condition_boolean", "decision", **link("predicate", source, "result", "bool"),
                 _true=passed, _false=failed),
            node("wait", passed, duration_s="0", reason="PASS: configured criteria met", _next="end"),
            node("wait", failed, duration_s="0", reason="FAIL or missing/invalid result", _next="end")]


def fet_recipe(instrument="b1500", *, transfer_only=False, output_only=False, double=False):
    common = {"sweep_points": "41", "pre_settle_s": "0.2", "post_sweep_pause_s": "0.2"}
    if instrument == "b1500":
        common.update(drain_current_compliance_a="1e-4", gate_current_compliance_a="1e-8",
                      staircase_nplc="1", measure_gate_leak="true")
    else:
        common.update(drain_current_limit_a="1e-4", gate_current_limit_a="1e-6", drain_nplc="1")
    transfer = node(instrument + "_transfer", "transfer", **common,
                    sweep_start_v="0", sweep_end_v="2", bias_values_v="0.1,0.5",
                    scan_type="double" if double else "single" if instrument == "b1500" else "forward")
    output = node(instrument + "_output", "output", **common,
                  sweep_start_v="0", sweep_end_v="1", bias_values_v="0,1,2")
    if output_only:
        return recipe([output], [], [
            ("output", "live", "Output family: Id vs Vd (A)", "A"),
            ("output", "statistics.output_resistance_ohm", "Full-sweep fitted resistance (ohm)", "Ω")])
    if transfer_only:
        return recipe([transfer], [], [
            ("transfer", "live", "Transfer: Id vs Vg (A)", "A"),
            ("transfer", "vth", "Threshold voltage (V)", "V"),
            ("transfer", "on_off_ratio", "On/off ratio", "1"),
            ("transfer", "statistics.subthreshold_swing_v_dec", "Subthreshold swing (V/dec)", "V/dec")])
    acceptance = formula("acceptance", "ratio >= minimum_ratio", {"ratio": number("transfer", "on_off_ratio")},
                         constants={"minimum_ratio": 100}, boolean=True)
    branch = node("condition_boolean", "decision", **link("predicate", "acceptance", "result", "bool"),
                  _true="output", _false="fail")
    output["params"]["_next"] = "pass"
    controls = [transfer, branch, output,
                node("wait", "pass", duration_s="0", reason="PASS: transfer screen", _next="end"),
                node("wait", "fail", duration_s="0", reason="FAIL: transfer screen; output skipped", _next="end")]
    numeric = formula("acceptance_value", "1 if accepted else 0", {"accepted": ("acceptance", "result", "bool")}, unit="1")
    return recipe(controls, [acceptance, numeric], [
        ("transfer", "live", "Transfer: Id vs Vg (A)", "A"),
        ("output", "live", "Output family: Id vs Vd (A)", "A"),
        ("transfer", "vth", "Threshold voltage (V)", "V"),
        ("transfer", "on_off_ratio", "On/off ratio", "1"),
        ("output", "statistics.output_resistance_ohm", "Full-sweep fitted resistance (ohm)", "Ω"),
        ("acceptance_value", "result", "Transfer screen: pass=1, fail=0", "1")]
    )


def photo_fixed_recipe():
    analysis = [
        formula("photocurrent", "light - dark", {"light": number("bright", "statistics.mean_current_a"),
                                                 "dark": number("dark", "statistics.mean_current_a")}, unit="A"),
        formula("ratio", "abs(light) / abs(dark)", {"light": number("bright", "statistics.mean_current_a"),
                                                    "dark": number("dark", "statistics.mean_current_a")}, unit="1"),
        formula("acceptance", "abs(signal) >= minimum_current", {"signal": number("photocurrent")},
                constants={"minimum_current": 1e-8}, boolean=True),
    ]
    return recipe([light("dark_off", False), fixed("dark"), light("illuminate", True), fixed("bright"),
                   light("light_off", False), *verdict()], analysis, [
        ("dark", "live", "Dark current vs time (A)", "A"),
        ("bright", "live", "Illuminated current vs time (A)", "A"),
        ("dark", "statistics.mean_current_a", "Mean dark current (A)", "A"),
        ("bright", "statistics.mean_current_a", "Mean illuminated current (A)", "A"),
        ("photocurrent", "result", "Signed photocurrent: light - dark (A)", "A"),
        ("ratio", "result", "Absolute light/dark current ratio", "1")])


def photo_iv_recipe():
    def sweep(identity):
        return node("iv", identity, start="-0.2", stop="0.2", step="0.01", current_limit_a="1e-4",
                    output_off_after="true")
    analysis = [formula("dark_g", "slope(signal)", {"signal": curve("dark_iv")}, unit="S"),
                formula("light_g", "slope(signal)", {"signal": curve("light_iv")}, unit="S")]
    return recipe([light("dark_off", False), sweep("dark_iv"), light("illuminate", True),
                   sweep("light_iv"), light("light_off", False)], analysis, [
        ("dark_iv", "live", "Dark I-V (A)", "A"),
        ("light_iv", "live", "Illuminated I-V (A)", "A"),
        ("dark_g", "result", "Dark fitted conductance (S)", "S"),
        ("light_g", "result", "Illuminated fitted conductance (S)", "S")])


def photo_responsivity_recipe():
    cards = photo_fixed_recipe()
    responsivity = formula("responsivity", "abs(signal) / incident_power_w if incident_power_w > 0 else 1/0", {"signal": number("photocurrent")},
                           constants={"incident_power_w": 0}, unit="A/W")
    eqe = formula("eqe", "responsivity * 1239.841984 / wavelength_nm * 100 if wavelength_nm > 0 else 1/0", {"responsivity": number("responsivity")},
                  constants={"wavelength_nm": 650}, unit="%")
    for index, card in enumerate((responsivity, eqe), 4):
        card.update(x=330 * index, y=330)
    plots = panel([
        ("dark", "live", "Dark current vs time (A)", "A"),
        ("bright", "live", "Illuminated current vs time (A)", "A"),
        ("photocurrent", "result", "Signed photocurrent (A)", "A"),
        ("ratio", "result", "Absolute light/dark current ratio", "1"),
        ("responsivity", "result", "Responsivity: set incident_power_w (A/W)", "A/W"),
        ("eqe", "result", "EQE: set wavelength_nm (%)", "%")])
    plots.update(x=660, y=680)
    return cards[:-1] + [responsivity, eqe, plots]


def photo_transient_recipe():
    pulse = node("laser_pulse", "pulse", **DEFAULT_LASER_PARAMS, period_ms="1000", count="5",
                 waveform_points=serialize_waveform_points(preset_waveform("Pulse", duration_ms=1000,
                     peak_voltage_v=float(DEFAULT_LASER_PARAMS["peak_voltage_v"]),
                     peak_current_a=float(DEFAULT_LASER_PARAMS["peak_current_a"]))))
    transient = node("photo_transient", "response", duration_s="5.2", sample_interval_ms="10",
                     bias_v="0.1", current_limit_a="1e-4", hold_during_mapping="false", _light_source="pulse")
    analysis = [pulse, formula("charge", "integral(signal)", {"signal": curve("response")}, unit="C"),
                formula("rms_noise", "std(signal)", {"signal": curve("response")}, unit="A")]
    return recipe([transient], analysis, [
        ("response", "live", "Pulsed current vs time (A)", "A"),
        ("response", "statistics.response_amplitude_a", "Whole-trace max-min amplitude (A)", "A"),
        ("charge", "result", "Total signed charge, including baseline (C)", "C"),
        ("rms_noise", "result", "Whole-trace standard deviation (A)", "A")])


def photo_gradient_recipe():
    calibration = node("laser_iv_calibration", "calibration")
    power = node("photo_power_steps", "power", bias_v="0.1", current_limit_a="1e-4",
                 power_levels="5", hold_during_mapping="false", **link("calibration", "calibration", "calibration", "calibration"))
    width = node("photo_width_steps", "width", bias_v="0.1", current_limit_a="1e-4",
                 hold_during_mapping="false", **link("calibration", "calibration", "calibration", "calibration"))
    return recipe([calibration, power, width, light("light_off", False)], [], [
        ("power", "live", "Drive-power series I-t (A)", "A"),
        ("width", "live", "Pulse-width series I-t (A)", "A"),
        ("power", "statistics.max_pulse_response_a", "Maximum power-series response (A)", "A"),
        ("width", "statistics.max_pulse_response_a", "Maximum width-series response (A)", "A"),
        ("power", "statistics.power_sensitivity_a_per_mw", "Electrical drive-power sensitivity (A/mW)", "A/mW"),
        ("width", "statistics.width_sensitivity_a_per_s", "Pulse-width sensitivity (A/s)", "A/s")])


def continuity_recipe():
    # Source current for a low-resistance link; fit V(I), not I(V).
    sweep = node("iv", "continuity", sweep_mode="current", start="-1e-4", stop="1e-4", step="1e-5",
                 voltage_limit_v="0.1", output_off_after="true")
    resistance = formula("resistance", "abs(slope(voltage))", {"voltage": ("continuity", "curve_voltage", "curve")}, unit="Ω")
    acceptance = formula("acceptance", "r <= maximum_resistance and max(abs(min(voltage)), abs(max(voltage))) < voltage_guard", {
        "r": number("resistance"), "voltage": ("continuity", "curve_voltage", "curve")},
        constants={"maximum_resistance": 100, "voltage_guard": 0.095}, boolean=True)
    return recipe([sweep, *verdict()], [resistance, acceptance], [
        ("continuity", "live_voltage", "V-I: measured voltage (V)", "V"),
        ("resistance", "result", "Two-terminal link resistance (ohm)", "Ω")])


def leakage_recipe():
    acceptance = formula("acceptance", "max(abs(low), abs(high)) <= maximum_leakage", {
        "low": number("negative", "statistics.mean_current_a"),
        "high": number("positive", "statistics.mean_current_a")},
        constants={"maximum_leakage": 1e-8}, boolean=True)
    return recipe([fixed("negative", "-0.1"), fixed("positive", "0.1"), *verdict()], [acceptance], [
        ("negative", "live", "Leakage at -0.1 V (A)", "A"),
        ("positive", "live", "Leakage at +0.1 V (A)", "A"),
        ("negative", "statistics.mean_current_a", "Negative-bias mean leakage (A)", "A"),
        ("positive", "statistics.mean_current_a", "Positive-bias mean leakage (A)", "A")])


def stability_recipe():
    analysis = [formula("variation", "std(signal) / abs(mean(signal))", {"signal": curve("stability")}, unit="1"),
                formula("drift", "slope(signal)", {"signal": curve("stability")}, unit="A/s"),
                formula("acceptance", "variation <= maximum_variation", {"variation": number("variation")},
                        constants={"maximum_variation": 0.05}, boolean=True)]
    return recipe([fixed("stability", samples="200"), *verdict()], analysis, [
        ("stability", "live", "Current stability (A)", "A"),
        ("stability", "statistics.mean_current_a", "Mean current (A)", "A"),
        ("variation", "result", "Relative current variation", "1"),
        ("drift", "result", "Fitted current drift (A/s)", "A/s")])


def mr_sweep_recipe():
    sweep = node("iv", "switching", start="0", stop="1", step="0.02", scan_type="dualpolar",
                 current_limit_a="1e-4", output_off_after="true")
    return recipe([sweep], [], [
        ("switching", "live", "Bipolar switching I-V (A)", "A"),
        ("switching", "statistics.max_abs_current_a", "Peak absolute current (A)", "A")])


def mr_read_write_recipe():
    controls = [fixed("read_initial"), fixed("set", "1", "1"), fixed("read_set"),
                fixed("reset", "-1", "1"), fixed("read_reset")]
    analysis = []
    for identity in ("read_initial", "read_set", "read_reset"):
        analysis.append(formula(identity + "_r", "abs(mean(voltage) / mean(current))", {
            "voltage": (identity, "curve_voltage", "curve"), "current": curve(identity)}, unit="Ω"))
    analysis += [formula("window", "reset_r / set_r", {"reset_r": number("read_reset_r"),
                                                        "set_r": number("read_set_r")}, unit="1"),
                 formula("acceptance", "window >= minimum_window", {"window": number("window")},
                         constants={"minimum_window": 2}, boolean=True)]
    return recipe([*controls, *verdict()], analysis, [
        ("read_set", "live", "Read current after SET (A)", "A"),
        ("read_reset", "live", "Read current after RESET (A)", "A"),
        ("read_initial_r", "result", "Initial read resistance (ohm)", "Ω"),
        ("read_set_r", "result", "Read resistance after SET (ohm)", "Ω"),
        ("read_reset_r", "result", "Read resistance after RESET (ohm)", "Ω"),
        ("window", "result", "Resistance ratio: RESET / SET", "1")])


def cap_insulation_recipe():
    read = fixed("leakage", "0.1", "50")
    read["params"].update(settle_s="1", current_limit_a="1e-5")
    resistance = formula("insulation", "abs(mean(voltage) / mean(current))", {
        "voltage": ("leakage", "curve_voltage", "curve"), "current": curve("leakage")}, unit="Ω")
    acceptance = formula("acceptance", "abs(leakage) <= maximum_leakage", {
        "leakage": number("leakage", "statistics.mean_current_a")},
        constants={"maximum_leakage": 1e-8}, boolean=True)
    return recipe([read, *verdict()], [resistance, acceptance], [
        ("leakage", "live", "Current after bias settling (A)", "A"),
        ("leakage", "statistics.mean_current_a", "Mean DC leakage (A)", "A"),
        ("insulation", "result", "Apparent DC insulation resistance (ohm)", "Ω"),
        ("leakage", "statistics.std_current_a", "Leakage standard deviation (A)", "A")])


def device_templates():
    templates = {
        "FET / B1500 / Transfer": fet_recipe(transfer_only=True),
        "FET / B1500 / Double Sweep": fet_recipe(transfer_only=True, double=True),
        "FET / B1500 / Output Family": fet_recipe(output_only=True),
        "FET / B1500 / Screen and Characterize": fet_recipe(),
        "FET / HP6614C / Screen and Characterize": fet_recipe("hp6614c"),
        "PD / Dark Light Current": photo_fixed_recipe(),
        "PD / Dark Light IV": photo_iv_recipe(),
        "PD / Responsivity and EQE": photo_responsivity_recipe(),
        "PD / Pulse Response": photo_transient_recipe(),
        "PD / Calibrated Power and Width": photo_gradient_recipe(),
        "MR / Bipolar Switching IV": mr_sweep_recipe(),
        "MR / Read SET Read RESET Read": mr_read_write_recipe(),
        "Cap / DC Leakage and Insulation": cap_insulation_recipe(),
        "Cap / Bipolar Leakage": leakage_recipe(),
        "General / Continuity and Resistance": continuity_recipe(),
        "General / Bipolar Leakage": leakage_recipe(),
        "General / Current Stability": stability_recipe(),
    }
    return templates
