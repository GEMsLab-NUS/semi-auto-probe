from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk


class DeviceStatusPanel(ttk.Frame):
    def __init__(self, parent, *, busy, local_status, addresses):
        super().__init__(parent, style="Panel.TFrame")
        self.busy = busy
        self.local_status = local_status
        self.addresses = addresses
        self.scanning = False
        self.results = queue.Queue()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        header = ttk.Frame(self, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Device connections", font=("Segoe UI", 20, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Connected instruments and application devices", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.refresh_button = ttk.Button(header, text="Refresh devices", style="Accent.TButton", command=self.refresh)
        self.refresh_button.grid(row=0, column=1, rowspan=2, padx=(20, 0))

        toolbar = ttk.Frame(self, style="Panel.TFrame")
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(0, weight=1)
        self.summary = tk.StringVar()
        ttk.Label(toolbar, textvariable=self.summary, font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(toolbar, mode="indeterminate", length=100)
        self.progress.grid(row=0, column=1, padx=12)
        self.status = tk.StringVar(value="VISA not checked · Refresh to scan")
        ttk.Label(toolbar, textvariable=self.status, style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))

        table = ttk.Frame(self, style="Panel.TFrame")
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        style = ttk.Style(self)
        style.configure("Devices.Treeview", rowheight=38, font=("Segoe UI", 10))
        style.configure("Devices.Treeview.Heading", font=("Segoe UI", 10, "bold"))
        self.tree = ttk.Treeview(table, columns=("address", "status"), show="tree headings", height=7,
                                 style="Devices.Treeview", selectmode="browse")
        self.tree.heading("#0", text="Device")
        self.tree.heading("address", text="Connection / address")
        self.tree.heading("status", text="Status")
        self.tree.column("#0", width=270, minwidth=150)
        self.tree.column("address", width=230, minwidth=160)
        self.tree.column("status", width=200, minwidth=170)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.tag_configure("connected", foreground="#16805d")
        self.tree.tag_configure("error", foreground="#d85a5a")
        self.tree.tag_configure("idle", foreground="#858fa0")

        details = ttk.Frame(self, style="Panel.TFrame")
        details.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        details.columnconfigure(0, weight=1)
        ttk.Label(details, text="CONNECTION DETAILS", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(details, text="Copy details", command=self._copy_details).grid(row=0, column=1, sticky="e")
        colors = getattr(self.winfo_toplevel().master, "colors", {})
        self.detail_text = tk.Text(details, height=5, wrap="word", relief="flat", padx=12, pady=10,
                                   font=("Segoe UI", 10), bg=colors.get("surface_2", "#f1f5f9"),
                                   fg=colors.get("text", "#1e293b"))
        self.detail_text.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        detail_scroll = ttk.Scrollbar(details, orient="vertical", command=self.detail_text.yview)
        detail_scroll.grid(row=1, column=2, sticky="ns", pady=(8, 0))
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        self._set_detail("Select a device to view its full identity, address and diagnostic information.")
        ttk.Label(self, text="VISA devices are checked with an identity query. Other rows show app connections. Results are a snapshot.",
                  style="Muted.TLabel", wraplength=740).grid(row=4, column=0, sticky="w", pady=(14, 0))
        self.rows = {}
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self._display(self.local_status())
        self._poll_id = self.after(200, self._poll)
        self.bind("<Destroy>", self._destroy, add="+")

    def _destroy(self, event):
        if event.widget is self:
            self.after_cancel(self._poll_id)

    def _set_detail(self, text):
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", text)
        self.detail_text.configure(state="disabled")

    def _copy_details(self):
        self.clipboard_clear()
        self.clipboard_append(self.detail_text.get("1.0", "end-1c"))

    def _display(self, rows):
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        connected = 0
        attention = 0
        for row in rows:
            state = row["status"]
            if state in {"Connected", "App connected"}:
                tag, symbol = "connected", "●"
                connected += 1
            elif state == "App not connected":
                tag, symbol = "idle", "○"
            else:
                tag, symbol = "error", "!"
                attention += 1
            label = row["device"]
            if state == "Connected" and "," in label:
                label = " · ".join(label.split(",")[:2])
            shown_state = "Identity unverified" if state.startswith("Session open;") else state
            item = self.tree.insert("", "end", text=label, values=(row["address"], f"{symbol}  {shown_state}"), tags=(tag,))
            self.rows[item] = row
        self.summary.set(f"{connected} connected   /   {attention} need attention   /   {len(rows)} listed")
        items = self.tree.get_children()
        if items:
            selected = next((item for item in items if "error" in self.tree.item(item, "tags")), items[0])
            self.tree.selection_set(selected)
            self._select(None)

    def _select(self, _event):
        selected = self.tree.selection()
        if selected and selected[0] in self.rows:
            row = self.rows[selected[0]]
            self._set_detail(f"{row['device']}\nAddress: {row['address'] or '—'}   |   Status: {row['status']}\n\n{row['detail']}")

    def refresh(self):
        if self.scanning or self.busy():
            self.status.set("Measurement is busy. Refresh after it finishes.")
            return
        addresses = tuple(self.addresses())
        self.scanning = True
        self.refresh_button.configure(state="disabled")
        self.status.set("Checking VISA devices…")
        self.progress.start(12)
        self._display(self.local_status())

        def worker():
            try:
                env = os.environ.copy()
                env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("PYTHONPATH", "")
                result = subprocess.run(
                    [sys.executable, "-m", "semi_auto_probe.device_status", *addresses],
                    capture_output=True, text=True, timeout=30, env=env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode:
                    raise RuntimeError(result.stderr.strip() or "Device scan process failed")
                self.results.put((json.loads(result.stdout), ""))
            except Exception as exc:
                self.results.put(([], str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            rows, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.scanning = False
            self.progress.stop()
            self._display(self.local_status() + rows)
            self.status.set(f"Scan failed: {error}" if error else f"Last checked: {time.strftime('%H:%M:%S')}")
        self.refresh_button.configure(state="disabled" if self.scanning or self.busy() else "normal")
        self._poll_id = self.after(200, self._poll)


class DeviceStatusWindow(tk.Toplevel):
    """A reusable, non-modal window; hiding it does not interrupt an active scan."""
    def __init__(self, parent, **callbacks):
        super().__init__(parent)
        self.withdraw()
        self.title("Device connections")
        self.geometry("880x690")
        self.minsize(780, 620)
        self.configure(bg=getattr(parent, "colors", {}).get("surface", "#ffffff"))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.panel = DeviceStatusPanel(self, **callbacks)
        self.panel.configure(padding=24)
        self.panel.grid(row=0, column=0, sticky="nsew")
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.bind("<Escape>", lambda _event: self.withdraw())
        self.update_idletasks()
        x = max(0, parent.winfo_rootx() + (parent.winfo_width() - 880) // 2)
        y = max(0, parent.winfo_rooty() + (parent.winfo_height() - 690) // 2)
        self.geometry(f"+{x}+{y}")

    def show(self):
        self.deiconify()
        self.lift()
        self.focus_set()
