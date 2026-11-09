# OrCAD PDF export integration notes

This skill is independent of the AirBridge transport core.

- Build the Tcl file locally with scripts/submit_export.py.
- Transfer the Tcl file to host B with ordinary AirBridge file transfer.
- Execute it through the user-controlled Capture Tcl workflow.
- Retrieve the resulting PDF with ordinary file transfer or RFS.

AirBridge no longer provides /api/v1/eda/versions or /api/v1/eda/execute.