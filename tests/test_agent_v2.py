from __future__ import annotations

import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_agent_planner import context, FakeVar
from semi_auto_probe.agent import (
    AgentPlanner, AgentPlan, AgentWorkflowStep, OpenAICompatibleLLMPlanner,
    AGENT_ACTION_AUTOFOCUS, AGENT_ACTION_FOCUSMAP, AGENT_ACTION_IMAGE_CAPTURE,
    AGENT_ACTION_OPEN_PAGE, AGENT_ACTION_SINGLE_CAPTURE, AGENT_ACTION_STAGE_MOVE, AGENT_ACTION_RUN_MEASUREMENT,
    AGENT_PAGE_ALIASES, action_parameter_schema, build_agent_capabilities,
    merge_context_blockers, stage_move_parameter_blockers,
)
from semi_auto_probe.agent_knowledge import AGENT_TEMPLATES
from semi_auto_probe.agent_runtime import DesktopAgentRuntime, AgentExecutionResult, inspect_measurement


class AgentContractTests(unittest.TestCase):
    def test_help_questions_never_call_provider_or_execute(self):
        provider = Mock()
        planner = AgentPlanner(provider)
        for instruction in ("如何运行 autofocus？", "what is autofocus", "FocusMap 是什么", "how to move X=10", "/help workflow", "/status", "/check"):
            with self.subTest(instruction=instruction):
                plan = planner.plan(instruction, context())
                self.assertFalse(plan.executable)
                self.assertEqual(plan.steps, ())
                self.assertTrue(plan.reply_markdown)
        provider.plan.assert_not_called()

    def test_every_template_has_expected_behavior(self):
        expected = {"open": ["open_page"], "capture": [AGENT_ACTION_SINGLE_CAPTURE],
                    "focus_capture": [AGENT_ACTION_AUTOFOCUS, AGENT_ACTION_SINGLE_CAPTURE],
                    "stage": [AGENT_ACTION_STAGE_MOVE], "measure": [AGENT_ACTION_RUN_MEASUREMENT]}
        for key, _, prompt, _ in AGENT_TEMPLATES:
            with self.subTest(key=key):
                plan = AgentPlanner().plan(prompt, context(measurement_fingerprint="reviewed"))
                if key in expected:
                    self.assertEqual([step.action_id for step in plan.steps], expected[key])
                    self.assertTrue(plan.executable)
                else:
                    self.assertFalse(plan.executable)

    def test_terminal_failure_and_running_step_block_later_steps(self):
        plan = AgentPlanner().plan("run autofocus then capture one image", context())
        for status in ("running", "failed", "blocked", "cancelled", "unknown"):
            self.assertIsNone(plan.with_step_status(0, status).next_pending_step_index())
        self.assertEqual(plan.with_step_status(0, "done").next_pending_step_index(), 1)

    def test_sequence_preserves_requested_order(self):
        plan = AgentPlanner().plan("capture one image then run autofocus", context())
        self.assertEqual([step.action_id for step in plan.steps], [AGENT_ACTION_SINGLE_CAPTURE, AGENT_ACTION_AUTOFOCUS])

    def test_focusmap_is_not_also_autofocus(self):
        plan = AgentPlanner().plan("run FocusMap", context())
        self.assertEqual([step.action_id for step in plan.steps], [AGENT_ACTION_FOCUSMAP])

    def test_unit_fraction_and_partial_axis_requests_do_not_move(self):
        for prompt in ("move X=10 um", "move X=10微米", "move X 0.5 pulses", "move X=nan Y=10", "move X=1e309", "不要移动 X=10"):
            with self.subTest(prompt=prompt):
                self.assertFalse(AgentPlanner().plan(prompt, context()).executable)

    def test_invalid_pulse_values_and_extra_parameters_are_rejected(self):
        for value in (True, float("nan"), float("inf"), 0.1, 2**31):
            self.assertTrue(stage_move_parameter_blockers({"mode": "relative", "deltas": {"X": value}}))
        self.assertTrue(stage_move_parameter_blockers({"mode": "relative", "axes": ["X"], "deltas": {"X": 10, "Y": 20}}))
        self.assertTrue(stage_move_parameter_blockers({"mode": "zero", "axes": ["X", "BAD"]}))

    def test_local_metadata_cannot_be_downgraded_by_llm(self):
        provider = OpenAICompatibleLLMPlanner(api_key="test", model="test")
        plan = provider._plan_from_payload({"plan": {"steps": [{"action_id": AGENT_ACTION_STAGE_MOVE, "parameters": {"mode": "zero", "axes": ["X"]}, "requires_confirmation": False, "involves_motion": False}]}}, context())
        plan = merge_context_blockers(plan, context())
        self.assertTrue(plan.requires_confirmation)
        self.assertTrue(plan.involves_motion)

    def test_answer_only_llm_response_and_unknown_action(self):
        provider = OpenAICompatibleLLMPlanner(api_key="test", model="test")
        answer = provider._plan_from_payload({"reply_markdown": "Use Config Measure Flow.", "plan": {"steps": []}}, context())
        self.assertFalse(answer.needs_clarification)
        self.assertEqual(answer.steps, ())
        invalid = provider._plan_from_payload({"plan": {"steps": [{"action_id": "raw_serial"}]}}, context())
        self.assertTrue(invalid.needs_clarification)

    def test_dynamic_blockers_clear_but_model_blockers_remain(self):
        plan = AgentPlanner().plan("run autofocus", context(camera_running=False))
        self.assertFalse(plan.executable)
        self.assertTrue(merge_context_blockers(plan, context()).executable)
        step = replace(plan.steps[0], blockers=(*plan.steps[0].blockers, "Clarify focus range"))
        refreshed = merge_context_blockers(replace(plan, steps=(step,)), context())
        self.assertIn("Clarify focus range", refreshed.steps[0].blockers)

    def test_new_workflows_and_probe_contact_block_motion(self):
        for override in ({"autotest_running": True}, {"mapping_running": True}, {"other_busy": True}, {"probe_contact": True}):
            self.assertFalse(AgentPlanner().plan("move X=10", context(**override)).executable)

    def test_catalog_exports_parameter_contract(self):
        catalog = {item.action_id: item.to_prompt_dict() for item in build_agent_capabilities(context())}
        self.assertIn("fingerprint", catalog[AGENT_ACTION_RUN_MEASUREMENT]["parameters_schema"]["required"])
        self.assertIn("oneOf", catalog[AGENT_ACTION_STAGE_MOVE]["parameters_schema"])

    def test_open_page_schema_accepts_aliases_and_plans_visible_page_names(self):
        page_enum = action_parameter_schema(AGENT_ACTION_OPEN_PAGE)["properties"]["page"]["enum"]
        for alias, visible_name in AGENT_PAGE_ALIASES.items():
            with self.subTest(alias=alias):
                self.assertIn(alias, page_enum)
                self.assertIn(visible_name, page_enum)
                plan = AgentPlanner().plan(f"/open {alias}", context())
                self.assertEqual(plan.steps[0].parameters["page"], visible_name)
                self.assertEqual(plan.steps[0].title, f"Open {visible_name}")

        provider = OpenAICompatibleLLMPlanner(api_key="test", model="test")
        plan = provider._plan_from_payload({
            "plan": {"steps": [{
                "action_id": AGENT_ACTION_OPEN_PAGE,
                "title": "Open Settings",
                "parameters": {"page": "Settings"},
            }]}
        }, context())
        self.assertEqual(plan.steps[0].parameters["page"], "Config")
        self.assertEqual(plan.steps[0].title, "Open Config")


class AgentRuntimeTests(unittest.TestCase):
    def make_runtime(self, action=AGENT_ACTION_AUTOFOCUS):
        app = SimpleNamespace(agent_context=lambda: context(), status_var=FakeVar("idle"),
                              autofocus_running=False, af_plane_running=False, imgstitch_running=False,
                              autotest_running=False, motion_busy=False, current_position_values={"X": 0},
                              autofocus_stop_event=threading.Event(), imgstitch_stop_event=threading.Event(),
                              autotest_stop_event=threading.Event(), probe_config=SimpleNamespace(wire_bond_enabled=False))
        runtime = DesktopAgentRuntime(app)
        runtime.active = AgentExecutionResult("running", "Started", action, "run-1")
        return runtime, app

    def test_progress_and_nested_events_cannot_complete_autofocus(self):
        runtime, _ = self.make_runtime()
        for event in (("autofocus_status", "done saved completed"), ("imgstitch_done", "file.png"), ("motor_done",)):
            self.assertIsNone(runtime.observe(event))
        self.assertEqual(runtime.observe(("autofocus_done",)).status, "done")

    def test_failure_latches_until_cleanup(self):
        runtime, _ = self.make_runtime()
        self.assertIsNone(runtime.observe(("autofocus_error", ValueError("bad focus"))))
        self.assertEqual(runtime.observe(("autofocus_done",)).status, "failed")
        self.assertIsNone(runtime.active)

    def test_capture_waits_for_finished_after_file_saved(self):
        runtime, _ = self.make_runtime(AGENT_ACTION_IMAGE_CAPTURE)
        self.assertIsNone(runtime.observe(("imgstitch_done", "file.png")))
        self.assertIsNotNone(runtime.active)
        self.assertEqual(runtime.observe(("imgstitch_finished",)).status, "done")

    def test_stop_is_not_success(self):
        runtime, _ = self.make_runtime()
        runtime.request_stop()
        self.assertEqual(runtime.observe(("autofocus_done",)).status, "cancelled")

    def test_motion_requires_matching_readback_and_release(self):
        runtime, app = self.make_runtime(AGENT_ACTION_STAGE_MOVE)
        app.current_position_values["X"] = 9
        self.assertIsNone(runtime.observe(("read_positions", [], "agent_stage", {"X": 10})))
        self.assertIsNone(runtime.observe(("motor_done",)))
        self.assertEqual(runtime.observe(("motor_done", "agent_stage")).status, "failed")

    def test_stage_stop_calls_existing_emergency_stop_and_waits_for_owner(self):
        from semi_auto_probe.app import ProbeApp
        runtime, app = self.make_runtime(AGENT_ACTION_STAGE_MOVE)
        app.agent_runtime = runtime
        app.imgstitch_running = app.autofocus_running = app.af_plane_running = app.autotest_running = app.edge_trace_running = False
        app.emergency_stop = Mock()
        self.assertIn("Stage", ProbeApp.stop_agent_task(app))
        app.emergency_stop.assert_called_once_with()
        self.assertIsNone(runtime.observe(("motor_done",)))
        self.assertEqual(runtime.observe(("motor_done", "agent_stage")).status, "cancelled")

    def test_mapping_ignores_nested_autotest_finished(self):
        runtime, app = self.make_runtime(AGENT_ACTION_RUN_MEASUREMENT)
        app.probe_config.wire_bond_enabled = True
        self.assertIsNone(runtime.observe(("autotest_finished",)))
        self.assertEqual(runtime.observe(("mapping_finished", "completed", "", "output")).status, "done")

    def test_refused_start_is_blocked_not_running(self):
        runtime, app = self.make_runtime()
        runtime.active = None
        app.execute_agent_plan = Mock(return_value="Agent started autofocus")
        result = runtime.execute(AgentPlanner().plan("run autofocus", context()))
        self.assertEqual(result.status, "blocked")

    def test_measurement_config_change_requires_new_plan(self):
        runtime, app = self.make_runtime()
        runtime.active = None
        app.agent_context = lambda: context(measurement_fingerprint="new")
        app.start_autotest = Mock()
        plan = AgentPlanner().plan("/run measurement", context(measurement_fingerprint="old"))
        self.assertEqual(runtime.execute(plan).status, "blocked")
        app.start_autotest.assert_not_called()

    def test_navigation_uses_existing_page_and_resolves_provider_aliases(self):
        runtime, app = self.make_runtime()
        runtime.active = None
        app.show_page = Mock()

        result = runtime.execute(AgentPlanner().plan("/open AutoTest", context()))
        self.assertEqual(result.status, "done")
        app.show_page.assert_called_once_with("AutoTest")

        for alias, visible_name in AGENT_PAGE_ALIASES.items():
            with self.subTest(alias=alias):
                app.show_page.reset_mock()
                step = AgentWorkflowStep(
                    AGENT_ACTION_OPEN_PAGE, f"Open {alias}", "Navigation",
                    "Open existing application page.", {"page": alias},
                    requires_confirmation=False,
                )
                result = runtime.execute(AgentPlan("Open", alias, "", (step,)))
                self.assertEqual(result.status, "done")
                self.assertEqual(result.message, f"Opened {visible_name}")
                app.show_page.assert_called_once_with(visible_name)

    def test_inspection_missing_ui_is_read_only_and_explained(self):
        app = SimpleNamespace(probe_config=SimpleNamespace(wire_bond_enabled=False), autotest_panel=None)
        _, _, errors, fingerprint, _ = inspect_measurement(app)
        self.assertIn("AutoTest panel is not available.", errors)
        self.assertEqual(fingerprint, "")

    def test_real_mapping_graph_inspection_and_dispatch_without_hardware(self):
        from test_mapping import settings
        from semi_auto_probe.app import ProbeApp
        from semi_auto_probe.config import ProbeConfig
        from semi_auto_probe.measurement_workflow import AutoTestFlowStep
        flow = (
            AutoTestFlowStep("workflow_start", {"_node_id": "start", "_next": "wait"}),
            AutoTestFlowStep("wait", {"_node_id": "wait", "_next": "end", "seconds": "0.01"}),
            AutoTestFlowStep("workflow_end", {"_node_id": "end"}),
        )
        runtime, app = self.make_runtime()
        runtime.active = None
        app.probe_config = ProbeConfig(wire_bond_enabled=True)
        app.autotest_panel = SimpleNamespace(measurement_flow=lambda: flow)
        app.mapping_panel = SimpleNamespace(settings=lambda: settings(measurement_flow=flow))
        app._validate_mapping_workflow = ProbeApp._validate_mapping_workflow
        app._probe_down_guard_enabled = lambda: False
        app._mapping_scales = lambda: (1, 1, 1)
        app.camera_running = False
        app.latest_stitch_frame = None
        mode, summary, errors, fingerprint, _ = inspect_measurement(app)
        self.assertEqual(errors, ())
        self.assertEqual(summary["point_count"], 9)
        self.assertEqual(len(fingerprint), 64)
        app.agent_context = lambda: context(measurement_mode=mode, measurement_summary=summary, measurement_fingerprint=fingerprint)
        app.start_autotest = Mock(side_effect=lambda _settings: setattr(app, "autotest_running", True))
        result = runtime.execute(AgentPlanner().plan("请帮我运行当前测量流程", app.agent_context()))
        self.assertEqual(result.status, "running")
        self.assertEqual(app.start_autotest.call_args.args[0].measurement_flow, flow)

    def test_probe_dispatch_preserves_reviewed_custom_points(self):
        runtime, app = self.make_runtime()
        runtime.active = None
        points, settings, mapper = ("custom-1", "custom-2"), object(), object()
        app.agent_context = lambda: context(measurement_fingerprint="same")
        app.gds_stage_mapper_panel = SimpleNamespace(mapper=mapper)
        app.autotest_panel = SimpleNamespace(_points_for_settings=lambda s, m: points)
        app.start_autotest = Mock(side_effect=lambda *a, **kw: setattr(app, "autotest_running", True))
        with patch("semi_auto_probe.agent_runtime.inspect_measurement", return_value=("probe", {}, (), "same", settings)):
            result = runtime.execute(AgentPlanner().plan("/run measurement", app.agent_context()))
        self.assertEqual(result.status, "running")
        app.start_autotest.assert_called_once_with(settings, points_override=points)


if __name__ == "__main__":
    unittest.main()
