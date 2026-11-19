---
name: orcad-capture
description: Own OrCAD Capture version policy, process identity, Tcl Bridge session and Capture application lifecycle on the remote Host B through the generic AirBridge HTTP API. Use when a task must inspect or operate a Capture design through its Tcl/Dbo runtime.
---

# OrCAD Capture Skill

## Implementation status

This package currently contains the **M3.2.1 / M3.2.2 foundations and the M3.2.3 controlled-startup implementation**: configuration validation, Session Manifest, read-only process inventory, fail-closed HTTP client, guarded lifecycle state model, authorized Capture launch, unique capAutoLoad Hook deployment and Tcl READY handshake. The startup path is covered by Mock/Hosted CI only; it has not been executed on Host B or against a live Capture 17.2 installation. Opening a design, running Dbo workflows, saving and graceful exit remain unimplemented.

## Ownership boundary

This Skill owns Capture-specific decisions and workflow:

- Capture version selection and process identity verification;
- the per-session Tcl Bridge, capAutoLoad Hook, READY handshake and request/result queue;
- Capture Tcl and Dbo helpers;
- opening and verifying a DSN/OPJ, save policy and graceful Tcl `Exit`;
- checking the original Capture PID has exited before a session is marked closed.

AirBridge remains the generic communication channel. This Skill must not import AirBridge Core Python modules, open a serial port, implement ABFP, manage Agent tokens, or rely on AirBridge-managed EDA lifecycle behavior.

The new Skill must not call:

- `/api/v1/eda/versions` or `/api/v1/eda/execute`;
- EDA-specific ABFP frames or `target_file` / `keep_open`;
- the legacy `CaptureTclRuntimeManager`, `CaptureTclAdapter` or `EDAExecutor`;
- any API that silently launches, opens, saves or closes a Capture application.

The old implementation remains available only for existing compatibility callers until the separately planned retirement phase.

## Host model

Host A runs this Skill and calls only the Host-A AirBridge HTTP listener at `http://127.0.0.1:8888`. Host B runs the same `AirBridge.exe` as Host A and runs Cadence Capture. All Host-B process and file operations must be submitted through generic Shell and RFS endpoints.

Do not treat a Host-B path as a Host-A local path. Do not fall back to local sandbox filesystem operations when the remote link is unavailable.

## Authentication

Set `AIRBRIDGE_AGENT_TOKEN` in the Agent's Host-A environment. Provision only the scopes needed for the requested operation:

- `shell.execute` for remote process inspection and launch commands;
- `shell.read` for asynchronous Shell task status;
- `filesystem.read` for READY, Manifest, result and diagnostic reads;
- `filesystem.write` for creating this Skill's directories and uploading its own Bridge / Hook / request files.

Use `request.read` only when the calling workflow needs correlated request records. Do not request `eda.execute`.

The client rejects non-loopback API origins so that the Bearer token cannot be sent to another host. Tokens must never be copied into the Manifest, remote files, shell command arguments, requests, results, logs or exception messages.

## Current code

- `scripts/airbridge_client.py` — standard-library HTTP client. It sends Bearer-authenticated JSON requests to the loopback API, validates JSON objects, preserves HTTP 202 acceptance responses, refuses redirects, and never automatically retries. A transport error after submission is an **unknown outcome**; reconcile using the original identity before deciding what to do next.
- `scripts/capture_session.py` — non-secret configuration, Session Manifest persistence, process / READY / exit evidence structures and guarded lifecycle state transitions. `ProcessExitEvidence` binds the original Capture PID and process start timestamp to an explicit process-absent result and UTC check timestamp.
- `../../test/test_orcad_capture_skill.py` — local mock-HTTP and state-model tests.
- `../../test/test_orcad_capture_process.py` — read-only process inventory, response parsing, multiple-instance and ownership-matching tests.
- `../../test/test_orcad_capture_startup.py` — Mock end-to-end startup, authorization gate, per-child environment ticket, no-overwrite Hook creation, READY verification and UNKNOWN/no-retry tests.

## Session and safety rules

The state vocabulary is:

`absent → inspecting → starting → bridge_pending → ready ↔ busy → closing → closed`

Failures and indeterminate results are explicit `failed` or `unknown` states. `unknown` may return only to read-only inspection; it is not permission to submit a duplicate operation or start another Capture process.

Before a future implementation may mark a session READY, it must match the Session ID, high-entropy Startup Nonce, verified Capture PID, expected Tcl version and SHA-256 of the deployed Bridge. PID alone is not a process identity: compare the executable image path and process start time as well.

An unrecognized or manually launched Capture process is not owned by this Skill. The inventory classifies unmatched processes as unowned, multiple processes as ambiguous, and a reused or mismatched PID as an identity mismatch. Never inject a Hook into any of these processes, close them, or start a second instance merely because the existing process cannot be identified. An owned instance requires an exact PID, normalized executable path, process start timestamp and file-version match.

The capAutoLoad Hook lives in a shared installation directory, so it additionally requires the unique Session ID and Startup Nonce environment ticket inherited by the exact `Start-Process` child. A manually launched Capture that encounters the Hook without this ticket returns immediately before sourcing the Bridge. The Hook clears both ticket variables from its own process environment before Bridge startup.

Saving, overwriting, discarding changes, normal application exit and force termination require explicit user authorization. The M3.2.3 controlled-startup flow does not save or close Capture; the Session state model still requires explicit authorization before entering `closing`, and `closed` requires structured `ProcessExitEvidence` matching the original Capture PID and process start timestamp, confirming process absence at a recorded UTC check time.

## Configuration

Optional Host-A environment variables:

| Variable | Purpose |
|---|---|
| `AIRBRIDGE_API_BASE_URL` | Loopback AirBridge origin; defaults to `http://127.0.0.1:8888` |
| `AIRBRIDGE_AGENT_TOKEN` | Bearer token; read only by the HTTP client, never stored in the Manifest |
| `AIRBRIDGE_CAPTURE_EXE` | Explicit absolute Host-B `capture.exe` path |
| `AIRBRIDGE_CAPTURE_AUTOLOAD_DIR` | Explicit absolute Host-B `capAutoLoad` directory; must be selected together with `AIRBRIDGE_CAPTURE_EXE` |
| `AIRBRIDGE_CAPTURE_VERSION` | Expected Capture file version |
| `AIRBRIDGE_CAPTURE_TCL_VERSION` | Expected Tcl version; defaults to `8.6.0` |
| `AIRBRIDGE_CAPTURE_SESSION_ROOT` | Host-B session root; defaults to `%LOCALAPPDATA%\AirBridgeSkills\Capture\sessions` |
| `AIRBRIDGE_CAPTURE_SHELL_TIMEOUT` | Generic Shell task timeout in seconds |
| `AIRBRIDGE_CAPTURE_READY_TIMEOUT` | Future Bridge READY timeout in seconds |
| `AIRBRIDGE_CAPTURE_POLL_INTERVAL` | Future read-only status polling interval |

Do not infer an installation path from a version label. A later startup phase must require an explicit configured path or report the discovered candidates for user selection.

## Development checks

Run from the repository root:

```powershell
python -m unittest discover -s test -p "test_orcad_capture_skill.py" -v
python -m unittest discover -s test -p "test_orcad_capture_process.py" -v
python -m unittest discover -s test -p "test_orcad_capture_startup.py" -v
python -m py_compile skill/orcad-capture/scripts/airbridge_client.py skill/orcad-capture/scripts/capture_session.py skill/orcad-capture/scripts/capture_process.py skill/orcad-capture/scripts/capture_startup.py
```

These tests cover mock HTTP, Session state transitions, read-only process inventory and controlled-startup orchestration. They do not emulate Capture 17.2, Tcl/Dbo business operations, the physical serial link or the two-host deployment; live Host-B startup acceptance remains a separate validation step.
