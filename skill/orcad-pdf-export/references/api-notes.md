# AirBridge calls used by orcad-pdf-export

Base: `http://127.0.0.1:8888`

## Health

- `GET /api/v1/eda/versions`

## Submit export

- `POST /api/v1/eda/execute`
- Body: `{ "script": "...", "hint": "export_pdf.tcl", "version": "17.2", "timeout": 300 }`

## Remote checks

- `POST /api/v1/shell/exec` — locate gswin64c, Get-Printer
- `POST /api/v1/fs/stat` — verify .dsn
- `POST /api/v1/fs/ls` — list pdf_out
- `POST /api/v1/fs/read` — read `_cadence_stdout.log`
- `POST /api/v1/fs/pull` — bring PDF to host A

Shell and EDA HTTP responses may be `submitted` only; read AirBridge console for SHELL_RESULT / EDA logs.
