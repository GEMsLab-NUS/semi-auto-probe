from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from .auto_test import AutoTestFlowCard, AutoTestFlowStep, create_autotest_flow_card, measurement_flow_steps_from_cards
from .dp100 import DEFAULT_LASER_PARAMS, DP100Driver, DP100Setpoint, SimulatedDP100Driver
from .flow_editor import MeasurementFlowEditor
from .flow_graph import evaluate_condition, graph_start_node, step_node_id, validate_flow_graph
from .waveform import parse_waveform_points, run_dp100_waveform


COLORS = {
    "bg": "#090d12",
    "surface": "#101720",
    "surface_2": "#151e29",
    "surface_3": "#1c2835",
    "input": "#0b1118",
    "border": "#2a3949",
    "border_focus": "#38bdf8",
    "text": "#e5edf5",
    "muted": "#8da0b3",
    "accent": "#38bdf8",
}


class MeasureDebugApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Semi Auto Probe - Measure Debug")
        self.geometry("920x560")
        self.minsize(760, 480)
        self.configure(bg=COLORS["bg"])
        self.cards: list[AutoTestFlowCard] = []
        self.next_id = 1
        self.driver: DP100Driver | SimulatedDP100Driver | None = None
        self.stop_event = threading.Event()
        self.simulate_var = tk.BooleanVar(value=True)
        self.device_var = tk.StringVar(value="DP100: disconnected")
        self.flow_var = tk.StringVar(value="No measurement graph configured")
        self._configure_style()
        self.designer = MeasurementFlowEditor(
            self,
            self.cards,
            COLORS,
            self.allocate_id,
            self._flow_changed,
            use_parent_window=True,
        )
        self._build_measure_controls()

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 9))
        style.configure("TButton", background=COLORS["surface_3"], foreground=COLORS["text"], bordercolor=COLORS["border"], padding=(10, 7))
        style.map("TButton", background=[("active", "#26384a"), ("pressed", COLORS["input"]), ("disabled", "#111827")], foreground=[("disabled", "#64748b")])
        style.configure("Accent.TButton", background=COLORS["accent"], foreground="#061018", bordercolor=COLORS["accent"])
        style.configure("Danger.TButton", background="#7f1d1d", foreground=COLORS["text"], bordercolor="#991b1b")
        style.configure("TEntry", fieldbackground=COLORS["input"], background=COLORS["input"], foreground=COLORS["text"], insertcolor=COLORS["accent"], bordercolor=COLORS["border"], lightcolor=COLORS["border"], darkcolor=COLORS["border"], padding=(8, 6))
        style.map("TEntry", fieldbackground=[("focus", "#101b27"), ("!disabled", COLORS["input"])], foreground=[("!disabled", COLORS["text"])])
        style.configure("TCombobox", fieldbackground=COLORS["input"], background=COLORS["surface_3"], foreground=COLORS["text"], bordercolor=COLORS["border"], lightcolor=COLORS["border"], darkcolor=COLORS["border"], arrowcolor=COLORS["muted"], padding=(8, 6))
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["input"]), ("focus", "#101b27"), ("!disabled", COLORS["input"])], foreground=[("readonly", COLORS["text"]), ("!disabled", COLORS["text"])], background=[("active", "#26384a"), ("!disabled", COLORS["surface_3"])], arrowcolor=[("active", COLORS["accent"]), ("!disabled", COLORS["muted"])])
        style.configure("Treeview", background=COLORS["input"], fieldbackground=COLORS["input"], foreground=COLORS["text"], bordercolor=COLORS["border"], rowheight=28)
        style.configure("Treeview.Heading", background=COLORS["surface_3"], foreground=COLORS["text"], bordercolor=COLORS["border"], padding=(6, 5))
        style.map("Treeview", background=[("selected", "#0f766e")], foreground=[("selected", "#f8fafc")])
        style.configure("TNotebook", background=COLORS["surface_2"], borderwidth=0)
        style.configure("TNotebook.Tab", background=COLORS["input"], foreground=COLORS["muted"], padding=(12, 7), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", COLORS["surface_3"]), ("active", COLORS["border"])], foreground=[("selected", COLORS["text"]), ("active", COLORS["text"])])
        style.configure("Vertical.TScrollbar", background="#334155", troughcolor=COLORS["surface"], bordercolor=COLORS["surface"], arrowcolor=COLORS["muted"], lightcolor="#334155", darkcolor="#334155")
        self.option_add("*TCombobox*Listbox.background", COLORS["input"])
        self.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", "#0f766e")
        self.option_add("*TCombobox*Listbox.selectForeground", "#f8fafc")

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        header = tk.Frame(self, bg=COLORS["surface"], padx=18, pady=14)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        tk.Label(header, text="Measure Debug", bg=COLORS["surface"], fg=COLORS["text"], font=("Segoe UI Semibold", 17), anchor="w").grid(row=0, column=0, sticky="w")
        tk.Label(header, text="Measurement graph and light-source bench", bg=COLORS["surface"], fg=COLORS["muted"], font=("Segoe UI", 9), anchor="w").grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Button(header, text="Flow Designer", style="Accent.TButton", command=self.open_designer).grid(row=0, column=1, rowspan=2)

        controls = tk.Frame(self, bg=COLORS["surface_2"], padx=14, pady=12)
        controls.grid(row=1, column=0, sticky="ew", padx=14, pady=14)
        controls.columnconfigure(4, weight=1)
        tk.Checkbutton(controls, text="Simulation", variable=self.simulate_var, bg=COLORS["surface_2"], fg=COLORS["text"], selectcolor=COLORS["input"], activebackground=COLORS["surface_2"], activeforeground=COLORS["text"]).grid(row=0, column=0, padx=(0, 10))
        ttk.Button(controls, text="Connect DP100", command=self.connect_dp100).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(controls, text="Run graph", style="Accent.TButton", command=self.run_graph).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(controls, text="Stop", style="Danger.TButton", command=self.stop_event.set).grid(row=0, column=3, padx=(0, 12))
        tk.Label(controls, textvariable=self.device_var, bg=COLORS["surface_2"], fg=COLORS["muted"], anchor="e").grid(row=0, column=4, sticky="e")
        tk.Label(controls, textvariable=self.flow_var, bg=COLORS["surface_2"], fg=COLORS["muted"], anchor="w").grid(row=1, column=0, columnspan=5, sticky="ew", pady=(10, 0))

        log_frame = tk.Frame(self, bg=COLORS["surface"], padx=14, pady=12)
        log_frame.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        tools = tk.Frame(log_frame, bg=COLORS["surface"])
        tools.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tools.columnconfigure(0, weight=1)
        tk.Label(tools, text="Execution log", bg=COLORS["surface"], fg=COLORS["text"], font=("Segoe UI Semibold", 10)).grid(row=0, column=0, sticky="w")
        ttk.Button(tools, text="Load", command=self.load_flow).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(tools, text="Save", command=self.save_flow).grid(row=0, column=2, padx=(6, 0))
        self.log = tk.Text(log_frame, bg="#070b10", fg="#b9cad9", insertbackground=COLORS["accent"], relief="flat", padx=10, pady=10, font=("Cascadia Mono", 9), state="disabled")
        self.log.grid(row=1, column=0, sticky="nsew")
        self._write_log("Measure-only mode ready. Simulation is enabled.")

    def _build_measure_controls(self) -> None:
        header = self.designer.header
        ttk.Button(header, text="New Waveform", command=self.designer.create_waveform_node).grid(row=0, column=1, rowspan=2, padx=(12, 6))
        ttk.Button(header, text="Debug Run", style="Accent.TButton", command=self.run_graph).grid(row=0, column=2, rowspan=2, padx=(0, 6))
        ttk.Button(header, text="Stop", style="Danger.TButton", command=self.stop_event.set).grid(row=0, column=3, rowspan=2, padx=(0, 8))
        self._write_log("Measure-only mode ready. Debug runs use simulated instruments.")

    def allocate_id(self) -> str:
        value = f"flow_{self.next_id}"
        self.next_id += 1
        return value

    def open_designer(self) -> None:
        self.lift()

    def _flow_changed(self) -> None:
        titles = [card.type_id for card in self.cards]
        self.flow_var.set(f"{len(self.cards)} nodes: " + (" -> ".join(titles[:5]) if titles else "not configured"))

    def connect_dp100(self) -> None:
        try:
            if self.simulate_var.get():
                # Per-setpoint callbacks make waveforms unreadable. The runner
                # writes one compact summary after the operation instead.
                self.driver = SimulatedDP100Driver()
            else:
                self.driver = DP100Driver()
            if not self.driver.connect():
                raise RuntimeError("Connection rejected by device.")
            self.device_var.set("DP100: simulation connected" if self.simulate_var.get() else "DP100: connected")
            self._write_log(self.device_var.get())
        except Exception as exc:
            self.device_var.set("DP100: connection failed")
            self._write_log(f"ERROR {exc}")

    def run_graph(self) -> None:
        self.designer._finish_inline_edit()
        if self.designer._collect_issues():
            self.designer._validate()
            self._write_log("INVALID: resolve the issues shown below the canvas before running.")
            return
        steps = measurement_flow_steps_from_cards(self.cards)
        errors = validate_flow_graph(steps)
        if errors:
            self._write_log("INVALID " + " | ".join(errors))
            return
        self.stop_event.clear()
        threading.Thread(target=self._dry_run, args=(steps,), daemon=True).start()

    def _dry_run(self, steps: tuple[AutoTestFlowStep, ...]) -> None:
        if not steps:
            self._write_log("No nodes to run.")
            return
        by_id = {step_node_id(step, index): step for index, step in enumerate(steps)}
        current = graph_start_node(steps)
        previous: str | None = None
        results: dict[str, dict[str, object]] = {}
        visits = 0
        while current and current in by_id and not self.stop_event.is_set():
            visits += 1
            if visits > max(100, len(steps) * 10):
                self._write_log("STOP execution limit reached")
                return
            step = by_id[current]
            self._write_log(f"RUN {current} [{step.type_id}]")
            if step.type_id in {"workflow_start", "workflow_end"}:
                previous = current
                current = str(step.params.get("_next", "")) or None
                continue
            if step.type_id.startswith("condition_"):
                decision = evaluate_condition(step, results, previous, steps)
                self._write_log(f"  branch {'TRUE' if decision else 'FALSE'}")
                current = str(step.params.get("_true" if decision else "_false", "")) or None
                continue
            light_source_id = str(step.params.get("_light_source", "")).strip()
            light_step = by_id.get(light_source_id) if light_source_id else None
            if light_step is not None:
                self._write_log(f"  light input <- {light_source_id}")
                light_worker = threading.Thread(target=self._run_light_step, args=(light_step,), daemon=True)
                light_worker.start()
            elif step.type_id in {"laser_output", "laser_pulse"}:
                self._run_light_step(step)
            results[current] = self._simulated_result(step)
            if light_step is not None:
                light_worker.join()
            previous = current
            current = str(step.params.get("_next", "")) or None
        self._write_log("DONE" if not self.stop_event.is_set() else "STOPPED")

    def _run_light_step(self, step: AutoTestFlowStep) -> None:
        if self.driver is None:
            self.connect_dp100()
        if self.driver is None:
            return
        params = step.params
        point = DP100Setpoint(float(params.get("peak_voltage_v", DEFAULT_LASER_PARAMS["peak_voltage_v"])), float(params.get("peak_current_a", DEFAULT_LASER_PARAMS["peak_current_a"])), int(float(params.get("preset_index", 1))))
        if step.type_id == "laser_output":
            self.driver.configure_limits(
                point, ovp_v=float(params.get("ovp_v", DEFAULT_LASER_PARAMS["ovp_v"])),
                ocp_a=float(params.get("ocp_a", DEFAULT_LASER_PARAMS["ocp_a"])),
            )
            self.driver.set_output(point, str(params.get("output_state", "on")) == "on")
            if "tk" in self.__dict__:
                self._write_log(
                    f"  DP100 {'ON' if str(params.get('output_state', 'on')) == 'on' else 'OFF'} · "
                    f"{point.voltage_v:g} V · {point.current_a * 1000:g} mA"
                )
            return
        points = parse_waveform_points(params)
        run_dp100_waveform(self.driver, params, stop_event=self.stop_event)
        if "tk" in self.__dict__:
            self._write_log(f"  DP100 waveform done · {len(points)} points · {points[-1].time_ms:g} ms")

    @staticmethod
    def _simulated_result(step: AutoTestFlowStep) -> dict[str, object]:
        if step.type_id in {"iv", "hp6614c_transfer", "b1500_transfer"}:
            return {"statistics": {"resistance_ohm": 2.5e6, "max_abs_current_a": 4e-7, "on_off_ratio": 1200, "compliance_reached": False}}
        return {"statistics": {"completed": True}}

    def save_flow(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=(("JSON", "*.json"),))
        if not path:
            return
        payload = [{"type_id": step.type_id, "params": dict(step.params)} for step in measurement_flow_steps_from_cards(self.cards)]
        Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self._write_log(f"Saved {Path(path).name}")

    def load_flow(self) -> None:
        path = filedialog.askopenfilename(filetypes=(("JSON", "*.json"),))
        if not path:
            return
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.cards.clear()
        for index, item in enumerate(payload):
            params = {str(key): str(value) for key, value in item.get("params", {}).items()}
            card_id = params.pop("_node_id", f"flow_{index + 1}")
            card = create_autotest_flow_card(str(item["type_id"]), card_id, saved_params=params)
            card.params.update(params)
            card.x = float(card.params.pop("_x", 100 + index * 270))
            card.y = float(card.params.pop("_y", 100))
            self.cards.append(card)
        self.next_id = len(self.cards) + 1
        self._flow_changed()
        self._write_log(f"Loaded {Path(path).name}")

    def _write_log(self, message: str) -> None:
        def append() -> None:
            log = getattr(self, "log", None)
            if log is None:
                self.designer.status_var.set(message)
                return
            log.configure(state="normal")
            log.insert("end", message + "\n")
            log.see("end")
            log.configure(state="disabled")
        self.after(0, append)


def main() -> None:
    MeasureDebugApp().mainloop()
