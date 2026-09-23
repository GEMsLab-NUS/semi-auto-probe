# Manual Instrument acquisition timing

## Findings and changes

The original worker called Queue.get(timeout=0.05) before every acquisition phase.
This added a 50 ms wait even when a measurement or source step was already due,
and quantized short settling delays. Continuous measurement scheduled the next
sample relative to completion, adding read/communication time to the requested
interval. The 2450 path also made separate error and output queries for every point.

The worker now waits until the next deadline, while still prioritizing queued
Stop/OFF/Release commands. Continuous Period is start-to-start; 0 requests the
fastest available read rate. It is anchored at the first actual measurement start.
When a read overruns the next deadline, missed scheduling slots are counted and
skipped, without fabricating samples or issuing a catch-up burst. Windows 1 ms
multimedia timer resolution is requested only during active acquisition and released
on stop, release, fault, completion, or shutdown. This improves host scheduling;
it is not a hard-real-time guarantee.

The 2450 manual worker now sends one SCPI multi-query per point:

    :READ? "defbuffer1", SOUR, READ, REL;:OUTP?;:SYST:ERR?

All three responses are validated before the sample is published. In I-V, a source
level write is checked by that same frame's error query, so rejected source changes
cannot produce a saved point labeled with an unconfirmed new setpoint. Configuration
and output commands retain their existing checks. Standalone driver measurement and
autotest workflows keep their existing interfaces.

During a manual I-V scan, the adapter saves the instrument source delay/auto-delay,
disables those delays, and uses the explicit UI settling time after each source write.
Output OFF restores the saved delay settings. This avoids software settle plus a
second hidden source delay. It does not disable instrument measurement autoranging,
autozero, filters, or actual source readback. Settling time is a minimum wait after a
source command, not a promise of equal spacing for a variable-duration I-V scan.

## Reading the timing display and export

The latest-reading line includes actual delta-t and I/O duration for the 2450.
The time plot uses the device's REL timestamps relative to the first point of the
acquisition, rather than host receipt time. CSV includes:

- `instrument_time_s`: unmodified device REL timestamp; blank for other adapters.
- `elapsed_s`: device timestamp minus first device timestamp for 2450, otherwise host elapsed time.
- `host_elapsed_s`: response completion time relative to acquisition start.
- `timestamp`: UTC host receipt timestamp, not the exact ADC instant.
- `read_duration_s`: host duration of the complete measurement/status transaction.
- `sample_interval_s`: actual delta between the current and preceding recorded points.
- `missed_periods`: cumulative missed target scheduling slots, not discarded readings.

All real measurements still enter raw storage and CSV. Plot refresh is independent
(approximately 150 ms), so several readings can arrive between visible redraws.

## Why NPLC 1 is not a 20 ms point period

At 50 Hz, NPLC 1 specifies a 20 ms integration aperture. It excludes communication,
settling, range changes, automatic zero/reference measurements, and actual source
readback. Source readback measures the sourced quantity in addition to the selected
measurement quantity; it remains enabled to keep both displayed V/I values real.
For faster operation users can explicitly choose lower NPLC, fixed suitable ranges,
and Auto zero OFF, accepting the associated noise/drift tradeoffs. No such precision
settings are silently changed by this optimization.

Strictly periodic acquisition beyond Windows/VISA scheduling requires a separate
instrument-trigger/timer buffered acquisition path; this change accelerates the
existing host-controlled manual path and reports actual timing honestly.

## Reproduction and validation

Run `.venv/Scripts/python.exe scripts/benchmark_instrument_timing.py` (fake transport,
no hardware). With a simulated 20 ms measurement and 2 ms transport operations:

| Scenario | Original mean | Optimized mean | Optimized interval range |
| --- | --- | --- | --- |
| Continuous, target 50 ms | 84.1 ms | 50.1 ms | 49.1–51.5 ms |
| I-V, settle 20 ms | 145.4 ms | 45.9 ms | 45.2–46.6 ms |

These are local software benchmark observations, not measured 2450 performance.
Timing regression tests cover deadline arithmetic, overruns, device-vs-host clocks,
compound response/error parsing, timer pairing, and source delay restoration.

References:
- [2450 reference manual](https://download.tek.com/manual/2450-901-01_D_May_2015_Ref.pdf),
  SCPI multiple query syntax (5-2), READ/buffer elements, source delay/auto delay (6-76/77).
- [Tektronix source readback explanation](https://www.tek.com/en/support/faqs/2450-source-readback).
- [2450 datasheet](https://www.tek.com/en/datasheet/smu-2400-graphical-sourcemeter/model-2450-touchscreen-source-measure-unit-smu-instrument),
  measurement-speed test conditions.
