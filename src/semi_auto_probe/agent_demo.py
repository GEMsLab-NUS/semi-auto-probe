"""Offline Agent smoke runner. Never constructs a hardware client."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .agent import AgentContext, AgentPlanner
from .agent_knowledge import AGENT_TEMPLATES


def demo_context(ready: bool = False) -> AgentContext:
    return AgentContext(
        positions={"X": 0, "Y": 0, "Z": 0}, serial_connected=ready,
        motion_busy=False, keyboard_motion_busy=False, position_read_pending=False,
        camera_running=ready, camera_frame_available=ready,
        autofocus_running=False, focusmap_running=False, imgstitch_running=False,
        gds_target_selected=ready, gds_mapping_ready=ready,
        gds_target_stage_um=(10.0, 20.0) if ready else None,
        focusmap_points=4 if ready else 0, focusmap_has_plane=ready,
        current_page="AI Agent (SIMULATION)",
        measurement_summary={"mode": "probe", "simulation": True, "point_count": 1, "nodes": [{"type": "wait", "parameters": {"seconds": "0.01"}}]},
        measurement_blockers=() if ready else ("Simulation: no measurement configuration is loaded.",),
        measurement_fingerprint="simulation-only" if ready else "",
    )


def preview(context: AgentContext, screenshot: Path | None = None):
    import tkinter as tk
    from tkinter import ttk
    from uuid import uuid4
    from .app import ProbeApp
    from .ui.agent_panel import AgentPanel
    from .agent_runtime import AgentExecutionResult

    root = tk.Tk()
    root.title("AI Agent — OFFLINE SIMULATION — no hardware")
    root.geometry("1400x780+10+10")
    ProbeApp._configure_theme(root)
    ttk.Label(root, text="OFFLINE SIMULATION · No device connection or real execution", style="Title.TLabel").pack(fill="x", padx=10)
    parent = ttk.Frame(root)
    parent.pack(fill="both", expand=True)
    state = {"cancelled": False}

    def execute(plan):
        step = plan.steps[plan.next_pending_step_index()]
        run_id = uuid4().hex
        state["cancelled"] = False
        root.after(800, lambda: panel.apply_execution_result(AgentExecutionResult(
            "cancelled" if state["cancelled"] else "done", "SIMULATED result; no real measurements or files.", step.action_id, run_id)))
        return AgentExecutionResult("running", "SIMULATION: " + step.title, step.action_id, run_id)

    def stop():
        state["cancelled"] = True
        return "SIMULATION: stop requested."

    panel = AgentPanel(parent, root.colors, get_context=lambda: context,
                       get_microscope_preview=lambda: None, plan_instruction=AgentPlanner().plan,
                       execute_plan=execute, cancel_plan=lambda: "SIMULATION: plan cancelled.", stop_task=stop)
    panel._apply_generated_plan(AgentPlanner().plan("/help workflow", context))
    if screenshot is not None:
        def capture():
            from PIL import ImageGrab
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            root.update_idletasks()
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(screenshot)
            root.destroy()
        root.after(1200, capture)
    root.mainloop()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List reusable prompt templates")
    parser.add_argument("--all", action="store_true", help="Plan every template locally; never execute")
    parser.add_argument("--template", choices=[item[0] for item in AGENT_TEMPLATES])
    parser.add_argument("--prompt", help="Plan a custom prompt locally")
    parser.add_argument("--output", type=Path, help="Save plan JSONL explicitly as UTF-8")
    parser.add_argument("--ready", action="store_true", help="Use fictitious ready state, never real hardware")
    parser.add_argument("--preview", action="store_true", help="Open simulated Agent UI")
    parser.add_argument("--screenshot", type=Path, help="With --preview, save the demo window and close")
    args = parser.parse_args()
    if args.preview:
        preview(demo_context(args.ready), args.screenshot)
        return
    if args.list or not (args.template or args.all or args.prompt):
        for key, label, prompt, expected in AGENT_TEMPLATES:
            print(f"{key}: {label}\n  {prompt}\n  {expected}")
        return
    prompts = [(key, prompt) for key, _, prompt, _ in AGENT_TEMPLATES if args.all or key == args.template]
    if args.prompt:
        prompts.append(("custom", args.prompt))
    records = []
    for key, prompt in prompts:
        plan = AgentPlanner().plan(prompt, demo_context(args.ready))
        records.append(json.dumps({"simulation": True, "template": key, "prompt": prompt, "executable_in_simulation": plan.executable, "plan": asdict(plan)}, ensure_ascii=False))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("\n".join(records) + "\n", encoding="utf-8")
    else:
        print("\n".join(records))


if __name__ == "__main__":
    main()
