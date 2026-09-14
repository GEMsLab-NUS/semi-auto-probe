<p align="center">
  <img src="assets/logo-system-diagram.svg" alt="Semi Auto Probe logo" width="120" />
</p>

<h1 align="center">Semi Auto Probe</h1>

<p align="center">
  <a href="#overview"><img alt="Project type" src="https://img.shields.io/badge/project-open--hardware-2563eb"></a>
  <a href="#hardware"><img alt="Hardware" src="https://img.shields.io/badge/hardware-documented-0f766e"></a>
  <a href="#software"><img alt="Software" src="https://img.shields.io/badge/software-python-3776ab"></a>
  <a href="#cost-summary"><img alt="Build cost" src="https://img.shields.io/badge/build_cost-SGD_3%2C477.79-f59e0b"></a>
  <a href="#development"><img alt="Tests" src="https://img.shields.io/badge/tests-unittest-16a34a"></a>
</p>

<p align="center">
  An open-source semi-automatic probe station that combines a motorized XYZ stage, microscope vision, probe manipulation, autofocus, and stitched-field imaging in one reproducible desktop workflow.
</p>

<p align="center">
  <a href="#overview">Overview</a> |
  <a href="#hardware">Hardware</a> |
  <a href="#software">Software</a> |
  <a href="#getting-started">Getting Started</a> |
  <a href="#development">Development</a>
</p>

## Overview

`Semi Auto Probe` is an open-source probe-station project with both hardware and software layers:

- **Hardware stack:** a motorized `XYZ` micro-positioning stage, 5-phase stepper motor drive electronics, a 4-axis motion-control board, microscope optics, a USB camera, probe arms, tungsten probes, and optical-platform fixtures.
- **Software stack:** a Python desktop application that controls motion over RS-232, displays live microscope video, supports visual metrology, performs autofocus, and captures stitched mosaics.
- **System goal:** make a compact semi-automatic probing workflow reproducible enough that another lab or maker can understand what to buy, how the pieces fit together, and what the software contributes.

The physical build uses a 4-axis controller, but the application intentionally operates the first three axes as `X`, `Y`, and `Z`. The stage provides precise motion, the microscope stack provides visual feedback, and the software ties them together into a practical workflow for device probing and imaging.

### What the system can do

- Drive a 3-axis probe stage over `115200, N, 8, 1`
- Show live USB microscope video with focus overlays
- Move visible image points to the field center after calibration
- Run autofocus on the `Z` axis with multiple focus metrics
- Capture serpentine mosaics with FFT-based image registration
- Build and reuse FocusMap planes for tilted samples
- Capture GDS-aligned image matrices, time stacks, and Z stacks
- Run assisted AutoTest point grids with photos, Keithley 2450 IV sweeps, WobbTest contact optimization, and Keysight B1500 transfer/output curves
- Trace selected GDS edges with FocusMap-aware safe/start/contact motion
- Expose a token-protected browser dashboard for remote AutoTest session history and file access
- Plan guarded high-level workflows from the AI Agent panel
- Expose raw communication tools for controller debugging

### Strategic roadmap

The project roadmap focuses on three core technological enhancements:

- **Mechanical micro-nanofabrication:** develop an automated mobile platform for non-chemical-contact pattern processing, enabling precision patterning of 2D materials inside a controlled glovebox environment while reducing chemical contamination and interface damage.
- **Integrated optical systems:** upgrade the platform with multi-channel monochromatic light sources and DMD-based spatial light modulation, expanding the system toward PL in-situ testing and high-resolution photocurrent mapping.
- **High-stability electrical control:** transition toward a robust modular architecture with standardized wire-bonding array boards, moving beyond traditional probe-based workflows to improve testing efficiency and signal stability for multi-array devices.

## Hardware

The tables below list the hardware actually used in this build. Purchase links are intentionally omitted; the goal is to document the bill of materials and the rough cost of reproducing a comparable setup.

### Cost summary

| Subsystem | Main hardware | Cost |
| --- | --- | ---: |
| Microscope system | `sanqtid` coaxial microscope lens, focusing stand, USB microscope camera | `SGD 770.98` |
| Probe system | Probe holders, 3-axis probe fixtures, probe stage, tungsten probes | `SGD 1,219.51` |
| Motion and control | `KOHZU` motorized XYZ stage, `KOHZU MD-355F` driver, 4-axis controller, RS-232 cable | `SGD 1,161.93` |
| Optical-platform accessories | Magnetic base plates and M6 optical posts/adapters | `SGD 325.37` |
| **Total** |  | **`SGD 3,477.79`** |

> The total above is based on the saved order pages and their displayed `SGD` paid amounts. Some orders note that consolidated cross-border shipping can be paid separately, so any later standalone forwarding fees are not included here.

### Microscope system

| Item | Model / specification | Notes |
| --- | --- | --- |
| Coaxial microscope lens | `sanqtid` `3200x` coaxial-light lens | Listed as a 400-3600x industrial electronic microscope lens |
| Focusing support | `sanqtid` stereo fine-focus stand | 76 mm support with fine adjustment |
| Microscope camera | USB2.0 microscope camera, `5.1 MP` | Used for live vision, focus scoring, and mosaic capture |

### Probe system

| Item | Model / specification | Notes |
| --- | --- | --- |
| Left probe holder + 3-axis fixture | `JY050-12-L + JY800-1.5-TRB` | Manual precision positioning |
| Right probe holder + 3-axis fixture | `JY050-12-R + JY800-1.5-TRB` | Manual precision positioning |
| Additional probe stage | 3-axis probe sliding fixture | Listed as probe holder plus 3-axis clamp |
| Tungsten probes | `WG-38-0.5`, `WG-38-1.0`, `WG-38-2.0`, `WG-38-5.0` | Probe-tip sizes span roughly 1-10 micrometers across purchased variants |

### Motion and control system

| Item | Model / specification | Notes |
| --- | --- | --- |
| Motorized stage | `KOHZU` electric `XYZ` precision stage | Listed travel: `20 x 20 x 9 mm` |
| Motor driver | `KOHZU MD-355F` | 3-axis driver for 5-phase stepper motors; up to 250 microstep divisions |
| Motor type | 5-phase stepper motor | Driver datasheet uses a `0.72 deg` basic step angle |
| Motion controller | 4-axis controller module | RS-232 by default, with 16 NPN inputs and 16 transistor outputs |
| Serial adapter | USB to RS-232 cable | Used between the PC and controller |

Local reference documents for this subsystem are kept in [`refs/`](refs/):

- [`MotorDriverDatasheet.pdf`](refs/MotorDriverDatasheet.pdf)
- [`ControlUnitDatasheet.pdf`](refs/ControlUnitDatasheet.pdf)
- [`4-Axis Controller Communication Protoco·.pdf`](refs/4-Axis%20Controller%20Communication%20Protoco%C2%B7.pdf)
- [`Comm Protocal.txt`](refs/Comm%20Protocal.txt)

### Optical-platform accessories

| Item | Model / specification | Quantity |
| --- | --- | ---: |
| Magnetic optical base plate | `LPTP20080` | `3` |
| M6 optical post / adapter | `LPMP125`, 25 mm | `12` |
| M6 optical post / adapter | `LPMP1100`, 100 mm | `12` |
| M6 optical post / adapter | `LPMP1150`, 150 mm | `12` |

## Software

The software is a Python desktop application for operating the semi-automatic station. It combines motion control, microscope vision, calibration, autofocus, and imaging workflows in one interface.

### Core capabilities

- 3-axis controller integration over serial communication
- Live USB camera preview with focus-score overlays
- Visual tools for point-to-point distance, point-to-line distance, polygon area, and image-point centering
- Autofocus with coarse search, refinement, focus-history plots, and CSV export
- FocusMap plane generation, 3D plane visualization, save/load, and optional Z lock for mapped XY moves
- Serpentine image stitching with flat-field correction, FFT phase-correlation registration, quality overlay, manual recomposition, T-stack, and Z-stack acquisition
- Read-only GDS layout viewing, affine GDS-to-stage calibration, live FOV overlay, matrix overlays, and two-step click-to-move navigation
- GDS-aligned ImgMatrix capture with per-session image output and manifest files
- AutoTest automation for layout-defined point arrays, probe-assist overlays, contact approach, photo capture, IV/WobbTest/B1500 measurement flows, and per-device result folders
- EdgeTrace planning from GDS geometry with work bounds, path preview, safe-height moves, start moves, contact moves, segment execution, and auto-run mode
- AI Agent planning panel with live hardware context, rule-based fallback, LLM planner support, and confirmation-gated workflow execution
- Read-only FastAPI web dashboard with token-protected AutoTest session, JSON metadata, file download, and client-connection endpoints
- Persistent local configuration for optical calibration and motor mapping
- Raw TX/RX communication console for protocol debugging

### Application pages

| Page | Purpose |
| --- | --- |
| `Main` | Live vision, visual measurement, image-point centering, keyboard/jog motion, probe-down guard, GDS/FOV overlays, position readout, administrator-gated three-axis homing, zeroing |
| `Communication` | Raw command entry, communication-test frame loading, last TX/RX display, hex history |
| `AutoFocus` | Z autofocus, focus metric selection, score plots, manual Z jog, Z zeroing |
| `FocusMap` | AF-plane mesh setup, autofocus sampling, plane fitting, 3D visualization, mapping save/load, mapped-Z lock |
| `LayoutBond` / `LayoutMap` | Read-only GDS viewer, layer toggles, GDS/stage calibration, current FOV overlay, selected-target movement |
| `ImgStitch` | Serpentine mosaic capture, overlap settings, stitch preview, quality diagnostics, recomposition, T-stack, Z-stack, optional four-corner or FocusMap plane Z |
| `ImgMatrix` | GDS-aligned matrix acquisition using LayoutMap calibration, FOV preview overlays, per-tile capture, T-stack/Z-stack reuse |
| `AutoTest` | Grid/device automation with FocusMap-aware Z approach, probe-assist overlays, photos, Keithley IV, WobbTest, and Keysight B1500 flows |
| `EdgeTrace` | GDS edge/path selection, work-bound filtering, safe/start/contact steps, segment run, and auto edge tracing |
| `AI Agent` | Natural-language workflow planning, live microscope/context view, guarded execution of existing high-level workflows |
| `Config` | Objective/eyepiece selection, pixel calibration, motor mapping, controller speed read/write, conversion display |

### Supported protocol capabilities

- Communication feedback test
- Realtime position enable/disable
- Single-axis position reads
- I/O status reads for home inputs
- Three-axis Go Home with completion feedback
- Controller speed readback (`D5` / `B2`-`B4`) and temporary or power-off-persistent writes (items 10-13)
- Clear-position commands
- Single-axis relative and absolute moves
- 4-axis coordinated relative move command generation
- Coordinated-move completion handling
- Decelerated and emergency stops

### Instrument automation

AutoTest can combine motion, imaging, contact handling, and instrument measurements into one point-array workflow. Current measurement cards include:

| Flow | Instrument / action | Output |
| --- | --- | --- |
| `Entity Pause` | Timed wait between steps | Status log only |
| `Photo` | Current microscope frame | `autotest_session/<timestamp>/images/*.png` |
| `Keithley IV` | Keithley 2450 voltage or current sweep over VISA | `iv/*_iv.csv` plus JSON metadata |
| `WobbTest` | Contact-current search using Keithley 2450 while wobbling Z and optionally XY | `wobb/*_wobb.csv` plus JSON metadata |
| `B1500 Transfer` | Keysight B1500 FET `Id-Vg` curves with configurable drain/gate SMUs | long/wide/per-curve CSV files plus JSON metadata |
| `B1500 Output` | Keysight B1500 FET `Id-Vd` curves with configurable gate-bias list | long/wide/per-curve CSV files plus JSON metadata |

VISA-backed measurement flows require the relevant instrument, driver stack, and resource names to be available on the acquisition PC. The app validates parameters locally and writes outputs under `autotest_session/`.

### DP100 laser defaults

The DP100 laser nodes and built-in light-waveform templates default to the installed **3–5 V, <30 mA, <5 mW laser module**: **3.0 V supply, 10 mA current limit, 3.3 V OVP and 25 mA OCP**. The three-drive-level template uses 3.0 V with 5/8/10 mA limits. These are electrical settings, not calibrated optical power or guaranteed distinct optical intensities. Previously saved custom workflows keep their explicit settings; newly created nodes/templates use the new defaults. Set the constant laser drive in **Config Measure Flow → DP100 Laser Output → Output → Voltage (V) / Current limit (A)**; enter `0.010` A for 10 mA. Waveforms finish with output off by default.

### Wire-bond Test and the shared measurement workflow

In **Config → ADMIN MODE**, unlock the existing admin controls and select **Enable Wire-bond Test in AutoTest**. The setting is saved in `probe_config.local.json`. Only an admin can change it, and changes are blocked during active tasks or motion. Operators can run the mode an admin has enabled. AutoTest automatically shows either Probe Test or Wire-bond Test; there are no mode tabs in AutoTest.

Both modes use the same **Config Measure Flow** button, editor, node parameters, graph connections, conditions, light-source links, and workflow library. Switching modes preserves the workflow. Probe Test retains device coordinates and contact/approach settings. Wire-bond Test substitutes a rectangular stage scan and fixed-Z XY motion, executing the shared workflow at every settled position.

1. Enable Wire-bond Test in Config and keep the probes in **Separate**. Chip and bonded wiring move with the stage. Alignment/autofocus can be performed before the scan.
2. Jog to opposite corners and use **Record corner A/B**, or enter stage-coordinate bounds in µm.
3. Set ΔX/ΔY, `serpentine` (alternating rows) or `raster` (+X each row), and move-settle time.
4. Use **Config Measure Flow** to configure the instruments. **Electrical → 2450 Fixed-bias Current** applies a bias, waits and samples current, then closes its output when the node exits by default; Mapping can optionally hold that bias until scan exit. It is available in both modes. IV, HP6614C, B1500 and implemented laser nodes use their existing executors. WobbTest is rejected before a fixed-Z scan because it moves the probes. Unimplemented placeholder nodes are also rejected rather than reported as measurements.
5. **Preview plan** displays coverage, path, point count and the workflow size. **Start mapping** moves XY, waits for settling, then runs the workflow. No workflow runs during a move or row return. Camera, GDS, LayoutMap and FocusMap are not prerequisites; a Photo node requires a camera frame.
6. Choose a numeric workflow result in the heatmap selector, or display workflow completion. Click a cell to inspect metrics, sample readings and artifact paths. **Load mapping…** reloads completed or partial results without hardware.

The grid starts at `(Xmin, Ymin)`, with X as the fast axis. Each axis uses `min + k × step` up to its maximum; an unaligned endpoint is not appended. The preview reports the unsampled remainder. Each axis is limited to 500 points, and steps that collapse to duplicate controller positions are rejected. Heatmap axes are **stage coordinates**, not registered chip/image coordinates. The scan captures the current Z and never commands Z motion.

Sessions save `mapping.json` (workflow snapshot, geometry, calibration, fixed Z and final status), `samples.csv` (2450 samples with node/position associations), `mapping.csv` (per-point status and workflow metrics), `mapping.png`, and `mapping_artifacts.sqlite3`. Successful points store their complete node outputs and execution records as compressed, transactional SQLite artifacts, then remove the temporary `points/<point>/` tree; failed or interrupted points keep their loose directory for diagnosis. Artifact paths in `mapping.csv` remain stable and can be read from either old loose-file sessions or the compact database. Different nodes or biases are never averaged together. Pending cells are gray, interrupted cells amber, failed cells red, and completed cells without the selected scalar are green. Previous version-1 fixed-bias and version-2 loose-file mapping sessions remain readable. Existing Web file browsing and downloads include the compact artifact database.

Bias lifetime follows the workflow: the fixed-bias current node closes its output on node exit unless Mapping bias holding is enabled; an IV node with `Output off after=false` can retain bias between nodes/positions. All outputs used and retained by Wire-bond Test are closed at scan exit. A DP100 used by the workflow is also shut down. **Stop mapping** cancels waits and exits after an outstanding move/read returns. Emergency stop also stops stage motion; the UI stays locked until cleanup completes. Acquired partial results are preserved, cleanup errors are displayed, and the stage remains at its last position. There is no automatic dark-current subtraction, refocusing during a scan, or resume of interrupted scans.

Set **Move settle (s; 0 = fast)** to `0` for the fast reached-position path. After the controller reports that each move has finished, Mapping performs one XYZ read to verify target X/Y and fixed Z, rather than waiting for three identical XYZ reads. Samples and the point result are flushed together at every point boundary. A controlled Stop or point error still flushes samples acquired at that point. This remains settled point sampling rather than continuous-motion synchronized acquisition. For the quickest TEMT6000 workflow use `NPLC=0.1`, `Samples=1`, and `Bias settle=0`; increase NPLC or samples when lower noise matters more than scan time. Existing saved workflows keep their explicit values.

Hardware acceptance: use a small 3×3 area with experiment-appropriate workflow limits; verify scan direction, fixed Z, node order, Stop/output OFF, and correspondence between manual readings and map cells. Automated tests use simulated instruments and do not validate physical illumination or bonding clearance.

### TEMT6000 spatial-response template

Open **Config Measure Flow → Template → TEMT6000 Mapping - Fixed Bias + Constant Laser**. This connects a constant DP100 laser-output node to a 2450 fixed-bias current node:

| Setting | Template default |
| --- | --- |
| Detector connection | Keithley 2450 **front** SOURCE HI → collector, SOURCE LO → emitter |
| Detector bias / compliance | **+5.0 V / 1 mA**; VISA `GPIB0::18::INSTR` |
| Current acquisition | Auto range, NPLC 0.1, 1 sample per position (fast mapping default) |
| Detector bias settling | No additional delay (fast mapping default) |
| Laser electrical drive | **3.0 V**, 10 mA limit, OVP 3.3 V, OCP 25 mA; preset 1 |
| Laser settling | 2 s after first enabling output |
| Optical conversion | TEMT6000, **650 nm**, power correction ×1, measured dark offset 0 A |

The detector default uses the 5 V characterization condition in the [Vishay TEMT6000X01 datasheet](https://www.vishay.com/docs/81579/temt6000.pdf). Wiring assumes direct access to the phototransistor collector/emitter. A breakout board with an emitter/load resistor does not necessarily hold the transistor itself at the applied supply voltage; check its circuit before using this connection. The 1 mA compliance is a starting limit, not a promised maximum illuminated current.

In Mapping, **Set once per mapping scan=true** applies DP100 settings only at the first visited laser node. **Hold bias during mapping=true** establishes the 2450 bias at the first measurement node, then reuses the session at subsequent positions. Neither output is cycled between points or during row returns; only settled positions are sampled. Completion, Stop, or an error closes both outputs. A conflicting node using the same held 2450 resource, or another DP100 node alongside a set-once laser node, is rejected before scanning. In Probe Test these Mapping-only options leave the usual per-point execution unchanged.

The template enables **Check measured bias**: a voltage deviation exceeding the larger of 5% and 50 mV stops the scan and retains partial data, with a message to check compliance and wiring. Mapping defaults to two plots: signed total current and **estimated irradiance** (mW/cm²). Set the wavelength in the detector node (default 650 nm). Both use the sequential Inferno color map, equal XY scales and independent automatic color ranges. Standard deviation, raw samples, conversion parameters and derived irradiance are saved. A power correction factor and measured dark-current offset support user calibration; defaults are ×1 and 0 A. The irradiance estimate uses the datasheet spectral response normalized to its illuminant-A characterization, with typical-device and beam-geometry limitations. See [the conversion model and calibration notes](docs/temt6000-irradiance.md). The DP100 settings hold electrical drive, and do not measure or regulate optical output power.

### Virtual-mask illumination mapping

In Wire-bond Test, open **Config Measure Flow → Template → Virtual Mask Mapping - NUS Logo**. The `Virtual Mask Illumination` Canvas node uses the packaged grayscale NUS mask by default, or accepts PNG/JPEG/BMP/TIFF/PGM images. Corner A/B, ΔX, ΔY and Path on the main Mapping page define every stage scan point. At each point the node inverse-maps XY (or LayoutMap UV) into the image, samples its grayscale, and maps that value linearly between the configured black-pixel and white-pixel powers; points outside the positioned image use black. Before stage scanning, the workflow sweeps DP100 current from 1 to 25 mA, reads the actual laser voltage, builds a measured electrical power-to-voltage curve from P=VI, and reuses that curve for every mask point. Targets below the calibrated minimum are dark; targets above the calibrated maximum are rejected. These are calibrated electrical drive powers, not measured optical powers. Raw hexadecimal traffic printed by the vendor DLL is suppressed; normal application logs use compact English DP100 summaries.

Use **Configure mask, mapping & power…** only to position the mask and set its power endpoints: first switch explicitly between **Stage XY** and **GDS UV**, then click a pixel in the image and enter the correspondingly labelled X/Y or U/V coordinate before adding/updating the row. Enable **Invert grayscale intensity (black ↔ white)** to use `1 - grayscale`; the image preview updates immediately and the setting is saved with the workflow. The table headings change with the selected mode. At least three non-collinear points are required, and **Reset corners** provides a four-corner 100 × 50 default. UV requires a fitted LayoutMap; XY directly uses logical stage micrometers. Preview and result plots are cell heatmaps in actual stage coordinates. The default workflow displays only the **Measured Current (A)** heatmap. Each saved point includes image pixel, normalized gray level, target power, stage XY and optional GDS UV. The virtual-mask node must have exclusive DP100 control and cannot share a workflow with another DP100 laser-output/waveform node.

### TEMT6000 calibrated pulse-response mapping template

Open **Config Measure Flow -> Template -> TEMT6000 Mapping - Calibrated Pulse Response**. The Canvas now presents four explicit modules:

1. **DP100 Laser I-V Calibration** sweeps 1-25 mA in 1 mA steps at a 3.3 V ceiling and exposes the measured current-to-voltage table through a green Calibration port.
2. **Power-gradient Pulse Response** receives that calibration table, measures fixed-bias TEMT6000 I-t while laser current rises 1->25 mA and falls 24->1 mA, and exposes response amplitude and intensity-sensitivity feature ports.
3. **Pulse-width Gradient Response** also receives the calibration table and runs after the power test through the control-flow connection. It measures 15 mA pulses from 100 to 500 ms in 100 ms steps and exposes response amplitude and width-sensitivity features.
4. **Mapping Heatmap** accepts two connected numeric features. Its user-visible labels become the default selectable spatial maps.

The calibration readback no longer treats the first DP100 callback as final. After each output command it waits for the configured settle time and retries voltage/current callbacks for up to **Readback timeout per level** (default 2 s, retry every 100 ms). This filters stale OFF snapshots such as 0 V/0 mA. A persistent zero readback still stops the scan and reports that the DP100 output/load connection or DLL callback should be checked. Calibration runs once per Mapping session and saves one root-level CSV/JSON table.

At each spatial point the power-gradient and pulse-width tests run serially while reusing the same held Keithley 2450 front-terminal +5 V bias. Each test starts detector acquisition before releasing its laser waveform, saves a separate I-t trace, analyzes pulse-relative baselines, and publishes maximum/mean pulse response plus the phase-specific sensitivity. Every pulse has a full 1 s dark gap by default. Clicking a completed heatmap cell opens both I-t curves and overlays their corresponding laser target-current programs.

The DP100 pulse edges remain software-timed and actual 2450 sampling cadence is limited by VISA/read latency. Use a hardware pulse/trigger source where deterministic timing is required. The template is bounded by 3.3 V OVP and 28 mA OCP for the specified <30 mA laser module.

## Getting Started

### Requirements

#### Hardware

- A compatible 4-axis motion controller connected through RS-232 or a USB-to-RS232 adapter
- A Windows-visible USB microscope camera
- A probe stage wired so the first three controller axes map to application axes `X`, `Y`, and `Z`
- Optional VISA instruments for automated electrical measurements: Keithley 2450 and/or Keysight B1500
- Optional ATK-DP100 supply for laser LED output; the vendor x64 DLL is loaded only by the Windows measurement process

#### Software

- Python `>=3.10`
- Recommended dependency manager: `uv`
- Python packages are declared in `pyproject.toml` and mirrored in `requirements.txt` for pip-based installs.
- GDS layout loading requires `gdstk`. If it is missing, the application still starts, but the `LayoutBond` page will ask you to install it.
- MII USB microscope cameras use the MiiCam SDK. The app discovers `MII/ImageView/x64/miicam.dll` (or `x86` for a 32-bit process) under the Windows Program Files directories, as well as downloaded `mmgr.*` SDKs. Set `SEMI_AUTO_PROBE_MIICAM_SDK` for a custom SDK location. Select `miicam:0` to require the microscope camera; `auto` can fall back to a built-in webcam if the microscope SDK is unavailable.
- Web monitoring requires the declared `fastapi` and `uvicorn[standard]` dependencies.
- VISA measurement workflows require the declared `pyvisa` / `qcodes` packages plus a working local VISA backend.

### Installation

Create the local environment and install dependencies:

```powershell
uv sync
```

After dependency changes, refresh the uv lockfile and environment:

```powershell
uv lock
uv sync
```

If you only need to add the GDS dependency in an existing checkout, use:

```powershell
uv add gdstk
```

Run the GUI:

```powershell
uv run python -m semi_auto_probe
```

### F1 / CHM help

Press `F1` in the desktop app to open the help topic for the current page or dialog. Build the Windows manual with `.\docs\help\build-help.ps1`; the packaged artifact is `src\semi_auto_probe\assets\SemiAutoProbeHelp.chm`. See the [CHM source and build guide](docs/help/README.md) for validation, compiler setup, and maintenance details.

Run the standalone measurement bench without camera, stage, FocusMap, or LayoutMap startup:

```powershell
uv run semi-auto-probe-measure
```

The standalone measurement bench starts in simulation mode; clear `Simulation` to connect a real DP100 without starting the camera, stage, FocusMap, or LayoutMap subsystems. In the main AutoTest workflow, a linked DP100 waveform connects to the device automatically when the light-driven test runs. For real ATK-DP100 control on Windows, install the optional adapter and place the vendor x64 DLL in `refs/`:

```powershell
uv sync --extra dp100
```

The DP100 DLL exposes output on/off and setpoint commands, but no hardware arbitrary-waveform trigger. DP100 waveform nodes therefore execute editable time/voltage/current points with software timing and are intended for millisecond-scale illumination; use a dedicated pulse driver for precision edges or deterministic synchronization.

The workflow editor uses typed ports and links: cyan for control flow, purple for numbers, orange for Boolean values, and yellow for light waveforms. The waveform editor runs as a separate PySide6/pyqtgraph process so direct point dragging, pan/zoom, snapping, undo/redo, and editable voltage/current curves do not compete with Tk's event loop.

Restore the latest FocusMap plane and LayoutMap calibration on startup:

```powershell
uv run python -m semi_auto_probe --restore-last
```

or:

```powershell
uv run semi-auto-probe
```

Run the command-line communication test:

```powershell
uv run python -m semi_auto_probe.cli test --port COM3
```

The desktop app also supports the Raspberry Pi serial bridge. Install the Windows OpenSSH client and configure key/agent authentication for `icalculate@100.77.247.59`, then select `Remote / SSH` in the `SERIAL` toolbar and click `Connect`. Selecting remote mode or clicking `Refresh` checks the Pi service, physical motor serial connection, and bridge-session availability through SSH without opening a control session. The app owns a loopback-only local forward to the Pi's `127.0.0.1:9500` bridge and closes it when serial is disconnected.

Run the read-only web dashboard:

```powershell
$env:SEMI_AUTO_PROBE_WEB_TOKEN="change-this-token"
uv run semi-auto-probe-web
```

Then open:

```text
http://127.0.0.1:8000
```

See [`docs/web-deploy.md`](docs/web-deploy.md) and [`src/semi_auto_probe/web/README.md`](src/semi_auto_probe/web/README.md) for tray helpers, token setup, AutoTest file APIs, LAN binding, Cloudflare Tunnel deployment, and connection monitoring.

### First-run workflow

1. Connect the controller and camera.
2. Launch the app, select the correct serial port, and click `Connect`.
3. Click `Test` to verify controller feedback.
4. Open `Config`, confirm motor settings, select the active objective/eyepiece pair, and run pixel calibration if image-to-stage conversion is needed.
5. On `Main`, read the current position, verify axis direction, and use `Set New Zero` only after the stage is at the intended coordinate origin.
6. Use `AutoFocus` to find a usable `Z` position before imaging.
7. Use `FocusMap` if later LayoutMap, ImgMatrix, AutoTest, EdgeTrace, or ImgStitch steps need mapped Z compensation across a tilted sample.
8. Use `LayoutBond` / `LayoutMap` to bind GDS coordinates to stage coordinates when layout-aware motion or acquisition is needed.
9. Use `ImgStitch`, `ImgMatrix`, `AutoTest`, or `EdgeTrace` only after travel distance, overlap/FOV, contact behavior, and optional plane compensation have been checked.

## Workflow Details

### Main page

- Position cells show `X`, `Y`, and `Z`
- Single-click a coordinate cell to enter a relative move
- Double-click a coordinate cell to enter an absolute target
- `Move`, `Read`, `Continue`, jog controls, `Go Home`, zeroing, and emergency stop are available from the main motion panel
- `Go Home` sends protocol item 18 for all axes and remains disabled until Config admin mode is enabled
- Vision tools include `Center +`, `Point-Point`, `Point-Line`, `Polygon Area`, and `Move Center`

### LayoutBond

- Load read-only `.gds` files, display the selected top cell, toggle layers, zoom, pan, and fit the layout to view
- Cursor coordinates are snapped to a selectable GDS grid: `100 nm`, `1 um`, `5 um`, or `10 um`
- Calibration points `P1` to `P4` can be typed manually, set from the current stage position, or picked from the layout
- Click `Set GDS` to enter pick mode; the active button turns amber, then double-click a snapped layout point to fill the GDS coordinate and restore the button color
- After fitting the affine mapping, LayoutBond previews selected GDS targets in stage micrometers and moves only after `Move to Selected Target`
- FocusMap Z lock can move Z to the fitted plane during mapped XY movement when enabled

### AutoFocus

- Available metrics: `Laplacian`, `Tenengrad`, `Brenner`
- Search flow: center sample -> coarse sweep -> local refinement -> return to best usable `Z`
- Each run writes `last_autofocus_history.csv`

### FocusMap

- Generates autofocus sample meshes and records measured `X/Y/Z` points
- Fits a sample plane and shows residuals in a 3D view when Matplotlib is available
- Saves the latest mapping to `last_focusmap_mapping.json`
- Can lock later mapped XY moves to the stored plane Z
- Can be reused by `ImgStitch`, `ImgMatrix`, `AutoTest`, `EdgeTrace`, and `LayoutBond` movements

### Image stitching

- Traversal is serpentine
- Neighboring fields are registered with FFT phase correlation
- Flat-field correction is applied before stitching
- Final output is written to `last_imgstitch.png`
- Session data is written under `imgstitch_session/`, including raw tiles, `session.json`, recomposed images, stack outputs, and `last_imgstitch.png`
- T-stack and Z-stack modes can save fused images and optional raw frames
- Four-corner plane AF or a stored FocusMap plane can compensate tilted samples

### ImgMatrix

- Uses the fitted LayoutMap affine transform to generate a GDS-aligned row/column acquisition matrix
- Shows the planned FOV footprint on the layout before running
- Saves images to `imgmatrix_session/<timestamp>/images/`
- Writes `manifest.json` with settings, point metadata, output paths, and completion state
- Reuses the ImgStitch tile mode, T-stack mode, and Z-stack mode settings

### AutoTest

- Generates named device points from a layout origin and `U/V` vectors
- Requires LayoutMap mapping and FocusMap plane readiness before motion execution
- Supports probe-assist overlays for Source, Drain, and Gate alignment
- Applies configurable fast/slow Z approach, down margin, Z offset, and optional Z wobble
- Runs measurement-flow cards in order: pause, photo, Keithley IV, WobbTest, B1500 transfer, and B1500 output
- Saves per-run data under `autotest_session/<timestamp>/images/`, `iv/`, `wobb/`, and `b1500/`

### EdgeTrace

- Builds trace plans from visible GDS geometry and optional work bounds
- Uses LayoutMap for XY conversion and FocusMap for contact/safe Z values
- Provides separate safe move, start move, contact, segment, and auto-run actions
- Tracks completed polylines and current needle position in the layout viewer

### AI Agent

- Offline help: `/help`, `/help workflow`, `/help mapping`, `/status`; how-to questions do not create hardware actions.
- Current Workflow v2 support: `/check` inspects the configured graph/scan locally; `/run measurement` reviews and runs the current Probe or Wire-bond workflow with a configuration fingerprint.
- Existing stage, GDS, AutoFocus, FocusMap and image workflows run through local guards. Completion events release the next step; failure or Stop prevents later steps from running.
- Keeps conversation-only, authorized-step (default) and automatic-step modes. Right-side tabs provide Steps, Help, 12 Templates and the original task Preview.
- Rule planning and bundled help work without an API key; configured compatible providers can plan other supported natural-language requests.

Try the isolated simulator without connecting devices:

```powershell
.venv/Scripts/python.exe -m semi_auto_probe.agent_demo --preview --ready
.venv/Scripts/python.exe -m semi_auto_probe.agent_demo --all --ready
```

`--ready` is fictitious state, not hardware readiness. See [copyable test templates](docs/agent-test-templates.md),
[execution and maintenance interfaces](docs/agent-execution-spec.md), and [provider contract](docs/agent-function-spec.md).

### Web dashboard

- Runs as `semi-auto-probe-web` using FastAPI and Uvicorn
- Serves a token-protected read-only dashboard for AutoTest session history, run files, JSON metadata, image previews, downloads, and active client connections
- No longer publishes or proxies camera frames through the web service
- Listens on `127.0.0.1:8000` by default and can be configured with `SEMI_AUTO_PROBE_WEB_HOST`, `SEMI_AUTO_PROBE_WEB_PORT`, `SEMI_AUTO_PROBE_WEB_TOKEN`, `SEMI_AUTO_PROBE_AUTOTEST_SESSION_DIR`, and related environment variables

### Configuration

Local settings are stored in:

```text
probe_config.local.json
```

The configuration page controls optical calibration, active objective/eyepiece selection, camera source/resolution/exposure/gain, motor microstep settings, axis polarity, speed profiles, controller motion parameters, keyboard jog levels, focus thresholds, Agent API settings, and derived `um/pulse` conversions. Controller speed values are read only on demand from this page; item 10/11 applies temporary values and item 12/13 saves power-off-persistent values.

Core-field example:

```json
{
  "active_motor_speed_profile": "fast",
  "agent_base_url": "https://api.deepseek.com",
  "agent_model": "deepseek-chat",
  "base_angle_deg": 0.72,
  "camera_fov_rotation_deg": 0.0,
  "camera_resolution_width": "half",
  "camera_source": "auto",
  "calibrations": {
    "objective_20__eyepiece_1.5": 0.42
  },
  "cc_accel_time_s": 0.3,
  "cc_speed_percent": 100,
  "eyepiece": 1.5,
  "fine_speed_percent": 40,
  "lead_xy_mm": 1.0,
  "lead_z_mm": 0.5,
  "microstep": 2,
  "motor_axis_polarity": {
    "X": 1,
    "Y": 1,
    "Z": 1
  },
  "objective": 20,
  "safe_speed_percent": 15
}
```

## Project Layout

```text
src/semi_auto_probe/
  app.py                   Tkinter application and workflow orchestration
  agent.py                 AI Agent planning contracts and rule/LLM planners
  auto_test.py             AutoTest point-grid UI and measurement-flow definitions
  b1500.py                 Keysight B1500 transfer/output sweep runner
  camera.py                USB camera capture, overlays, focus metrics
  camera_stage_transform.py Camera-to-stage coordinate transforms
  config.py                Persistent optical/motor configuration
  edge_trace_panel.py      GDS edge trace UI and motion-plan panel
  focusmap_3d.py           FocusMap 3D visualization
  gds_edge_trace.py        GDS edge extraction and trace-plan generation
  gds_stage_mapper.py      GDS viewer and affine stage mapping
  img_matrix.py            GDS-aligned matrix acquisition planning
  protocol.py              Frame builders and response parsers
  serial_client.py         Thread-safe serial transport helpers
  img_stitch.py            Stitching, flat-field correction, plane fitting
  keithley2450.py          Keithley 2450 IV and contact-current helpers
  monitor_feed.py          Local desktop camera/status publisher
  web_app.py               Read-only FastAPI monitoring dashboard
  web/static/              Browser dashboard assets
  ui/vision.py             Main-page visual tools
  ui/calibration_dialog.py Pixel-calibration dialog
```

## Generated and Local Files

| File | Meaning |
| --- | --- |
| `probe_config.local.json` | Local optical/motor configuration |
| `last_autofocus_history.csv` | Most recent autofocus sampling history |
| `last_focusmap_mapping.json` | Most recent FocusMap plane/mapping payload |
| `last_layoutbond_mapping.json` | Most recent LayoutMap calibration payload |
| `last_imgstitch.png` | Most recent stitched mosaic |
| `imgstitch_session/` | Latest stitch/stack session tiles, JSON, and recomposed output |
| `imgmatrix_session/<timestamp>/` | ImgMatrix images and manifest |
| `autotest_session/<timestamp>/` | AutoTest images, IV CSV/JSON, WobbTest CSV/JSON, and B1500 outputs |
| `.runtime/` | Web-service PID files and local runtime logs |

These files are ignored by Git and are safe to keep local.

## Development

Run the full test suite:

```powershell
uv run python -m unittest discover -s tests
```

If you run tests outside the project environment, make sure the active interpreter has `opencv-python`, `numpy`, and `pyserial` installed. Importing the GUI stack also imports stitching code, so OpenCV is required even for some non-camera tests.
For GDS viewer tests or manual GDS loading, the interpreter also needs `gdstk`.

## Troubleshooting

### No serial ports appear

- Confirm the adapter is visible in Windows Device Manager
- Install the USB-to-RS232 driver if required
- Click `Refresh` after connecting the adapter

### Communication test fails

- Confirm the selected COM port
- Confirm controller power and RS-232 wiring
- Verify the controller uses `115200, N, 8, 1`
- In `Remote / SSH` mode, verify key-based SSH login works and that another remote session is not already using the Raspberry Pi bridge

### Camera preview is unavailable

- Try another camera index
- Close other applications already using the camera
- Click `Restart`

### GDS loading says gdstk is missing

- If you use `uv`, run `uv sync` after pulling the latest dependency files.
- If the lockfile has not been updated yet, run `uv lock` and then `uv sync`.
- For a local one-off fix, run `uv add gdstk`; this writes the dependency to `pyproject.toml` and updates the uv environment.

### Vision move is disabled

- Run pixel calibration for the currently selected objective/eyepiece pair
- Confirm the stage conversion settings are correct before using image-to-stage moves

### Stitching quality is poor

- Verify overlap values match the actual field overlap
- Recheck flat-field behavior under the current illumination
- Confirm the configured physical step sizes match the current optical calibration and motor mapping
- Enable plane AF when the sample surface is tilted across the stitched area

## Safety Notes

- Verify axis directions at low jog distances before using large moves
- Confirm the coordinate origin before using `Set New Zero`
- Keep the emergency-stop path accessible during any automated motion
- Use conservative `Z` ranges until sample clearance is known

### Measurement Workflow v2

Desktop AutoTest and Mapping now share typed data connections, custom scalar/curve-feature formulas, and a configurable 1×1 / 1×2 / 2×2 / 2×3 Plot Panel. See [workflow setup, file format and performance validation](docs/measurement-workflow-v2.md).

The template library is organized as a tree by FET, PD, MR, Cap and general electrical tests. Device recipes are shared by probe and bonded-position scans; workflow files use version 2 only. See [template selection, editable thresholds, and feature definitions](docs/device-workflow-templates.md).
