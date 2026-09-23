# Manual DP100 and HP 6614C supplies

Instrument supports the existing Keithley 2450 plus DP100 and HP/Agilent/Keysight
6614C. Other Hewlett models remain unsupported rather than receiving 6614C commands.

DP100 uses the application's shared vendor-DLL connection, not VISA discovery.
Its local device row is visible as **Connect USB** even when the application has not
connected yet. Open device attempts the connection on the manual worker thread;
this is not a claim that the device has already been physically detected. The normal
connected state appears after successful connection. No output is automatically enabled.

DP100 controls: voltage, current limit, OVP, OCP, writable preset 1–9. Apply writes the
selected preset and activates it with output off, then confirms settings and output
using GetCurrentBasic. DLL units are whole mV/mA; sub-milli settings are rejected.
The manual panel bounds output at 30 V, 5 A, 100 W. Existing workflow defaults for
the installed laser are unchanged. Preset writes occur only on explicit Apply.

HP 6614C controls: voltage, current limit, OVP, OCP trip enable, and 20 mA / 500 mA
measurement range. There is no fabricated Auto-range or NPLC option. Apply requires
output OFF and checks device errors and programmed-value readback. OFF / Clear HP
protection first disables output; it never resumes output. CV / CC / CC- and protection
conditions come from the operation and questionable condition registers. The supply
regulates voltage or current according to the load and the selected voltage/current limits.

Both panels display actual measured V/I with SI formatting, calculated V×I power,
separate programmed-value readback, single/continuous acquisition, and shared time
plots. CSV records all measurements, output state, power, and available device status.
The plots, pause, Replot, history and export behavior are shared with Instrument.
These supply panels do not offer the Keithley I-V sweep function.

All I/O uses the existing serial manual worker and application task exclusion. Close
and device switch stop acquisition, confirm output OFF and release ownership; shared
DP100 USB stays connected. A failed OFF confirmation retains ownership and reports
unknown output, allowing an explicit Release / retry OFF. Disconnect stops collection
and retains recorded data; reconnect does not resume output automatically.

Protocol references:
- Local `refs/ATK-DP100 DLL使用文档.pdf`, sections 3.5–3.10 and 3.13; bundled DLL public
  signatures were also inspected (its system-parameter API differs from the PDF revision).
- [Agilent 663xxA/663xB/661xC programming guide](https://ridl.cfd.rit.edu/products/manuals/Agilent/power%20supplies/CD1/model/663xxprg.pdf),
  current measurement range, output/protection, and status condition registers.
- [ALIENTEK DP100 user manual](https://akizukidenshi.com/goodsaffix/DP100_manual.pdf).

Validation uses fake transports and Tk integration. Live instrument verification remains
necessary for the installed DLL/firmware and HP VISA transport; tests do not drive hardware.
