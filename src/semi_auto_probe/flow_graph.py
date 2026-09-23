from __future__ import annotations

import operator
import math
import json
from .workflow_ports import LIGHT_OUTPUT_TYPES, LIGHT_INPUT_TYPES
from .workflow_ports import DATA_ONLY_TYPES, MATH_TYPES, input_ports, output_type, compatible
from .workflow_formula import InvalidValue, Curve, evaluate_expression, parse_expression
from collections.abc import Mapping, Sequence
from typing import Any

from .auto_test import AutoTestFlowStep, is_condition_flow_type


GRAPH_KEYS = frozenset({"_node_id", "_x", "_y", "_next", "_true", "_false", "_light_source", "_outputs", "_result_source", "_result_metric"})
_RESOLVING = object()


def is_graph_workflow(steps):
    return any(step.type_id == "plot_panel" or step.params.get("_workflow_version") == "2"
               or any(step.params.get(key) for key in ("_next", "_true", "_false", "_light_source"))
               for step in steps)


def step_node_id(step: AutoTestFlowStep, index: int) -> str:
    return str(step.params.get("_node_id") or f"step_{index + 1}")


def validate_flow_graph(steps: Sequence[AutoTestFlowStep]) -> tuple[str, ...]:
    if not steps:
        return ()
    ids = [step_node_id(step, index) for index, step in enumerate(steps)]
    errors: list[str] = []
    if len(ids) != len(set(ids)):
        errors.append("Node IDs must be unique.")
    for step in steps:
        try:
            input_ports(step)
            if step.type_id == "math_formula":
                tree = parse_expression(step.params.get("expression", "a + b"), dict(input_ports(step)))
                constants = json.loads(step.params.get("constants", "{}"))
                if not isinstance(constants, dict):
                    raise ValueError("Constants must be a JSON object")
                import ast
                used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
                for name, dtype in input_ports(step):
                    if name not in used or step.params.get(f"_input_{name}_source"):
                        continue
                    value = constants.get(name, step.params.get(f"fallback_{name}"))
                    if value is None or dtype == "curve":
                        raise ValueError(f"Connect input {name} or supply its constant")
                    if dtype == "number" and (isinstance(value, bool) or not math.isfinite(float(value))):
                        raise ValueError(f"Constant {name} must be finite")
                if step.params.get("output_type", "number") not in {"number", "bool"}:
                    raise ValueError("Formula output must be number or bool")
            if step.params.get("on_invalid", "invalid") not in {"invalid", "stop"}:
                raise ValueError("Invalid-result policy must be invalid or stop")
            if step.type_id in {"condition_metric", "boolean_compare", "boolean_compare_pair"}:
                if step.params.get("operator", ">=") not in {">", ">=", "<", "<=", "==", "!="}:
                    raise ValueError("Unknown comparison operator")
                if not math.isfinite(float(step.params.get("threshold", "0"))):
                    raise ValueError("Comparison threshold must be finite")
            if step.type_id == "plot_panel":
                from .workflow_plot import validate_panel
                validate_panel(step)
        except (ValueError, SyntaxError, TypeError) as exc:
            errors.append(f"{step.params.get('_node_id', step.type_id)}: {exc}")
    if errors:
        return tuple(errors)
    known = set(ids)
    incoming = {node_id: 0 for node_id in ids}
    auxiliary_sources: set[str] = set()
    for index, step in enumerate(steps):
        node_id = ids[index]
        edges = ("_true", "_false") if is_condition_flow_type(step.type_id) else ("_next",)
        for edge in edges:
            target = str(step.params.get(edge, "")).strip()
            if target and target not in known:
                errors.append(f"{node_id}: {edge[1:]} target {target!r} does not exist.")
            elif target:
                incoming[target] += 1
        if is_condition_flow_type(step.type_id) and not (step.params.get("_true") or step.params.get("_false")):
            errors.append(f"{node_id}: condition has no True/False connection.")
        light_source = str(step.params.get("_light_source", "")).strip()
        if light_source:
            auxiliary_sources.add(light_source)
            if step.type_id not in LIGHT_INPUT_TYPES:
                errors.append(f"{node_id}: this node does not accept a light waveform.")
            if light_source not in known:
                errors.append(f"{node_id}: light source {light_source!r} does not exist.")
            else:
                source_index = ids.index(light_source)
                if steps[source_index].type_id not in LIGHT_OUTPUT_TYPES:
                    errors.append(f"{node_id}: {light_source!r} is not a light waveform node.")
        for key, value in step.params.items():
            if not key.startswith("_input_") or not key.endswith("_source"):
                continue
            source_id = str(value).strip()
            input_name = key[len("_input_") : -len("_source")]
            expected_type = dict(input_ports(step)).get(input_name, "")
            input_type = str(step.params.get(f"_input_{input_name}_type", expected_type)).strip()
            output_name = str(step.params.get(f"_input_{input_name}_output", "")).strip()
            if not expected_type:
                errors.append(f"{node_id}: unknown data input {input_name!r}.")
                continue
            if not compatible(input_type, expected_type):
                errors.append(f"{node_id}: {input_name} is declared as {input_type or 'untyped'}, expected {expected_type}.")
            if source_id not in known:
                errors.append(f"{node_id}: {input_type} input source {source_id!r} does not exist.")
                continue
            source_step = steps[ids.index(source_id)]
            actual_type = _data_output_type(source_step, output_name)
            if input_type and not compatible(actual_type, input_type):
                errors.append(f"{node_id}: {input_name} expects {input_type}, but {source_id} outputs {actual_type or 'no data'}.")
    errors.extend(_data_cycle_errors(steps, ids))
    panels = [step for step in steps if step.type_id == "plot_panel"]
    strict = bool(panels) or any(step.params.get("_workflow_version") == "2" for step in steps)
    if strict:
        starts_explicit = [ids[i] for i, step in enumerate(steps) if step.type_id == "workflow_start"]
        ends = [ids[i] for i, step in enumerate(steps) if step.type_id == "workflow_end"]
        if len(starts_explicit) != 1 or len(ends) != 1:
            errors.append("Workflow v2 requires exactly one Start and one End.")
        adjacency = {node_id: [str(step.params[key]) for key in ("_next", "_true", "_false") if step.params.get(key) in known] for node_id, step in zip(ids, steps)}
        state = {}
        def walk(node_id):
            if state.get(node_id) == 1:
                errors.append(f"{node_id}: control cycles are not supported in workflow v2.")
                return
            if state.get(node_id) == 2:
                return
            state[node_id] = 1
            for target in adjacency[node_id]:
                if steps[ids.index(target)].type_id in DATA_ONLY_TYPES:
                    errors.append(f"{target}: data-only nodes cannot accept control flow.")
                walk(target)
            state[node_id] = 2
        if starts_explicit:
            walk(starts_explicit[0])
        for node_id, step in zip(ids, steps):
            if step.type_id in {"it", "light_control", "light_pulse", "wavelength"}:
                errors.append(f"{node_id}: {step.type_id} has no instrument executor; use an implemented measurement/light card.")
            if step.type_id in DATA_ONLY_TYPES or node_id in auxiliary_sources:
                continue
            if node_id not in state:
                errors.append(f"{node_id}: unreachable control node.")
            if step.type_id == "workflow_end":
                if adjacency[node_id]:
                    errors.append(f"{node_id}: End must not have outgoing control links.")
            elif is_condition_flow_type(step.type_id):
                if not step.params.get("_true") or not step.params.get("_false"):
                    errors.append(f"{node_id}: both True and False must be connected.")
            elif not step.params.get("_next"):
                errors.append(f"{node_id}: connect the next step or End.")
        # Calibration must dominate its consumer: every possible execution path
        # to the consumer must execute the calibration first.
        for node_id, step in zip(ids, steps):
            calibration_id = step.params.get("_input_calibration_source")
            if not calibration_id or not starts_explicit:
                continue
            pending, seen = [starts_explicit[0]], set()
            while pending:
                current = pending.pop()
                if current == calibration_id or current in seen:
                    continue
                seen.add(current)
                pending.extend(adjacency[current])
            if node_id in seen:
                errors.append(f"{node_id}: calibration must execute on every incoming path.")
    starts = [node_id for node_id, count in incoming.items() if count == 0 and node_id not in auxiliary_sources and steps[ids.index(node_id)].type_id not in DATA_ONLY_TYPES]
    if len(starts) > 1:
        errors.append("Graph has multiple disconnected start nodes: " + ", ".join(starts))
    if not starts:
        errors.append("Graph has no start node; check for a cycle.")
    return tuple(errors)


def control_node_runs_on_every_path(steps: Sequence[AutoTestFlowStep], target_id: str) -> bool:
    """Whether every Start-to-End control path visits target_id."""
    by_id = {step_node_id(step, index): step for index, step in enumerate(steps)}
    if target_id not in by_id or by_id[target_id].type_id in DATA_ONLY_TYPES:
        return False
    pending = [graph_start_node(steps)]
    seen = set()
    while pending:
        node_id = pending.pop()
        if node_id is None or node_id == target_id or node_id in seen:
            continue
        seen.add(node_id)
        step = by_id[node_id]
        if step.type_id == "workflow_end":
            return False
        pending.extend(str(step.params[key]) for key in ("_next", "_true", "_false")
                       if step.params.get(key) in by_id)
    return True


def graph_start_node(steps: Sequence[AutoTestFlowStep]) -> str | None:
    if not steps:
        return None
    ids = [step_node_id(step, index) for index, step in enumerate(steps)]
    incoming = {node_id: 0 for node_id in ids}
    auxiliary_sources = {
        str(step.params.get("_light_source", "")).strip()
        for step in steps
        if str(step.params.get("_light_source", "")).strip() in incoming
    }
    auxiliary_sources.update(node_id for node_id, step in zip(ids, steps) if step.type_id in DATA_ONLY_TYPES)
    for step in steps:
        for key in ("_next", "_true", "_false"):
            target = str(step.params.get(key, "")).strip()
            if target in incoming:
                incoming[target] += 1
    return next((node_id for node_id in ids if incoming[node_id] == 0 and node_id not in auxiliary_sources), None)


def flatten_result(payload: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            flat.update(flatten_result(value, path))
        else:
            flat[path] = value
            flat.setdefault(str(key), value)
    return flat


def evaluate_condition(
    step: AutoTestFlowStep,
    results: Mapping[str, Mapping[str, Any]],
    previous_node: str | None = None,
    steps: Sequence[AutoTestFlowStep] = (),
) -> bool:
    params = step.params
    for name, _dtype in input_ports(step):
        if params.get(f"_input_{name}_source"):
            linked = resolve_data_input(step, name, steps, results)
            if linked is None or isinstance(linked, InvalidValue) or (isinstance(linked, (float, int)) and not math.isfinite(linked)):
                if params.get("on_invalid") == "stop":
                    raise ValueError(f"{params.get('_node_id', step.type_id)}: invalid {name}")
                return False
    if step.type_id == "condition_boolean":
        value = resolve_data_input(step, "predicate", steps, results)
        if value is None:
            return str(params.get("fallback", "false")).lower() == "true"
        return bool(value)
    source = str(params.get("source_node", "previous")).strip()
    source_id = previous_node if source in {"", "previous"} else source
    values = flatten_result(results.get(source_id or "", {}))
    missing_default = str(params.get("missing_result", "false")).lower() == "true"
    if step.type_id == "condition_compliance":
        actual = resolve_data_input(step, "reached", steps, results)
        if actual is None:
            actual = values.get("compliance_reached", values.get("statistics.compliance_reached"))
        if actual is None:
            return missing_default
        expected = str(params.get("expected", "true")).lower() == "true"
        return bool(actual) is expected
    if step.type_id == "condition_device_state":
        state = str(params.get("state", "open_circuit"))
        resistance = _number(resolve_data_input(step, "resistance", steps, results))
        current = _number(resolve_data_input(step, "current", steps, results))
        if resistance is None:
            resistance = _number(values.get("statistics.resistance_ohm", values.get("resistance_ohm")))
        if current is None:
            current = _number(values.get("statistics.max_abs_current_a", values.get("max_abs_current_a")))
        if state == "open_circuit":
            return resistance is not None and resistance >= float(params.get("resistance_threshold_ohm", "1e9"))
        if state == "short_circuit":
            return current is not None and current >= float(params.get("current_threshold_a", "1e-3"))
        if state == "healthy":
            return resistance is not None and resistance < float(params.get("resistance_threshold_ohm", "1e9"))
        return missing_default
    metric = str(params.get("metric", "statistics.resistance_ohm"))
    value = _number(resolve_data_input(step, "value", steps, results))
    if value is None:
        value = _number(values.get(metric))
    if value is None:
        return missing_default
    threshold = float(params.get("threshold", "0"))
    operation = {
        ">": operator.gt,
        ">=": operator.ge,
        "<": operator.lt,
        "<=": operator.le,
        "==": operator.eq,
        "!=": operator.ne,
    }.get(str(params.get("operator", ">")), operator.gt)
    return bool(operation(value, threshold))


def resolve_data_input(
    step: AutoTestFlowStep,
    input_name: str,
    steps: Sequence[AutoTestFlowStep],
    results: Mapping[str, Mapping[str, Any]],
    memo: dict[tuple[str, str], Any] | None = None,
) -> Any:
    source_id = str(step.params.get(f"_input_{input_name}_source", "")).strip()
    output_name = str(step.params.get(f"_input_{input_name}_output", "")).strip()
    if not source_id or not output_name:
        return None
    by_id = {step_node_id(candidate, index): candidate for index, candidate in enumerate(steps)}
    return resolve_data_output(source_id, output_name, by_id, results, memo if memo is not None else {})


def resolve_data_output(source_id, output_name, by_id, results, memo):
    try:
        value = _resolve_data_output(source_id, output_name, by_id, results, memo)
        step = by_id.get(source_id)
        if isinstance(value, InvalidValue) and step is not None and step.params.get("on_invalid") == "stop":
            raise RuntimeError(f"{source_id}: {value.reason}")
        return value
    except (ValueError, TypeError, ArithmeticError) as exc:
        step = by_id.get(source_id)
        if step is not None and step.params.get("on_invalid") == "stop":
            raise RuntimeError(f"{source_id}: {exc}") from exc
        value = InvalidValue(f"{source_id}: {exc}")
        memo[source_id, output_name] = value
        return value


def _resolve_data_output(
    source_id: str,
    output_name: str,
    by_id: Mapping[str, AutoTestFlowStep],
    results: Mapping[str, Mapping[str, Any]],
    memo: dict[tuple[str, str], Any],
) -> Any:
    key = (source_id, output_name)
    if key in memo:
        return None if memo[key] is _RESOLVING else memo[key]
    step = by_id.get(source_id)
    if step is None:
        return None
    memo[key] = _RESOLVING
    if step.type_id in MATH_TYPES:
        values = {}
        constants = json.loads(step.params.get("constants", "{}")) if "operator" not in step.params else {}
        for name, dtype in input_ports(step):
            linked = bool(step.params.get(f"_input_{name}_source"))
            value = _linked_value(step, name, by_id, results, memo) if linked else constants.get(name, step.params.get(f"fallback_{name}"))
            if dtype == "curve" and isinstance(value, dict):
                label = step.params.get("curve_series", "last")
                value = next(reversed(value.values()), None) if label == "last" else value.get(label)
            if not linked and value is not None:
                value = str(value).lower() == "true" if dtype == "bool" else float(value) if dtype == "number" else value
            values[name] = value
        symbol = {"math_add": "+", "math_subtract": "-", "math_multiply": "*", "math_divide": "/"}.get(step.type_id)
        legacy = step.params.get("operator", "+")
        legacy_expression = f"{legacy}(a,b)" if legacy in {"min", "max"} else f"a {legacy} b"
        expression = f"a {symbol} b" if symbol else legacy_expression if "operator" in step.params else step.params.get("expression", legacy_expression)
        value = evaluate_expression(expression, values, step.params.get("output_type", "number"), step.params.get("on_invalid", "invalid"))
    elif step.type_id == "boolean_compare_pair":
        values = {n: _data_number(step, n, by_id, results, memo, f"fallback_{n}") for n in ("a", "b")}
        value = evaluate_expression(f"a {step.params.get('operator', '>=')} b", values, "bool", step.params.get("on_invalid", "invalid"))
    elif step.type_id == "boolean_compare":
        linked = _linked_value(step, "value", by_id, results, memo)
        if linked is None or isinstance(linked, InvalidValue):
            value = linked if isinstance(linked, InvalidValue) else InvalidValue("Missing comparison input")
            if step.params.get("on_invalid") == "stop":
                raise ValueError(value.reason)
            memo[key] = value
            return value
        number = _number(linked)
        if number is None:
            return InvalidValue("Comparison input is not finite")
        threshold = float(step.params.get("threshold", "0"))
        value = {
            ">": operator.gt,
            ">=": operator.ge,
            "<": operator.lt,
            "<=": operator.le,
            "==": operator.eq,
            "!=": operator.ne,
        }.get(str(step.params.get("operator", ">=")), operator.ge)(number, threshold)
    elif step.type_id == "boolean_logic":
        names = ("a",) if str(step.params.get("operator", "AND")).upper() == "NOT" else ("a", "b")
        values = {}
        for name in names:
            linked = bool(step.params.get(f"_input_{name}_source"))
            value = _linked_value(step, name, by_id, results, memo) if linked else str(step.params.get(f"fallback_{name}", "false")).lower() == "true"
            if value is None or isinstance(value, InvalidValue):
                return value if isinstance(value, InvalidValue) else InvalidValue(f"Missing Boolean input: {name}")
            values[name] = bool(value)
        a, b = values["a"], values.get("b", False)
        value = {"AND": a and b, "OR": a or b, "XOR": a ^ b, "NOT": not a}[str(step.params.get("operator", "AND")).upper()]
    else:
        result = results.get(source_id)
        if result is None:
            return InvalidValue(f"{source_id}: source not executed")
        if result.get("valid") is False:
            return InvalidValue(f"{source_id}: {result.get('status', 'invalid acquisition')}")
        values = flatten_result(result)
        value = result.get(output_name, values.get(output_name))
        validity = result.get("outputs", {}).get(output_name, {})
        if validity.get("valid") is False:
            value = InvalidValue(f"{source_id}: {validity.get('reason', 'feature unavailable')}")
    if isinstance(value, InvalidValue) and step.params.get("on_invalid") == "stop":
        raise RuntimeError(f"{source_id}: {value.reason}")
    memo[key] = value
    return value


def _data_number(step: AutoTestFlowStep, name: str, by_id: Mapping[str, AutoTestFlowStep], results: Mapping[str, Mapping[str, Any]], memo: dict[tuple[str, str], Any], fallback: str) -> float:
    value = _linked_value(step, name, by_id, results, memo)
    return value if isinstance(value, InvalidValue) else (InvalidValue(f"Missing input: {name}") if value is None and step.params.get(f"_input_{name}_source") else float(step.params.get(fallback, "0")) if value is None else float(value))


def _data_bool(step: AutoTestFlowStep, name: str, by_id: Mapping[str, AutoTestFlowStep], results: Mapping[str, Mapping[str, Any]], memo: dict[tuple[str, str], Any], fallback: str) -> bool:
    value = _linked_value(step, name, by_id, results, memo)
    return str(step.params.get(fallback, "false")).lower() == "true" if value is None else bool(value)


def _linked_value(step: AutoTestFlowStep, name: str, by_id: Mapping[str, AutoTestFlowStep], results: Mapping[str, Mapping[str, Any]], memo: dict[tuple[str, str], Any]) -> Any:
    source = str(step.params.get(f"_input_{name}_source", "")).strip()
    output = str(step.params.get(f"_input_{name}_output", "")).strip()
    return resolve_data_output(source, output, by_id, results, memo) if source and output else None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _data_output_type(step: AutoTestFlowStep, output_name: str) -> str:
    return output_type(step, output_name)


def _data_cycle_errors(steps: Sequence[AutoTestFlowStep], ids: Sequence[str]) -> tuple[str, ...]:
    dependencies: dict[str, set[str]] = {node_id: set() for node_id in ids}
    for node_id, step in zip(ids, steps):
        for key, value in step.params.items():
            if key.startswith("_input_") and key.endswith("_source") and str(value).strip() in dependencies:
                dependencies[node_id].add(str(value).strip())
    state: dict[str, int] = {}
    path: list[str] = []
    cycle_nodes: set[str] = set()

    def visit(node_id: str) -> None:
        if state.get(node_id) == 1:
            cycle_nodes.update(path[path.index(node_id) :])
            return
        if state.get(node_id) == 2:
            return
        state[node_id] = 1
        path.append(node_id)
        for dependency in dependencies[node_id]:
            visit(dependency)
        path.pop()
        state[node_id] = 2

    for node_id in ids:
        visit(node_id)
    return tuple(f"{node_id}: cyclic data dependency." for node_id in sorted(cycle_nodes))
