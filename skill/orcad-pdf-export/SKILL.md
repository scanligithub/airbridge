---
name: orcad-pdf-export
description: Export OrCAD Capture schematic designs to intelligent PDF via Tcl and Ghostscript without GUI dialogs. Use when the user asks to export schematic PDF, smart PDF, DSN to PDF, OrHandlerPDFExport, or print schematics through Ghostscript on an air-gapped Cadence host reached by AirBridge.
---

# OrCAD Schematic PDF Export

Export an OrCAD Capture `.dsn` design to PDF on host B using Capture Tcl + PostScript driver + Ghostscript. Prefer this skill whenever the goal is schematic PDF, not general AirBridge file or shell work.

**Depends on AirBridge** on host A (`http://127.0.0.1:8888`) with serial link to the Cadence machine (host B). This skill does not talk to Cadence by itself; it drives AirBridge EDA and RFS APIs.

## When to use

- User wants schematic / design PDF from a `.dsn`
- Mentions Ghostscript, smart PDF, Export PDF automation, no dialogs
- Batch export of one or more designs on the isolated OrCAD PC

Do **not** instruct the user to click File → Export → PDF in the GUI.

## Prerequisites (verify on host B via AirBridge)

1. Cadence / OrCAD Capture installed (e.g. SPB 17.2)
2. Ghostscript 64-bit CLI available, typically `gswin64c.exe`
3. OrCAD PostScript printer installed (name often `OrCADPS_17.2` for SPB 17.2)
4. AirBridge healthy: `GET http://127.0.0.1:8888/api/v1/eda/versions` returns success

Quick checks (host A):

```powershell
# Cadence versions
Invoke-RestMethod http://127.0.0.1:8888/api/v1/eda/versions

# Ghostscript (remote shell)
$body = @{ command = 'Get-Command gswin64c -ErrorAction SilentlyContinue | Format-List; if (Test-Path "C:\Program Files\gs") { Get-ChildItem "C:\Program Files\gs" -Recurse -Filter gswin64c.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName }'; shell = "powershell"; timeout = 30 } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8888/api/v1/shell/exec -Method POST -Body $body -ContentType "application/json; charset=utf-8"
```

Watch AirBridge console for `SHELL_RESULT` when HTTP only returns `submitted`.

## Core pipeline (no GUI)

```text
ab_open_dsn  →  OrHandlerPDFExport::printPDF  →  PS printer  →  gswin64c  →  .pdf
```

Official automation entry is `package require OrHandlerPDFExport` then `::OrHandlerPDFExport::printPDF` with a JSON-like config string. Ghostscript only converts PostScript to PDF; it cannot read `.dsn` directly.

## Default paths on host B

| Item | Default |
|------|---------|
| Workspace | `E:\_airbridge_temp` |
| PDF output | `E:\_airbridge_temp\pdf_out` |
| SPB example | `D:\Cadence\SPB_17.2` |
| PS driver example | `OrCADPS_17.2` |

Override when the user provides real paths.

## Export workflow

### 1. Confirm inputs

Collect or discover:

- Absolute path to `.dsn` on B
- Output directory and PDF file name
- Full path to `gswin64c.exe`
- PostScript printer name
- Cadence version string for AirBridge (`17.2` or `auto`)

Optional RFS checks:

```powershell
$body = @{ path = "E:\path\to\design.dsn"; timeout = 10 } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8888/api/v1/fs/stat -Method POST -Body $body -ContentType "application/json"
```

### 2. Submit Tcl via AirBridge EDA API

`POST http://127.0.0.1:8888/api/v1/eda/execute`

Body fields:

- `script`: full Tcl (see `references/export-template.tcl`)
- `hint`: must end with `.tcl` (e.g. `export_pdf.tcl`) so the engine selects OrCAD
- `version`: `"17.2"` or `"auto"`
- `timeout`: 300 or higher for large designs

Template parameters to substitute before submit:

- `DSN_PATH` — forward-slash path preferred
- `OUT_DIR`, `PDF_NAME`
- `GS_EXE` — absolute path to gswin64c
- `PS_DRIVER` — e.g. OrCADPS_17.2

### 3. Interpret results

HTTP often returns only `{"status":"submitted",...}`. Authoritative progress is in the AirBridge EDA console log.

Then list output dir:

```powershell
$body = @{ path = "E:\_airbridge_temp\pdf_out"; timeout = 10 } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8888/api/v1/fs/ls -Method POST -Body $body -ContentType "application/json"
```

Pull PDF back to A with `/api/v1/fs/pull` when the user needs the file locally.

On failure, read:

- `E:\_airbridge_temp\_cadence_stdout.log`
- `E:\_airbridge_temp\_bootstrap.tcl`

via `/api/v1/fs/read`.

## Agent rules

- Always state actions run on **host B through AirBridge**, not the local sandbox
- Never recommend GUI Export PDF clicks as the primary method
- Prefer absolute paths; normalize to `/` inside Tcl strings
- Put `gswin64c` absolute path in `PStoPdfCommand`
- Keep commands non-interactive; rely on AirBridge dialog watchdog for residual popups
- If PS driver name is unknown, ask user to run `Get-Printer` on B via shell and look for OrCAD/PS names
- For multiple designs, loop one export at a time unless the user requests batching

## Common failures

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| Postscript Driver not found | Wrong `PS_DRIVER` name | List printers on B; fix name |
| `package require OrHandlerPDFExport` fails | Missing Capture PDF Tcl utils | Check `tools\capture\tclscripts` under CDSROOT |
| Ghostscript not found | Bad `GS_EXE` | Locate `gswin64c.exe` via shell |
| ab_open_dsn failed | Bad path / locked design | `fs/stat` path; close design on B if open in GUI |
| Timeout | Large design | Raise `timeout` to 300–600 |
| HTTP submitted but no PDF | Export failed in Capture | Read `_cadence_stdout.log` |

## Reference files

- `references/export-template.tcl` — canonical Tcl script
- `references/api-notes.md` — AirBridge call shapes for this task
- `scripts/submit_export.py` — optional host-A helper to fill template and POST

## Relation to airbridge skill

General RFS, shell, and EDA dispatch live in the `airbridge` skill. This skill owns **schematic PDF export policy and templates** only. Use both when the session needs broad remote control plus PDF export.
