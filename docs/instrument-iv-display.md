# Instrument I-V display

The I-V view shows the entire acquisition's history, rather than a rolling tail.
Four consecutive feature-point segments form one display cycle; the last cycle may be incomplete.
Up to 100 cycles are displayed. Beyond that, cycles are selected uniformly across the
whole acquisition, always including the first and latest. Selected cycles remain in
chronological drawing order, with the latest above older curves. Turning endpoints
are shared between adjacent cycles to keep each displayed path continuous.

For rendering performance, a dense cycle is uniformly sampled to at most 2,000
points, including both endpoints. This is only a display approximation; narrow
features between displayed points may not appear. CSV exports retain every raw
measurement. The newest measured point and fresh trail use original samples,
independent of history decimation.

The blue hollow circle marks the latest measured point in the selected acquisition.
Fresh points selects 0–1,000 recent points (default 10); the oldest is green and
newest orange. Zero hides this trail, but leaves the current-point marker visible.
Log axes use absolute values; a zero on a log axis is omitted, including its marker.
Pause freezes all drawing while acquisition continues. Resume catches up.
Replot clears visible history and markers, without deleting recorded CSV data.

Time-mode plots retain their existing rolling display behavior.
