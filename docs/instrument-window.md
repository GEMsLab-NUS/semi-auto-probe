# Instrument manual test window

Click **Instrument** after **Connect** and **Test** in the main panel toolbar. It is an independent, reusable
window; the main camera and stage controls continue to work. No AutoTest setup is
required.

1. Refresh devices (the existing Device connections discovery is reused).
2. Select an idle Keithley 2450 and click **Open device**.
3. Choose **Source V / Measure I** or **Source I / Measure V**, select front/rear
   terminals, enter the source value and protection limit, and select source and
   measurement ranges. Click **Apply parameters**.
4. Enable **Output ON** explicitly. Use **Single measurement** or **Start continuous**.
5. Use **OFF** to stop acquisition and disable output, or close the window to stop,
   confirm output OFF and release the device. The VISA connection remains open.

## 2450 functions, units and ranges

| Function | Source setting | Protection limit | Source range | Measurement range |
| --- | --- | --- | --- | --- |
| Source V / Measure I | Voltage (V) | Current (A) | Voltage | Current |
| Source I / Measure V | Current (A) | Voltage (V) | Current | Voltage |

Source and protection inputs accept plain numbers, scientific notation and SI
prefixes: `p`, `n`, `u` / `µ` / `μ`, `m`, `k`, `M`. Examples: `100mV`, `10uA`,
`500n`, `1e-6`. A bare suffix uses the field's unit: `500n` in a current field is
500 nA. Units are checked, so `10mA` cannot be entered in a voltage field. `m` is
milli and `M` is mega.

Both range dropdowns offer **Auto** (default) plus supported fixed ranges:

- Voltage: 20 mV, 200 mV, 2 V, 20 V, 200 V.
- Current: 10 nA, 100 nA, 1 uA, 10 uA, 100 uA, 1 mA, 10 mA, 100 mA, 1 A.
- Front terminals omit the rear-only 10 nA / 100 nA ranges. Changing terminals
  resets an unavailable selection to Auto without sending commands.
- A fixed source range must contain the setpoint, within the instrument's 105%
  range allowance and this UI's overall 200 V / 1 A limits. The 200 V and 1 A
  ranges cannot be combined. Protection must also fit the source power envelope.

Function and range changes require output OFF. When changing ranges with output
OFF, the old setpoint is first zeroed to avoid a range-reduction error. Measurement
ranges are selected before applying the requested protection limit. Fixed ranges
and protection are read back after Apply; unexpected instrument adjustments are
reported as failures. The applied summary shows normalized source/limit values
and both requested range selections. Changes in the fields alone do not send
commands.

## Integration and auto zero

**NPLC (0.01-10)** accepts a custom number or presets 0.01, 0.1, 0.5, 1, 2, 5,
and 10. Default is 1. Integration time is NPLC divided by the instrument's power
line frequency: 1 PLC is 20 ms at 50 Hz or approximately 16.67 ms at 60 Hz.
Longer integration reduces reading noise at the cost of measurement speed.
This is separate from the interval between completed readings.

**Auto zero** defaults to ON. It updates internal zero/reference measurements,
adding measurement time; OFF can increase drift. Both settings apply to the
selected measurement function (current when sourcing voltage, voltage when
sourcing current). Editing sends no commands. Apply writes and reads back both
settings, and shows the confirmed values in the applied summary. Stop continuous
acquisition before editing or applying settings. Actual sample spacing includes
integration, auto zero, communication and the configured interval.

## Readings and traces

Voltage and current come from the same instrument reading. Native 2450 source
readback is enabled; the programmed source value is never substituted for a
measurement. Output state is queried from the instrument, including after OFF.
The adapter targets native 2450 SCPI, not TSP or SCPI-2400 emulation.

The sample interval is the wait **after** a completed reading; measurement and
communication time also contribute to the actual rate. This is a basic live
monitor, not a high-speed digitizer. The 2450 adapter is single-channel; other
instruments, including multi-channel analyzers, currently show connection
information only.

- **Stop** stops acquisition while leaving output unchanged.
- **Pause plot** freezes the traces, not acquisition or numeric readouts.
- **Auto scroll** follows the latest 60 seconds. Disable it to pan/zoom; **Resume
  live** selects the latest segment and restores scrolling.
- The segment selector recalls individual continuous runs or single readings.
- **Replot** clears only the selected segment's visible trace and resumes live
  drawing from new points. Time traces restart their visible clock at the last
  cleared reading. Previously recorded samples are retained for CSV export.
- **Export CSV** saves all retained readings, including UTC timestamps, segment,
  elapsed time, device, VISA address, channel, voltage in V, current in A,
  recording kind (`time` or `iv`), IV source mode and source setpoint in base units.
- Closing and reopening preserves data within this application session. Data are
  not persisted automatically across application restarts. The live display is
  bounded to the latest 2,000 points per time segment and the latest 10,000
  points per I-V run. CSV includes all retained readings.

## Ownership and failures

The application's existing measurement gate operates at whole-test granularity.
While a manual control page owns a source meter, AutoTest/Mapping and VISA scans
are blocked. During an automatic test, manual control cannot be entered. Release
the manual device to hand control back. Camera viewing and stage operation are
not blocked by the Instrument window. Separate application processes or external
instrument software are outside this desktop gate.

Opening a page does not reset or enable output. Parameters change only on Apply;
mode/terminal changes require output OFF. Instrument range errors are surfaced.
Before Apply, queued instrument errors are read and recorded in the application
log rather than silently cleared with `*CLS`. Once that queue is empty, the same
Apply operation continues with the requested parameters. Success reports the
number of historical errors without requiring a second click or function change.
Each new configuration command is checked immediately: a fresh rejection names
that command and stops the batch. A queue that still has errors after 64 reads
blocks configuration and requests explicit recovery.

On I/O failure, acquisition stops, the controls disable, and an OFF attempt is
made. If OFF cannot be confirmed, ownership remains locked and the window stays
visible with an unknown-output/error message. **Release device / retry OFF**
reopens a failed transport only to retry cleanup, never to restore output.
Application exit waits for successful cleanup; emergency stop also requests it.

Implementation: `instrument_control.py` contains the serial I/O worker and the
2450 adapter; `ui/instrument_window.py` contains the Tk window. The existing
`DeviceStatusPanel` and 2450 VISA opener are reused. No new dependencies.

Protocol references: [2450 reference manual](https://download.tek.com/manual/2450-901-01_D_May_2015_Ref.pdf),
[2450 specifications](https://www.tek.com/en/datasheet/smu-2400-graphical-sourcemeter/model-2450-touchscreen-source-measure-unit-smu-instrument).

Hardware-free tests cover native command/reading mapping, off confirmation,
disconnection, ownership, queued-command cancellation, CSV, window close/reopen,
plot pause, and Tk layout. Bench validation remains necessary with the actual
2450 firmware, VISA backend and connected load before relying on measurements.

## I-V sweep

Select **Sweep V / Measure I (I-V)** or **Sweep I / Measure V (I-V)** directly
in **Function**. The control page shows start, stop, positive step magnitude (SI
prefixes accepted) and settling time per point (0-60 seconds), alongside protection,
ranges, NPLC and auto zero. Click **Apply parameters**, then **Start I-V (output ON)**.
Apply configures the source without enabling output. Editing any settings requires
Apply again before starting. Function changes require output OFF. Choose the initial direction and number of segments; each segment ends at the
next feature point (Top, Start, Low, Start when scanning up) without repeating
turnaround readings. Up to 10,000,000 points are
allowed; all points are checked against the applied ranges and protection before
output is enabled.

**Start I-V (output ON)** explicitly enables output. The existing manual
session owns the device throughout the sweep; no second VISA connection is
created. Each source level is checked for instrument errors, followed by the
settling delay and an actual V/I readback. Settling waits are interruptible between
commands. The I-V graph uses measured voltage on X and measured current on Y;
**Time / I-V** changes the graph view. Each run gets its own history segment.

Completion, **Stop** during I-V, **OFF**, and closing the window turn output OFF.
Partial scans remain available for export. An unconfirmed OFF preserves the
existing fault/ownership behavior. After a sweep, explicitly Apply manual settings
again before enabling manual output, because the sweep changed the source level.
Stop during ordinary continuous monitoring still leaves output unchanged.

## Compact monitor and stable readings

**Pin** keeps the Instrument window above other windows. **Compact** hides the
device list and source controls and reduces the window to a resizable monitor of
readings and plots. Pin, Compact, Stop, OFF, plot controls and export stay
accessible. Turn Compact off to restore the previous full-window size. Neither
option changes acquisition or output.

Readings use a sign, three decimal places, monospaced text and engineering units
(including n, u and m). Prefix hysteresis prevents repeated unit changes near
boundaries. This is display formatting only; stored/exported measurements retain
their full numeric precision.

### I-V voltage axis and engineering ticks

The monitor's **Measured V / Set V** selector changes the I-V horizontal axis.
Measured V uses the actual source readback; Set V uses the recorded voltage sweep
setpoints. Neither option changes measured current or exported data. Current-source
scans have no voltage setpoint and use measured voltage; selecting Set V returns
to Measured V with an explanation. Axis changes reset I-V zoom extents.

Voltage and current plot ticks use engineering prefixes (nA, microamps, mA,
microvolts, mV, etc.) instead of a separate scientific multiplier. Time traces
use the same formatting on their voltage/current axes.

### Fixed measurement range and protection compatibility

Before sending any Apply commands, fixed measurement ranges are checked against
the protection limit: the limit must exceed 0.1% and not exceed 105% of the range.
For example, a 1 uA measurement range cannot be combined with a 10 uA current
limit. Select a compatible range (10 uA in that example), Auto, or explicitly
choose a compatible protection limit. Selecting a smaller fixed measurement range automatically reduces an excessive
protection value to the nominal selected range, with a visible old/new-value
notice. This only edits the field; Apply is still required. Selecting a larger
range or Auto never raises protection. Invalid combinations entered afterward
remain editable without disconnecting the device.
The same check applies to voltage protection when sourcing current.

See the [2450 reference manual](https://download.tek.com/manual/2450-901-01_D_May_2015_Ref.pdf)
and [2450 firmware release notes](https://download.tek.com/software/supporting_files/2450-FRP-v1_7_12_Apr_2022_RNs_2450-FRP-V1.7.12.pdf).

### Inline segment scan settings

All settings are on the I-V control panel: **Start**, **Top**, **Low**, **Initial
direction** (up/down), **Segments**, step magnitude and settling time. There is
no separate path dialog or single/return/cycle mode.

One segment is one monotonic leg ending at a feature point. Up follows
`Top -> Start -> Low -> Start`; down follows `Low -> Start -> Top -> Start`.
For Start=0, Top=1, Low=-1, up:

- 1 segment: `0 -> 1`.
- 2 segments: `0 -> 1 -> 0`.
- 3 segments: `0 -> 1 -> 0 -> -1`.
- 4 segments: `0 -> 1 -> 0 -> -1 -> 0`.

The scan ends at the last segment's feature point and turns output OFF. Start must
be within the bounds. At a boundary choose the inward direction; zero-length legs
are omitted so consecutive readings are not duplicated.
Current scans use the same controls in A. Each leg includes its exact endpoint,
with a shorter final step when necessary; turnaround points are not duplicated.
Up to 1,000,000 segments and 10,000,000 total readings are supported. Edit, **Apply**, then
**Start I-V**. Changing a bound, direction or segment count requires Apply again.

### I-V semilog / loglog

The plot toolbar has independent **X log** and **Y log** toggles. Both off gives
linear I-V; one on gives semilog; both on gives loglog. Each logarithmic axis uses
the absolute value and its axis label shows magnitude bars. A linear axis keeps
its sign. Zero values on a log axis are omitted with a line break, without adding
an artificial floor. Empty or zero-only data retain an empty usable plot.
Measured V / Set V selection works with both scales. Engineering-unit tick labels
remain enabled. Switching scales resets plot zoom but does not change acquisition,
the numeric readouts, retained samples or CSV exports. Time plots stay linear.

### Long runs

Sweep plans are computed lazily with constant memory, rather than preallocating
millions of source levels. Apply reports the planned number of points and segments.
The live I-V trace keeps the latest 10,000 points to bound redraw work. All collected
readings remain in the session and CSV export includes all of them; this display
window does not stop acquisition. Collected data still consume memory proportional
to the run length and are not automatically saved to disk. Export before exiting.


## Scan progress

The monitor header shows `OUTPUT ON · Segment current/total` during I-V scans
(`Seg` in Compact). Segment means one directional leg, not a full round trip.
The two-pixel blue line in the existing gap above the reading cards shows acquired
points / planned points, so it advances within a segment without adding a layout
row. Short first legs and shared turning points use the actual executed plan.
Normal completion retains 100%; stopping/fault retains actual partial progress.
Applying parameters, opening another device, or starting a non-sweep acquisition
clears the old indicator. Group mode reports the scan member's progress even when
an auxiliary time plot is selected. Plot pause and Replot do not affect progress.


## AutoSave

The **AutoSave** checkbox sits beside Export and remains available in Compact.
It defaults to on; new acquisitions are saved automatically. Re-enabling it backfills raw readings retained in the current
window and saves new readings in a background writer; disabling it flushes queued
records and stops enqueueing new ones. Re-enabling catches up without duplicating
records. Plot pause, history sampling and Replot do not discard saved points.

The root is the project directory (not the shell's current directory):
`manualtest_session/YYYY-MM-DD/`. The date is the acquisition start date in local
time; a run crossing midnight stays together. This directory is Git-ignored.

Example CSV name:
`143012_084_IV-V_start0V_low-1V_top1V_up_seg20_NPLC1_8a31f942__2450_GPIB0-18_CH1.csv`

Names include time, test type, representative settings, run identifier, device,
address and channel. The matching `_config.json` contains the complete executed
configuration: limits, source/measurement ranges, terminal, NPLC/auto-zero,
sweep step, bounds, direction, segment count and settle delay. Constant/one-shot
measurements receive their own run identifiers. Group runs share one configuration
file and have one CSV per device, retaining group run IDs and independent clocks.
The existing manual CSV/ZIP Export commands remain available.

Files are appended incrementally and flushed about once per second, on acquisition
end, and during clean shutdown. The disk worker drains queued records before normal
process exit. A disk error disables AutoSave and reports the error without stopping
measurement; after fixing the destination, re-enable to write a recovery copy of
retained data. An abrupt process/power failure can lose the last unflushed records.
