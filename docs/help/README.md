# Semi Auto Probe CHM source

This directory is the source project for the Windows F1 help manual. The published artifact is `SemiAutoProbeHelp.chm`; do not edit a decompiled CHM as source.

## Validate the source

Run the standard-library-only verifier before compiling:

```powershell
python .\docs\help\tools\verify_help.py --root .\docs\help
```

The check covers the HHP file list and options, TOC, keyword index, map/alias consistency, all local links and anchors, UTF-8 metadata, shared CSS, and tags or URI schemes that are unsafe in the legacy CHM browser.

## Build

Install Microsoft HTML Help Workshop so `hhc.exe` is available, then run:

The checked-in HHP, HHC, HHK, and topic files remain UTF-8. For compatibility with Microsoft HTML Help Compiler 4.74, the build script creates temporary CP936 files with CRLF line endings for the HHP, HHC, HHK, and context map, then removes them after compilation.

The project uses the table of contents and keyword index and does not depend on the optional Itircl full-text indexing component.

```powershell
.\docs\help\build-help.ps1
```

The script validates sources, finds `hhc.exe`, compiles the project, verifies the ITSF header and decompiled topic set with Windows `hh.exe`, and copies the result to `src\semi_auto_probe\assets\SemiAutoProbeHelp.chm`.

Use explicit paths when the tools are not on `PATH`:

```powershell
.\docs\help\build-help.ps1 `
  -CompilerPath 'C:\Tools\HTML Help Workshop\hhc.exe' `
  -PythonPath '.\.venv\Scripts\python.exe'
```

`HTML_HELP_COMPILER` may also point to `hhc.exe`. Pass `-NoDecompile` only on a build host without `hh.exe`; file-header validation still runs.

## Maintenance rules

- Keep topic files in UTF-8 with the `.htm` names declared in `SemiAutoProbeHelp.hhp`.
- Add every local topic, image, and stylesheet to the HHP `[FILES]` section.
- Update TOC and keyword index entries whenever a topic is added or renamed.
- Treat the values in `context_ids.h` as a published API. Changing an ID can make an installed application open the wrong F1 topic.
- Use relative links and the shared `assets\help.css`. Avoid scripts, frames, embedded objects, web fonts, Flexbox, Grid, and other features rejected by the verifier.
