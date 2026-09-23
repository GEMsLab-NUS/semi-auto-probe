"""Modeless manual instrument UI. All hardware I/O belongs to ManualSession."""
from __future__ import annotations

from collections import deque
import math
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from .instrument_system_view import InstrumentSystemView
from ..manual_autosave import ManualAutoSave
from ..iv_plot import IVPlotBuffer
from ..power_supply_control import SupplySettings, supply_kind

from ..instrument_control import (
    ManualSession, ManualSettings, ManualSweep, StableReading, export_samples, is_keithley2450,
    FUNCTION_LABELS, parse_si_value, format_si_value, range_options,
)


IV_FUNCTION_LABELS = {'voltage': 'Sweep V / Measure I (I-V)', 'current': 'Sweep I / Measure V (I-V)'}


class InstrumentWindow(InstrumentSystemView, tk.Toplevel):
    def __init__(self, parent, *, rows, busy, scanning, refresh, activity, session=None):
        super().__init__(parent)
        self.withdraw()
        self.title('Instrument · Manual test')
        self.geometry('1220x790')
        self.minsize(1060, 710)
        self.rows_callback = rows
        self.busy = busy
        self.scanning = scanning
        self.refresh_callback = refresh
        self.activity = activity
        self.session = session or ManualSession()
        self._init_system()
        self.auto_save = tk.BooleanVar(value=True)
        self._autosaver = None
        self._autosave_enabled = True
        self._autosave_metadata = {}
        self._autosave_offset = 0
        self.samples = []
        self.segments = {}
        self.plot_samples = {}
        self.devices = {}
        self.known_devices = {}
        self._device_snapshot = None
        self.pending = False
        self.supply_type = None
        self.supply_vars = {key: tk.StringVar(value=value) for key, value in
            dict(voltage="0", current="10mA", ovp="3.3", ocp="25mA", preset="1", ocp_enabled="OFF", measure_range="500 mA").items()}
        self.supply_readback = tk.StringVar(value="No status readback")
        self._has_control_view = False
        self._sidebar_expanded = True
        self.closing = False
        self.after_release = None
        self.snapshot = {}
        self._dirty_plot = False
        self._export_events = queue.Queue()
        self._exporting = False
        self.status = tk.StringVar(value='Select a connected instrument to inspect or control it.')
        self.heading = tk.StringVar(value='No instrument selected')
        self.output_text = tk.StringVar(value='OUTPUT —')
        self.voltage = tk.StringVar(value='— V')
        self.current = tk.StringVar(value='— A')
        self.updated = tk.StringVar(value='No measurements')
        self.mode = tk.StringVar(value='voltage')
        self.function = tk.StringVar(value=FUNCTION_LABELS['voltage'])
        self.source_range = tk.StringVar(value='Auto')
        self.measure_range = tk.StringVar(value='Auto')
        self.source_range_label = tk.StringVar(value='Voltage range')
        self.measure_range_label = tk.StringVar(value='Current range')
        self.value = tk.StringVar(value='0')
        self.limit = tk.StringVar(value='10uA')
        self.terminal = tk.StringVar(value='rear')
        self.channel = tk.StringVar(value='1')
        self.interval = tk.StringVar(value='0.5')
        self.nplc = tk.StringVar(value='1')
        self.autozero = tk.StringVar(value='ON')
        self.value_label = tk.StringVar(value='Set voltage (V)')
        self.limit_label = tk.StringVar(value='Current limit (A)')
        self.applied = tk.StringVar(value='Parameters not applied')
        self.segment_choice = tk.StringVar()
        self.paused = tk.BooleanVar(value=False)
        self.follow = tk.BooleanVar(value=True)
        self.topmost = tk.BooleanVar(value=False)
        self.compact = tk.BooleanVar(value=False)
        self.plot_mode = tk.StringVar(value='Time')
        self.iv_x_source = tk.StringVar(value='Measured V')
        self.iv_log_x = tk.BooleanVar(value=False)
        self.iv_log_y = tk.BooleanVar(value=False)
        self.iv_recent_count = tk.StringVar(value="10")
        self.reading_formatters = (StableReading('V'), StableReading('A'))
        self.power_formatter = StableReading('W')
        self.plot_origins = {}
        self.sweep_stop = tk.StringVar(value='1')
        self.sweep_step = tk.StringVar(value='50m')
        self.sweep_delay = tk.StringVar(value='0.02')
        self.sweep_low = tk.StringVar(value='-1')
        self.sweep_direction = tk.StringVar(value='up')
        self.sweep_segments = tk.StringVar(value='1')
        self.sweep_stop_label = tk.StringVar(value='Top (V)')
        self.sweep_low_label = tk.StringVar(value='Low (V)')
        self.sweep_step_label = tk.StringVar(value='Step (V)')
        self._applied_sweep = None
        self._applied_function = None
        self._build(parent)
        self._build_system_controls()
        self._update_range_options()
        self.protocol('WM_DELETE_WINDOW', self.request_close)
        self.bind('<Escape>', lambda _event: self.request_close())
        self.bind('<Destroy>', self._destroyed, add='+')
        self._poll_id = self.after(100, self._poll)
        self._sync_devices()
        self._controls()
        self.detail.grid_remove()
        self.outer.columnconfigure(0, weight=1)
        self.outer.columnconfigure(1, weight=0)
        self.sidebar.columnconfigure(0, weight=1)
        self.minsize(380, 420)
        self.geometry("440x520")
        self.status_label.configure(wraplength=400)

    def _build(self, parent):
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.backend_bases import NavigationToolbar2
        from matplotlib.figure import Figure
        from matplotlib.ticker import EngFormatter
        colors = getattr(parent, 'colors', {})
        surface = colors.get('surface', '#ffffff')
        text = colors.get('text', '#1e293b')
        self.configure(bg=surface)
        outer = self.outer = ttk.Frame(self, padding=10, style='Panel.TFrame')
        outer.pack(fill='both', expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)
        top = self.header = ttk.Frame(outer, style='Panel.TFrame')
        top.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 6))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text='Instrument', style='Panel.TLabel', font=('Segoe UI', 11, 'bold')).grid(row=0, column=0, sticky='w')
        self.refresh_button = ttk.Button(top, text='Refresh', style='Instrument.Control.TButton', command=self.refresh)
        self.refresh_button.grid(row=0, column=2)

        sidebar = self.sidebar = ttk.Frame(outer, style='Panel.TFrame')
        sidebar.grid(row=1, column=0, sticky='nsew', padx=(0, 10))
        sidebar.rowconfigure(1, weight=1)
        ttk.Label(sidebar, text='Devices', style='Section.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 10))
        self.tree = ttk.Treeview(sidebar, columns=('status',), show='tree headings', selectmode='browse', height=8)
        self.tree.heading('#0', text='Device / model')
        self.tree.heading('status', text='State')
        self.tree.column('#0', width=155, minwidth=120, stretch=True)
        self.tree.column('status', width=85, minwidth=65, stretch=False)
        self.tree.grid(row=1, column=0, sticky='nsew')
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self._controls())
        self.tree.bind('<Double-1>', lambda _event: self.open_selected())
        self.tree.bind('<ButtonRelease-1>', self._check_device)
        self.tree.bind('<space>', self._check_device)
        self.open_button = ttk.Button(sidebar, text='Open device →', style='Accent.TButton', command=self.open_selected)
        self.open_button.grid(row=2, column=0, sticky='ew', pady=(12, 6))
        self.release_button = ttk.Button(sidebar, text='Release device / retry OFF', command=self.release)
        self.release_button.grid(row=3, column=0, sticky='ew')
        ttk.Label(sidebar, text='Choose a device, then Open.\nBusy devices are unavailable.', style='Muted.TLabel').grid(row=4, column=0, sticky='w', pady=14)

        self.sidebar_rail = ttk.Frame(outer)
        ttk.Button(self.sidebar_rail, text='›', width=2, command=self.toggle_sidebar).pack(anchor='n')
        self.sidebar_toggle = ttk.Button(sidebar, text='‹ Hide', command=self.toggle_sidebar)
        self.sidebar_toggle.grid(row=0, column=0, sticky='e')
        self.sidebar_toggle.grid_remove()

        detail = self.detail = ttk.Frame(outer, style='Panel.TFrame')
        detail.grid(row=1, column=1, sticky='nsew')
        detail.columnconfigure(1, weight=1)
        detail.rowconfigure(1, weight=1)
        self.device_heading = ttk.Label(top, textvariable=self.heading, style='Panel.TLabel', font=('Segoe UI', 10), width=1, wraplength=0)
        self.device_heading.grid(row=0, column=1, sticky='ew', padx=14)
        form = self.form = ttk.LabelFrame(detail, text='Source & measurement', padding=(10, 8), style='Instrument.TLabelframe')
        form.grid(row=1, column=0, sticky='ns', padx=(0, 10))
        style = ttk.Style(self)
        style.configure('Instrument.TEntry', padding=(7, 2))
        style.configure('Instrument.Control.TButton', padding=(6, 3))
        style.configure('Instrument.Accent.TButton', padding=(6, 3))
        style.configure('Instrument.Danger.TButton', padding=(6, 3))
        style.configure('Instrument.TCombobox', padding=(7, 2))
        self.editors = []
        self._field_labels = []
        self.mode_box = self._combo(form, 'Function', self.function, tuple(FUNCTION_LABELS.values()) + tuple(IV_FUNCTION_LABELS.values()))
        self.mode_box.configure(width=31)
        self.mode_box.bind('<<ComboboxSelected>>', self._mode_changed)
        terminal_col, channel_col = self._field_pair(form)
        self.terminal_box = self._combo(terminal_col, 'Output terminal', self.terminal, ('rear', 'front'))
        self.terminal_box.configure(width=14)
        self.terminal_box.bind('<<ComboboxSelected>>', lambda _event: self._update_range_options())
        self.nplc_box = self._combo(channel_col, 'NPLC (0.01-10)', self.nplc, ('0.01', '0.1', '0.5', '1', '2', '5', '10'))
        self.nplc_box.configure(width=12)
        value_col, source_col = self._field_pair(form)
        self.value_entry = self._entry(value_col, self.value_label, self.value)
        self.value_entry.configure(width=14)
        self.source_range_box = self._combo(source_col, self.source_range_label, self.source_range, ('Auto',))
        self.source_range_box.configure(width=12)
        limit_col, measure_col = self._field_pair(form)
        self.limit_entry = self._entry(limit_col, self.limit_label, self.limit)
        self.limit_entry.configure(width=14)
        self.measure_range_box = self._combo(measure_col, self.measure_range_label, self.measure_range, ('Auto',))
        self.measure_range_box.configure(width=12)
        self.measure_range_box.bind('<<ComboboxSelected>>', self._measure_range_changed)
        zero_row = ttk.Frame(form)
        zero_row.pack(fill='x', pady=(5, 0))
        ttk.Label(zero_row, text='Auto zero').pack(side='left', padx=(0, 8))
        self.autozero_box = ttk.Combobox(zero_row, textvariable=self.autozero,
                                       values=('ON', 'OFF'), state='readonly', width=6,
                                       style='Instrument.TCombobox')
        self.autozero_box.pack(side='left')
        self.editors.append(self.autozero_box)
        ttk.Label(zero_row, text='n · µ · m · k', style='Muted.TLabel').pack(side='right')
        self.sweep_fields = ttk.Frame(form)
        top_col, low_col = self._field_pair(self.sweep_fields)
        self._entry(top_col, self.sweep_stop_label, self.sweep_stop).configure(width=14)
        self._entry(low_col, self.sweep_low_label, self.sweep_low).configure(width=12)
        direction_col, segments_col = self._field_pair(self.sweep_fields)
        self._combo(direction_col, 'Initial direction', self.sweep_direction, ('up', 'down')).configure(width=14)
        self._entry(segments_col, tk.StringVar(value='Segments'), self.sweep_segments).configure(width=12)
        step_col, delay_col = self._field_pair(self.sweep_fields)
        self._entry(step_col, self.sweep_step_label, self.sweep_step).configure(width=14)
        self._entry(delay_col, tk.StringVar(value='Settling time (s)'), self.sweep_delay).configure(width=12)
        self.apply_button = ttk.Button(form, text='Apply parameters', style='Instrument.Accent.TButton', command=self.apply)
        self.apply_button.pack(fill='x', pady=(5, 3))
        self.applied_label = ttk.Label(form, textvariable=self.applied, wraplength=280, style='Muted.TLabel')
        self.applied_label.pack(anchor='w', pady=(0, 6))
        self.manual_actions = ttk.Frame(form)
        self.manual_actions.pack(fill='x')
        output = ttk.Frame(self.manual_actions)
        output.pack(fill='x')
        self.on_button = ttk.Button(output, text='Output ON', style='Instrument.Control.TButton', command=lambda: self._send('on'))
        self.on_button.pack(side='left', expand=True, fill='x', padx=(0, 4))
        self.off_button = ttk.Button(output, text='OFF', style='Instrument.Danger.TButton', command=lambda: self._send('off'))
        self.off_button.pack(side='left', expand=True, fill='x')
        ttk.Separator(self.manual_actions).pack(fill='x', pady=2)
        interval_col, once_col = self._field_pair(self.manual_actions)
        self._entry(interval_col, tk.StringVar(value='Period s (0=fast)'), self.interval).configure(width=14)
        ttk.Label(once_col, text='Single reading').pack(anchor='w', pady=(2, 1))
        self.once_button = ttk.Button(once_col, text='Measure once', style='Instrument.Control.TButton', command=lambda: self._send('once'))
        self.once_button.pack(fill='x')
        acquire_row = ttk.Frame(self.manual_actions)
        acquire_row.pack(fill='x', pady=2)
        self.start_button = ttk.Button(acquire_row, text='Start continuous', style='Instrument.Control.TButton', command=self.start)
        self.start_button.pack(side='left', fill='x', expand=True, padx=(0, 4))
        self.stop_button = ttk.Button(acquire_row, text='Stop', style='Instrument.Control.TButton', command=lambda: self._send('stop'))
        self.stop_button.pack(side='left', fill='x', expand=True)
        ttk.Label(self.manual_actions, text='Stop pauses readings · OFF stops output', wraplength=280, style='Muted.TLabel').pack(anchor='w', pady=(4, 0))

        self.sweep_actions = ttk.Frame(form)
        self.iv_button = ttk.Button(self.sweep_actions, text='Start I-V (output ON)', style='Instrument.Accent.TButton', command=self.start_iv)
        self.iv_button.pack(side='left', fill='x', expand=True)
        self.iv_stop_button = ttk.Button(self.sweep_actions, text='Stop / OFF', style='Instrument.Danger.TButton', command=lambda: self._send('off'))
        self.iv_stop_button.pack(side='left', padx=(4, 0))

        self.smu_form_children = [w for w in form.winfo_children()
                                  if w not in (self.apply_button, self.applied_label, self.manual_actions, self.sweep_actions)]
        self.supply_fields = ttk.Frame(form)
        self.supply_editors = []
        self.supply_optional_rows = {}
        for key, label, options in (
            ('voltage', 'Set voltage (V)', None), ('current', 'Current limit (A)', None),
            ('ovp', 'OVP (V)', None), ('ocp', 'OCP (A)', None),
            ('preset', 'Preset (saved on Apply)', tuple(str(i) for i in range(1, 10))),
            ('ocp_enabled', 'HP OCP trip enable', ('OFF', 'ON')),
            ('measure_range', 'Measure I range', ('20 mA', '500 mA'))):
            row = ttk.Frame(self.supply_fields)
            row.pack(fill='x', pady=3)
            ttk.Label(row, text=label).pack(side='left')
            widget = (ttk.Combobox(row, textvariable=self.supply_vars[key], values=options, state='readonly', width=12)
                      if options else ttk.Entry(row, textvariable=self.supply_vars[key], width=14))
            widget.pack(side='right', padx=(6, 0))
            self.supply_editors.append(widget)
            self.supply_optional_rows[key] = row
        self.supply_note = ttk.Label(self.supply_fields, wraplength=280, style='Muted.TLabel')
        self.supply_note.pack(fill='x', pady=4)
        self.supply_status_label = ttk.Label(self.supply_fields, textvariable=self.supply_readback, wraplength=280, style='Muted.TLabel')
        self.supply_status_label.pack(fill='x', pady=3)
        self.clear_protection_button = ttk.Button(self.supply_fields, text='OFF / Clear HP protection',
            command=lambda: self._send('clear_protection'))

        display = self.display = ttk.Frame(detail, style='Panel.TFrame')
        display.grid(row=1, column=1, sticky='nsew')
        display.columnconfigure(0, weight=1)
        display.rowconfigure(3, weight=1)
        monitor_header = ttk.Frame(display, style='Panel.TFrame')
        monitor_header.grid(row=0, column=0, sticky='ew')
        self.output_label = ttk.Label(monitor_header, textvariable=self.output_text, style='Panel.TLabel', font=('Segoe UI', 10, 'bold'))
        monitor_tools = ttk.Frame(monitor_header)
        monitor_tools.pack(side='right')
        self.output_label.pack(side='left', fill='x', expand=True)
        # Place in the existing 4 px gap above the reading cards: no grid row
        # or requested height is added, including in Compact mode.
        self.sweep_progress_track = tk.Frame(display, bg='#23354a', height=2, borderwidth=0)
        self.sweep_progress_fill = tk.Frame(self.sweep_progress_track, bg='#3989de', borderwidth=0)
        self.sweep_progress_fill.place(x=0, y=0, relheight=1, relwidth=0)
        self._progress_header = monitor_header
        self._progress_payload = {}
        monitor_header.bind('<Configure>', lambda _event: self._position_sweep_progress())
        ttk.Checkbutton(monitor_tools, text='Pin', variable=self.topmost, command=self.set_topmost).pack(side='left')
        ttk.Checkbutton(monitor_tools, text='Compact', variable=self.compact, command=self.set_compact).pack(side='left')
        self.monitor_stop = ttk.Button(monitor_tools, text='Stop', width=4, style='Instrument.Control.TButton', command=lambda: self._send('stop'))
        self.monitor_stop.pack(side='left')
        self.monitor_off = ttk.Button(monitor_tools, text='OFF', width=4, style='Instrument.Danger.TButton', command=lambda: self._send('off'))
        self.monitor_off.pack(side='left')
        self.iv_x_box = ttk.Combobox(monitor_tools, textvariable=self.iv_x_source,
                                     values=('Measured V', 'Set V'), state='readonly', width=12)
        self.iv_x_box.pack(side='left', padx=(6, 0))
        self.iv_x_box.bind('<<ComboboxSelected>>', lambda _event: self._iv_x_changed())
        readings = ttk.Frame(display, style='Panel.TFrame')
        readings.grid(row=1, column=0, sticky='ew', pady=(4, 4))
        for col, (name, var, color) in enumerate((('MEASURED VOLTAGE', self.voltage, '#3989de'), ('MEASURED CURRENT', self.current, '#20a88a'))):
            readings.columnconfigure(col, weight=1)
            card = ttk.Frame(readings, padding=(12, 7), style='Instrument.Card.TFrame')
            card.grid(row=0, column=col, sticky='ew', padx=(0, 4) if col == 0 else (4, 0))
            ttk.Label(card, text=name, style='Instrument.CardMuted.TLabel').pack(anchor='w')
            ttk.Label(card, textvariable=var, style='Instrument.CardValue.TLabel', foreground=color, font=('Consolas', 21, 'bold'), width=13).pack(anchor='w', pady=(2, 0))
        ttk.Label(display, textvariable=self.updated, style='Muted.TLabel').grid(row=2, column=0, sticky='w', pady=(0, 6))
        self.figure = Figure(figsize=(5, 4), dpi=100, facecolor=surface)
        self.axes = self.figure.subplots(2, 1, sharex=True)
        self.lines = []
        for ax, label, color in zip(self.axes, ('Voltage (V)', 'Current (A)'), ('#3989de', '#20a88a')):
            ax.set_facecolor(surface)
            ax.set_ylabel(label, color=text, fontsize=9)
            ax.yaxis.set_major_formatter(EngFormatter(unit='V' if label.startswith('Voltage') else 'A', sep=' '))
            ax.tick_params(colors=text, labelsize=8)
            ax.grid(True, alpha=0.18)
            for spine in ax.spines.values():
                spine.set_color('#8391a2')
            self.lines.append(ax.plot([], [], color=color, linewidth=1.4, marker='.', markersize=3)[0])
        self.iv_axis = self.figure.add_subplot(111, visible=False)
        self.iv_axis.set_facecolor(surface)
        self.iv_axis.set_xlabel('Measured voltage (V)', color=text)
        self.iv_axis.set_ylabel('Measured current (A)', color=text)
        self.iv_axis.tick_params(colors=text, labelsize=8)
        self.iv_axis.grid(True, alpha=.18)
        self.iv_axis.xaxis.set_major_formatter(EngFormatter(unit='V', sep=' '))
        self.iv_axis.yaxis.set_major_formatter(EngFormatter(unit='A', sep=' '))
        self.iv_line, = self.iv_axis.plot([], [], color='#20a88a', marker='.', markersize=3)
        self.iv_history_lines = []
        from matplotlib.collections import LineCollection
        self.iv_recent_line = LineCollection([], linewidths=2, zorder=201.5)
        self.iv_axis.add_collection(self.iv_recent_line)
        self.iv_recent = self.iv_axis.scatter([], [], s=24, zorder=202)
        self.iv_current, = self.iv_axis.plot([], [], linestyle='none', marker='o',
            markersize=9, markerfacecolor='none', markeredgecolor='#3498ff',
            markeredgewidth=2, zorder=203)
        self.iv_line.set_zorder(201)
        self.axes[1].set_xlabel('Elapsed time (s)', color=text, fontsize=9)
        self.figure.subplots_adjust(left=0.15, right=0.98, top=0.97, bottom=0.13, hspace=0.17)
        for axis in (*self.axes, self.iv_axis):
            axis.spines['top'].set_visible(False)
            axis.spines['right'].set_visible(False)
            axis.spines['left'].set_color(colors.get('border', '#263545'))
            axis.spines['bottom'].set_color(colors.get('border', '#263545'))
            axis.grid(True, color=colors.get('muted', '#8fa0b3'), alpha=.13, linewidth=.6)
        self.canvas = FigureCanvasTkAgg(self.figure, master=display)
        self.canvas.get_tk_widget().grid(row=3, column=0, sticky='nsew')
        self.canvas.mpl_connect('resize_event', self._fit_plot_margins)
        # Keep Matplotlib's navigation state machinery without a toolbar widget.
        self.navigation = NavigationToolbar2(self.canvas)
        graph_controls = ttk.Frame(display)
        graph_controls.grid(row=5, column=0, sticky='ew', pady=3)
        self.log_x_button = ttk.Checkbutton(graph_controls, text='X log', state='disabled', variable=self.iv_log_x, command=self._iv_x_changed)
        self.log_x_button.pack(side='left')
        self.log_y_button = ttk.Checkbutton(graph_controls, text='Y log', state='disabled', variable=self.iv_log_y, command=self._iv_x_changed)
        self.log_y_button.pack(side='left')
        ttk.Checkbutton(graph_controls, text='Pause plot', variable=self.paused, command=self._plot_option).pack(side='left')
        ttk.Checkbutton(graph_controls, text='Auto scroll', variable=self.follow, command=self._plot_option).pack(side='left', padx=8)
        ttk.Label(graph_controls, text='Fresh points').pack(side='left')
        self.fresh_box = ttk.Spinbox(graph_controls, from_=0, to=1000, width=4,
            textvariable=self.iv_recent_count, command=self._plot_option)
        self.fresh_box.pack(side='left', padx=3)
        self.iv_recent_count.trace_add('write', lambda *_: self._plot_option())
        ttk.Button(graph_controls, text='Resume live', command=self.resume).pack(side='right')
        history = ttk.Frame(display)
        ttk.Label(history, text='Run', style='Muted.TLabel').pack(side='left', padx=(0, 6))
        history.grid(row=6, column=0, sticky='ew')
        self.segment_box = ttk.Combobox(history, textvariable=self.segment_choice, state='readonly', width=12)
        self.segment_box.pack(side='left', fill='x', expand=True)
        self.segment_box.bind('<<ComboboxSelected>>', lambda _event: self._select_segment())
        self.plot_mode_box = ttk.Combobox(history, textvariable=self.plot_mode, values=('Time', 'I-V'), state='readonly', width=5)
        self.plot_mode_box.pack(side='left', padx=4)
        self.plot_mode_box.bind('<<ComboboxSelected>>', lambda _event: self._change_plot_mode())
        ttk.Button(history, text='Replot', command=self.replot).pack(side='left', padx=5)
        self.export_button = ttk.Button(history, text='Export CSV', command=self.export)
        self.export_button.pack(side='left')
        self.autosave_check = ttk.Checkbutton(history, text='AutoSave', variable=self.auto_save, command=self._toggle_autosave)
        self.autosave_check.pack(side='left', padx=(5, 0))
        self.status_label = ttk.Label(outer, textvariable=self.status, wraplength=1100, style='Muted.TLabel')
        self.status_label.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(6, 0))
        self._style_window(colors)
        self.bind('<Configure>', self._resize_status, add='+')

    def _style_window(self, colors):
        """Scoped styles: keep the application's other windows untouched."""
        style = ttk.Style(self)
        surface = colors.get('surface', '#111821')
        card = colors.get('surface_2', '#151f2b')
        border = colors.get('border', '#263545')
        text = colors.get('text', '#e5edf5')
        muted = colors.get('muted', '#8fa0b3')
        style.configure('Instrument.TFrame', background=surface)
        style.configure('Instrument.Card.TFrame', background=card)
        style.configure('Instrument.TLabel', background=surface, foreground=text, font=('Segoe UI', 9))
        style.configure('Instrument.Muted.TLabel', background=surface, foreground=muted, font=('Segoe UI', 9))
        style.configure('Instrument.CardMuted.TLabel', background=card, foreground=muted, font=('Segoe UI', 9))
        style.configure('Instrument.CardValue.TLabel', background=card, foreground=text)
        style.configure('Instrument.TLabelframe', background=surface, bordercolor=border, relief='solid', borderwidth=1)
        style.configure('Instrument.TLabelframe.Label', background=surface, foreground=muted, font=('Segoe UI Semibold', 9))
        for name, bg, fg, hover in (
            ('Control', '#1b2735', text, '#26384e'),
            ('Accent', '#245c9e', '#eff6ff', '#2f70b8'),
            ('Danger', '#4b2030', '#ffb8c7', '#69283c')):
            key = f'Instrument.{name}.TButton'
            style.configure(key, background=bg, foreground=fg, bordercolor=bg,
                            padding=(8, 4), font=('Segoe UI', 9), relief='flat')
            style.map(key, background=[('disabled', card), ('pressed', bg), ('active', hover)],
                      foreground=[('disabled', '#62748b'), ('!disabled', fg)],
                      bordercolor=[('focus', '#60a5fa'), ('!disabled', bg)])
        for key in ('Instrument.TEntry', 'Instrument.TCombobox', 'Instrument.TSpinbox'):
            style.configure(key, padding=(6, 3), font=('Segoe UI', 9))
        self._style_checkboxes(style, surface)
        style.configure('Instrument.TCheckbutton', background=surface, foreground=muted,
                        padding=(3, 3), font=('Segoe UI', 9))
        style.map('Instrument.TCheckbutton', background=[('active', surface)],
                  foreground=[('disabled', '#62748b'), ('selected', '#dbeafe'), ('active', text)])
        style.configure('Instrument.Treeview', background=surface, fieldbackground=surface,
                        foreground=text, rowheight=32, borderwidth=0, font=('Segoe UI', 9))
        style.map('Instrument.Treeview', background=[('selected', '#193d64')], foreground=[('selected', '#dbeafe')])
        for state, bg, fg in (('On', '#40351c', '#f5cb74'), ('Off', card, muted), ('Unknown', '#4b2030', '#ffb8c7')):
            style.configure(f'Instrument.Output{state}.TLabel', background=bg, foreground=fg,
                            padding=(7, 4), font=('Segoe UI Semibold', 9))
        def apply(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Frame) and child.cget('style') != 'Instrument.Card.TFrame':
                    child.configure(style='Instrument.TFrame')
                elif isinstance(child, ttk.Label) and not str(child.cget('style')).startswith('Instrument.Card'):
                    child.configure(style='Instrument.Muted.TLabel' if child.cget('style') in ('Muted.TLabel', 'Section.TLabel') else 'Instrument.TLabel')
                elif isinstance(child, ttk.Button) and not str(child.cget('style')).startswith('Instrument.'):
                    child.configure(style='Instrument.Control.TButton')
                elif isinstance(child, ttk.Checkbutton):
                    child.configure(style='Instrument.TCheckbutton')
                elif isinstance(child, ttk.Combobox):
                    child.configure(style='Instrument.TCombobox')
                elif isinstance(child, ttk.Spinbox):
                    child.configure(style='Instrument.TSpinbox')
                elif isinstance(child, ttk.Entry):
                    child.configure(style='Instrument.TEntry')
                apply(child)
        apply(self.outer)
        self.tree.configure(style='Instrument.Treeview')
        self.open_button.configure(style='Instrument.Accent.TButton')
        self.start_button.configure(style='Instrument.Accent.TButton')
        self.output_label.configure(style='Instrument.OutputOff.TLabel', font=('Segoe UI Semibold', 9))

    def _style_checkboxes(self, style, surface):
        """Small rounded indicator, retaining ttk focus, hit targets and bindings."""
        from PIL import Image, ImageDraw, ImageTk
        root = self._root()
        element = 'Instrument.Check.indicator'
        if element not in style.element_names():
            size = max(12, round(14 * float(self.tk.call('tk', 'scaling')) / (96 / 72)))
            scale = 4
            def indicator(fill, outline, checked=False, ink='#ffffff'):
                image = Image.new('RGBA', ((size + 6) * scale, size * scale))
                draw = ImageDraw.Draw(image)
                # Draw at 4x resolution for a clean outline and check at small sizes.
                def xy(value):
                    return round(value * size / 14 * scale)
                draw.rounded_rectangle((xy(.5), xy(.5), xy(13.5), xy(13.5)),
                    radius=xy(3), fill=fill, outline=outline, width=max(1, xy(1)))
                if checked:
                    draw.line([(xy(3.5), xy(7)), (xy(6), xy(9.5)), (xy(10.5), xy(4.5))],
                              fill=ink, width=xy(1.7), joint='curve')
                return ImageTk.PhotoImage(image.resize((size + 6, size), Image.Resampling.LANCZOS), master=root)
            off = indicator(surface, '#566a80')
            hover = indicator('#17283b', '#67a7ef')
            on = indicator('#327dcc', '#327dcc', True)
            on_hover = indicator('#4292e1', '#4292e1', True)
            disabled = indicator(surface, '#334255')
            disabled_on = indicator('#263d55', '#334f6b', True, '#8294a8')
            # The element is shared by this Tcl interpreter, so keep images alive
            # on the root even when the Instrument window is destroyed/reopened.
            root._instrument_check_images = (off, hover, on, on_hover, disabled, disabled_on)
            style.element_create(element, 'image', off,
                ('disabled', 'selected', disabled_on), ('disabled', disabled),
                ('selected', 'active', on_hover), ('selected', 'focus', on_hover),
                ('selected', on), ('active', hover), ('focus', hover))
        style.layout('Instrument.TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
            (element, {'side': 'left', 'sticky': ''}),
            ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
                ('Checkbutton.label', {'sticky': 'nswe'})]})]})])

    def _fit_plot_margins(self, event):
        # Reserve pixels for tick labels instead of shrinking their margin with
        # the window: compact mode must keep the time-axis title readable.
        self.figure.subplots_adjust(left=min(.24, 88 / max(1, event.width)),
                                    right=.98, top=.97,
                                    bottom=min(.28, 52 / max(1, event.height)), hspace=.22)
        self.canvas.draw_idle()

    def _resize_status(self, event):
        if event.widget is self:
            self.status_label.configure(wraplength=max(320, event.width - 32))

    def _configure_device_form(self, identity):
        self.supply_type = supply_kind(identity)
        self.function.set(FUNCTION_LABELS['voltage'])
        self.mode.set('voltage')
        self._mode_changed()
        self.supply_fields.pack_forget()
        if self.supply_type:
            for widget in self.smu_form_children:
                widget.pack_forget()
            self.sweep_actions.pack_forget()
            self.manual_actions.pack(fill='x')
            self.form.configure(text='DP100 · Output settings' if self.supply_type == 'dp100' else 'HP 6614C · Output settings')
            self.supply_fields.pack(fill='x', before=self.apply_button)
            for key, row in self.supply_optional_rows.items():
                row.pack_forget()
            for key in ('voltage', 'current', 'ovp') + (('ocp', 'preset') if self.supply_type == 'dp100' else ('ocp_enabled', 'measure_range')):
                self.supply_optional_rows[key].pack(fill='x', pady=2, before=self.supply_note)
            self.clear_protection_button.pack_forget()
            if self.supply_type == 'hp6614c':
                self.clear_protection_button.pack(fill='x')
            self.supply_note.configure(text=('0–30 V / 0–5 A / 100 W; whole mV/mA. Apply requires output OFF.'
                if self.supply_type == 'dp100' else '0–100 V / 0–500 mA. Apply requires output OFF. No NPLC or source-range control.'))
            self.supply_readback.set('Waiting for device status…')
        else:
            self.form.configure(text='Source & measurement')
            for widget in self.smu_form_children:
                widget.pack(fill='x', before=self.apply_button)
            self._mode_changed()

    def toggle_sidebar(self):
        if not self._has_control_view:
            return
        self._sidebar_expanded = not self._sidebar_expanded
        self._layout_sidebar()

    def _layout_sidebar(self):
        self.sidebar.grid_remove()
        self.sidebar_rail.grid_remove()
        if self.compact.get():
            return
        if self._sidebar_expanded:
            self.sidebar.grid()
        else:
            self.sidebar_rail.grid(row=1, column=0, sticky='ns', padx=(0, 4))

    def _show_control_view(self):
        first_open = not self._has_control_view
        self._has_control_view = True
        self._sidebar_expanded = False
        self.sidebar_toggle.grid()
        self.detail.grid()
        self.outer.columnconfigure(0, weight=0)
        self.outer.columnconfigure(1, weight=1)
        self._layout_sidebar()
        if first_open:
            self.minsize(1060, 710)
            self.geometry('1120x760')
            self.status_label.configure(wraplength=1050)

    def set_topmost(self):
        self.attributes('-topmost', self.topmost.get())

    def set_compact(self):
        if self.compact.get():
            self._full_geometry = self.geometry()
            for widget in (self.header, self.sidebar, self.sidebar_rail, self.form, self.device_heading):
                widget.grid_remove()
            self.detail.grid_configure(column=0, columnspan=2)
            self.display.grid_configure(column=0, columnspan=2)
            self.outer.configure(padding=8)
            self.status_label.configure(wraplength=580)
            self.minsize(620, 500)
            self.geometry('680x560')
        else:
            for widget in (self.header, self.form, self.device_heading):
                widget.grid()
            self._layout_sidebar()
            self.detail.grid_configure(column=1, columnspan=1)
            self.display.grid_configure(column=1, columnspan=1)
            self.outer.configure(padding=10)
            self.status_label.configure(wraplength=1100)
            self.minsize(1060, 710)
            self.geometry(getattr(self, '_full_geometry', '1220x790'))
        self._dirty_plot = True

    def _iv_x_changed(self):
        # Changing coordinates invalidates the old zoom extents, not the data.
        self.navigation.update()
        self.iv_axis.set_autoscalex_on(True)
        self.iv_axis.set_autoscaley_on(True)
        self._draw()
        self.iv_axis.relim()
        self.iv_axis.autoscale_view()
        self.canvas.draw_idle()

    def _change_plot_mode(self):
        iv = self.plot_mode.get() == 'I-V'
        for ax in self.axes:
            ax.set_visible(not iv)
        self.iv_axis.set_visible(iv)
        self.log_x_button.configure(state='normal' if iv else 'disabled')
        self.log_y_button.configure(state='normal' if iv else 'disabled')
        self.navigation.update()
        self._dirty_plot = True

    def replot(self):
        segment = self._selected_segment()
        points = self.plot_samples.get(segment)
        if points:
            self.plot_origins[segment] = points[-1].elapsed_s
            points.clear()
        self.paused.set(False)
        self.follow.set(True)
        self.navigation.update()
        self._draw()
        self.status.set('Current plot cleared; new readings continue here. Recorded data remain available for CSV export.')

    def is_iv(self):
        return self.function.get() in IV_FUNCTION_LABELS.values()

    def _read_sweep(self):
        unit = 'V' if self.mode.get() == 'voltage' else 'A'
        try:
            segments = int(self.sweep_segments.get())
        except ValueError:
            raise ValueError('Segments must be an integer from 1 to 1,000,000.') from None
        return ManualSweep(parse_si_value(self.value.get(), unit),
                           parse_si_value(self.sweep_stop.get(), unit),
                           parse_si_value(self.sweep_step.get(), unit),
                           float(self.sweep_delay.get()),
                           low=parse_si_value(self.sweep_low.get(), unit),
                           direction=self.sweep_direction.get(), segments=segments)

    def start_iv(self):
        try:
            if not self.is_iv() or not self.session.configured or self.session.output is not False:
                raise ValueError('Select an I-V function and Apply parameters with output OFF.')
            settings = self._read_settings()
            sweep = self._read_sweep()
            if settings != self.session.adapter.settings or sweep != self._applied_sweep:
                raise ValueError('Parameters changed. Click Apply before starting I-V.')
            sweep.values(settings)
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self._send('sweep', sweep)

    def _position_sweep_progress(self):
        if self._progress_payload.get('sweep_points_total', 0):
            self.sweep_progress_track.place(x=0, y=self._progress_header.winfo_height() + 1,
                                            relwidth=1, height=2)
            self.sweep_progress_track.lift()
        else:
            self.sweep_progress_track.place_forget()

    def _show_scan_progress(self, payload):
        self._progress_payload = payload
        total = payload.get('sweep_points_total', 0)
        done = payload.get('sweep_points_done', 0)
        self.sweep_progress_fill.place_configure(relwidth=min(1., max(0., done / total)) if total else 0.)
        self._position_sweep_progress()
        if not total:
            return ''
        current, segments = payload['sweep_segment_current'], payload['sweep_segments_total']
        name = 'Seg' if self.compact.get() else 'Segment'
        return f' · {name} {current}/{segments}'

    def _field_pair(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill='x')
        left, right = ttk.Frame(row), ttk.Frame(row)
        left.pack(side='left', fill='x', expand=True, padx=(0, 8))
        right.pack(side='left', fill='x', expand=True)
        return left, right

    def _combo(self, parent, label, variable, values):
        options = {'textvariable': label} if isinstance(label, tk.Variable) else {'text': label}
        ttk.Label(parent, **options).pack(anchor='w', pady=(2, 1))
        widget = ttk.Combobox(parent, textvariable=variable, values=values, state='readonly', width=23, style='Instrument.TCombobox')
        widget.pack(fill='x')
        self.editors.append(widget)
        return widget

    def _entry(self, parent, label, variable):
        self._field_labels.append(label)
        ttk.Label(parent, textvariable=label).pack(anchor='w', pady=(2, 1))
        widget = ttk.Entry(parent, textvariable=variable, width=25, style='Instrument.TEntry')
        widget.pack(fill='x')
        self.editors.append(widget)
        return widget

    def _mode_changed(self, _event=None):
        labels = IV_FUNCTION_LABELS if self.is_iv() else FUNCTION_LABELS
        self.mode.set(next(mode for mode, label in labels.items() if label == self.function.get()))
        voltage = self.mode.get() == 'voltage'
        self.value_label.set(('Start voltage (V)' if voltage else 'Start current (A)') if self.is_iv() else ('Set voltage (V)' if voltage else 'Set current (A)'))
        self.sweep_stop_label.set('Top (V)' if voltage else 'Top (A)')
        self.sweep_low_label.set('Low (V)' if voltage else 'Low (A)')
        self.sweep_step_label.set('Step (V)' if voltage else 'Step (A)')
        self.sweep_stop.set('1' if voltage else '10u')
        self.sweep_step.set('50m' if voltage else '1u')
        self.sweep_low.set('-1' if voltage else '-10u')
        self.sweep_segments.set('1')
        self.sweep_direction.set('up')
        self._applied_sweep = None
        if self.is_iv():
            self.manual_actions.pack_forget()
            self.sweep_fields.pack(fill='x', before=self.apply_button)
            self.sweep_actions.pack(fill='x', pady=4)
        else:
            self.sweep_fields.pack_forget()
            self.sweep_actions.pack_forget()
            self.manual_actions.pack(fill='x')
        self.plot_mode.set('I-V' if self.is_iv() else 'Time')
        self._change_plot_mode()
        self.limit_label.set('Current limit (A)' if voltage else 'Voltage limit (V)')
        self.value.set('0')
        self.limit.set('10uA' if voltage else '20V')
        self.source_range.set('Auto')
        self.measure_range.set('Auto')
        self._update_range_options()
        self._controls()

    def _measure_range_changed(self, _event=None):
        selected = self._measure_options.get(self.measure_range.get())
        if selected is None:
            return
        unit = 'A' if self.mode.get() == 'voltage' else 'V'
        try:
            previous = parse_si_value(self.limit.get(), unit)
        except ValueError:
            return  # Apply reports invalid input; do not guess a protection value.
        if previous > selected * 1.05 * (1 + 1e-12):
            replacement = format_si_value(selected, unit)
            self.limit.set(replacement)
            self.status.set(f'Protection limit reduced from {format_si_value(previous, unit)} to '
                            f'{replacement} for the selected measurement range. Click Apply to send changes.')

    def _update_range_options(self):
        voltage = self.mode.get() == 'voltage'
        self.source_range_label.set('Source V range' if voltage else 'Source I range')
        self.measure_range_label.set('Measure I range' if voltage else 'Measure V range')
        self._source_options = range_options(self.mode.get(), self.terminal.get())
        self._measure_options = range_options('current' if voltage else 'voltage', self.terminal.get())
        for widget, variable, options in ((self.source_range_box, self.source_range, self._source_options),
                                           (self.measure_range_box, self.measure_range, self._measure_options)):
            widget.configure(values=tuple(options))
            if variable.get() not in options:
                variable.set('Auto')
                self.status.set('Range reset to Auto for this function / terminal. Click Apply to update the instrument.')

    def show(self):
        self.deiconify()
        self.lift()
        self.focus_set()
        self._dirty_plot = True

    def refresh(self):
        if self.control_session.claimed:
            self.status.set('Release the current device before refreshing connections.')
            return
        if self.busy():
            self.status.set('Automatic measurement is busy. Refresh after it finishes.')
            return
        self.refresh_callback()
        self.status.set('Checking device connections…')

    def _sync_devices(self):
        rows = self.rows_callback()
        if not self.scanning():
            present = {row['address'] or row['device'] for row in rows}
            for key, row in self.known_devices.items():
                if key not in present:
                    row['status'] = 'Disconnected'
        for row in rows:
            key = row['address'] or row['device']
            if row['status'] in {'Connected', 'App connected'} or supply_kind(row['device']) == 'dp100':
                self.known_devices[key] = dict(row)
            elif key in self.known_devices:
                self.known_devices[key] = dict(row, status='Disconnected')
        snapshot = []
        for key, row in self.known_devices.items():
            state = 'Idle' if row['status'] in {'Connected', 'App connected'} else ('Connect USB' if supply_kind(row['device']) == 'dp100' else 'Disconnected')
            if self.system and key in self.system.sessions and self.system.sessions[key].claimed:
                member = self.system.sessions[key]
                state = 'Fault' if member.fault else ('Manual' if member.connected else 'Connecting')
            elif key == self.session.address and self.session.claimed:
                state = 'Manual' if self.snapshot.get('connected') else 'Connecting'
                if self.snapshot.get('fault'):
                    state = 'Fault'
            elif self.busy():
                state = 'Busy'
            elif self.scanning():
                state = 'Checking'
            snapshot.append((key, row['device'], state))
        if snapshot == self._device_snapshot:
            return
        selected = self.tree.selection()
        selected_key = self.devices.get(selected[0], {}).get('address') if selected else None
        self._device_snapshot = snapshot
        self.tree.delete(*self.tree.get_children())
        self.devices.clear()
        for key, identity, state in snapshot:
            parts = identity.split(',')
            label = f"{parts[0].split()[0]} · {parts[1].replace('MODEL ', '')}" if len(parts) > 1 else identity
            item = self.tree.insert('', 'end', text=('☑ ' if key in self.checked_devices else '☐ ') + label, values=(state,))
            self.devices[item] = dict(self.known_devices[key], display_state=state)
            if key == selected_key:
                self.tree.selection_set(item)
                self.tree.focus(item)
        if not snapshot:
            self.status.set('No connected devices. Use Refresh devices or the existing connection settings.')

    def open_selected(self):
        if self.system or len(self.checked_devices) > 1:
            return self._open_system_selected()
        selection = self.tree.selection()
        if not selection or self.pending or self.closing:
            return
        row = next((r for r in self.devices.values() if r["address"] in self.checked_devices), self.devices[selection[0]])
        if self.busy() or self.scanning() or row['display_state'] in {'Disconnected', 'Busy', 'Checking'}:
            self.status.set('Device unavailable or occupied. Wait until it is idle.')
            return
        if self.session.claimed:
            if row['address'] == self.session.address:
                return
            self.after_release = lambda: self._open(row)
            self.release()
        else:
            self._open(row)

    def _open(self, row):
        if self.busy() or self.scanning():
            self.status.set('Measurement or device discovery is busy.')
            return
        self.heading.set(f"{row['device']}  |  {row['address']}")
        if not is_keithley2450(row['device']) and not supply_kind(row['device']):
            self.status.set('Connected · Manual control is not available for this device yet.')
            return
        self._configure_device_form(row['device'])
        self.pending = True
        self.applied.set('Parameters not applied')
        self.voltage.set('— V')
        self.current.set('— A')
        self.updated.set('No measurements for this connection')
        self.session.claim(row['address'])
        self.status.set('Opening control session… Output is not enabled automatically.')
        self._controls()

    def _send(self, action, value=None):
        if self.system:
            return self._system_send(action, value)
        if self.pending or self.closing or not self.session.claimed:
            return
        self.pending = True
        self.session.submit(action, value)
        self.status.set(f'{action.capitalize()}…')
        self._controls()

    def _read_settings(self):
        if self.supply_type:
            v = self.supply_vars
            try:
                settings = SupplySettings(self.supply_type, parse_si_value(v['voltage'].get(), 'V'),
                    parse_si_value(v['current'].get(), 'A'), parse_si_value(v['ovp'].get(), 'V'),
                    parse_si_value(v['ocp'].get(), 'A') if self.supply_type == 'dp100' else .025,
                    int(v['preset'].get()) if self.supply_type == 'dp100' else 1,
                    v['ocp_enabled'].get() == 'ON', .02 if v['measure_range'].get() == '20 mA' else .5)
            except ValueError as exc:
                raise ValueError(f'Invalid supply parameter: {exc}') from exc
            settings.validate()
            return settings
        voltage = self.mode.get() == 'voltage'
        if self.source_range.get() not in self._source_options or self.measure_range.get() not in self._measure_options:
            raise ValueError('Select a supported range or Auto from the range lists.')
        settings = ManualSettings(
            self.mode.get(), parse_si_value(self.value.get(), 'V' if voltage else 'A'),
            parse_si_value(self.limit.get(), 'A' if voltage else 'V'), self.terminal.get(),
            self._source_options[self.source_range.get()], self._measure_options[self.measure_range.get()],
            nplc=float(self.nplc.get()), autozero=self.autozero.get() == 'ON',
        )
        settings.validate()
        return settings

    def apply(self):
        if self.system:
            return self._system_apply(False)
        try:
            settings = self._read_settings()
            sweep = self._read_sweep() if self.is_iv() else None
            if sweep is not None:
                sweep.values(settings)
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self._sweep_candidate = sweep
        self._applied_candidate = settings
        self._send('apply', settings)

    def start(self):
        try:
            interval = float(self.interval.get())
        except ValueError:
            self.status.set('Enter a numeric sample interval.')
            return
        self._send('start', interval)

    def release(self):
        if self.system:
            self.system.submit("release")
            self.pending = True
            return
        if self.session.releasing:
            return
        if not self.session.claimed:
            return
        self.pending = True
        self.session.submit('release')
        self.status.set('Stopping acquisition and confirming output OFF…')
        self._controls()

    def request_close(self):
        if not self.control_session.claimed:
            self.closing = False
            self.withdraw()
            return
        self.closing = True
        self.after_release = None
        self.release()

    def _controls(self):
        if self.system:
            return self._system_controls()
        ready = self.snapshot.get('connected', False) and self.session.claimed and not self.pending and not self.closing and not self.session.releasing
        running = self.snapshot.get('acquiring', False)
        configured = self.snapshot.get('configured', False) and self.function.get() == self._applied_function
        for editor in self.supply_editors:
            editor.configure(state=('readonly' if isinstance(editor, ttk.Combobox) else 'normal') if ready and not running else 'disabled')
        self.clear_protection_button.configure(state='normal' if ready and not running else 'disabled')
        for editor in self.editors:
            editor.configure(state=('readonly' if isinstance(editor, ttk.Combobox) and editor is not self.nplc_box else 'normal') if ready and not running else 'disabled')
        if self.snapshot.get('output') is not False:
            self.mode_box.configure(state='disabled')
            self.terminal_box.configure(state='disabled')
            self.source_range_box.configure(state='disabled')
            self.measure_range_box.configure(state='disabled')
        for button, enabled in ((self.apply_button, ready and not running),
                                (self.iv_button, self.is_iv() and self._applied_sweep is not None and ready and configured and not running and self.snapshot.get('output') is False),
                                (self.iv_stop_button, ready),
                                (self.monitor_stop, ready and running),
                                (self.monitor_off, ready),
                                (self.on_button, ready and configured and self.snapshot.get('output') is False),
                                (self.off_button, ready),
                                (self.once_button, ready and configured and not running),
                                (self.start_button, ready and configured and not running),
                                (self.stop_button, ready and running),
                                (self.release_button, self.session.claimed and not self.session.releasing),
                                (self.open_button, bool(self.tree.selection()) and not self.pending and not self.closing and not self.busy() and not self.scanning()),
                                (self.refresh_button, not self.session.claimed and not self.busy() and not self.scanning()),
                                (self.export_button, bool(self.samples) and not self._exporting)):
            button.configure(state='normal' if enabled else 'disabled')

    def _add_sample(self, sample, *, select_new=True):
        if sample.segment not in self.segments:
            label = f'{sample.segment} · {sample.address} · CH{sample.channel}'
            self.segments[sample.segment] = label
            self.plot_samples[sample.segment] = IVPlotBuffer() if sample.kind == 'iv' else deque(maxlen=2000)
            self.segment_box.configure(values=tuple(self.segments.values()))
            if select_new:
                self.segment_choice.set(label)
                self.plot_mode.set('I-V' if sample.kind == 'iv' else 'Time')
                self._change_plot_mode()
        self.samples.append(sample)
        if self.auto_save.get():
            if self._autosaver is None:
                self._autosaver = ManualAutoSave()
            self._autosaver.append(self._autosave_offset + len(self.samples) - 1, sample,
                                   self._autosave_metadata.get(sample.segment))
        self.plot_samples[sample.segment].append(sample)
        if self._selected_segment() == sample.segment:
            self.voltage.set(self.reading_formatters[0].format(sample.voltage_v))
            self.current.set(self.reading_formatters[1].format(sample.current_a))
            power = f' · P {self.power_formatter.format(sample.power_w)}' if self.supply_type and sample.power_w is not None else ''
            timing = ''
            if sample.sample_interval_s is not None and sample.instrument_time_s is not None:
                timing = f' · Δt {sample.sample_interval_s * 1000:.1f} ms · I/O {sample.read_duration_s * 1000:.1f} ms'
                if sample.missed_periods:
                    timing += f' · missed {sample.missed_periods}'
            self.updated.set(f'{sample.timestamp[11:23]} UTC · {len(self.samples)} pts{power}{timing}')
        self._dirty_plot = True

    def _selected_segment(self):
        return next((key for key, label in self.segments.items() if label == self.segment_choice.get()), None)

    def _select_segment(self):
        points = self.plot_samples.get(self._selected_segment(), ())
        if points:
            sample = points[-1]
            self.voltage.set(self.reading_formatters[0].format(sample.voltage_v))
            self.current.set(self.reading_formatters[1].format(sample.current_a))
            self.updated.set(f'History · {sample.timestamp} · CH{sample.channel}')
        self._dirty_plot = True

    def _plot_interaction(self, event):
        if event.inaxes is not None and self.navigation.mode:
            self.follow.set(False)

    def _plot_option(self):
        self._dirty_plot = True

    def resume(self):
        if self.system:
            self.paused.set(False)
            self.follow.set(True)
            self._view_member(self.view_device, restore=False)
            return
        self.paused.set(False)
        self.follow.set(True)
        if self.segments:
            self.segment_choice.set(next(reversed(self.segments.values())))
        if self.navigation.mode:
            if 'zoom' in str(self.navigation.mode).lower():
                self.navigation.zoom()
            else:
                self.navigation.pan()
        self._select_segment()
        self._dirty_plot = True

    def _draw(self):
        points = self.plot_samples.get(self._selected_segment(), ())
        if self.plot_mode.get() == 'I-V':
            use_set = self.iv_x_source.get() == 'Set V'
            if use_set and points and (points[-1].source_mode != 'voltage' or points[-1].source_value is None):
                self.iv_x_source.set('Measured V')
                use_set = False
                self.status.set('Set V is only available for voltage-source I-V scans; showing measured voltage.')
            from matplotlib.ticker import EngFormatter, NullFormatter
            log_x, log_y = self.iv_log_x.get(), self.iv_log_y.get()
            x_scale, y_scale = ('log' if log_x else 'linear'), ('log' if log_y else 'linear')
            if (self.iv_axis.get_xscale(), self.iv_axis.get_yscale()) != (x_scale, y_scale):
                self.iv_line.set_data([], [])
                self.iv_axis.relim()
                if log_x:
                    self.iv_axis.set_xlim(1e-3, 1, auto=True)
                if log_y:
                    self.iv_axis.set_ylim(1e-9, 1e-6, auto=True)
                self.iv_axis.set_xscale(x_scale)
                self.iv_axis.set_yscale(y_scale)
                self.iv_axis.xaxis.set_major_formatter(EngFormatter(unit='V', sep=' '))
                self.iv_axis.yaxis.set_major_formatter(EngFormatter(unit='A', sep=' '))
                self.iv_axis.xaxis.set_minor_formatter(NullFormatter())
                self.iv_axis.yaxis.set_minor_formatter(NullFormatter())
            x_label = 'Set voltage' if use_set else 'Measured voltage'
            self.iv_axis.set_xlabel(f'|{x_label}| (V)' if log_x else f'{x_label} (V)')
            self.iv_axis.set_ylabel('|Measured current| (A)' if log_y else 'Measured current (A)')
            def coordinates(readings):
                xs, ys = [], []
                for point in readings:
                    x = point.source_value if use_set else point.voltage_v
                    y = point.current_a
                    x, y = (abs(x) if log_x else x), (abs(y) if log_y else y)
                    if (log_x and x == 0) or (log_y and y == 0):
                        # Break the line at zero, rather than bridging a missing log point.
                        x = y = math.nan
                    xs.append(x)
                    ys.append(y)
                return xs, ys
            curves = points.display_curves() if isinstance(points, IVPlotBuffer) else ([points] if points else [])
            while len(self.iv_history_lines) < max(0, len(curves) - 1):
                line, = self.iv_axis.plot([], [], color='#20a88a', linewidth=1, alpha=.65)
                self.iv_history_lines.append(line)
            for index, line in enumerate(self.iv_history_lines):
                line.set_data(*coordinates(curves[index]) if index < len(curves) - 1 else ([], []))
                line.set_zorder(2 + index)
            self.iv_line.set_data(*coordinates(curves[-1]) if curves else ([], []))
            try:
                fresh_count = max(0, min(1000, int(self.iv_recent_count.get())))
            except ValueError:
                fresh_count = 10
            recent = []
            if fresh_count:
                recent = (points if isinstance(points, list) else list(points))[-fresh_count:]
            import numpy as np
            from matplotlib.colors import to_rgb
            xs, ys = coordinates(recent)
            positions = np.column_stack((xs, ys))
            self.iv_recent.set_offsets(positions)
            green, orange = np.array(to_rgb('#20a88a')), np.array(to_rgb('#ff922e'))
            ages = np.linspace(0, 1, len(recent)) if len(recent) > 1 else np.ones(len(recent))
            colors = np.array([green + age * (orange - green) for age in ages])
            self.iv_recent.set_facecolors(colors)
            # Match each connecting segment to its newer point; keep log-axis
            # zero/invalid gaps disconnected rather than bridging them.
            segments = np.stack((positions[:-1], positions[1:]), axis=1)
            valid = np.isfinite(segments).all(axis=(1, 2))
            self.iv_recent_line.set_segments(segments[valid])
            self.iv_recent_line.set_colors(colors[1:][valid])
            self.iv_recent.set_edgecolors('none')
            self.iv_current.set_data(*coordinates([points[-1]]) if points else ([], []))
            if self.follow.get():
                self.iv_axis.relim()
                self.iv_axis.autoscale_view()
            self.canvas.draw_idle()
            self._dirty_plot = False
            return
        origin = self.plot_origins.get(self._selected_segment(), 0.)
        xs = [point.elapsed_s - origin for point in points]
        for ax, line, attribute in zip(self.axes, self.lines, ('voltage_v', 'current_a')):
            line.set_data(xs, [getattr(point, attribute) for point in points])
            if self.follow.get():
                ax.relim()
                ax.autoscale_view(scalex=False)
        if self.follow.get():
            end = xs[-1] if xs else 1.0
            self.axes[1].set_xlim(max(0, end - 60), max(1, end))
        self.canvas.draw_idle()
        self._dirty_plot = False

    def clear(self):
        self._autosave_offset += len(self.samples)
        self.samples.clear()
        self.segments.clear()
        self.plot_samples.clear()
        self.plot_origins.clear()
        self.segment_box.configure(values=())
        self.segment_choice.set('')
        self.voltage.set('— V')
        self.current.set('— A')
        self.updated.set('Data cleared')
        self._dirty_plot = True
        self._draw()

    def _toggle_autosave(self):
        self._autosave_enabled = self.auto_save.get()
        if self._autosave_enabled:
            if self._autosaver is None:
                self._autosaver = ManualAutoSave()
            self._autosaver.resume()
            self._autosaver.backfill(self._autosave_offset, tuple(self.samples), dict(self._autosave_metadata))
            self._autosaver.flush()
            self.status.set('AutoSave enabled · manualtest_session / date · CSV + configuration JSON')
        else:
            if self._autosaver is not None:
                self._autosaver.flush()
            self.status.set('AutoSave disabled; existing files and in-memory data are retained.')

    def _poll_autosave(self):
        if self._autosaver is None:
            return
        while True:
            try:
                kind, payload = self._autosaver.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'error':
                self.auto_save.set(False)
                self._autosave_enabled = False
                self.status.set(f'AutoSave failed: {payload}. Data remain in memory; re-enable to retry.')
            # Successful background writes do not overwrite acquisition/fault status.

    def _drain_autosave_tail(self):
        """Preserve samples queued before confirmed OFF, even if Tk exits before its next poll."""
        source = self.system or self.session
        while True:
            try:
                kind, payload = source.events.get_nowait()
            except queue.Empty:
                return
            sample = None
            if self.system:
                if kind == 'member':
                    address, event, data = payload
                    if event == 'sample':
                        sample = self._prepare_system_sample(address, data)
            elif kind == 'acquisition':
                self._autosave_metadata[payload['segment']] = payload
            elif kind == 'sample':
                sample = payload
            if sample is not None:
                index = self._autosave_offset + len(self.samples)
                self.samples.append(sample)
                self._autosaver.append(index, sample, self._autosave_metadata.get(sample.segment))

    def _flush_autosave(self):
        if self._autosaver is not None:
            self._autosaver.flush()

    def export(self):
        if self.system:
            return self._export_system()
        path = filedialog.asksaveasfilename(parent=self, title='Export all manual readings', defaultextension='.csv', filetypes=[('CSV', '*.csv')])
        if not path:
            return
        samples = tuple(self.samples)
        self._exporting = True
        self._controls()
        def worker():
            try:
                export_samples(path, samples)
                self._export_events.put(f'Exported {len(samples)} readings to {path}')
            except Exception as exc:
                self._export_events.put(f'Export failed: {exc}')
        threading.Thread(target=worker, name='instrument-csv', daemon=True).start()

    def _poll(self):
        if self.system:
            return self._poll_system()
        error_seen = False
        for _ in range(250):
            try:
                kind, payload = self.session.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'acquisition':
                self._autosave_metadata[payload['segment']] = payload
            elif kind == 'sample':
                self._add_sample(payload)
            elif kind == 'notice':
                self.status.set(payload)
            elif kind == 'error':
                error_seen = True
                self.status.set(payload)
                self.closing = False
                self.after_release = None
            elif kind in {'failed', 'cancelled'}:
                self.pending = False
            elif kind == 'done':
                if payload in {'once', 'stop', 'off', 'release'}:
                    self._flush_autosave()
                self.pending = False
                if not error_seen:
                    self.status.set(f'{payload.capitalize()} complete' if payload else self.status.get())
                    if payload == 'sweep':
                        self.applied.set('I-V sweep active; source level varies.\nOutput turns OFF when finished.')
                    if payload == 'apply':
                        self._applied_sweep = self._sweep_candidate
                        self._applied_function = self.function.get()
                        s = self._applied_candidate
                        if isinstance(s, SupplySettings):
                            self.applied.set(f'Applied {format_si_value(s.value, "V")} / limit {format_si_value(s.limit, "A")}')
                            continue
                        source_unit, measure_unit = ('V', 'A') if s.mode == 'voltage' else ('A', 'V')
                        source_range = 'Auto' if s.source_range is None else format_si_value(s.source_range, source_unit)
                        measure_range = 'Auto' if s.measure_range is None else format_si_value(s.measure_range, measure_unit)
                        self.applied.set(f'Applied {format_si_value(s.value, source_unit)} · Limit {format_si_value(s.limit, measure_unit)}\n'
                                         f'Ranges: source {source_range} / measure {measure_range}\n'
                                         f'NPLC {s.nplc:g} / Auto zero {"ON" if s.autozero else "OFF"}')
                        if self._applied_sweep is not None:
                            count = len(self._applied_sweep.values(s))
                            self.status.set(f'Apply complete: {count:,} points across {self._applied_sweep.segments:,} segments. '
                                            'Plot spans the full run (up to 100 representative cycles, 4 segments/cycle); CSV retains all readings.')
            elif kind == 'state':
                newly_connected = payload['connected'] and not self.snapshot.get('connected')
                if self.snapshot.get('acquiring') and not payload['acquiring']:
                    self._flush_autosave()
                self.snapshot = payload
                if newly_connected:
                    self._show_control_view()
                if not payload['configured'] and self.applied.get().startswith(('Applied', 'I-V sweep')):
                    self.applied.set('Parameters not applied')
                if self.supply_type:
                    readback = payload.get('readback', {})
                    if readback:
                        text = f"Readback set: {format_si_value(readback['voltage'], 'V')} / {format_si_value(readback['current'], 'A')}\nOVP {format_si_value(readback['ovp'], 'V')}"
                        if self.supply_type == 'dp100':
                            text += f" / OCP {format_si_value(readback['ocp'], 'A')} / P{readback['preset']}"
                        else:
                            text += f" / OCP {'ON' if readback['ocp_enabled'] else 'OFF'}\nI range {format_si_value(readback['measure_range'], 'A')} · {readback['mode']} · {readback['protection']}"
                        self.supply_readback.set(text)
                    else:
                        self.supply_readback.set('Readback unavailable / disconnected')
                output = payload['output']
                label = 'ON' if output is True else ('OFF' if output is False else 'UNKNOWN')
                suffix = self._show_scan_progress(payload)
                if not suffix and payload['acquiring']:
                    suffix = ' · ACQUIRING'
                self.output_text.set(f'OUTPUT {label}{suffix}')
                self.output_label.configure(style=f'Instrument.Output{"On" if output is True else "Off" if output is False else "Unknown"}.TLabel')
                self.activity('Instrument · !' if payload['fault'] else ('Instrument · RUN' if output is True or payload['acquiring'] else 'Instrument'))
                if not payload['claimed']:
                    if self.closing:
                        self.closing = False
                        self.withdraw()
                    callback, self.after_release = self.after_release, None
                    if callback:
                        callback()
        try:
            self.status.set(self._export_events.get_nowait())
            self._exporting = False
        except queue.Empty:
            pass
        self._poll_autosave()
        self._sync_devices()
        self._controls()
        if self._dirty_plot and not self.paused.get() and self.wm_state() != 'withdrawn':
            self._draw()
        self._poll_id = self.after(150, self._poll)

    def _destroyed(self, event):
        if event.widget is self:
            self.after_cancel(self._poll_id)
            if self._autosaver is not None:
                if self._autosave_enabled:
                    self._drain_autosave_tail()
                self._autosaver.shutdown()
            if not self.control_session.claimed:
                self.control_session.shutdown()
                if self.system:
                    self._single_session.shutdown()
