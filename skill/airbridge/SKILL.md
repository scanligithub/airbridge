---
name: airbridge
description: Control an air-gapped host B from host A via AirBridge HTTP API at 127.0.0.1-8888. Use when the user mentions AirBridge, remote OrCAD or Allegro, isolated Cadence machine, physical network gate, RFS, remote PowerShell on host B, or needs file transfer, EDA script execution, or shell commands across a serial air gap.
---

# AirBridge

Operate the isolated machine (host B) from the control machine (host A) through AirBridge.

**Prerequisite:** AirBridge is running on both hosts, serial link is up, and host A exposes `http://127.0.0.1:8888`.

All agent calls go to **host A localhost only**. Never assume the sandbox can reach the user's 127.0.0.1.

## Architecture (do not confuse roles)

| Role | Machine | AirBridge role |
|------|---------|----------------|
| Host A | Networked / agent machine | Control side + HTTP API on 8888 |
| Host B | Air-gapped Cadence PC | Executes FS / EDA / Shell over serial |

Default temp dir on B: `E:\_airbridge_temp`.

## Quick health check

Before real work, verify the link:

```bash
curl -s http://127.0.0.1:8888/api/v1/eda/versions
```

Expect `success: true` and a `versions` map (e.g. SPB 17.2). If this fails, stop and tell the user to check serial connection and that AirBridge is running on both sides.

## API base

- Base URL: `http://127.0.0.1:8888`
- JSON body, UTF-8
- Shell and some EDA paths return **submitted** immediately; full results appear in AirBridge console logs via serial frames (`SHELL_RESULT`, `SHELL_OUTPUT`, EDA log stream). Tell the user to watch the AirBridge console when results are not in the HTTP body.

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
  -H "Content-Type: application/json" -d '{"timeout":10}'

# List
curl -s -X POST http://127.0.0.1:8888/api/v1/fs/ls \
  -H "Content-Type: application/json" \
  -d '{"path":"E:\\\\_airbridge_temp","timeout":10}'

# Read preview (max ~64KB by default)
curl -s -X POST http://127.0.0.1:8888/api/v1/fs/read \
  -H "Content-Type: application/json" \
  -d '{"path":"E:\\\\_airbridge_temp\\\\_cadence_stdout.log","max_preview_bytes":10000}'
```

On Windows PowerShell, use `Invoke-RestMethod` with a hashtable body converted to JSON (escape paths carefully).

## 2. Remote Shell (PowerShell / CMD)

Default shell is **PowerShell**. Dangerous commands are blocked on B (format, shutdown, diskpart, recursive wipe of system paths, etc.).

```bash
curl -s -X POST http://127.0.0.1:8888/api/v1/shell/exec \
  -H "Content-Type: application/json" \
  -d '{
    "command": "Get-Date; hostname",
    "shell": "powershell",
    "cwd": "E:\\\\_airbridge_temp",
    "timeout": 60,
    "async": false
  }'
```

HTTP response is typically:

```json
{"status":"submitted","async":false,"hint":"..."}
```

Actual `exit_code` / `stdout` / `stderr` arrive as `SHELL_RESULT` in the AirBridge UI log. Ask the user to paste console lines when you need the real output.

Other endpoints:

- `POST /api/v1/shell/kill` — body `{"task_id":"shell-..."}` or `{"pid":1234}`
- `POST /api/v1/shell/tasks` — request task list (result via serial log)

**Safety rules for the agent:**

- Do not attempt shutdown, format, diskpart, or mass delete of `C:\`, `Windows`, `System32`
- Prefer working under `E:\_airbridge_temp` unless the user specifies another path
- Prefer short, non-interactive commands; avoid prompts

## 3. EDA automation (OrCAD Tcl / Allegro SKILL)

### Detect versions

```bash
curl -s http://127.0.0.1:8888/api/v1/eda/versions
```

### Submit script

```bash
curl -s -X POST http://127.0.0.1:8888/api/v1/eda/execute \
  -H "Content-Type: application/json" \
  -d '{
    "script": "puts \"hello from OrCAD\"",
    "hint": "agent_auto.tcl",
    "version": "auto",
    "timeout": 60
  }'
```

- `hint` ending in `.tcl` → OrCAD Capture Tcl path
- Skill-style content / `.il` style → Allegro SKILL path
- `version`: `"auto"` or a known SPB version string such as `"17.2"`

Logs stream back to the AirBridge console. Bootstrap and stdout on B often live under:

- `E:\_airbridge_temp\_bootstrap.tcl`
- `E:\_airbridge_temp\_cadence_stdout.log`

Read them via RFS when diagnosing failures.

## 4. Recommended workflows

### A. One-shot remote command

1. Health check (`/api/v1/eda/versions` or a trivial `shell/exec`)
2. `shell/exec` with a clear command and `cwd`
3. Ask user for AirBridge console `SHELL_RESULT` if stdout is required

### B. Cadence batch on B

1. Detect versions
2. Optionally upload scripts/data via RFS write or file send UI
3. `eda/execute` with script + version
4. Poll/read `E:\_airbridge_temp\_cadence_stdout.log` via RFS
5. Pull result files back if needed

### C. Diagnose failed OrCAD run

1. `fs/ls` on `E:\_airbridge_temp`
2. `fs/read` on `_cadence_stdout.log` and `_bootstrap.tcl`
3. Summarize errors; fix script; resubmit

Helper script for diagnosis (run on host A): `scripts/diag_temp.py`.

## 5. Agent behavior

- Always state that actions target **host B through AirBridge**, not the local sandbox
- If API connection fails, do not invent success; report link/serial/AirBridge process issues
- When HTTP only returns `submitted`, explicitly say results are in the AirBridge console
- Prefer RFS for file inspection; prefer Shell for general Windows tasks; prefer EDA API for Cadence
- Keep commands minimal and reversible when possible

## 6. Known limits

- Shell HTTP API does not yet return full stdout in the HTTP body (serial log is authoritative)
- Large file transfer may need the GUI or pull API; confirm with user for multi-hundred-MB files
- Encoding: Chinese Windows on B may show mojibake in logs; content on disk is usually fine when read via RFS with auto decode
- Interactive PowerShell sessions are not supported; one-shot or async tasks only

## Optional scripts

- `scripts/diag_temp.py` — list and read B temp workspace via API
- `scripts/shell_exec.py` — small CLI wrapper for `/api/v1/shell/exec`
