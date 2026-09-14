import threading
import tkinter as tk
import unittest
from tkinter import ttk

from test_agent_planner import context
from semi_auto_probe.app import ProbeApp
from semi_auto_probe.agent import AgentPlanner, AgentPlan
from semi_auto_probe.agent_runtime import AgentExecutionResult
from semi_auto_probe.ui.agent_panel import AgentPanel


class AgentPanelTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        ProbeApp._configure_theme(self.root)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        parent = ttk.Frame(self.root)
        parent.pack(fill="both", expand=True)
        self.executions = []
        self.panel = AgentPanel(parent, self.root.colors, get_context=context,
                                get_microscope_preview=lambda: None,
                                plan_instruction=AgentPlanner().plan,
                                execute_plan=self.execute,
                                cancel_plan=lambda: "Cancelled",
                                stop_task=lambda: "Stopping")

    def execute(self, plan):
        step = plan.steps[plan.next_pending_step_index()]
        self.executions.append(step.action_id)
        return AgentExecutionResult("running", "Started", step.action_id, "test-run")

    def tearDown(self):
        for timer in self.root.tk.call("after", "info"):
            self.panel.parent.after_cancel(timer)
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def plan(self):
        return AgentPlanner().plan("run autofocus then capture one image", context())

    def test_manual_mode_cannot_skip_running_step_and_ignores_status_prose(self):
        self.panel._apply_generated_plan(self.plan())
        self.panel.confirm_next_step()
        self.panel.set_status("Image saved; autofocus done!")
        self.panel.confirm_next_step()
        self.assertEqual(len(self.executions), 1)
        self.assertEqual(self.panel.pending_plan.steps[0].status, "running")
        self.panel.apply_execution_result(AgentExecutionResult("done", "Released", run_id="stale-run"))
        self.assertEqual(self.panel.pending_plan.steps[0].status, "running")
        self.panel.apply_execution_result(AgentExecutionResult("done", "Released", run_id="test-run"))
        self.panel.confirm_next_step()
        self.assertEqual(len(self.executions), 2)

    def test_conversation_only_never_executes(self):
        self.panel.permission_mode_var.set(self.panel.MODE_CHAT)
        self.panel._apply_generated_plan(self.plan())
        self.panel.confirm_next_step()
        self.panel._run_next_step(auto=True)
        self.assertEqual(self.executions, [])

    def test_stop_cancels_remaining_steps_even_before_first_run(self):
        self.panel._apply_generated_plan(self.plan())
        self.panel.stop_current_task()
        self.panel.confirm_next_step()
        self.assertEqual(self.executions, [])
        self.assertIsNone(self.panel.pending_plan.next_pending_step_index())

    def test_cancel_running_keeps_ownership_but_cancels_tail(self):
        self.panel._apply_generated_plan(self.plan())
        self.panel.confirm_next_step()
        self.panel.cancel_pending_plan()
        self.assertEqual(self.panel.active_run_id, "test-run")
        self.assertEqual(self.panel.pending_plan.steps[1].status, "cancelled")
        self.panel.apply_execution_result(AgentExecutionResult("done", "Released", run_id="test-run"))
        self.panel.confirm_next_step()
        self.assertEqual(len(self.executions), 1)

    def test_cancel_discards_late_planner_response(self):
        generation = self.panel.request_generation
        self.panel.cancel_pending_plan()
        self.panel.planning_results.put((generation, self.plan(), None))
        self.panel._poll_planning_results()
        self.assertIsNone(self.panel.pending_plan)

    def test_worker_does_not_read_tk_or_schedule_tk_callbacks(self):
        calls = []
        self.panel.plan_instruction = lambda prompt, snapshot: calls.append((threading.get_ident(), snapshot)) or self.plan()
        snapshot = context()
        worker = threading.Thread(target=self.panel._generate_plan_worker, args=("focus", snapshot, 0))
        worker.start()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertIs(calls[0][1], snapshot)
        self.assertNotEqual(calls[0][0], threading.get_ident())
        self.panel._poll_planning_results()
        self.assertIsNotNone(self.panel.pending_plan)

    def test_planning_exception_reaches_ui(self):
        def fail(*args):
            raise ValueError("planned failure")
        self.panel.plan_instruction = fail
        self.panel._generate_plan_worker("test", context(), 0)
        self.panel._poll_planning_results()
        self.assertIn("planned failure", self.panel.status_var.get())

    def test_template_load_is_not_send_and_help_is_preserved(self):
        self.panel.template_list.selection_set(2)
        self.panel._load_template()
        self.assertEqual(self.panel.input_text.get("1.0", "end").strip(), "/help workflow")
        self.assertEqual(self.executions, [])
        answer = AgentPlanner().plan("/help workflow", context())
        self.panel._apply_generated_plan(answer)
        self.assertEqual(self.panel.phase_var.get(), "Answered")
        self.assertIn("Measurement Workflow v2", self.panel.chat_text.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()
