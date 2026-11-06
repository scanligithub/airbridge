# AirBridge HTTP API reference (host A)

Base: `http://127.0.0.1:8888`

## EDA

### GET or POST `/api/v1/eda/versions`

Returns remote Cadence installations discovered on host B.

Example success:

```json
{
  "req_id": 123,
  "action": "eda_versions",
  "success": true,
  "versions": { "17.2": { } }
}
```

### POST `/api/v1/eda/execute` (alias `/api/eda/exec`)

Body:

```json
{
  "script": "...",
  "hint": "agent_auto.tcl",
  "version": "auto",
  "target_file": "",
  "timeout": 60
}
```

Response: `{"status":"submitted","hint":"...","version":"..."}`

## Filesystem

All under `/api/v1/fs/<action>` with JSON body. Common field: `timeout` (seconds).

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

`read` returns truncated preview when file larger than `max_preview_bytes` (default 65536).

## Shell

### POST `/api/v1/shell/exec`

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

HTTP response:

```json
{
  "status": "submitted",
  "async": false,
  "hint": "结果将通过 SHELL_RESULT / 控制台日志回传；..."
}
```

Authoritative result frame fields (console):

```json
{
  "success": true,
  "task_id": "shell-...",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "",
  "duration_ms": 781,
  "cwd": "E:\\_airbridge_temp"
}
```

Blocked command example:

```json
{
  "success": false,
  "error": "安全策略拦截: 命中安全黑名单: \\bshutdown\\b",
  "blocked": true
}
```

### POST `/api/v1/shell/kill`

```json
{ "task_id": "shell-...", "pid": 0 }
```

### POST `/api/v1/shell/tasks` (or `/api/v1/shell/list`)

Triggers list request; details appear on serial log path.

## Blacklist themes (host B)

Blocked patterns include format/diskpart/volume wipe, shutdown/restart, dangerous net user or HKLM deletes, recursive force delete of system roots. Prefer non-destructive commands.
