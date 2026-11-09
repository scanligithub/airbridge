---
name: orcad-pdf-export
description: Prepare an OrCAD Capture Tcl script for schematic PDF export using the local Capture Tcl environment and Ghostscript. AirBridge may transfer the script and resulting PDF but does not launch or control Cadence.
---

# OrCAD Schematic PDF Export

This is a separate Capture-side workflow. AirBridge only transports the Tcl text/file and the resulting PDF; it does not execute Tcl or manage Capture's lifecycle.

The user must start Capture using their chosen workflow and run the generated Tcl script inside Capture. Any automated listener or application-side adapter is maintained separately from AirBridge.

## Prepare the export script

Use references/export-template.tcl and substitute:

- DSN_PATH — absolute DSN path on host B
- OUT_DIR and PDF_NAME
- GS_EXE — absolute path to gswin64c.exe
- PS_DRIVER — installed OrCAD PostScript printer name

Run scripts/submit_export.py on host A to generate the Tcl file locally:

```powershell
python scripts/submit_export.py --dsn "E:\path\design.dsn" --gs "C:\Program Files\gs\bin\gswin64c.exe" --output "export_pdf.tcl"
```

The helper only writes a Tcl file. It does not send an execute request or claim that Capture ran it. Transfer the file to host B with AirBridge's ordinary file-transfer function, then run it through the user-controlled Capture Tcl workflow.

## Capture-side requirements

- Capture's OrHandlerPDFExport Tcl package must be available.
- The configured PostScript driver must be installed.
- Ghostscript CLI must be available at the supplied path.
- The script opens the requested DSN with Capture's Tcl Open command and requests PDF export.
- The script does not close Capture. The application-side Tcl workflow owns any exit behavior.

## Retrieve the output

Use AirBridge's ordinary file transfer or RFS to retrieve the generated PDF. AirBridge does not inspect PDF generation state or Cadence logs.