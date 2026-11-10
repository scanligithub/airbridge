---
name: orcad-pdf-export
description: Export OrCAD Capture schematic DSN files to PDF through an AirBridge-managed Capture GUI session. Automates the export without manual GUI interaction; this is not a headless or Capture Batch-mode workflow.
---

# OrCAD Schematic PDF Export

Export an OrCAD Capture .dsn design to PDF on host B through AirBridge.

**Important:** Capture Tcl runs inside the Capture GUI process. Cadence states that Capture Tcl cannot run in Capture Batch mode; it runs with the Capture GUI. This Skill automates the GUI process without requiring the user to operate its menus or dialogs. It does **not** provide a true headless Capture engine. See [Cadence Support: Capture Tcl invocation](https://support1.cadence.com/public/docs/content/20466470.html).

## Architecture and lifecycle

The Skill uses the verified AirBridge Capture lifecycle:

~~~text
Host A: preflight DSN / Ghostscript / output directory
  -> POST /api/v1/eda/execute (target_file=DSN, keep_open=false)
  -> Host B: capture.exe "<DSN path>"
  -> Capture opens DSN natively before capAutoLoad runs
  -> AirBridge capAutoLoad hook runs export-template.tcl
  -> Tcl retrieves the already-open design with DboSession GetActiveDesign
  -> OrHandlerPDFExport::printPDF
  -> OrCADPS_17.2 -> Ghostscript -> PDF
  -> Check PDF signature and Ghostscript nullpage validation
  -> Write per-job result marker
  -> AirBridge requests normal WM_CLOSE (no force kill)
  -> Host A polls RFS result marker and verifies PDF size
~~~

Key rules:

1. target_file carries the DSN path on host B. AirBridge passes it as a native capture.exe command-line argument. Capture opens the design before the Tcl hook runs.
2. The export Tcl uses DboSession GetActiveDesign. Do not call ab_open_dsn or GetDesignAndSchematics again; the DSN is already open in the Capture GUI session.
3. Submit with keep_open=false. When the Tcl script completes, AirBridge sends WM_CLOSE and waits for normal exit. It does not force-kill Capture.
4. Do not call Tcl exit, ab_save_and_close, or a second design-close API from this Skill. AirBridge owns the application lifecycle.
5. HTTP status submitted is only an acknowledgement. The Python helper waits for the per-job result marker and verifies the remote PDF before reporting success.

## Prerequisites

1. OrCAD Capture / Cadence SPB installed (target version: SPB 17.2).
2. The selected DSN exists and is not simultaneously open in another Capture instance. Close any pre-existing Capture session first.
3. Ghostscript 64-bit CLI exists, typically gswin64c.exe.
4. An OrCAD PostScript printer is installed. The expected 17.2 name is often OrCADPS_17.2; confirm the exact printer name on host B.
5. AirBridge is running on both hosts, serial is connected, and host A exposes http://127.0.0.1:8888.

If the DSN path is not known, use RFS drives and directory listing on host B to find .dsn candidates. Show the candidate paths to the user and obtain the intended design before submitting the export. Do not search the local host-A filesystem or open the DSN through Tcl.

Quick health check:

~~~powershell
Invoke-RestMethod http://127.0.0.1:8888/api/v1/eda/versions
~~~

Check printers on host B through remote Shell, then inspect SHELL_RESULT in the AirBridge console:

~~~powershell
Get-Printer | Where-Object { $_.Name -match 'OrCAD|PS|PostScript' } | Select-Object -ExpandProperty Name
~~~

## Default host-B paths

| Item | Default |
|---|---|
| Workspace | E:\_airbridge_temp |
| PDF output | E:\_airbridge_temp\pdf_out |
| SPB example | D:\Cadence\SPB_17.2 |
| PostScript printer example | OrCADPS_17.2 |

## Export workflow

### 1. Collect inputs

- Absolute DSN path on host B.
- Output directory on host B.
- PDF filename (plain filename ending in .pdf); omitted filename defaults to the DSN basename.
- Use --overwrite only when the existing output PDF is intentionally being replaced.
- Absolute gswin64c.exe path on host B.
- Exact OrCAD PostScript printer name.
- Cadence version (17.2 or auto).

### 2. Run the host-A submitter

Run scripts/submit_export.py on host A, not in the model sandbox:

~~~powershell
python .\skill\orcad-pdf-export\scripts\submit_export.py --dsn "E:\Projects\Example\Design.dsn" --out-dir "E:\_airbridge_temp\pdf_out" --gs "C:\Program Files\gs\gs10.05.1\bin\gswin64c.exe" --ps-driver "OrCADPS_17.2" --version "17.2"
~~~

The helper checks Cadence availability, verifies the DSN and Ghostscript with RFS, creates the output directory, uploads a per-job UTF-8 config sidecar, submits Tcl with target_file and keep_open=false, polls the result marker, and verifies the final PDF size. If the host-B EDA workspace was overridden with AIRBRIDGE_WORKSPACE_DIR, pass the same value through --workspace. The default is E:\\_airbridge_temp.

The Tcl template checks the %PDF- signature and asks Ghostscript to parse/render the PDF to the nullpage device. This checks basic readability; it does not certify page count, bookmark correctness, or visual completeness.

Implementation status: the Capture-native open, active-design lookup, PDF export, result polling, and graceful-close path are wired together in this Skill. The complete PDF export flow still requires an end-to-end run on the target host-B SPB 17.2 installation before it can be marked PASS.

### 3. Close behavior

No separate close request is needed for a normal PDF export. With keep_open=false, AirBridge sends WM_CLOSE to the Capture process it just launched after the Tcl task completes. A save, license, or confirmation dialog may prevent exit; AirBridge reports the condition and never force-kills the process. Handle such prompts on host B.

## Failure diagnosis

| Symptom | Likely cause | Action |
|---|---|---|
| No result marker | Capture failed to start, capAutoLoad hook was not injected, or Tcl did not reach its result writer | Inspect _cadence_stdout.log and _bootstrap.tcl through RFS |
| No active DboDesign | Native DSN open failed or the wrong Capture session became active | Verify target_file, file existence, version, and other open Capture instances |
| OrHandlerPDFExport package load fails | Capture PDF Tcl utilities are missing | Check tools\capture\tclscripts under CDSROOT |
| PostScript driver not found | Wrong printer name or printer not installed | Run Get-Printer on host B |
| Ghostscript missing | Incorrect --gs path | Verify with RFS fs/stat |
| PDF signature or Ghostscript validation fails | PDF is incomplete or invalid | Preserve output and inspect Capture / Ghostscript logs |
| Capture remains open | Modal prompt prevented normal WM_CLOSE | Resolve prompt manually; do not force-kill |

## Reference files

- references/export-template.tcl — Capture Tcl export and validation workflow
- references/api-notes.md — AirBridge request fields and RFS result polling
- scripts/submit_export.py — host-A preflight, submit, wait, and verify helper

The general airbridge Skill owns remote transport, RFS, Shell, and EDA dispatch. This Skill owns the OrCAD PDF export workflow and does not reimplement Capture startup, DSN opening, or GUI closing.
