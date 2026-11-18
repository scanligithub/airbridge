---
name: airbridge
description: Control an air-gapped host B from host A via AirBridge HTTP API at 127.0.0.1-8888. Use when the user mentions AirBridge, remote OrCAD or Allegro, isolated Cadence machine, physical network gate, RFS, remote PowerShell on host B, or needs file transfer, EDA script execution, or shell commands across a serial air gap.
---

# AirBridge

Operate the isolated machine (host B) from the control machine (host A) through AirBridge.

**Prerequisite:** The same AirBridge.exe is running on host A and host B, the serial link is up, host A exposes `http://127.0.0.1:8888`, and the calling Agent has a valid AirBridge Bearer Token with the required scopes.

All agent calls go to **host A localhost only**. Never assume the sandbox can reach the user's 127.0.0.1. Every `/api/` request must include `Authorization: Bearer <AGENT_TOKEN>`. Use the Agent's configured token; never ask the user to paste a secret into a prompt or write the token into logs or task files.

## Architecture (do not confuse roles)

| Role | Machine | AirBridge role |
|------|---------|----------------|
| Host A | Networked / agent machine | Control side + HTTP API on 8888 |
| Host B | Air-gapped Cadence PC | Executes FS / EDA / Shell over serial |

Default temp dir on B: `E:\_airbridge_temp`.

## Quick health check

Before remote work, verify the generic RFS path (this does not require Cadence to be installed):

```bash
curl -s -X POST http://127.0.0.1:8888/api/v1/fs/drives \
  -H "Authorization: Bearer $AIRBRIDGE_AGENT_TOKEN" \
  -H "Content-Type: application/json" -d '{"timeout":10}'
```

Expect `success: true` and a remote drive listing. If this fails, stop and report the AirBridge / serial link issue rather than falling back to the local sandbox.

## API base

- Base URL: `http://127.0.0.1:8888`
- JSON body, UTF-8
- Synchronous Shell requests return a correlated structured result in the HTTP response. Asynchronous Shell requests first return an acceptance receipt; query the final task state through `GET /api/v1/shell/tasks/by-request/{request_id}`. Legacy EDA requests may use older acknowledgement / log behavior.

Detailed request/response shapes: see `references/api-reference.md`.

## 1. Remote filesystem (RFS)

Prefer these for listing, reading, writing, and managing paths on B.

| Action | Method | Path |
|--------|--------|------|
| List drives | POST | `/api/v1/fs/drives` |
| List directory | POST | `/api/v1/fs/ls` |
| Read / preview file | POST | `/api/v1/fs/read` |
| Write file | POST | `/api/v1/fs/write` |
| Make directory | POST | `/api/v1/fs/mkdir` |
| Delete | POST | `/api/v1/fs/rm` |
| Stat / MD5 | POST | `/api/v1/fs/stat` |
| Pull large file to A | POST | `/api/v1/fs/pull` |

Examples:

```bash
# Drives
curl -s -X POST http://127.0.0.1:8888/api/v1/fs/drives \
  -H "Authorization: Bearer $AIRBRIDGE_AGENT_TOKEN" \
  -H "Content-Type: application/json" -d '{"timeout":10}'

# List
curl -s -X POST http://127.0.0.1:8888/api/v1/fs/ls \
  -H "Authorization: Bearer $AIRBRIDGE_AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path":"E:\\\\_airbridge_temp","timeout":10}'

# Read preview (max ~64KB by default)
curl -s -X POST http://127.0.0.1:8888/api/v1/fs/read \
  -H "Authorization: Bearer $AIRBRIDGE_AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path":"E:\\\\_airbridge_temp\\\\_cadence_stdout.log","max_preview_bytes":10000}'
```

On Windows PowerShell, use `Invoke-RestMethod` with a hashtable body converted to JSON (escape paths carefully).

## 2. Remote Shell (PowerShell / CMD)

Default shell is **PowerShell**. Dangerous commands are blocked on B (format, shutdown, diskpart, recursive wipe of system paths, etc.).

```bash
curl -s -X POST http://127.0.0.1:8888/api/v1/shell/exec \
  -H "Authorization: Bearer $AIRBRIDGE_AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "command": "Get-Date; hostname",
    "shell": "powershell",
    "cwd": "E:\\\\_airbridge_temp",
    "timeout": 60,
    "async": false
  }'
```

With `async:false` (the default), the HTTP request waits for the correlated remote Shell result and returns a RequestTracker response. The result is under `result`; inspect both `result.success` and the remote command's `result.exit_code`, then read `result.stdout` / `result.stderr`.

With `async:true`, the initial response is normally HTTP `202` with `state: "accepted"` and a `result.task_id`. This is only task-start acknowledgement. Query the final task snapshot using the original Shell request ID:

```http
GET /api/v1/shell/tasks/by-request/{request_id}
```

The result includes `finished`, `killed`, `exit_code`, `stdout`, and `stderr`. HTTP `202` means the task is still running; retry the same query. A missing/expired remote task is reported as `unknown`, not as successful completion. The query requires `shell.read` and is restricted to the Agent that submitted the original Shell request.

Other endpoints:

- `POST /api/v1/shell/kill` — body `{"task_id":"shell-..."}` or `{"pid":1234}`
- `POST /api/v1/shell/tasks` — request task list (legacy response is displayed in the AirBridge console)
- `GET /api/v1/shell/tasks/by-request/{request_id}` — query the asynchronous task started by a specific Shell request

**Safety rules for the agent:**

- Do not attempt shutdown, format, diskpart, or mass delete of `C:\`, `Windows`, `System32`
- Prefer working under `E:\_airbridge_temp` unless the user specifies another path
- Prefer short, non-interactive commands; avoid prompts

## 3. Legacy EDA compatibility API

The following EDA-specific endpoints are retained only for older clients:

- `GET /api/v1/eda/versions`
- `POST /api/v1/eda/execute`

They retain historical Cadence discovery, script-type guessing, `target_file`, `keep_open`, and AirBridge-managed process lifecycle behavior. EDA execution is disabled by default unless the server administrator explicitly enables it. The API requires the `eda.versions.read` or `eda.execute` scope and a valid Bearer Token.

**New Capture / Allegro Skills must not use these endpoints to manage professional application lifecycle.** They should use generic Shell, RFS, and file-transfer APIs to perform their own software discovery, startup, Bridge deployment, design opening, professional operations, save policy, and graceful exit. AirBridge must not make those decisions on a Skill's behalf.

Existing legacy workspace logs may still be inspected through RFS when diagnosing an old EDA request, but they are not a new Skill integration contract.

## 4. Recommended workflows

### A. One-shot remote command

1. Health check and capability check using authenticated read-only APIs
2. `shell/exec` with a clear command and `cwd`
3. Check `result.exit_code`; use the asynchronous task-result query when the command was launched with `async:true`

### B. Professional software workflow

1. Let the relevant professional Skill determine software version and lifecycle policy
2. Use generic Shell to inspect / start the requested application on B
3. Use RFS or file transfer to deploy that Skill's own Bridge and helper scripts
4. Let the Skill submit professional commands and verify their results
5. Let the Skill decide whether to save and when to request normal application exit

### C. Diagnose failed OrCAD run

1. `fs/ls` on `E:\_airbridge_temp`
2. `fs/read` on `_cadence_stdout.log` and `_bootstrap.tcl`
3. Summarize errors; fix script; resubmit

Helper script for diagnosis (run on host A): `scripts/diag_temp.py`.

## 5. Agent behavior

- Always state that actions target **host B through AirBridge**, not the local sandbox
- If API connection fails, do not invent success; report link/serial/AirBridge process issues
- For asynchronous Shell, treat `accepted` as task-start acknowledgement and query `/api/v1/shell/tasks/by-request/{request_id}` for its final state
- Prefer RFS for remote file inspection and Shell for general Windows tasks; use EDA-specific API routes only for explicitly supported legacy clients
- Keep commands minimal and reversible when possible

## 6. Known limits

- Asynchronous Shell execution returns an acceptance receipt first; use `GET /api/v1/shell/tasks/by-request/{request_id}` to retrieve its current or final task result
- Large file transfer may need the GUI or pull API; confirm with user for multi-hundred-MB files
- Encoding: Chinese Windows on B may show mojibake in logs; content on disk is usually fine when read via RFS with auto decode
- Interactive PowerShell sessions are not supported; one-shot or async tasks only

## Optional scripts

- `scripts/diag_temp.py` — list and read B temp workspace via API
- `scripts/shell_exec.py` — small CLI wrapper for `/api/v1/shell/exec`
