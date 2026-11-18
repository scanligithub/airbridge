# AirBridge HTTP API reference (host A)

Base: `http://127.0.0.1:8888`

## Authentication and authorization

Every `/api/` request requires:

```http
Authorization: Bearer <AGENT_TOKEN>
```

Use the token provisioned for the calling Agent. Do not hardcode it into Skill source, task files, or logs.

Relevant scopes:

- `shell.execute`: submit remote Shell commands.
- `shell.read`: query Shell task state / list tasks.
- `shell.manage`: submit Shell task termination.
- `request.read`: read a correlated request record.
- `filesystem.read` / `filesystem.write`: RFS reads and mutations.
- `eda.execute`: legacy EDA execution route; this route is disabled by default unless the server is explicitly configured to allow it.

A request ID is bound to the Agent that created it. Another Agent cannot read its request or Shell task result.

## EDA (legacy compatibility API)

### GET or POST `/api/v1/eda/versions`

Returns legacy Cadence installation discovery from host B. This is retained for older clients; new professional Skills should perform their own version discovery through generic Shell / RFS instead of depending on EDA-specific Core behavior.

### POST `/api/v1/eda/execute` (alias `/api/eda/exec`)

Legacy compatibility route. It uses the EDA-specific `target_file`, `keep_open`, script routing and lifecycle behavior described in older documentation. It requires the `eda.execute` scope and is disabled by default by server policy. New Skills should not use it as their professional application lifecycle interface.

Body:

```json
{
  "script": "...",
  "hint": "agent_auto.tcl",
  "version": "auto",
  "target_file": "",
  "timeout": 60,
  "keep_open": false
}
```

## Filesystem

All under `/api/v1/fs/<action>` with JSON body.

| action | important fields | required scope |
|--------|------------------|----------------|
| drives | (none) | `filesystem.read` |
| ls | path | `filesystem.read` |
| read | path, max_preview_bytes, as_base64 | `filesystem.read` |
| stat | path | `filesystem.read` |
| write | path, content, is_base64, mode | `filesystem.write` |
| mkdir | path | `filesystem.write` |
| rm | path | `filesystem.write` |
| mv | src, dst | `filesystem.write` |
| pull | remote path fields per server | `filesystem.read` |

`read` returns a truncated preview when the file is larger than `max_preview_bytes` (default 65536). RFS performs file operations only; it does not interpret Tcl, SKILL, DSN, BRD, or other professional content.

## Shell

### POST `/api/v1/shell/exec`

Run a command on host B. Default shell is PowerShell.

```json
{
  "command": "Get-Process Capture -ErrorAction SilentlyContinue | Select-Object Id, Path",
  "shell": "powershell",
  "cwd": "E:\\_airbridge_temp",
  "timeout": 60,
  "async": false,
  "env": {}
}
```

The endpoint requires `shell.execute`, serial connectivity, and negotiated `request.correlation` plus `request.deduplication` support.

#### Synchronous request

With `async:false` (the default), the HTTP request waits for the correlated Shell result, subject to `wait_timeout_ms` and a maximum server wait of 300 seconds.

Example response:

```json
{
  "request_id": "7c0b2b77-41cb-4d6e-a8f3-45bc261a7d56",
  "kind": "shell.exec",
  "state": "completed",
  "result": {
    "success": true,
    "task_id": "shell-20261118-080000-a1b2c3",
    "exit_code": 0,
    "stdout": "Capture process listing...",
    "stderr": "",
    "duration_ms": 781,
    "cwd": "E:\\_airbridge_temp"
  }
}
```

Check `result.exit_code`; `result.success:true` means the remote Shell request was executed and returned a result, not that the command's exit code was zero. A nonzero `exit_code` must be handled as command failure by the caller.

If the HTTP wait expires before a response arrives, the API can return `202 pending`; poll `GET /api/v1/requests/{request_id}`. If the transport outcome is indeterminate, it returns `503 unknown`; query the same request ID and do not resubmit with a new ID.

#### Asynchronous request

With `async:true`, the initial HTTP request returns an acceptance receipt, normally HTTP `202`:

```json
{
  "request_id": "7c0b2b77-41cb-4d6e-a8f3-45bc261a7d56",
  "kind": "shell.exec",
  "state": "accepted",
  "result": {
    "success": true,
    "task_id": "shell-20261118-080000-a1b2c3",
    "status": "running",
    "async": true,
    "pid": 4321
  }
}
```

This does **not** mean the remote command has finished. The request record remains an acceptance receipt. Query the actual Shell task using the original Shell request ID:

### GET `/api/v1/shell/tasks/by-request/{request_id}`

Requires `shell.read`. The path parameter is the original UUID returned by `POST /api/v1/shell/exec`, not the Shell Engine's `task_id`. Only the Agent that owns the original Shell request may query it.

The endpoint asks host B for the current task snapshot using a correlated read-only ABFP request. It returns:

- `200` and `state:"completed"` when the Shell task has finished;
- `202` and `state:"accepted"` while it is still running (repeat the same GET);
- `503` for an unknown / unavailable task state or when the peer has not negotiated `shell.task.result-query`;
- `404` when the original request is unknown, expired, or not owned by the calling Agent.

Example completed response:

```json
{
  "request_id": "7c0b2b77-41cb-4d6e-a8f3-45bc261a7d56",
  "query_request_id": "d6be9a51-6907-4d44-bc88-f5b4ad79d22f",
  "task_id": "shell-20261118-080000-a1b2c3",
  "state": "completed",
  "result": {
    "success": true,
    "task_id": "shell-20261118-080000-a1b2c3",
    "finished": true,
    "killed": false,
    "exit_code": 0,
    "stdout": "final command output",
    "stderr": "",
    "command": "Get-Process",
    "cwd": "E:\\_airbridge_temp"
  },
  "error": null
}
```

The caller must still inspect `result.exit_code` and `result.killed`. A completed Shell task is not necessarily a successful professional workflow.

If this endpoint returns `202` with a `query_request_id`, the caller may poll `GET /api/v1/requests/{query_request_id}` to inspect that same query request without sending another ABFP frame. Calling the original Shell task endpoint again is also safe because it is read-only, but it issues a new status query to host B.

### GET `/api/v1/requests/{request_id}`

Reads the correlated request receipt. For an asynchronous Shell request, this remains the initial `accepted` acknowledgement; use `/api/v1/shell/tasks/by-request/{request_id}` for the task's current / final state.

### POST `/api/v1/shell/kill`

Body:

```json
{ "task_id": "shell-...", "pid": 0 }
```

This submits a termination request to the Shell Engine and immediately acknowledges submission. It is not a confirmation that a target professional application has closed. Shell Kill only controls tasks known to the Shell Engine; it is not a substitute for a Skill's normal Capture / Allegro exit workflow.

### POST `/api/v1/shell/tasks`

Requests a legacy task list; details are currently reported through the AirBridge console. Use the correlated task-result endpoint above for a specific asynchronous task.

## Runtime capability feature

Peers that support asynchronous Shell result queries advertise:

`shell.task.result-query`

Host A must not send `SHELL_TASK_GET` to a peer that has not advertised this feature.

## Blacklist themes (host B)

Blocked patterns include format/diskpart/volume wipe, shutdown/restart, dangerous `net user` or HKLM deletes, recursive force delete of system roots. Prefer non-destructive commands. The blacklist is a safety backstop, not a substitute for explicit user authorization and Skill-level validation.
