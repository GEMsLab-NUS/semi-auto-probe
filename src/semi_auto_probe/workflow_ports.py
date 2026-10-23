"""Shared workflow ports. UI and executor use the same declarations."""
from __future__ import annotations
import re

PLOT_LAYOUTS = {"1x1": (1, 1), "1x2": (1, 2), "2x2": (2, 2), "2x3": (2, 3)}

LIGHT_OUTPUT_TYPES = frozenset({"laser_output", "laser_pulse", "light_control", "light_pulse"})
LIGHT_INPUT_TYPES = frozenset({"photo_iv", "photo_transient"})
MATH_TYPES = frozenset({'math_formula', 'math_add', 'math_subtract', 'math_multiply', 'math_divide'})
DATA_ONLY_TYPES = MATH_TYPES | {'mapping_heatmap', 'plot_panel', 'boolean_compare', 'boolean_compare_pair', 'boolean_logic', 'waveform', 'drive_waveform'}
CONTROL_CONTAINERS: dict[str, tuple[str, ...]] = {
    "hp6614c_bias_loop": ("iv",),
}
DATA_INPUTS: dict[str, tuple[tuple[str, str], ...]] = {
    "it": (("profile", "waveform"),),
    "laser_pulse": (("profile", "waveform"),),
    "virtual_mask": (("calibration", "calibration"),),
    "photo_power_steps": (("calibration", "calibration"),),
    "photo_width_steps": (("calibration", "calibration"),),
    "mapping_heatmap": (("primary", "number"), ("secondary", "number")),
    "math_formula": (("a", "number"), ("b", "number")),
    "boolean_compare": (("value", "number"),),
    "boolean_logic": (("a", "bool"), ("b", "bool")),
    "condition_boolean": (("predicate", "bool"),),
    "condition_metric": (("value", "number"),),
    "condition_device_state": (("resistance", "number"), ("current", "number")),
    "condition_compliance": (("reached", "bool"),),
}
DATA_OUTPUTS: dict[str, tuple[tuple[str, str], ...]] = {
    "waveform": (("signal", "waveform"),),
    "drive_waveform": (("waveform", "waveform"),),
    "laser_iv_calibration": (("calibration", "calibration"),),
    "math_formula": (("result", "number"),),
    "boolean_compare": (("result", "bool"),),
    "boolean_logic": (("result", "bool"),),
}
RESULT_OUTPUTS: dict[str, tuple[str, ...]] = {
    "virtual_mask": ("target_power_mw", "normalized_intensity"),
    "constant_current": ("statistics.mean_current_a", "statistics.std_current_a", "statistics.estimated_irradiance_w_m2"),
    "iv": ("statistics.resistance_ohm", "statistics.max_abs_current_a", "statistics.compliance_reached"),
    "it": ("statistics.steady_current_a", "statistics.peak_current_a", "statistics.steady_voltage_v", "statistics.peak_voltage_v"),
    "wobb_test": ("statistics.best_current_a", "statistics.contact_resistance_ohm"),
    "hp6614c_transfer": ("vth", "on_off_ratio", "statistics.max_abs_current_a"),
    "hp6614c_output": ("statistics.output_resistance_ohm", "statistics.max_abs_current_a"),
    "hp6614c_bias_loop": ("statistics.output_resistance_ohm", "statistics.max_abs_current_a"),
    "b1500_transfer": ("vth", "on_off_ratio", "statistics.subthreshold_swing_v_dec"),
    "b1500_output": ("statistics.output_resistance_ohm", "statistics.max_abs_current_a"),
    "photo_iv": ("photocurrent_a", "photo_dark_ratio", "responsivity_a_w"),
    "photo_transient": ("statistics.response_amplitude_a", "statistics.peak_current_a", "statistics.baseline_current_a", "statistics.mean_current_a"),
    "photo_power_steps": ("statistics.max_pulse_response_a", "statistics.mean_pulse_response_a", "statistics.power_sensitivity_a_per_mw"),
    "photo_width_steps": ("statistics.max_pulse_response_a", "statistics.mean_pulse_response_a", "statistics.width_sensitivity_a_per_s"),
}

for kind in MATH_TYPES - {'math_formula'}:
    DATA_INPUTS[kind] = (("a", "number"), ("b", "number"))
    DATA_OUTPUTS[kind] = (("result", "number"),)
DATA_INPUTS['boolean_compare_pair'] = (("a", "number"), ("b", "number"))
DATA_OUTPUTS['boolean_compare_pair'] = (("result", "bool"),)
CURVE_NODES = frozenset(RESULT_OUTPUTS) - {'virtual_mask'}
for kind in CURVE_NODES:
    RESULT_OUTPUTS[kind] += ('live', 'curve', 'live_voltage', 'curve_voltage', 'live_resistance', 'curve_resistance')

def variable_ports(params):
    ports = []
    for token in params.get('variables', 'a:number,b:number').split(','):
        name, _, dtype = token.strip().partition(':')
        dtype = dtype or 'number'
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name) or dtype not in {'number', 'bool', 'curve'}:
            raise ValueError('Variables must be name:number, name:bool or name:curve.')
        if name in {n for n, _ in ports}:
            raise ValueError('Variable names must be unique.')
        ports.append((name, dtype))
    if len(ports) > 16:
        raise ValueError('At most 16 formula inputs are supported.')
    return tuple(ports)

def input_ports(node):
    if node.type_id == 'math_formula':
        return variable_ports(node.params)
    if node.type_id == 'plot_panel':
        return tuple((f'slot{i}', 'plot') for i in range(1, 7))
    return DATA_INPUTS.get(node.type_id, ())

def visible_input_ports(node):
    """Keep saved slot declarations stable; expose only slots in the current layout."""
    ports = input_ports(node)
    if node.type_id == 'plot_panel':
        rows, columns = PLOT_LAYOUTS.get(node.params.get('layout', '1x2'), (1, 2))
        return ports[:rows * columns]
    return ports


def output_ports(node):
    if node.type_id == 'math_formula':
        return (('result', node.params.get('output_type', 'number')),)
    return DATA_OUTPUTS.get(node.type_id, ())

def metric_type(name):
    if name.startswith('live'):
        return 'trace'
    if name.startswith('curve'):
        return 'curve'
    return 'bool' if name.rsplit('.', 1)[-1] == 'compliance_reached' else 'number'

def output_type(node, name):
    if node.type_id == 'laser_pulse' and name == 'signal':
        return 'waveform'
    declared = dict(output_ports(node))
    if name in declared:
        return declared[name]
    if name in RESULT_OUTPUTS.get(node.type_id, ()):
        return metric_type(name)
    return ''

def compatible(actual, expected):
    return actual == expected or (expected == 'plot' and actual in {'number', 'trace', 'curve'})

def metric_unit(name):
    leaf = name.rsplit('.', 1)[-1]
    units = {"subthreshold_swing_v_dec": "V/dec", "ss": "V/dec", "responsivity_a_w": "A/W",
             "power_sensitivity_a_per_mw": "A/mW", "width_sensitivity_a_per_s": "A/s",
             "target_power_mw": "mW", "normalized_intensity": "1", "on_off_ratio": "1", "photo_dark_ratio": "1"}
    if leaf in units:
        return units[leaf]
    if name.endswith('_ohm') or 'resistance' in name:
        return 'Ω'
    if name.endswith('_a') or name in {'live', 'curve'}:
        return 'A'
    if name.endswith('_v') or 'voltage' in name or name == 'vth':
        return 'V'
    if name.endswith('_w_m2'):
        return 'W/m²'
    return ''
