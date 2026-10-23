"""Editor-only history, validation feedback and wire manipulation."""
from __future__ import annotations

import copy
import math
import re
import tkinter as tk
from tkinter import ttk
from .auto_test import AutoTestFlowCard, measurement_flow_steps_from_cards
from .flow_graph import validate_flow_graph
from .workflow_ports import input_ports, compatible


class FlowEditorUX:
    def _init_ux(self):
        self._history = []
        self._history_index = -1
        self._history_group = None
        self._history_suspended = False
        self._draft_values = {}
        self._field_errors = {}
        self._field_widgets = {}
        self._error_labels = {}
        self._validation_issues = []
        self.selected_edge = None
        self._rewire = None
        self._history_pool = {}

    def _reset_edit_feedback(self):
        self._finish_inline_edit(commit=False)
        self._draft_values.clear()
        self._field_errors.clear()
        self._validation_issues.clear()
        self.selected_edge = self._rewire = self.pending_edge = self.drag_id = None
        self._history_group = None
        self._refresh_issue_list()

    def _snapshot(self):
        return copy.deepcopy([(c.card_id, c.type_id, c.params, c.x, c.y, c.expanded) for c in self.cards])

    def _record_history(self, group=None):
        if not hasattr(self, "_history") or self._history_suspended:
            return
        state = self._snapshot()
        if self._history_index >= 0 and state == self._history[self._history_index]:
            return
        self._history_pool.update({c.card_id: c for c in self.cards})
        self._history = self._history[:self._history_index+1]
        if group is not None and group == self._history_group and self._history_index > 0:
            self._history[-1] = state
        else:
            self._history.append(state)
            self._history = self._history[-101:]
        self._history_index = len(self._history)-1
        self._history_group = group
        self._update_history_buttons()

    def _update_history_buttons(self):
        if hasattr(self, "undo_button"):
            self.undo_button.configure(state="normal" if self._history_index > 0 else "disabled")
            self.redo_button.configure(state="normal" if self._history_index+1 < len(self._history) else "disabled")

    def _restore_snapshot(self, state):
        self._history_pool.update({c.card_id: c for c in self.cards})
        restored = []
        for identity, kind, params, x, y, expanded in copy.deepcopy(state):
            card = self._history_pool.get(identity)
            if card is None:
                card = AutoTestFlowCard(identity, kind, params)
            card.type_id, card.params, card.x, card.y, card.expanded = kind, params, x, y, expanded
            restored.append(card)
        self.cards[:] = restored

    def _history_move(self, delta, event=None):
        if event is not None:
            inline = getattr(self, "_inline_editor", None)
            if inline and event.widget is inline[0]:
                if delta < 0:
                    self._inline_redo_text = inline[2].get()
                    inline[2].set(inline[3].params.get(inline[4], ""))
                    self._field_feedback(inline[3], inline[4], "", "")
                elif hasattr(self, "_inline_redo_text"):
                    inline[2].set(self._inline_redo_text)
                return "break"
            field = next((identity for identity, widget in self._field_widgets.items() if widget is event.widget), None)
            if field and field in self._draft_values:
                self._discard_parameter_draft(self._card(field[0]), field[1])
                return "break"
            if field is None and event.widget.winfo_class() in {"Entry", "TEntry", "TCombobox", "Text", "Spinbox", "TSpinbox"}:
                return None
        self._finish_inline_edit(commit=False)
        index = self._history_index+delta
        if not 0 <= index < len(self._history):
            return "break"
        self._history_index = index
        self._history_group = None
        self._draft_values.clear()
        self._field_errors.clear()
        self._validation_issues.clear()
        self.pending_edge = self.drag_id = self.selected_edge = self._rewire = None
        self._restore_snapshot(self._history[index])
        card = self._card(getattr(self, "_inspected_id", None))
        if card and self._inspector_is_visible():
            self._show_inspector(card)
        elif self._inspector_is_visible():
            self._hide_inspector()
        if self._card(self.selected_id) is None:
            self.selected_id = None
        self.on_change()
        self.redraw()
        self._update_history_buttons()
        self._refresh_issue_list()
        self.status_var.set("Undone." if delta < 0 else "Redone.")
        return "break"

    @staticmethod
    def _parameter_unit(card, param):
        suffixes = {"_ms":"ms", "_s":"s", "_v":"V", "_a":"A", "_ohm":"Ω", "_mw":"mW", "_nm":"nm", "_um":"µm"}
        for suffix, unit in suffixes.items():
            if param.key.lower().endswith(suffix):
                return unit
        if param.key in {"start", "stop", "step", "low", "source_value", "source_range"}:
            mode = card.params.get("source_mode", card.params.get("sweep_mode"))
            return "A" if mode == "current" else "V"
        return ""

    def _validate_parameter(self, card, key, value, _list_item=False):
        param = next((p for p in self.definitions[card.type_id].parameters if p.key == key), None)
        keithley_keys = {
            'start', 'stop', 'low', 'step', 'source_value', 'duration_s', 'sample_interval_s',
            'source_range', 'measure_range', 'voltage_limit_v', 'current_limit_a', 'segments',
        }
        if card.type_id in {'iv', 'it'} and key in keithley_keys:
            from .smu_settings import parse_si_value, MAX_SWEEP_SEGMENTS
            raw = str(value).strip()
            try:
                if key == 'segments':
                    if not raw:
                        return '', ''
                    count = int(raw)
                    if not 1 <= count <= MAX_SWEEP_SEGMENTS:
                        raise ValueError('Segments must be 1-1,000,000.')
                    return str(count), ''
                if key in {'source_range', 'measure_range'} and raw.lower() == 'auto':
                    return 'auto', ''
                mode = card.params.get('source_mode', card.params.get('sweep_mode', 'voltage'))
                unit = 'A' if mode == 'current' else 'V'
                if key == 'voltage_limit_v': unit = 'V'
                if key == 'current_limit_a': unit = 'A'
                if key == 'measure_range': unit = 'V' if unit == 'A' else 'A'
                if key in {'duration_s', 'sample_interval_s'}: unit = 's'
                separator = ':' if ':' in raw else ','
                if key == 'stop' and separator in raw:
                    return separator.join(f'{parse_si_value(v, unit):.12g}' for v in raw.split(separator)), ''
                number = parse_si_value(raw, unit)
                if key in {'step', 'duration_s', 'sample_interval_s', 'source_range', 'measure_range', 'voltage_limit_v', 'current_limit_a'} and number <= 0:
                    raise ValueError('Value must be positive.')
                return f'{number:.12g}', ''
            except ValueError as exc:
                return value, str(exc)
        if param is None or self._choices(card.type_id, key):
            return value, ""
        if key == "bias_values_v" and not _list_item:
            values = []
            for item in str(value).split(","):
                canonical, error = self._validate_parameter(card, key, item, _list_item=True)
                if error:
                    return value, "Enter comma-separated finite voltages, e.g. 0, 0.5, 1."
                values.append(canonical)
            return ",".join(values), ""
        try:
            if key != "bias_values_v":
                float(param.default)
        except ValueError:
            return value, ""
        raw = str(value).strip()
        try:
            number = float(raw)
        except ValueError:
            match = re.fullmatch(r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\s*([A-Za-zµμΩ]+)", raw)
            unit = self._parameter_unit(card, param)
            scales = {"s":1., "ms":1e-3, "us":1e-6, "µs":1e-6, "μs":1e-6,
                      "V":1., "mV":1e-3, "uV":1e-6, "µV":1e-6, "μV":1e-6,
                      "A":1., "mA":1e-3, "uA":1e-6, "µA":1e-6, "μA":1e-6, "nA":1e-9, "pA":1e-12,
                      "Ω":1., "ohm":1., "kohm":1e3, "Mohm":1e6,
                      "W":1., "mW":1e-3, "uW":1e-6, "µW":1e-6,
                      "m":1., "mm":1e-3, "um":1e-6, "µm":1e-6, "nm":1e-9}
            groups = ({"s","ms","us","µs","μs"}, {"V","mV","uV","µV","μV"},
                      {"A","mA","uA","µA","μA","nA","pA"}, {"Ω","ohm","kohm","Mohm"},
                      {"W","mW","uW","µW"}, {"m","mm","um","µm","nm"})
            entered_unit = match[2] if match else ""
            if not match or not any(unit in group and entered_unit in group for group in groups):
                return value, f"Enter a finite number{(' in '+unit) if unit else ''} (scientific notation accepted)."
            number = float(match[1]) * scales[entered_unit] / scales[unit]
        if not math.isfinite(number):
            return value, "Enter a finite number."
        if key in {"sample_count", "sweep_points", "power_levels", "count", "cycles", "preset_index"}:
            if number < 1 or not number.is_integer():
                return value, "Enter a whole number of at least 1."
        positive = {"nplc", "staircase_nplc", "drain_nplc", "sample_interval_ms", "period_ms"}
        nonnegative = {"duration_s", "settle_s", "source_delay_s", "step_delay_s", "pre_settle_s", "post_sweep_pause_s", "trigger_delay_ms", "delay_ms", "rise_ms", "fall_ms", "width_ms", "current_limit_a", "voltage_limit_v", "drain_current_compliance_a", "gate_current_compliance_a"}
        if key in positive and number <= 0:
            return value, "Must be greater than 0."
        if key in nonnegative and number < 0:
            return value, "Must be 0 or greater."
        if key == "step" and number == 0:
            return value, "Sweep step cannot be 0."
        return (raw if re.fullmatch(r"[+\-0-9.eE]+", raw) else format(number, ".12g")), ""

    def _field_feedback(self, card, key, raw, error):
        identity = (card.card_id, key)
        if error:
            self._draft_values[identity] = raw
            self._field_errors[identity] = error
            self.status_var.set(error)
        else:
            self._draft_values.pop(identity, None)
            self._field_errors.pop(identity, None)
        label = self._error_labels.get(identity)
        if label is not None and label.winfo_exists():
            label.configure(text=error)
        widget = self._field_widgets.get(identity)
        if widget is not None and widget.winfo_exists() and widget.winfo_class() == "TEntry":
            widget.configure(style="Flow.Invalid.TEntry" if error else "TEntry")

    def _end_parameter_edit(self, card, key):
        self._history_group = None
        return None

    def _discard_parameter_draft(self, card, key):
        self._field_feedback(card, key, "", "")
        if getattr(self, "_inspected_id", None) == card.card_id and key in self.param_vars:
            self.param_vars[key].set(card.params.get(key, ""))
        self._history_group = None
        return "break"

    @staticmethod
    def _clear_input(target, prefix):
        source = target.params.get(prefix+"_source")
        output = target.params.get(prefix+"_output")
        for suffix in ("source", "output", "type", "unit"):
            target.params.pop(prefix+"_"+suffix, None)
        if source and target.params.get("_result_source") == source and target.params.get("_result_metric") == output:
            target.params.pop("_result_source", None)
            target.params.pop("_result_metric", None)
            if target.params.get("source_node") == source:
                target.params.pop("source_node", None)

    def _remove_node_references(self, identity):
        for card in self.cards:
            for key, value in list(card.params.items()):
                if key.startswith("_input_") and key.endswith("_source") and value == identity:
                    self._clear_input(card, key[:-7])
            for key in ("_next", "_true", "_false", "_light_source", "source_node", "_child", "_parent"):
                if card.params.get(key) == identity:
                    card.params.pop(key, None)
            if card.params.get("_result_source") == identity:
                card.params.pop("_result_source", None)
                card.params.pop("_result_metric", None)
        for mapping in (self._draft_values, self._field_errors):
            for key in list(mapping):
                if key[0] == identity:
                    mapping.pop(key)

    def _collect_issues(self):
        issues = [(node, key, message) for (node, key), message in self._field_errors.items() if self._card(node)]
        for card in self.cards:
            for param in self.definitions[card.type_id].parameters:
                if (card.card_id, param.key) not in self._field_errors:
                    _, error = self._validate_parameter(card, param.key, card.params.get(param.key, param.default))
                    if error:
                        issues.append((card.card_id, param.key, error))
        for message in validate_flow_graph(measurement_flow_steps_from_cards(self.cards)):
            prefix = message.split(":", 1)[0]
            card = self._card(prefix)
            key = ""
            if card:
                key = next((p.key for p in self.definitions[card.type_id].parameters if p.key.lower() in message.lower()), "")
                if not key and card.type_id == "math_formula":
                    key = "variables" if "input" in message.lower() or "variable" in message.lower() else "expression"
            issues.append((card.card_id if card else "", key, message))
        return list(dict.fromkeys(issues))

    def _refresh_issue_list(self):
        if not hasattr(self, "issue_tree") or not self.issue_tree.winfo_exists():
            return
        self.issue_tree.delete(*self.issue_tree.get_children())
        for index, (identity, key, message) in enumerate(self._validation_issues):
            card = self._card(identity)
            label = (self.definitions[card.type_id].title + " / " + identity) if card else "Workflow"
            self.issue_tree.insert("", "end", iid=str(index), values=(label, key, message))
        self.issue_summary.configure(text=f"{len(self._validation_issues)} issues — select a row to locate the node / parameter")

    def _show_issues(self):
        if not hasattr(self, "issue_frame") or not self.issue_frame.winfo_exists():
            self.issue_frame = tk.Frame(self.canvas.master, bg=self.colors["surface_2"])
            self.issue_frame.columnconfigure(0, weight=1)
            self.issue_summary = tk.Label(self.issue_frame, bg=self.colors["surface_2"], fg=self.colors["text"], anchor="w")
            self.issue_summary.grid(row=0, column=0, sticky="ew")
            ttk.Button(self.issue_frame, text="Close", command=self.issue_frame.grid_remove).grid(row=0, column=1)
            self.issue_tree = ttk.Treeview(self.issue_frame, columns=("node", "field", "message"), show="headings", height=4)
            for key, width in (("node",180), ("field",130), ("message",440)):
                self.issue_tree.heading(key, text=key.title())
                self.issue_tree.column(key, width=width, minwidth=60)
            self.issue_tree.grid(row=1, column=0, columnspan=2, sticky="ew")
            bar = ttk.Scrollbar(self.issue_frame, orient="vertical", command=self.issue_tree.yview)
            bar.grid(row=1, column=2, sticky="ns")
            self.issue_tree.configure(yscrollcommand=bar.set)
            horizontal = ttk.Scrollbar(self.issue_frame, orient="horizontal", command=self.issue_tree.xview)
            horizontal.grid(row=2, column=0, columnspan=2, sticky="ew")
            self.issue_tree.configure(xscrollcommand=horizontal.set)
            self.issue_tree.bind("<<TreeviewSelect>>", self._locate_issue)
        self.issue_frame.grid(row=1, column=0, sticky="ew")
        self._refresh_issue_list()

    def _locate_issue(self, event=None):
        selection = self.issue_tree.selection()
        if not selection:
            return
        identity, key, message = self._validation_issues[int(selection[0])]
        card = self._card(identity)
        self.status_var.set(message)
        if not card:
            return
        self.selected_id = identity
        self.selected_edge = None
        self._show_inspector(card)
        self._ensure_card_visible(card)
        self.window.update_idletasks()
        widget = self._field_widgets.get((identity, key))
        if widget is not None and widget.winfo_exists():
            # Reveal a Plot tab if the error belongs to a subplot.
            parent = widget.master
            while parent is not self.param_body:
                if isinstance(parent.master, ttk.Notebook):
                    parent.master.add(parent)
                    parent.master.select(parent)
                    break
                parent = parent.master
            self.window.update_idletasks()
            y = widget.winfo_rooty()-self.param_body.winfo_rooty()
            self.param_canvas.yview_moveto(max(0, (y-25)/max(1,self.param_body.winfo_height())))
            widget.focus_set()
            if widget.winfo_class() == "TEntry":
                widget.configure(style="Flow.Invalid.TEntry")

    def _plot_slot_label(self, card, name):
        number = name.removeprefix("slot")
        source = self._card(card.params.get(f"_input_{name}_source"))
        result = card.params.get(f"_input_{name}_output", "")
        return f"{number} · {self._metric_label(result)}" if source and result else f"{number} · Unconnected"

    def _edge_info(self, edge):
        if not edge:
            return None
        identity, key, target_id = edge
        source, target = self._card(identity), self._card(target_id)
        if source is None or target is None:
            return None
        if key in {"_next", "_true", "_false"}:
            if source.params.get(key) != target_id:
                return None
            return source, key[1:], target, "flow"
        if key == "_light_source":
            return (source, "waveform@signal", target, "waveform@light") if target.params.get(key) == identity else None
        if key == "_result_source":
            metric = target.params.get("_result_metric", "")
            inputs = self._node_inputs(target)
            incoming = next((kind for kind, _ in inputs if self._port_type(kind) == "number"), "")
            return (source, f"number@{metric}", target, incoming) if metric and incoming else None
        if key.startswith("_input_") and key.endswith("_source"):
            name = key[7:-7]
            if target.params.get(key) != identity:
                return None
            expected = dict(input_ports(target)).get(name)
            dtype = target.params.get(f"_input_{name}_type", expected)
            metric = target.params.get(f"_input_{name}_output", "")
            return (source, f"{dtype}@{metric}", target, f"{expected}@{name}") if expected and metric else None
        return None

    def _edge_at(self, event):
        point = self.canvas.canvasx(event.x)/self.zoom, self.canvas.canvasy(event.y)/self.zoom
        nearest = None
        for route, _color, _width, tags in getattr(self, "_routed_edges", ()):
            marker = next((t for t in tags if t.startswith("edge:")), None)
            if not marker or len(route) < 2:
                continue
            distance = min(self._point_segment_distance(point, a, b) for a, b in zip(route, route[1:]))
            if distance <= 7/self.zoom and (nearest is None or distance < nearest[0]):
                nearest = distance, tuple(marker.split(":", 3)[1:])
        return nearest[1] if nearest else None

    def _paint_edge_highlight(self, edge=None):
        self.canvas.delete("edge_highlight")
        edge = edge or getattr(self, "selected_edge", None)
        info = self._edge_info(edge)
        if info is None:
            return
        source, kind, target, incoming = info
        route = self._wire_route(source, target, kind, target_kind=incoming)
        if len(route) < 2:
            return
        coords = [v*self.zoom for point in route for v in point]
        self.canvas.create_line(*coords, fill="#f7cc87", width=4, tags="edge_highlight", state="disabled")
        for x, y in (route[0], route[-1]):
            x, y = x*self.zoom, y*self.zoom
            self.canvas.create_oval(x-7,y-7,x+7,y+7,fill="#292a30",outline="#ffda97",width=2,tags="edge_highlight",state="disabled")

    def _hover_edge(self, event):
        edge = self._edge_at(event)
        self._paint_edge_highlight(edge)
        info = self._edge_info(edge)
        if info:
            source, kind, target, incoming = info
            metric = kind.split("@", 1)[-1]
            self.status_var.set(f"{self.definitions[source.type_id].title} [{source.card_id}] / {self._metric_label(metric)} → {self.definitions[target.type_id].title} [{target.card_id}] / {incoming}. Click to select; drag an endpoint to reconnect; Delete removes.")
        return edge

    def _disconnect_edge(self, edge):
        info = self._edge_info(edge)
        if info is None:
            return False
        source, kind, target, incoming = info
        key = edge[1]
        if key in {"_next", "_true", "_false"}:
            source.params.pop(key, None)
        elif key.startswith("_input_"):
            self._clear_input(target, key[:-7])
            if target.type_id == "it" and key == "_input_profile_source" and getattr(self, "_inspected_id", None) == target.card_id:
                self._show_inspector(target)
        else:
            target.params.pop(key, None)
            if key == "_result_source":
                target.params.pop("_result_metric", None)
                if target.params.get("source_node") == source.card_id:
                    target.params.pop("source_node", None)
        return True

    def _begin_rewire_at(self, event):
        info = self._edge_info(self.selected_edge)
        if info is None:
            return False
        source, kind, target, incoming = info
        point = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for endpoint, card, socket, is_input in (("source",source,kind,False), ("target",target,incoming,True)):
            position = self._socket_position(card, socket, incoming=is_input)
            if position and math.dist(point, (position[0]*self.zoom, position[1]*self.zoom)) <= 11:
                self._rewire = (self.selected_edge, endpoint)
                self.pending_edge = (source.card_id, kind)
                self._edge_dragging = True
                self.drag_id = None
                self.status_var.set("Drag to a compatible socket. Esc or release on empty canvas cancels.")
                return True
        return False

    def _output_at(self, event, expected, target_id):
        point = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        nearest = None
        for card in self.cards:
            if card.card_id == target_id:
                continue
            for kind, _label in self._node_outputs(card):
                if not compatible(self._port_type(kind), self._port_type(expected)):
                    continue
                x, y = self._socket_position(card, kind)
                distance = math.dist(point, (x*self.zoom, y*self.zoom))
                if distance <= 14 and (nearest is None or distance < nearest[0]):
                    nearest = distance, card.card_id, kind
        return (nearest[1], nearest[2]) if nearest else None

    def _apply_rewire(self, edge, source_id, kind, target_id, incoming):
        original = self._edge_info(edge)
        if original is not None:
            old_source, old_kind, old_target, old_incoming = original
            if source_id == old_source.card_id and kind != old_kind:
                return False
        before = self._snapshot()
        self._history_group = None
        self._history_suspended = True
        try:
            if not self._disconnect_edge(edge):
                return False
            success = self._connect(source_id, kind, target_id, incoming)
            if not success:
                self._restore_snapshot(before)
        except Exception:
            self._restore_snapshot(before)
            raise
        finally:
            self._history_suspended = False
        self.selected_edge = None
        if success:
            self._changed()
        else:
            self.redraw()
        return bool(success)

    def _finish_rewire(self, event):
        edge, endpoint = self._rewire
        info = self._edge_info(edge)
        replacement = None
        if info:
            source, kind, target, incoming = info
            if endpoint == "target":
                found = self._connection_target_at(event, kind)
                if found:
                    replacement = source.card_id, kind, found[0], found[1]
            else:
                found = self._output_at(event, incoming, target.card_id)
                if found:
                    replacement = found[0], found[1], target.card_id, incoming
        self._rewire = None
        self.pending_edge = None
        self._edge_dragging = False
        if replacement:
            self._apply_rewire(edge, *replacement)
        else:
            self.status_var.set("Reconnect cancelled; original connection retained.")
        self.redraw()
        return "break"

    def _preview_source_rewire(self, event):
        self.canvas.delete("edge_preview")
        info = self._edge_info(self._rewire[0])
        if info is None:
            return "break"
        _, _, target, incoming = info
        found = self._output_at(event, incoming, target.card_id)
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if found:
            x, y = self._socket_position(self._card(found[0]), found[1])
            x, y = x*self.zoom, y*self.zoom
        tx, ty = self._socket_position(target, incoming, incoming=True)
        tx, ty = tx*self.zoom, ty*self.zoom
        self.canvas.create_line(x,y,(x+tx)/2,y,(x+tx)/2,ty,tx,ty, smooth=True,
                                fill="#8ac782" if found else "#c3c6d0", width=3, tags="edge_preview", state="disabled")
        return "break"
