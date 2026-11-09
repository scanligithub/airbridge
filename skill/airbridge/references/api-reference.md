# AirBridge HTTP API reference (host A)

Base: http://127.0.0.1:8888

## Generic raw-data transport

### POST /api/v1/data/send

Send a UTF-8 text payload to the peer using the generic RAW_STREAM frame.

Request:

```json
{ "data": "arbitrary text or script source" }
```

Response:

```json
{
  "status": "queued",
  "bytes": 28,
  "execution": "not_performed"
}
```

The receiving AirBridge displays the payload in its generic data panel. It does not parse or execute the contents. Maximum single-frame payload: 65,000 UTF-8 bytes. Use the file-transfer feature for larger or binary files.

## Filesystem

All under /api/v1/fs/<action> with JSON body.

| action | important fields |
|--------|------------------|
| drives | (none) |
| ls | path |
| read | path, max_preview_bytes, as_base64 |
| write | path, content, is_base64, mode |
| mkdir | path |
| rm | path |
| mv | src, dst |
| stat | path |
| pull | remote path fields per server |

read returns a truncated preview when a file is larger than max_preview_bytes (default 65536).

## Optional remote Shell

### POST /api/v1/shell/exec

```json
{
  "command": "Get-ChildItem",
  "shell": "powershell",
  "cwd": "E:\\_airbridge_temp",
  "timeout": 60,
  "async": false,
  "env": {}
}
```

HTTP response indicates submission. Actual stdout/stderr arrive through the serial console as SHELL_RESULT and SHELL_OUTPUT.

### POST /api/v1/shell/kill

```json
{ "task_id": "shell-...", "pid": 0 }
```

### POST /api/v1/shell/tasks

Requests task-list output through the serial console.

Remote Shell is independent of Cadence and is not used by the generic raw-data channel.