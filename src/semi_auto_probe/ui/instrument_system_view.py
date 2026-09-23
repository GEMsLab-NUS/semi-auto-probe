"""Compact multi-device extension of the existing manual instrument window."""
from dataclasses import replace
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font as tkfont

from ..instrument_control import is_keithley2450, StableReading
from ..instrument_system import InstrumentSystem, SystemConfiguration, export_system
from ..power_supply_control import supply_kind

FORM_VARIABLES = ('function', 'mode', 'terminal', 'channel', 'value', 'limit', 'source_range',
    'measure_range', 'nplc', 'autozero', 'interval', 'sweep_stop', 'sweep_low', 'sweep_step',
    'sweep_delay', 'sweep_direction', 'sweep_segments', 'applied')
VIEW_VARIABLES = ('segment_choice', 'plot_mode', 'iv_x_source', 'iv_log_x', 'iv_log_y',
                  'iv_recent_count', 'paused', 'follow')


class InstrumentSystemView:
    @property
    def control_session(self):
        return self.system or self.session

    def _init_system(self):
        self.system = None
        self._single_session = self.session
        self.checked_devices = set()
        self.member_forms = {}
        self.member_rows = {}
        self.edit_device = self.view_device = ''
        self.member_states = {}
        self.member_latest = {}
        self.member_formatters = {}
        self.member_segments = {}
        self.member_views = {}
        self._system_segment_ids = {}
        self._system_segment_devices = {}
        self._past_systems = []

    def _build_system_controls(self):
        self.system_status_summary = tk.StringVar()
        self._status_font = tkfont.Font(font=('Segoe UI', 9))
        self.status_label.bind('<Button-1>', lambda _event: messagebox.showinfo('Instrument system status', self.status.get(), parent=self) if self.system else None)
        self.system_panel = ttk.Frame(self.form)
        self.member_tabs = ttk.Frame(self.system_panel)
        self.member_tabs.pack(fill='x', pady=(0, 3))
        self.scan_device = tk.StringVar()
        scan_row = ttk.Frame(self.system_panel)
        scan_row.pack(fill='x')
        ttk.Label(scan_row, text='Scan').pack(side='left')
        self.scan_box = ttk.Combobox(scan_row, textvariable=self.scan_device, width=17, state='readonly')
        self.scan_box.pack(side='left', fill='x', expand=True, padx=(4, 5))
        self.scan_box.bind('<<ComboboxSelected>>', self._change_scanner)
        self.aux_order_frame = ttk.Frame(self.system_panel)
        self.aux_order_frame.pack(fill='x')
        self.aux_order = []
        self.aux_settle = {}
        self.aux_period = tk.StringVar(value='1')
        timing = ttk.Frame(scan_row)
        timing.pack(side='right')
        ttk.Label(timing, text='Aux s').pack(side='left')
        self.aux_period_entry = ttk.Entry(timing, textvariable=self.aux_period, width=4)
        self.aux_period_entry.pack(side='right')
        self.system_actions = ttk.Frame(self.form)
        self.apply_all_button = ttk.Button(self.system_actions, text='Apply all', command=lambda: self._system_apply(True))
        self.apply_all_button.pack(side='left', expand=True, fill='x')
        self.system_start_button = ttk.Button(self.system_actions, text='Start system', style='Instrument.Accent.TButton', command=self._start_system)
        self.system_start_button.pack(side='left', expand=True, fill='x', padx=(4, 0))
        self.retry_member_button = ttk.Button(self.system_actions, text='Retry OFF', command=lambda: self.system.submit('retry', self.edit_device))
        self.aux_readouts = ttk.Frame(self.display)
        self.return_main = ttk.Button(self.aux_readouts, text='↩ Main', command=lambda: self._view_member(self.scan_device.get()))
        self.aux_buttons = {}
        self._tab_buttons = {}
        self._system_widgets = []
        self._style_window(getattr(self.master, 'colors', {}))

        self._refresh_open_label()

    def _refresh_open_label(self):
        if hasattr(self, 'open_button'):
            self.open_button.configure(text=f'Open selected · {len(self.checked_devices)}' if self.checked_devices else 'Open device →')

    def _check_device(self, event):
        item = self.tree.identify_row(event.y) if getattr(event, 'keysym', '') != 'space' else self.tree.focus()
        row = self.devices.get(item)
        if not row:
            return
        if self.control_session.claimed or self.pending:
            self.status.set('Release the current device/system before changing members.')
            return
        if self.busy() or self.scanning() or row['display_state'] in {'Busy', 'Checking', 'Disconnected'} or not (is_keithley2450(row['device']) or supply_kind(row['device'])):
            self.status.set('This device is unavailable for manual control.')
            return
        address = row['address']
        if address in self.checked_devices:
            self.checked_devices.remove(address)
        elif len(self.checked_devices) < 3:
            self.checked_devices.add(address)
        else:
            self.status.set('Select at most three devices.')
        self._device_snapshot = None
        self._sync_devices()
        self._refresh_open_label()
        self._controls()

    def _system_adapter(self, address):
        # Transfer an idle cached connection when moving from single to group.
        adapter = self._single_session.cache.pop(address, None)
        return adapter if adapter is not None else self._single_session.adapter_factory(address)

    def _open_system_selected(self):
        if self.pending or self.closing or self.busy() or self.scanning():
            return
        rows = [r for r in self.devices.values() if r['address'] in self.checked_devices]
        if not rows:
            rows = [self.devices[i] for i in self.tree.selection()]
        if not rows or any(r['display_state'] in {'Busy', 'Disconnected', 'Checking'} for r in rows):
            self.status.set('Select available devices first.')
            return
        if self.control_session.claimed:
            self.status.set('Release the current system before changing members.')
            return
        if len(rows) == 1:
            if self.system:
                member = self.system.sessions.get(rows[0]['address'])
                if member and rows[0]['address'] in member.cache:
                    self._single_session.cache[rows[0]['address']] = member.cache.pop(rows[0]['address'])
                self.system.shutdown()
                self._past_systems.append(self.system)
                self.system = None
            self.session = self._single_session
            self.session.segment = max(self.session.segment, max(self.segments, default=0), max(self._autosave_metadata, default=0))
            self.segment_box.configure(values=tuple(self.segments.values()))
            self.system_panel.pack_forget()
            self.system_actions.pack_forget()
            self.aux_readouts.grid_remove()
            self.status_label.configure(textvariable=self.status, cursor='')
            self.export_button.configure(text='Export CSV')
            self.release_button.configure(text='Release device / retry OFF')
            self.monitor_stop.configure(text='Stop', width=4)
            self.monitor_off.configure(text='OFF', width=4)
            self._open(rows[0])
            return
        if not any(is_keithley2450(r['device']) for r in rows):
            self.status.set('A multi-device system needs one Keithley 2450 scanner.')
            return
        self.system = self.system or InstrumentSystem(self._system_adapter)
        self.member_rows = {r['address']: r for r in rows}
        self.member_forms.clear()
        self.member_states.clear()
        self.member_latest.clear()
        self.member_segments.clear()
        self.member_views.clear()
        self.edit_device = self.view_device = ''
        self.pending = True
        self.status_label.configure(textvariable=self.system_status_summary, cursor='hand2')
        try:
            self.system.open(rows)
        except ValueError as exc:
            self.pending = False
            self.status.set(str(exc))
            return
        self.status.set('Opening selected devices; outputs remain unchanged…')
        self.activity('Instrument · RUN')

    def _save_member(self):
        if not self.edit_device:
            return
        self.member_forms[self.edit_device] = dict(
            variables={n: getattr(self, n).get() for n in FORM_VARIABLES},
            supply={n: v.get() for n, v in self.supply_vars.items()},
            applied_sweep=self._applied_sweep, applied_function=self._applied_function,
            applied_settings=getattr(self, '_applied_candidate', None))

    def _view_snapshot(self):
        return dict(variables={n: getattr(self, n).get() for n in VIEW_VARIABLES},
                    axes=[(a.get_xlim(), a.get_ylim()) for a in (self.iv_axis, *self.axes)])

    def _restore_view(self, view):
        for n, value in view['variables'].items():
            getattr(self, n).set(value)
        self._change_plot_mode()
        for axis, (xlim, ylim) in zip((self.iv_axis, *self.axes), view['axes']):
            axis.set_xlim(xlim)
            axis.set_ylim(ylim)
        self._dirty_plot = True

    def _edit_member(self, address):
        if not self.system or address not in self.system.sessions:
            return
        self._save_member()
        view = self._view_snapshot()
        self.edit_device = address
        self.session = self.system.sessions[address]
        self.snapshot = self.member_states.get(address, {})
        saved = self.member_forms.get(address)
        self._configure_device_form(self.member_rows[address]['device'])
        if saved:
            self.function.set(saved['variables']['function'])
            self._mode_changed()
            for n, v in saved['variables'].items():
                getattr(self, n).set(v)
            for n, v in saved['supply'].items():
                self.supply_vars[n].set(v)
            self._applied_sweep = saved['applied_sweep']
            self._applied_function = saved['applied_function']
            self._applied_candidate = saved['applied_settings']
        else:
            self._applied_sweep = self._applied_function = self._applied_candidate = None
            self.applied.set('Parameters not applied')
            if address == self.scan_device.get():
                from .instrument_window import IV_FUNCTION_LABELS
                self.function.set(IV_FUNCTION_LABELS['voltage'])
                self._mode_changed()
        self._update_range_options()
        self._restore_view(view)
        self.heading.set(f"System · {len(self.system.sessions)} devices · editing {address}")
        self.system_panel.pack(fill='x', before=next(w for w in self.form.pack_slaves() if w is not self.system_panel)) if not self.system_panel.winfo_manager() else None
        self._save_member()
        self._system_controls()

    def _setup_system_view(self):
        for child in self.member_tabs.winfo_children():
            child.destroy()
        scanners = [a for a, r in self.member_rows.items() if is_keithley2450(r['device'])]
        self.scan_box.configure(values=scanners)
        self.scan_device.set(scanners[0])
        self.aux_order = [a for a in self.member_rows if a != scanners[0]]
        self.aux_settle = {a: tk.StringVar(value='0.2') for a in self.member_rows}
        self._tab_buttons = {}
        for index, address in enumerate(self.member_rows, 1):
            short = self._short_name(address)
            b = ttk.Button(self.member_tabs, text=short, width=10, command=lambda a=address: self._edit_member(a))
            b.pack(side='left', expand=True, fill='x', padx=1)
            self._tab_buttons[address] = b
        self._rebuild_aux_order()
        for address in self.member_rows:
            self._edit_member(address)
        self._edit_member(scanners[0])
        self._view_member(scanners[0], restore=False)
        self.export_button.configure(text='Export ZIP')
        self.system_actions.pack(fill='x', pady=4)
        self.system_panel.pack(fill='x', before=next(w for w in self.form.pack_slaves() if w is not self.system_panel))
        self.aux_readouts.grid(row=2, column=0, sticky='ew')
        # Existing timing label moves below the plot; no extra title row.
        for widget in self.display.grid_slaves(row=2):
            if widget is not self.aux_readouts:
                widget.grid_configure(row=4)
        self.monitor_stop.configure(text='Stop all', width=7)
        self.monitor_off.configure(text='All OFF', width=7)
        self._style_window(getattr(self.master, 'colors', {}))
        self._show_control_view()
        self._system_controls()

    def _short_name(self, address):
        identity = self.member_rows[address]['device']
        model = '2450' if is_keithley2450(identity) else 'DP100' if supply_kind(identity) == 'dp100' else 'HP 6614C'
        return f'{model} · {list(self.member_rows).index(address) + 1}'

    def _rebuild_aux_order(self):
        for child in self.aux_order_frame.winfo_children():
            child.destroy()
        for child in self.aux_readouts.winfo_children():
            if child is not self.return_main:
                child.destroy()
        self._system_widgets = [self.scan_box, self.aux_period_entry]
        self.aux_buttons = {}
        for i, address in enumerate(self.aux_order):
            row = ttk.Frame(self.aux_order_frame)
            row.pack(fill='x', pady=1)
            ttk.Label(row, text=f'{i+1}. {self._short_name(address)} · settle s', width=23).pack(side='left')
            entry = ttk.Entry(row, textvariable=self.aux_settle[address], width=6)
            entry.pack(side='left')
            move = ttk.Button(row, text='↑' if i else '↓', width=2, command=self._reverse_aux)
            move.pack(side='right')
            self._system_widgets.append(entry)
            if len(self.aux_order) > 1:
                self._system_widgets.append(move)
            else:
                move.pack_forget()
            button = ttk.Button(self.aux_readouts, width=34, command=lambda a=address: self._view_member(a))
            button.pack(side='left', expand=True, fill='x', padx=2)
            self.aux_buttons[address] = button
        self.return_main.pack(side='right')
        self._style_window(getattr(self.master, 'colors', {}))

    def _reverse_aux(self):
        if not self.system.locked:
            self.aux_order.reverse()
            self._rebuild_aux_order()

    def _change_scanner(self, _event=None):
        if self.system.locked:
            return
        self.aux_order = [a for a in self.member_rows if a != self.scan_device.get()]
        self._rebuild_aux_order()
        self._view_member(self.scan_device.get())
        self.status.set('Set the scan device to an I-V function and auxiliary devices to constant functions, then Apply all.')

    def _collect_config(self):
        original = self.edit_device
        settings, sweeps = {}, {}
        try:
            for address in self.system.sessions:
                self._edit_member(address)
                settings[address] = self._read_settings()
                sweeps[address] = self._read_sweep() if self.is_iv() and not self.supply_type else None
                if address != self.scan_device.get() and sweeps[address] is not None:
                    raise ValueError(f'{address}: auxiliary devices require a constant function.')
            if sweeps[self.scan_device.get()] is None:
                raise ValueError('Select an I-V function for the scan device.')
            config = SystemConfiguration(self.scan_device.get(), settings, sweeps[self.scan_device.get()],
                tuple(self.aux_order), {a: float(v.get()) for a, v in self.aux_settle.items()}, float(self.aux_period.get()))
            config.validate(self.system.sessions)
            return config
        finally:
            self._edit_member(original)

    def _system_apply(self, all_members):
        if self.system.locked:
            return
        try:
            self._save_member()
            if all_members:
                config = self._collect_config()
                values = config.settings
            else:
                settings = self._read_settings()
                sweep = self._read_sweep() if self.is_iv() and not self.supply_type else None
                if sweep:
                    sweep.values(settings)
                values = {self.edit_device: settings}
            self._apply_targets = {}
            for a in values:
                form = self.member_forms[a]
                self._apply_targets[a] = dict(settings=values[a], function=form['variables']['function'],
                    sweep=self._read_sweep() if a == self.edit_device and self.is_iv() else None)
                if all_members and a == config.scanner:
                    self._apply_targets[a]['sweep'] = config.sweep
            self.system.submit('apply_all' if all_members else 'apply', values)
            self.pending = True
        except (ValueError, TypeError) as exc:
            self.status.set(str(exc))

    def _start_system(self):
        if self.system.locked:
            return
        try:
            config = self._collect_config()
            for address, settings in config.settings.items():
                form = self.member_forms[address]
                if form['applied_settings'] != settings or form['applied_function'] != form['variables']['function']:
                    raise ValueError(f'{address}: parameters changed; Apply before starting.')
            if self.member_forms[config.scanner]['applied_sweep'] != config.sweep:
                raise ValueError('Scan changed; Apply before starting.')
            self.system.submit('start', config)
            self.pending = True
            self._view_member(config.scanner)
        except ValueError as exc:
            self.status.set(str(exc))

    def _system_send(self, action, value=None):
        if action in {'off', 'stop'}:
            self.system.submit(action)
            self.status.set('Stopping and confirming all outputs OFF…')
        else:
            self.status.set('Use Apply / Apply all and Start system for grouped devices.')

    def _system_controls(self):
        if not hasattr(self, 'system_start_button'):
            return
        locked = self.system.locked or self.pending or self.closing
        ready = self.session.connected and self.session.claimed and not locked
        if not self.session.configured:
            self.applied.set('Parameters not applied')
        elif ready:
            try:
                changed = self._read_settings() != self.session.adapter.settings or self.function.get() != self._applied_function
                changed = changed or (self.is_iv() and self._read_sweep() != self._applied_sweep)
                self.applied.set('Changes not applied' if changed else 'Applied · ready')
            except ValueError:
                self.applied.set('Invalid parameters · review before Apply')
        for editor in self.editors + self.supply_editors:
            editor.configure(state=('readonly' if isinstance(editor, ttk.Combobox) and editor is not self.nplc_box else 'normal') if ready else 'disabled')
        for widget in self._system_widgets:
            widget.configure(state=('readonly' if isinstance(widget, ttk.Combobox) else 'normal') if not locked else 'disabled')
        for button in (self.on_button, self.off_button, self.once_button, self.start_button, self.stop_button,
                       self.iv_button, self.iv_stop_button, self.clear_protection_button):
            button.configure(state='disabled')
        self.manual_actions.pack_forget()
        self.sweep_actions.pack_forget()
        self.system_actions.pack(fill='x', pady=4)
        for button, enabled in ((self.apply_button, ready), (self.apply_all_button, ready),
                (self.system_start_button, not locked and all(s.connected and s.configured and s.output is False for s in self.system.sessions.values())),
                (self.monitor_stop, self.system.claimed), (self.monitor_off, self.system.claimed),
                (self.release_button, self.system.claimed and not self.system.releasing),
                (self.refresh_button, not self.system.claimed),
                (self.open_button, not self.system.claimed and not locked),
                (self.export_button, bool(self.samples) and not self._exporting)):
            button.configure(state='normal' if enabled else 'disabled')
        self.release_button.configure(text='Release all / retry OFF')
        if self.session.fault and self.session.claimed:
            self.retry_member_button.pack(side='left', padx=(4, 0))
            self.retry_member_button.configure(state='disabled' if locked else 'normal')
        else:
            self.retry_member_button.pack_forget()
        if self.session.output is not False:
            for editor in (self.mode_box, self.terminal_box, self.source_range_box, self.measure_range_box):
                editor.configure(state='disabled')
        for address, button in self._tab_buttons.items():
            button.configure(style='Instrument.Accent.TButton' if address == self.edit_device else 'Instrument.Control.TButton')

    def _view_member(self, address, *, restore=True):
        if not address:
            return
        if self.view_device:
            self.member_views[self.view_device] = self._view_snapshot()
        self.view_device = address
        self.segment_box.configure(values=tuple(label for segment, label in self.segments.items()
            if self._system_segment_devices.get(segment) == address))
        saved = self.member_views.get(address)
        if restore and saved:
            self._restore_view(saved)
        else:
            segment = self.member_segments.get(address)
            self.segment_choice.set(self.segments.get(segment, ''))
            self.plot_mode.set('I-V' if address == self.scan_device.get() else 'Time')
            self.follow.set(True)
            self._change_plot_mode()
        self._select_segment()
        self._dirty_plot = True

    def _update_system_status(self):
        text = self.status.get().replace('\n', ' ')
        available = max(200, self.winfo_width() - 32)
        if self._status_font.measure(text) > available:
            suffix = '… · Click for details'
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if self._status_font.measure(text[:middle] + suffix) <= available:
                    low = middle
                else:
                    high = middle - 1
            text = text[:low] + suffix
        self.system_status_summary.set(text)

    def _prepare_system_sample(self, address, data):
        self.member_latest[address] = data
        key = (self.system.sessions[address], address, data.segment)
        if key not in self._system_segment_ids:
            self._system_segment_ids[key] = max(max(self.segments, default=0), max(self._autosave_metadata, default=0)) + 1
        sample = replace(data, segment=self._system_segment_ids[key])
        self.member_segments[address] = sample.segment
        self._system_segment_devices[sample.segment] = address
        if sample.segment not in self._autosave_metadata:
            run = next((r for r in self.system.runs if r['run_id'] == sample.system_run_id), None)
            if run is not None:
                self._autosave_metadata[sample.segment] = dict(run, kind='system')
        return sample

    def _poll_system(self):
        for _ in range(1500):
            try:
                kind, payload = self.system.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'member':
                address, event, data = payload
                if event == 'state':
                    self.member_states[address] = data
                    if address == self.edit_device:
                        self.snapshot = data
                        if self.supply_type:
                            readback = data.get('readback', {})
                            self.supply_readback.set(' · '.join(f'{k}: {v}' for k, v in readback.items()) if readback else 'Readback unavailable')
                elif event == 'sample':
                    sample = self._prepare_system_sample(address, data)
                    self._add_sample(sample, select_new=address == self.view_device)
                    self.segment_box.configure(values=tuple(label for segment, label in self.segments.items()
                        if self._system_segment_devices.get(segment) == self.view_device))
                elif event == 'done' and data == 'apply':
                    target = getattr(self, '_apply_targets', {}).get(address)
                    if target:
                        form = self.member_forms[address]
                        form.update(applied_settings=target['settings'], applied_function=target['function'], applied_sweep=target['sweep'])
                        form['variables']['applied'] = 'Applied · ready'
                        if address == self.edit_device:
                            self._applied_candidate = target['settings']
                            self._applied_function = target['function']
                            self._applied_sweep = target['sweep']
                            self.applied.set('Applied · ready')
            elif kind == 'done':
                self._flush_autosave()
                self.pending = False
                self.status.set(f'System {payload} complete')
                if payload == 'open':
                    self._setup_system_view()
                if payload == 'release' and not self.system.claimed:
                    self.checked_devices.clear()
                    self._device_snapshot = None
                    self._refresh_open_label()
                    if self.closing:
                        self.closing = False
                        self.withdraw()
            elif kind == 'error':
                self._flush_autosave()
                self.pending = self.closing = False
                self.status.set(payload)
        now = time.monotonic()
        for address, button in self.aux_buttons.items():
            member = self.system.sessions.get(address)
            if not member:
                continue
            point = self.member_latest.get(address)
            output = 'ON' if member.output is True else 'OFF' if member.output is False else 'UNKNOWN'
            formatters = self.member_formatters.setdefault(address, (StableReading('V'), StableReading('A')))
            reading = f'{formatters[0].format(point.voltage_v)}  {formatters[1].format(point.current_a)}' if point else '— V  — A'
            age = f'{max(0., now - point.host_monotonic_s):.1f}s ago' if point else 'no reading'
            button.configure(text=f'{self._short_name(address)} · {output} · {age}\n{reading}')
        viewed = self.system.sessions.get(self.view_device)
        if viewed:
            output = 'ON' if viewed.output is True else 'OFF' if viewed.output is False else 'UNKNOWN'
            scanner = self.system.sessions.get(self.scan_device.get())
            suffix = self._show_scan_progress(scanner.sweep_progress() if scanner else {})
            self.output_text.set(f'{self.system.state.upper()} · {output}{suffix}')
            style = 'On' if viewed.output is True else 'Off' if viewed.output is False else 'Unknown'
            self.output_label.configure(style=f'Instrument.Output{style}.TLabel')
        self.activity('Instrument · !' if self.system.fault else 'Instrument · RUN' if self.system.acquiring or any(s.output is True for s in self.system.sessions.values()) else 'Instrument')
        try:
            self.status.set(self._export_events.get_nowait())
            self._exporting = False
        except queue.Empty:
            pass
        self._poll_autosave()
        self._update_system_status()
        self._sync_devices()
        self._controls()
        if self._dirty_plot and not self.paused.get() and self.wm_state() != 'withdrawn':
            self._draw()
        self._poll_id = self.after(150, self._poll)

    def _export_system(self):
        path = filedialog.asksaveasfilename(parent=self, title='Export complete system data', defaultextension='.zip', filetypes=[('System ZIP', '*.zip')])
        if not path:
            return
        systems = [*self._past_systems, self.system]
        samples = tuple(p for system in systems for p in system.samples)
        runs = tuple(r for system in systems for r in system.runs)
        self._exporting = True
        def worker():
            try:
                export_system(path, samples, runs)
                self._export_events.put(f'Exported {len(samples)} raw readings to {path}')
            except Exception as exc:
                self._export_events.put(f'Export failed: {exc}')
        threading.Thread(target=worker, daemon=True, name='instrument-system-export').start()
