"""Per-node acquisition context and coalesced, display-only snapshots."""
from __future__ import annotations

import math
import threading
import time
from dataclasses import asdict

from .workflow_formula import Curve, InvalidValue


def json_value(value):
    if isinstance(value, InvalidValue):
        return {"valid": False, "reason": value.reason}
    if isinstance(value, Curve):
        return json_value(asdict(value))
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class DisplayMailbox:
    """At most one queued notification per producer, newest snapshot wins."""
    def __init__(self):
        self.lock = threading.Lock()
        self.pending = None
        self.queued = False

    def publish(self, queue, event):
        with self.lock:
            self.pending = event
            if not self.queued:
                self.queued = True
                queue.put(("workflow_display", self))

    def take(self):
        with self.lock:
            event, self.pending, self.queued = self.pending, None, False
            return event


class NodeCapture:
    def __init__(self, node_id, type_id, run_id, point, queue, plot=False, *, sweep_mode="voltage"):
        self.node_id, self.type_id, self.run_id = node_id, type_id, run_id
        self.point, self.queue, self.plot = point, queue, plot
        self.sweep_mode = sweep_mode
        self.series = {}
        self.last_publish = 0
        self.completed = False
        self.mailbox = DisplayMailbox()

    def sample(self, sample, label=None):
        if hasattr(sample, "to_dict"):
            sample = sample.to_dict()
        elif not isinstance(sample, dict):
            sample = vars(sample)
        label = str(label or sample.get("curve_label") or sample.get("curve_index") or "Current")
        is_transfer = "transfer" in self.type_id
        time_axis = self.type_id in {"constant_current", "photo_transient", "photo_power_steps", "photo_width_steps", "it", "wobb_test"}
        x = sample.get("elapsed_s", sample.get("index", 0)) if time_axis else sample.get("gate_voltage_v", sample.get("vg_v")) if is_transfer else sample.get("source_value", sample.get("drain_voltage_v", sample.get("voltage_v")))
        if x is None:
            return
        current = sample.get("current_a", sample.get("drain_current_a", sample.get("id_a")))
        voltage = sample.get("voltage_v", sample.get("drain_voltage_v", sample.get("vd_v", x)))
        if current is None:
            return
        current_axis = self.type_id == "iv" and self.sweep_mode == "current"
        if current_axis:
            x = current
        unit = "s" if time_axis else "A" if current_axis else "V"
        if label not in self.series:
            self.series[label] = {"x": [], "current": [], "voltage": [], "resistance": [], "x_unit": unit}
        data = self.series[label]
        data["x"].append(float(x))
        data["current"].append(float(current))
        data["voltage"].append(float(voltage))
        data["resistance"].append(float(voltage) / float(current) if current else float("nan"))
        self.publish()

    def family(self, quantity):
        family = {}
        current_axis = self.type_id == "iv" and self.sweep_mode == "current"
        for label, data in self.series.items():
            indices = range(len(data["x"]))
            family[label] = Curve(tuple(data["x"][i] for i in indices), tuple(data[quantity][i] for i in indices), label, data["x_unit"], {"current": "A", "voltage": "V", "resistance": "Ω"}[quantity],
                                  tuple(data["voltage"][i] for i in indices) if self.type_id == "iv" and not current_axis else None)
        return family

    def publish(self, force=False):
        now = time.monotonic()
        if self.plot and (force or now - self.last_publish >= .05):
            outputs = {name: self.family(quantity) for name, quantity in (("live", "current"), ("live_voltage", "voltage"), ("live_resistance", "resistance"))}
            if self.completed:
                outputs.update({name: self.family(quantity) for name, quantity in (("curve", "current"), ("curve_voltage", "voltage"), ("curve_resistance", "resistance"))})
            self.mailbox.publish(self.queue, (self.run_id, self.node_id, outputs))
            self.last_publish = time.monotonic()

    def result(self, payload, status):
        payload = dict(payload or {})
        from .workflow_ports import RESULT_OUTPUTS, metric_type, metric_unit
        import numpy as np
        statistics = dict(payload.get("statistics", {}))
        currents = np.asarray([v for data in self.series.values() for v in data["current"] if math.isfinite(v)])
        if currents.size:
            for name, value in (("max_abs_current_a", np.max(np.abs(currents))),
                                ("peak_current_a", np.max(currents)), ("mean_current_a", np.mean(currents)),
                                ("std_current_a", np.std(currents)), ("steady_current_a", currents[-1])):
                statistics.setdefault(name, float(value))
        if self.type_id in {"hp6614c_transfer", "b1500_transfer"}:
            from .hp6614c import analyze_hp6614c_transfer_records
            records = [{"gate_voltage_v": x, "drain_voltage_v": voltage, "drain_current_a": current,
                        "bias_v": voltage, "curve_index": index}
                       for index, data in enumerate(self.series.values(), 1)
                       for x, voltage, current in zip(data["x"], data["voltage"], data["current"])]
            for metric in ("vth", "ss", "on_off_ratio"):
                analysis = analyze_hp6614c_transfer_records(records, metric)
                payload.setdefault(metric, analysis.mean_value)
            statistics.setdefault("subthreshold_swing_v_dec", payload.get("ss"))
        if self.type_id in {"hp6614c_output", "hp6614c_bias_loop", "b1500_output"} and self.series:
            from .workflow_formula import evaluate_expression
            slopes = [evaluate_expression("slope(c)", {"c": curve}) for curve in self.family("current").values()]
            resistances = [1 / value for value in slopes if isinstance(value, float) and value != 0]
            statistics.setdefault("output_resistance_ohm", float(np.mean(resistances)) if resistances else None)
        payload["statistics"] = statistics
        # Missing physical features remain explicit, rather than being fabricated
        # from an unrelated measurement (e.g. a missing dark reference).
        validity = {}
        for name in RESULT_OUTPUTS.get(self.type_id, ()):
            if metric_type(name) in {"curve", "trace"}:
                continue
            container = statistics if name.startswith("statistics.") else payload
            value = container.get(name.rsplit(".", 1)[-1])
            valid = isinstance(value, (bool, int, float)) and math.isfinite(value) and status == "done"
            validity[name] = {"valid": valid, "unit": metric_unit(name),
                              "reason": None if valid else "Feature unavailable for this completed acquisition" if status == "done" else status}
        payload["outputs"] = validity
        payload.update(run_id=self.run_id, node_id=self.node_id,
                       point={"name": self.point.name, "row": self.point.row, "col": self.point.col},
                       status=status, valid=status == "done")
        for output, quantity in (("curve", "current"), ("curve_voltage", "voltage"), ("curve_resistance", "resistance")):
            payload[output] = self.family(quantity)
        self.completed = True
        self.publish(force=True)
        return payload
