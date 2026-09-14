"""Desktop adapter: local preflight, typed execution results, worker completion.

All methods run on the Tk thread. The LLM receives only an AgentContext snapshot.
Worker status prose is diagnostic; it cannot advance a plan.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from uuid import uuid4

from .agent import (
    AGENT_ACTION_AUTOFOCUS, AGENT_ACTION_CHECK_WORKFLOW, AGENT_ACTION_FOCUSMAP,
    AGENT_ACTION_HELP, AGENT_ACTION_IMAGE_CAPTURE, AGENT_ACTION_LAYOUT_OVERLAY,
    AGENT_ACTION_MOVE_GDS, AGENT_ACTION_OPEN_PAGE, AGENT_ACTION_RUN_MEASUREMENT,
    AGENT_ACTION_SINGLE_CAPTURE, AGENT_ACTION_STAGE_MOVE, AGENT_ACTION_STATUS,
    AgentPlan, action_parameter_blockers, canonical_agent_page, context_blockers_for_action,
)
from .agent_knowledge import help_markdown


@dataclass(frozen=True)
class AgentExecutionResult:
    status: str  # running / done / blocked / failed / cancelled
    message: str
    action_id: str = ""
    run_id: str = ""


def inspect_measurement(app):
    """Read UI and validate locally, without connecting hardware or writing files."""
    mode = "wire-bond" if app.probe_config.wire_bond_enabled else "probe"
    summary = {"mode": mode, "workflow_version": 2, "connectivity": "not verified"}
    blockers = []
    fingerprint = ""
    settings = None
    try:
        from .flow_graph import validate_flow_graph
        from .af_plane import get_sample_plane_model
        panel = app.autotest_panel
        if panel is None:
            raise ValueError("AutoTest panel is not available.")
        if mode == "wire-bond":
            settings = replace(app.mapping_panel.settings(), measurement_flow=panel.measurement_flow()).normalized()
        else:
            settings = panel.settings_from_ui().normalized()
        flow = settings.measurement_flow
        if not flow:
            raise ValueError("Configure a measurement workflow in Config Measure Flow.")
        errors = validate_flow_graph(flow)
        blockers.extend(errors)
        summary["nodes"] = [{"type": step.type_id, "parameters": dict(step.params)} for step in flow]
        if mode == "wire-bond":
            from .mapping import generate_mapping_plan
            app._validate_mapping_workflow(settings)
            if app._probe_down_guard_enabled():
                blockers.append("Retract probes and select Separate before mapping.")
            points = generate_mapping_plan(settings, app._mapping_scales()).points
        else:
            mapper = app.gds_stage_mapper_panel.mapper if app.gds_stage_mapper_panel else None
            if mapper is None:
                blockers.append("Bind LayoutMap before Probe AutoTest.")
            if get_sample_plane_model() is None:
                blockers.append("Run or load FocusMap before Probe AutoTest.")
            points = panel._points_for_settings(settings, mapper) if mapper is not None else ()
        summary["point_count"] = len(points)
        if not points:
            blockers.append("No measurement points are available.")
        if any(step.type_id == "photo" for step in flow) and not (app.camera_running and app.latest_stitch_frame is not None):
            blockers.append("Photo node requires an available camera frame.")
        # Include all reviewed settings, geometry and motion configuration. Only
        # the digest is sent; the application config may contain credentials.
        material = repr((asdict(settings), tuple(points), app.probe_config, get_sample_plane_model()))
        fingerprint = hashlib.sha256(material.encode("utf-8")).hexdigest()
        summary["fingerprint"] = fingerprint
    except Exception as exc:
        blockers.append(str(exc))
    return mode, summary, tuple(dict.fromkeys(blockers)), fingerprint, settings


class DesktopAgentRuntime:
    def __init__(self, app):
        self.app = app
        self.active: AgentExecutionResult | None = None
        self.error = ""
        self.saw_output = False
        self.stop_requested = False
        self.move_readback = False

    def execute(self, plan: AgentPlan) -> AgentExecutionResult:
        index = plan.next_pending_step_index()
        if self.active is not None:
            return AgentExecutionResult("blocked", "A previous Agent workflow is still running.")
        if not plan.executable or index is None:
            return AgentExecutionResult("blocked", "No executable pending step.")
        step = plan.steps[index]
        action = step.action_id
        context = self.app.agent_context()
        blockers = (*action_parameter_blockers(action, step.parameters), *context_blockers_for_action(action, context))
        if blockers:
            return AgentExecutionResult("blocked", "; ".join(blockers), action)
        run_id = uuid4().hex
        try:
            if action == AGENT_ACTION_OPEN_PAGE:
                page = canonical_agent_page(step.parameters["page"])
                if page is None:
                    return AgentExecutionResult("blocked", "Unknown application page.", action, run_id)
                self.app.show_page(page)
                return AgentExecutionResult("done", "Opened " + page, action, run_id)
            if action in {AGENT_ACTION_HELP, AGENT_ACTION_STATUS, AGENT_ACTION_CHECK_WORKFLOW}:
                message = help_markdown(str(step.parameters.get("topic", "overview"))) if action == AGENT_ACTION_HELP else json.dumps(context.to_prompt_dict(), ensure_ascii=False, indent=2)
                return AgentExecutionResult("done", message, action, run_id)
            if action == AGENT_ACTION_RUN_MEASUREMENT:
                if step.parameters.get("fingerprint") != context.measurement_fingerprint:
                    return AgentExecutionResult("blocked", "Measurement settings changed. Generate and review a new plan.", action, run_id)
                _, _, errors, fingerprint, settings = inspect_measurement(self.app)
                if errors or fingerprint != context.measurement_fingerprint:
                    return AgentExecutionResult("blocked", "; ".join(errors) or "Measurement settings changed.", action, run_id)
                if context.measurement_mode == "probe":
                    points = self.app.autotest_panel._points_for_settings(settings, self.app.gds_stage_mapper_panel.mapper)
                    self.app.start_autotest(settings, points_override=points)
                else:
                    self.app.start_autotest(settings)
                message = "Started current " + context.measurement_mode + " measurement."
                started = self.app.autotest_running
            else:
                message = self.app.execute_agent_plan(plan)
                if action in {AGENT_ACTION_SINGLE_CAPTURE, AGENT_ACTION_LAYOUT_OVERLAY}:
                    # Legacy synchronous adapter returns diagnostics; only its
                    # explicit success prefix is accepted, never a generic 'saved'.
                    success = message.startswith(("Agent captured one frame:", "Latest image associated"))
                    return AgentExecutionResult("done" if success else "failed", message, action, run_id)
                flag = {AGENT_ACTION_AUTOFOCUS: "autofocus_running", AGENT_ACTION_FOCUSMAP: "af_plane_running", AGENT_ACTION_IMAGE_CAPTURE: "imgstitch_running", AGENT_ACTION_STAGE_MOVE: "motion_busy", AGENT_ACTION_MOVE_GDS: "motion_busy"}.get(action)
                started = bool(flag and getattr(self.app, flag, False))
                if action == AGENT_ACTION_STAGE_MOVE and message.startswith("Agent stage move skipped:"):
                    return AgentExecutionResult("done", message, action, run_id)
            if not started:
                diagnostic = self.app.status_var.get()
                if action == AGENT_ACTION_RUN_MEASUREMENT:
                    panel = self.app.mapping_panel if context.measurement_mode == "wire-bond" else self.app.autotest_panel
                    variable = getattr(panel, "status", None) if context.measurement_mode == "wire-bond" else getattr(panel, "status_var", None)
                    if variable is not None:
                        diagnostic = variable.get()
                return AgentExecutionResult("blocked", "Workflow did not start: " + diagnostic, action, run_id)
            self.active = AgentExecutionResult("running", message, action, run_id)
            self.error, self.saw_output, self.stop_requested, self.move_readback = "", False, False, False
            return self.active
        except Exception as exc:
            return AgentExecutionResult("failed", str(exc), action, run_id)

    def request_stop(self):
        self.stop_requested = True

    def observe(self, event: tuple) -> AgentExecutionResult | None:
        """Called after app event handling, when busy flags have been updated.

The app serializes workflows. run_id correlates the active UI step; legacy
workers have no run_id, so matching action/source and exclusive ownership are
required. Progress messages never count as terminal evidence.
"""
        if self.active is None:
            return None
        kind, action = event[0], self.active.action_id
        errors = {AGENT_ACTION_AUTOFOCUS: "autofocus_error", AGENT_ACTION_FOCUSMAP: "af_plane_error", AGENT_ACTION_IMAGE_CAPTURE: "imgstitch_error", AGENT_ACTION_RUN_MEASUREMENT: "autotest_error"}
        if kind == errors.get(action):
            self.error = str(event[-1])
        terminal = False
        if action in {AGENT_ACTION_STAGE_MOVE, AGENT_ACTION_MOVE_GDS}:
            source = "agent_stage" if action == AGENT_ACTION_STAGE_MOVE else "gds_mapper"
            if kind == "motor_error":
                self.error = str(event[-1])
            if kind == "read_positions" and event[2] == source:
                self.move_readback = True
                expected = event[3] if len(event) > 3 else None
                if expected and any(self.app.current_position_values.get(axis) != value for axis, value in expected.items()):
                    self.error = "Position readback does not match requested targets."
            terminal = kind == "motor_done" and len(event) > 1 and event[1] == source
            if terminal and not self.move_readback and not self.error and not self.stop_requested:
                self.error = "Motion finished without matching position readback."
        elif action == AGENT_ACTION_AUTOFOCUS:
            terminal = kind == "autofocus_done"
            if terminal:
                self.stop_requested |= self.app.autofocus_stop_event.is_set()
        elif action == AGENT_ACTION_FOCUSMAP:
            terminal = kind == "af_plane_done"
            if terminal:
                self.stop_requested |= bool(event[1])
                if not self.app.af_plane_model_stored:
                    self.error = self.error or "No valid FocusMap plane was stored."
        elif action == AGENT_ACTION_IMAGE_CAPTURE:
            if kind == "imgstitch_done":
                self.saw_output = True
            terminal = kind == "imgstitch_finished"
            if terminal:
                self.stop_requested |= self.app.imgstitch_stop_event.is_set()
                if not self.saw_output:
                    self.error = self.error or "Image workflow finished without output."
        elif action == AGENT_ACTION_RUN_MEASUREMENT:
            if kind == "autotest_done":
                self.saw_output = True
            if kind == "mapping_finished":
                terminal = True
                self.saw_output = event[1] == "completed"
                self.stop_requested |= event[1] in {"stopped", "incomplete", "cancelled"}
                self.error = str(event[2] or "")
            elif kind == "autotest_finished" and not self.app.probe_config.wire_bond_enabled:
                terminal = True
            if terminal:
                self.stop_requested |= self.app.autotest_stop_event.is_set()
                if not self.saw_output:
                    self.error = self.error or "Measurement finished without completion evidence."
        if not terminal:
            return None
        status = "cancelled" if self.stop_requested else "failed" if self.error else "done"
        result = replace(self.active, status=status, message=self.error or ("Workflow stopped." if self.stop_requested else "Workflow completed; resources released."))
        self.active = None
        return result
