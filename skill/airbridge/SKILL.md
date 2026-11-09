---
name: airbridge
description: Transfer files and arbitrary text between host A and host B over a serial link using AirBridge. Also supports optional remote filesystem and remote shell utilities. Use for AirBridge, serial data transfer, isolated host file transfer, RFS, or generic remote shell.
---

# AirBridge

AirBridge is a serial transport between host A and host B. It transmits bytes and files; it does not interpret or execute transmitted scripts.

**Prerequisite:** AirBridge is running on both hosts, serial link is connected, and host A exposes http://127.0.0.1:8888.

## Architecture

| Role | Machine | AirBridge responsibility |
|------|---------|--------------------------|
| Host A | Control / networked machine | GUI, serial transmission, local HTTP API |
| Host B | Isolated machine | Serial reception, data display, file reception |

AirBridge does not discover Cadence installations, launch/close Capture or Allegro, inject Tcl/SKILL, or execute scripts. Any application integration must be a separate host-side adapter or an application script running under the target application's control.

## Link health

Use a generic filesystem request (not an EDA version probe):

```powershell
Invoke-RestMethod http://127.0.0.1:8888/api/v1/fs/drives -Method POST -Body '{}' -ContentType 'application/json'
```

## Generic raw-data transport

Send a UTF-8 string over the serial link:

```powershell
$body = @{ data = 'puts "hello"' } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8888/api/v1/data/send -Method POST -Body $body -ContentType 'application/json; charset=utf-8'
```

The destination AirBridge displays the received text in its generic data panel. It does not parse or execute it. The payload limit is 65,000 bytes per frame; use file transfer for larger scripts or binary files.

## Remote filesystem (RFS)

| Action | Method | Path |
|--------|--------|------|
| List drives | POST | /api/v1/fs/drives |
| List directory | POST | /api/v1/fs/ls |
| Read / preview file | POST | /api/v1/fs/read |
| Write file | POST | /api/v1/fs/write |
| Make directory | POST | /api/v1/fs/mkdir |
| Delete | POST | /api/v1/fs/rm |
| Stat / MD5 | POST | /api/v1/fs/stat |
| Pull large file to A | POST | /api/v1/fs/pull |

## Optional remote Shell

The optional Shell API remains a separate general-purpose facility:

```powershell
$body = @{ command = 'Get-Date; hostname'; shell = 'powershell'; timeout = 30 } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8888/api/v1/shell/exec -Method POST -Body $body -ContentType 'application/json'
```

Avoid destructive commands. Prefer short, non-interactive operations.

## Agent behavior

- Treat RAW_STREAM as data delivery only; never claim that received Tcl/SKILL was executed.
- For large scripts, transfer them as files using the normal file-transfer UI.
- Report HTTP submission as transmission queued, not as application execution.
- Ask the user to verify application-side execution using their independent Tcl/SKILL workflow.