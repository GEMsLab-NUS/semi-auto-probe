# Instrument multi-device systems

Select up to three supported devices using the checkboxes, then **Open selected**.
Opening reads identity/status only and never enables an output. Multiple devices
require a Keithley 2450 scanner; other 2450s, HP 6614C and one shared USB DP100 can
provide constant outputs. Single-device operation retains its existing controls.

## Operation

- Device tabs edit one member at a time and retain each form and Apply result.
  Switching tabs sends no device commands and does not change the displayed plot.
- Select the scan device by address. Configure its I-V function, bounds, direction,
  segments, step and settling time using the existing controls. Auxiliaries use
  constant functions. The arrows arrange auxiliary startup order; each has a
  settling delay (default 0.2 s). **Aux s** is the independent auxiliary sample
  period (default 1 s, allowed 0.2–60 s).
- Apply one member or **Apply all**, then **Start system**. All outputs must first
  be confirmed OFF; an already-enabled device is never silently taken over.
- Auxiliaries start in the selected order, each output is confirmed and its settle
  delay is observed. The scan then runs. Auxiliary reads are independent and do
  not become a barrier at every scan point.
- Normal completion closes the scanner first and auxiliaries in reverse order.
  **Stop all**, **All OFF**, any run fault, release and window close close the group.
  Failed OFF/readback leaves that member claimed and marked unknown; other members
  still receive shutdown requests. Select its tab for **Retry OFF**, or retry the
  group release. Connections are retained as with the existing adapters.
- Group membership, roles and parameters are locked during execution. Tabs and
  graphs remain selectable. AutoTest and Mapping remain globally excluded while
  any manual member is claimed, including a failed cleanup.

Auxiliary readout strips show the latest real V/I and its age, not a synchronized
reading of the current scan point. Click one for its time graph, and **Main** to
restore the scanner view. Compact retains readouts, graph and group stop controls.
Long status messages use a compact summary; click it for full details.
Existing fresh point/line gradients, current-point marker, log axes, representative
history and Replot remain unchanged. Replot only clears the selected display.

## Data and timing

A ZIP export contains `configuration.json`, `devices.json` mapping file names to
stable device addresses, and one complete CSV per device. Run UUID, role, source
configuration, independent instrument timestamp and host-relative system time
allow streams to be associated without equating device clocks. Raw data and run
snapshots survive window close for the current application session. CSV export
continues for single devices.

`InstrumentSystem` coordinates independent `ManualSession` workers. GPIB interface
transactions share a non-preemptive priority/FIFO arbiter: OFF requests precede scan
requests, which precede auxiliary polling. An already-started transaction cannot
be interrupted. Missed sampling deadlines are skipped rather than caught up.
Consequently this provides software sequencing, not hardware synchronization or
guaranteed constant spacing across a shared bus.

The configuration separates device bindings, constant settings, a list containing
one scan axis, and startup/end policy. Samples include a path index reserved for
future outer scan axes; there is no multi-axis editor in this version.

## Validation

Injected-device tests cover multiple addressed 2450s; 2450 + HP + DP100; partial
open/apply failure; output takeover rejection; startup order/delays; stopping during
settling; disconnect and OFF failure; independent slow auxiliary I/O; bus priority;
run IDs, complete ZIP data and reopening with different members. Tk tests cover
selection limits, per-tab state, no control writes on tab changes, view restoration,
Compact, minimum geometry, mixed forms and window-close cleanup.

Physical instrument integration must still check actual bus latency, DP100 DLL
behavior and output readback under the intended wiring and protection settings.
