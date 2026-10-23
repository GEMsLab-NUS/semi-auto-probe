from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from .auto_test import AutoTestFlowCard, create_autotest_flow_card, measurement_flow_steps_from_cards
from .device_templates import device_templates
from .virtual_mask import DEFAULT_MASK_PATH


WORKFLOW_FORMAT = "semi_auto_probe.measurement_workflow"
DEFAULT_USERS = ("Xinchuan", "Leo", "Micheal")
TEMT6000_MAPPING_TEMPLATE = "PD / TEMT6000 / Fixed Bias and Constant Light"
TEMT6000_PULSED_MAPPING_TEMPLATE = "PD / TEMT6000 / Power and Width Response"
VIRTUAL_MASK_MAPPING_TEMPLATE = "Spatial / Virtual Mask / NUS Logo"
KEITHLEY_IT_CONSTANT_TEMPLATE = "Keithley / I-V-t / Constant I-t"
KEITHLEY_IT_WAVEFORM_TEMPLATE = "Keithley / I-V-t / Waveform I-t"


def default_workflow_root() -> Path:
    return Path.cwd() / "workflow_library"


def default_user_folder(root: Path | None = None) -> Path:
    return (root or default_workflow_root()) / DEFAULT_USERS[0]


def ensure_workflow_library(root: Path | None = None) -> Path:
    library = root or default_workflow_root()
    library.mkdir(parents=True, exist_ok=True)
    for username in DEFAULT_USERS:
        (library / username).mkdir(exist_ok=True)
    return library


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip()).strip("._")
    return cleaned or "workflow"


def workflow_payload(cards: list[AutoTestFlowCard], *, name: str = "Workflow") -> dict[str, Any]:
    for card in cards:
        _require_current_card(card.type_id, card.params)
    return {
        "format": WORKFLOW_FORMAT,
        "version": 2,
        "name": name,
        "nodes": [{"type_id": step.type_id, "params": dict(step.params)} for step in measurement_flow_steps_from_cards(cards)],
    }


def _require_current_card(type_id, params):
    if type_id == "mapping_heatmap":
        raise ValueError("Use a Plot Panel in the current workflow format.")
    if type_id == "math_formula" and "operator" in params:
        raise ValueError("Use a custom expression or a dedicated arithmetic card.")


def cards_from_workflow_payload(payload: Any) -> list[AutoTestFlowCard]:
    if (not isinstance(payload, dict) or payload.get("format") != WORKFLOW_FORMAT
            or payload.get("version") != 2):
        raise ValueError("Only workflow format version 2 is supported. Load a current template to create a new workflow.")
    items = payload.get("nodes")
    if not isinstance(items, list):
        raise ValueError("Workflow JSON must contain a nodes list.")
    cards: list[AutoTestFlowCard] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("Workflow nodes must be objects.")
        params = {str(key): str(value) for key, value in item.get("params", {}).items()}
        _require_current_card(item["type_id"], params)
        card_id = params.pop("_node_id", f"flow_{index + 1}")
        card = create_autotest_flow_card(str(item["type_id"]), card_id, saved_params=params)
        card.params.update(params)
        card.x = float(card.params.pop("_x", index * 280))
        card.y = float(card.params.pop("_y", 0))
        cards.append(card)
    return cards


def save_workflow(path: Path, cards: list[AutoTestFlowCard], *, name: str | None = None) -> Path:
    path = path.with_suffix(".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workflow_payload(cards, name=name or path.stem), indent=2) + "\n", encoding="utf-8")
    return path


def overwrite_workflow(path: Path, cards: list[AutoTestFlowCard]) -> Path:
    """Replace an existing user workflow while keeping its name and file path."""
    if not path.is_file():
        raise FileNotFoundError(path)
    existing = json.loads(path.read_text(encoding="utf-8"))
    name = existing.get("name") if isinstance(existing, dict) else None
    payload = workflow_payload(cards, name=name if isinstance(name, str) and name else path.stem)
    replacement = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        replacement.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        replacement.replace(path)
    finally:
        replacement.unlink(missing_ok=True)
    return path


def load_workflow(path: Path) -> list[AutoTestFlowCard]:
    return cards_from_workflow_payload(json.loads(path.read_text(encoding="utf-8")))


def keithley_time_templates() -> dict[str, list[dict[str, Any]]]:
    common = {
        "resource": "GPIB0::18::INSTR",
        "output_terminal": "rear",
        "source_mode": "voltage",
        "source_value": "0.1",
        "duration_s": "10",
        "sample_interval_s": "0.1",
        "voltage_limit_v": "20",
        "current_limit_a": "1e-3",
        "source_range": "auto",
        "measure_range": "auto",
        "nplc": "1",
        "autozero": "true",
        "output_off_after": "true",
        "_outputs": "live,curve,statistics.steady_current_a,statistics.peak_current_a",
        "_next": "end",
    }
    plot = {
        "type_id": "plot_panel",
        "id": "plots",
        "x": 340,
        "y": 420,
        "params": {
            "layout": "1x2",
            "_input_slot1_source": "measure",
            "_input_slot1_output": "live",
            "_input_slot1_type": "trace",
            "slot1_title": "I-t live current (A)",
            "_input_slot2_source": "measure",
            "_input_slot2_output": "curve",
            "_input_slot2_type": "curve",
            "slot2_title": "I-t current curve (A)",
        },
    }
    return {
        KEITHLEY_IT_CONSTANT_TEMPLATE: [
            {"type_id": "workflow_start", "id": "start", "x": 0, "y": 0,
             "params": {"_workflow_version": "2", "_next": "measure"}},
            {"type_id": "it", "id": "measure", "x": 340, "y": 0,
             "params": {**common, "drive_mode": "constant"}},
            {"type_id": "workflow_end", "id": "end", "x": 680, "y": 0, "params": {}},
            plot,
        ],
        KEITHLEY_IT_WAVEFORM_TEMPLATE: [
            {"type_id": "workflow_start", "id": "start", "x": 0, "y": 0,
             "params": {"_workflow_version": "2", "_next": "measure"}},
            {"type_id": "waveform", "id": "drive_profile", "x": 0, "y": 240,
             "params": {"profile_points": "0,0;1,0.1;5,0.1;6,0;10,0"}},
            {"type_id": "it", "id": "measure", "x": 340, "y": 0,
             "params": {**common, "drive_mode": "profile",
                        "profile_points": "0,0;1,0.1;5,0.1;6,0;10,0",
                        "_input_profile_source": "drive_profile",
                        "_input_profile_output": "signal",
                        "_input_profile_type": "waveform"}},
            {"type_id": "workflow_end", "id": "end", "x": 680, "y": 0, "params": {}},
            plot,
        ],
    }


def builtin_templates() -> dict[str, list[dict[str, Any]]]:
    return {**device_templates(), **keithley_time_templates(), **{'General / Examples / Current and Heatmap': [{'type_id': 'workflow_start',
                                               'id': 'start',
                                               'params': {'_next': 'read', '_workflow_version': '2'}},
                                              {'type_id': 'constant_current',
                                               'id': 'read',
                                               'x': 250,
                                               'params': {'_next': 'end',
                                                          '_outputs': 'live,statistics.mean_current_a'}},
                                              {'type_id': 'workflow_end', 'id': 'end', 'x': 530, 'params': {}},
                                              {'type_id': 'plot_panel',
                                               'id': 'plots',
                                               'x': 550,
                                               'y': 200,
                                               'params': {'layout': '1x2',
                                                          '_input_slot1_source': 'read',
                                                          '_input_slot1_output': 'live',
                                                          '_input_slot1_type': 'trace',
                                                          'slot1_title': 'Current vs time',
                                                          '_input_slot2_source': 'read',
                                                          '_input_slot2_output': 'statistics.mean_current_a',
                                                          '_input_slot2_type': 'number',
                                                          'slot2_title': 'Mean current'}}],
 'General / Examples / Curve Feature Branch': [{'type_id': 'workflow_start',
                                                'id': 'start',
                                                'params': {'_next': 'read', '_workflow_version': '2'}},
                                               {'type_id': 'iv',
                                                'id': 'read',
                                                'x': 250,
                                                'params': {'_next': 'branch', '_outputs': 'live,curve'}},
                                               {'type_id': 'math_formula',
                                                'id': 'feature',
                                                'x': 500,
                                                'y': 200,
                                                'params': {'variables': 'signal:curve',
                                                           'expression': 'slope(signal)',
                                                           '_input_signal_source': 'read',
                                                           '_input_signal_output': 'curve',
                                                           '_input_signal_type': 'curve'}},
                                               {'type_id': 'condition_metric',
                                                'id': 'branch',
                                                'x': 520,
                                                'params': {'_input_value_source': 'feature',
                                                           '_input_value_output': 'result',
                                                           '_input_value_type': 'number',
                                                           'operator': '>',
                                                           'threshold': '0',
                                                           '_true': 'accepted',
                                                           '_false': 'end'}},
                                               {'type_id': 'wait',
                                                'id': 'accepted',
                                                'x': 800,
                                                'params': {'duration_s': '0',
                                                           'reason': 'Positive conductance',
                                                           '_next': 'end'}},
                                               {'type_id': 'workflow_end', 'id': 'end', 'x': 1050, 'params': {}},
                                               {'type_id': 'plot_panel',
                                                'id': 'plots',
                                                'x': 800,
                                                'y': 280,
                                                'params': {'layout': '1x2',
                                                           '_input_slot1_source': 'read',
                                                           '_input_slot1_output': 'live',
                                                           '_input_slot1_type': 'trace',
                                                           'slot1_title': 'I-V',
                                                           '_input_slot2_source': 'feature',
                                                           '_input_slot2_output': 'result',
                                                           '_input_slot2_type': 'number',
                                                           'slot2_title': 'Conductance'}}],
 'Spatial / Virtual Mask / NUS Logo': [{'type_id': 'workflow_start',
                                        'id': 'start',
                                        'params': {'_workflow_version': '2', '_next': 'mask_power_calibration'},
                                        'x': 0,
                                        'y': 0},
                                       {'type_id': 'laser_iv_calibration',
                                        'id': 'mask_power_calibration',
                                        'x': 330,
                                        'y': 0,
                                        'params': {'calibration_voltage_v': '3.3',
                                                   'calibration_current_min_a': '0.001',
                                                   'calibration_current_max_a': '0.025',
                                                   'calibration_current_step_a': '0.001',
                                                   'calibration_settle_ms': '100',
                                                   'readback_timeout_s': '2.0',
                                                   'readback_interval_ms': '100',
                                                   'ovp_v': '3.3',
                                                   'ocp_a': '0.025',
                                                   'preset_index': '1',
                                                   '_next': 'nus_virtual_mask',
                                                   '_outputs': 'calibration'}},
                                       {'type_id': 'virtual_mask',
                                        'id': 'nus_virtual_mask',
                                        'x': 660,
                                        'y': 0,
                                        'params': {'image_path': str(DEFAULT_MASK_PATH),
                                                   'coordinate_frame': 'xy',
                                                   'bindings': '0,31,0,0;63,31,100,0;0,0,0,50;63,0,100,50',
                                                   'black_power_mw': '0',
                                                   'white_power_mw': '5',
                                                   'invert_intensity': 'false',
                                                   'settle_s': '0.05',
                                                   '_input_calibration_source': 'mask_power_calibration',
                                                   '_input_calibration_output': 'calibration',
                                                   '_input_calibration_type': 'calibration',
                                                   '_next': 'mask_current'}},
                                       {'type_id': 'constant_current',
                                        'id': 'mask_current',
                                        'x': 990,
                                        'y': 0,
                                        'params': {'resource_name': 'GPIB0::18::INSTR',
                                                   'output_terminal': 'front',
                                                   'voltage_v': '5.0',
                                                   'current_limit_a': '0.001',
                                                   'measure_range': 'auto',
                                                   'nplc': '0.1',
                                                   'sample_count': '1',
                                                   'settle_s': '0',
                                                   'hold_during_mapping': 'true',
                                                   'verify_bias': 'true',
                                                   '_outputs': 'statistics.mean_current_a',
                                                   '_next': 'end'}},
                                       {'type_id': 'workflow_end', 'id': 'end', 'params': {}, 'x': 1320, 'y': 0},
                                       {'type_id': 'plot_panel',
                                        'id': 'plots',
                                        'params': {'layout': '1x1',
                                                   '_input_slot1_source': 'mask_current',
                                                   '_input_slot1_output': 'statistics.mean_current_a',
                                                   '_input_slot1_type': 'number',
                                                   'slot1_title': 'Measured Current (A)',
                                                   '_input_slot1_unit': 'A'},
                                        'x': 660,
                                        'y': 680}],
 'PD / TEMT6000 / Fixed Bias and Constant Light': [{'type_id': 'workflow_start',
                                                    'id': 'start',
                                                    'params': {'_workflow_version': '2',
                                                               '_next': 'constant_laser'},
                                                    'x': 0,
                                                    'y': 0},
                                                   {'type_id': 'laser_output',
                                                    'id': 'constant_laser',
                                                    'x': 330,
                                                    'y': 0,
                                                    'params': {'peak_voltage_v': '3.0',
                                                               'peak_current_a': '0.010',
                                                               'ovp_v': '3.3',
                                                               'ocp_a': '0.025',
                                                               'output_state': 'on',
                                                               'preset_index': '1',
                                                               'mapping_once': 'true',
                                                               'settle_s': '2',
                                                               '_next': 'temt6000_current'}},
                                                   {'type_id': 'constant_current',
                                                    'id': 'temt6000_current',
                                                    'x': 660,
                                                    'y': 0,
                                                    'params': {'resource_name': 'GPIB0::18::INSTR',
                                                               'output_terminal': 'front',
                                                               'voltage_v': '5.0',
                                                               'current_limit_a': '0.001',
                                                               'measure_range': 'auto',
                                                               'nplc': '0.1',
                                                               'sample_count': '1',
                                                               'settle_s': '0',
                                                               'hold_during_mapping': 'true',
                                                               'verify_bias': 'true',
                                                               'optical_model': 'TEMT6000',
                                                               'wavelength_nm': '650',
                                                               'power_correction': '1',
                                                               'dark_current_a': '0',
                                                               '_outputs': 'statistics.mean_current_a,statistics.std_current_a,statistics.estimated_irradiance_w_m2',
                                                               '_next': 'end'}},
                                                   {'type_id': 'workflow_end',
                                                    'id': 'end',
                                                    'params': {},
                                                    'x': 990,
                                                    'y': 0},
                                                   {'type_id': 'plot_panel',
                                                    'id': 'plots',
                                                    'params': {'layout': '1x2',
                                                               '_input_slot1_source': 'temt6000_current',
                                                               '_input_slot1_output': 'live',
                                                               '_input_slot1_type': 'trace',
                                                               'slot1_title': 'Current vs time (A)',
                                                               '_input_slot1_unit': 'A',
                                                               '_input_slot2_source': 'temt6000_current',
                                                               '_input_slot2_output': 'statistics.mean_current_a',
                                                               '_input_slot2_type': 'number',
                                                               'slot2_title': 'Mean current (A)',
                                                               '_input_slot2_unit': 'A'},
                                                    'x': 660,
                                                    'y': 680}],
 'PD / TEMT6000 / Power and Width Response': [{'type_id': 'workflow_start',
                                               'id': 'start',
                                               'params': {'_workflow_version': '2',
                                                          '_next': 'laser_iv_calibration'},
                                               'x': 0,
                                               'y': 0},
                                              {'type_id': 'laser_iv_calibration',
                                               'id': 'laser_iv_calibration',
                                               'x': 330,
                                               'y': 0,
                                               'params': {'calibration_voltage_v': '3.3',
                                                          'calibration_current_min_a': '0.001',
                                                          'calibration_current_max_a': '0.025',
                                                          'calibration_current_step_a': '0.001',
                                                          'calibration_settle_ms': '100',
                                                          'readback_timeout_s': '2.0',
                                                          'readback_interval_ms': '100',
                                                          'ovp_v': '3.3',
                                                          'ocp_a': '0.028',
                                                          'preset_index': '1',
                                                          '_next': 'power_gradient_response'}},
                                              {'type_id': 'photo_power_steps',
                                               'id': 'power_gradient_response',
                                               'x': 660,
                                               'y': 0,
                                               'params': {'resource_name': 'GPIB0::18::INSTR',
                                                          'output_terminal': 'front',
                                                          'bias_v': '5.0',
                                                          'current_limit_a': '0.001',
                                                          'measure_range': 'auto',
                                                          'nplc': '0.01',
                                                          'sample_interval_ms': '10',
                                                          'power_min_mw': 'auto',
                                                          'power_max_mw': 'auto',
                                                          'power_levels': '25',
                                                          'power_width_ms': '200',
                                                          'pulse_gap_ms': '1000',
                                                          'program_lead_ms': '100',
                                                          'hold_during_mapping': 'true',
                                                          'verify_bias': 'true',
                                                          '_input_calibration_source': 'laser_iv_calibration',
                                                          '_input_calibration_output': 'calibration',
                                                          '_input_calibration_type': 'calibration',
                                                          '_outputs': 'statistics.max_pulse_response_a,statistics.mean_pulse_response_a,statistics.power_sensitivity_a_per_mw',
                                                          '_next': 'width_gradient_response'}},
                                              {'type_id': 'photo_width_steps',
                                               'id': 'width_gradient_response',
                                               'x': 990,
                                               'y': 0,
                                               'params': {'resource_name': 'GPIB0::18::INSTR',
                                                          'output_terminal': 'front',
                                                          'bias_v': '5.0',
                                                          'current_limit_a': '0.001',
                                                          'measure_range': 'auto',
                                                          'nplc': '0.01',
                                                          'sample_interval_ms': '10',
                                                          'width_power_mw': 'auto',
                                                          'width_min_ms': '100',
                                                          'width_max_ms': '500',
                                                          'width_step_ms': '100',
                                                          'pulse_gap_ms': '1000',
                                                          'program_lead_ms': '100',
                                                          'hold_during_mapping': 'true',
                                                          'verify_bias': 'true',
                                                          '_input_calibration_source': 'laser_iv_calibration',
                                                          '_input_calibration_output': 'calibration',
                                                          '_input_calibration_type': 'calibration',
                                                          '_outputs': 'statistics.max_pulse_response_a,statistics.mean_pulse_response_a,statistics.width_sensitivity_a_per_s',
                                                          '_next': 'end'}},
                                              {'type_id': 'workflow_end',
                                               'id': 'end',
                                               'params': {},
                                               'x': 1320,
                                               'y': 0},
                                              {'type_id': 'plot_panel',
                                               'id': 'plots',
                                               'params': {'layout': '2x2',
                                                          '_input_slot1_source': 'power_gradient_response',
                                                          '_input_slot1_output': 'live',
                                                          '_input_slot1_type': 'trace',
                                                          'slot1_title': 'Power-series I-t (A)',
                                                          '_input_slot1_unit': 'A',
                                                          '_input_slot2_source': 'width_gradient_response',
                                                          '_input_slot2_output': 'live',
                                                          '_input_slot2_type': 'trace',
                                                          'slot2_title': 'Width-series I-t (A)',
                                                          '_input_slot2_unit': 'A',
                                                          '_input_slot3_source': 'power_gradient_response',
                                                          '_input_slot3_output': 'statistics.max_pulse_response_a',
                                                          '_input_slot3_type': 'number',
                                                          'slot3_title': 'Power response (A)',
                                                          '_input_slot3_unit': 'A',
                                                          '_input_slot4_source': 'width_gradient_response',
                                                          '_input_slot4_output': 'statistics.max_pulse_response_a',
                                                          '_input_slot4_type': 'number',
                                                          'slot4_title': 'Width response (A)',
                                                          '_input_slot4_unit': 'A'},
                                               'x': 660,
                                               'y': 680}]}}



def template_tree() -> dict[str, Any]:
    """Category nodes contain children; leaves contain canonical template names."""
    root: dict[str, Any] = {}
    for name in builtin_templates():
        parts = name.split(" / ")
        parent = root
        for category in parts[:-1]:
            parent = parent.setdefault(category, {})
        parent[parts[-1]] = name
    return root


def cards_from_template(name: str) -> list[AutoTestFlowCard]:
    cards: list[AutoTestFlowCard] = []
    for item in builtin_templates()[name]:
        card = create_autotest_flow_card(str(item["type_id"]), str(item["id"]), saved_params={str(k): str(v) for k, v in item.get("params", {}).items()})
        card.x, card.y = float(item.get("x", 0)), float(item.get("y", 0))
        card.params.update({str(key): str(value) for key, value in item.get("params", {}).items()})
        cards.append(card)
    return cards
