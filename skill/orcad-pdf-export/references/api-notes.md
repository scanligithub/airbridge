# AirBridge calls used by orcad-pdf-export

Base URL on host A: http://127.0.0.1:8888

## Preflight

- GET /api/v1/eda/versions — verify the Cadence version is available.
- POST /api/v1/fs/stat — verify the host-B DSN and Ghostscript executable.
- POST /api/v1/fs/mkdir — create the PDF output directory.
- POST /api/v1/fs/write — upload the UTF-8 per-job Tcl configuration sidecar.

## Submit Capture PDF export

POST /api/v1/eda/execute body:

~~~json
{
  "script": "<rendered export-template.tcl>",
  "hint": "airbridge_pdf_export.tcl",
  "version": "17.2",
  "target_file": "E:\\Projects\\Example\\Design.dsn",
  "timeout": 600,
  "keep_open": false
}
~~~

Lifecycle meaning:

- target_file is the DSN path on host B. AirBridge validates it and starts capture.exe "<target_file>", so Capture opens the design before capAutoLoad executes Tcl.
- keep_open=false makes AirBridge request normal WM_CLOSE after the Tcl task completes. Do not add exit, ab_save_and_close, or a second close sequence to the template.
- The Tcl uses DboSession GetActiveDesign to get the design that Capture has already opened.

The HTTP response is only a submission acknowledgement (status: submitted), not the export result.

## Poll and verify

- POST /api/v1/fs/stat — poll the unique _pdf_export_result_<job-id>.txt marker.
- POST /api/v1/fs/read — read the marker and parse STATUS, PDF_PATH, PDF_SIZE, and MESSAGE.
- POST /api/v1/fs/stat — verify the PDF exists and its byte size matches the marker.
- POST /api/v1/fs/rm — remove the per-job temporary configuration sidecar after success.
- POST /api/v1/fs/pull — optionally transfer the PDF back to host A.

On timeout or a missing marker, read E:\_airbridge_temp\_cadence_stdout.log and E:\_airbridge_temp\_bootstrap.tcl via RFS. Shell and EDA command results may also appear in the AirBridge console logs.


The helper stores per-job config and result marker files in the AirBridge EDA workspace (default E:\\_airbridge_temp). If AIRBRIDGE_WORKSPACE_DIR is configured on host B, pass the same remote path with the submitter's --workspace option. The workspace path must be absolute and ASCII-only because its marker filename is embedded in the Tcl source.
