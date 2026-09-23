# Keithley IV workflow card

New AutoTest/Mapping workflow IV cards use the same segment sweep settings as
Instrument: source voltage / measure current or source current / measure voltage,
Start, initial direction, Top, Low, Segments, step and settling time. Each segment
ends at one feature point. Up follows Top -> Start -> Low -> Start; down follows
Low -> Start -> Top -> Start. For Start=0, Top=1, Low=-1 and up, three segments
give 0 -> 1 -> 0 -> -1, while four segments return to 0.

The node exposes these fields directly and its inspector also includes source
range, measurement range, voltage/current protection, terminal, NPLC and auto zero.
Use Auto or a supported fixed range, with SI values such as 10uA, 100mV or 500n.
Top retains the previous list/range capability, e.g. 1,2,3 or 1:1:3; each Top gets
its own sweep with the same Low and segment settings. Selecting a smaller measure
range reduces an excessive protection field to the nominal range with a notice;
loading a workflow alone never changes protection.

Both controls share the pure settings and lazy point planner in smu_settings.py.
The new workflow runner configures the existing VISA connection through the same
checked adapter as Instrument, and reads paired measured V/I values. Plans permit
10,000,000 points and 1,000,000 segments without preallocating the point list.
The existing workflow output_off_after setting is retained; default true confirms
output OFF at completion. Interrupted settling waits check Stop every 50 ms between
instrument commands. Existing workflow CSV/statistics output remains available;
metadata includes Low, direction, segments, source range and auto zero.

## Existing workflows

Stored workflows without Segments keep a blank Segments field and continue using
their original single/double/dualpolar or bidirectional path. Saving and reloading
preserves that behavior, including old non-divisible-step and multi-Top paths.
To update an old card, explicitly fill Segments and review Start, Top, Low and
initial direction. Blank Segments is a compatibility marker, not an additional
scan mode for new cards. Original legacy instrument behavior remains isolated from
the new checked configuration path.

Tests use injected VISA devices; actual firmware/transport bench validation has
not been performed by the agent.
