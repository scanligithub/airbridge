---
name: orcad-capture
description: Own OrCAD Capture version policy, process identity, Tcl Bridge session and Capture application lifecycle on the remote Host B through the generic AirBridge HTTP API. Use when a task must inspect or operate a Capture design through its Tcl/Dbo runtime.
---

# OrCAD Capture Skill

## Implementation status

This package currently contains the **M3.2.1–M3.2.5 foundations**: Session Manifest, fail-closed HTTP client, read-only process inventory, authorized Capture startup, unique capAutoLoad Hook, Tcl READY handshake, the frame-safe Tcl request/result queue, controlled OPJ opening and Dbo read-only inspection. The M3.2.5 workflows are covered by Mock tests and Python-hosted Tcl 8.6 tests with mocked Dbo commands; they have not been executed inside live Capture 17.2 on Host B. Saving, design mutation, graceful Exit and process-close confirmation remain unimplemented.

## Ownership boundary

This Skill owns Capture-specific decisions and workflow:

- Capture version selection and process identity verification;
- the per-session Tcl Bridge, capAutoLoad Hook, READY handshake and request/result queue; each generic RFS operation is bounded to 16 KiB so its request and response fit within a single ABFP frame;
- Capture Tcl and Dbo helpers;
- opening and verifying one explicitly selected OPJ per Capture Session, plus read-only Dbo Session / active Design inventory; save policy and graceful Tcl `Exit` remain future work;
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
- `filesystem.write` for POST `/api/v1/fs/*` operations that create directories and upload Bridge / Hook / request files. The current AirBridge HTTP authorization map also classifies POST `/api/v1/fs/read` as `filesystem.write`; until the action-specific read/write scope split from PR #7 is integrated, the same scope is required for RFS reads.

Use `request.read` only when the calling workflow needs correlated request records. Do not request `eda.execute`.

The client rejects non-loopback API origins so that the Bearer token cannot be sent to another host. Tokens must never be copied into the Manifest, remote files, shell command arguments, requests, results, logs or exception messages.

## Current code

- `scripts/airbridge_client.py` — standard-library HTTP client. It sends Bearer-authenticated JSON requests to the loopback API, validates JSON objects, preserves HTTP 202 acceptance responses, refuses redirects, and never automatically retries. A transport error after submission is an **unknown outcome**; reconcile using the original identity before deciding what to do next.
- `scripts/capture_session.py` — non-secret configuration, Session Manifest persistence, process / READY evidence structures and the allowed state transitions. Capture-specific process and application decisions remain in this Skill.
- `../../test/test_orcad_capture_skill.py` — local mock-HTTP and state-model tests.
- `../../test/test_orcad_capture_process.py` — read-only process inventory, response parsing, multiple-instance and ownership-matching tests.
- `scripts/capture_requests.py` — authorized Tcl request submission in 16 KiB RFS write/append chunks, Host-B full-file length/SHA-256 verification and atomic publication, multipart result reassembly and read-only UNKNOWN reconciliation.
- `scripts/capture_design.py` — explicit OPJ path validation, read-only Host-B file preflight, single-design open guard, Dbo / Project Manager active-design identity verification, DboSession Design / Library enumeration, and local design-context reconciliation.
- `../../test/test_orcad_capture_startup.py` — Mock startup, authorization gate, per-child environment ticket, no-overwrite Hook creation, READY verification and UNKNOWN/no-retry tests.
- `../../test/test_orcad_capture_requests.py` — Mock queue protocol, exact identity/hash/CRC validation, authorization, no-overwrite publication, result polling, and UNKNOWN reconciliation tests.
- `../../test/test_orcad_capture_bridge_queue.py` — executes the actual Tcl 8.6 Bridge with Python's Tcl interpreter to verify READY creation, Unicode request/result round-trip, Tcl errors, serial processing and duplicate suppression.
- `../../test/test_orcad_capture_design.py` — exercises OPJ workflow guards and the Tcl Dbo probes using Tcl 8.6 with mocked Dbo wrappers; it does not emulate or validate Capture 17.2 itself.

## Session and safety rules

The state vocabulary is:

`absent → inspecting → starting → bridge_pending → ready ↔ busy → closing → closed`

Failures and indeterminate results are explicit `failed` or `unknown` states. `unknown` permits only read-only reconciliation by the original Request ID. A session may return to READY only after the matching result envelope and exact original Capture process identity are positively verified; it is never permission to resubmit.

Before a future implementation may mark a session READY, it must match the Session ID, high-entropy Startup Nonce, verified Capture PID, expected Tcl version and SHA-256 of the deployed Bridge. PID alone is not a process identity: compare the executable image path and process start time as well.

An unrecognized or manually launched Capture process is not owned by this Skill. The inventory classifies unmatched processes as unowned, multiple processes as ambiguous, and a reused or mismatched PID as an identity mismatch. Never inject a Hook into any of these processes, close them, or start a second instance merely because the existing process cannot be identified. An owned instance requires an exact PID, normalized executable path, process start timestamp and file-version match.

The capAutoLoad Hook lives in a shared installation directory, so it additionally requires the unique Session ID and Startup Nonce environment ticket inherited by the exact `Start-Process` child. A manually launched Capture that encounters the Hook without this ticket returns immediately before sourcing the Bridge. The Hook clears both ticket variables from its own process environment before Bridge startup.

The M3.2.4 queue accepts only explicitly authorized scripts. The current ABFP RFS endpoint sends one FS request/response per frame (maximum payload 65,000 bytes), so the Skill caps each raw transfer to 16 KiB. Host A writes the UUID-named `.tmp` request through RFS in one `w` chunk followed by `a` chunks. Generic Shell then verifies the complete staging-file length and SHA-256 and atomically renames it to `.req` without overwrite. Tcl 8.6 polls the session queue, claims one request by rename to `.running`, validates Session ID / Nonce / Request ID / payload length / CRC-32, evaluates the Tcl script once, and writes the ASCII result envelope to 16 KiB `.result.partNNNN` files. It publishes the small `.result` manifest only after all parts are written. Host A reads the manifest and each part using bounded RFS reads, reassembles the envelope and verifies total length and envelope CRC-32 before validating the result identity and per-field CRCs. The protocol requires no Tcl JSON or SHA package. Scripts are limited to 256 KiB; each result text field is limited to 512 KiB; the envelope is capped at 1.1 MB (at most 68 result parts).

If upload, publication, execution or result retrieval becomes indeterminate, the Skill persists the original Request ID and marks the Session UNKNOWN. It does not retry the upload, publish, or Tcl script. Reconciliation reads only the original result file and verifies the original Capture PID/path/start-time/version using the read-only process inventory before clearing UNKNOWN.

M3.2.5 supports one `.opj` path per Capture Session. It first confirms that the remote file exists on Host B and that no Design is already active. Opening requires explicit user authorization. The `Open` command and active-design verification are one correlated Tcl request. Success requires a non-NULL Dbo `GetActiveDesign`, a non-NULL `GetActivePMDesign`, matching object identity, and matching READY Capture PID / Tcl version. The Skill refuses to switch or adopt an existing active Design. A local Design Context is created before the Open request; an UNKNOWN or unverifiable result blocks all further Open attempts until the original Request ID is reconciled. The read-only Dbo inventory enumerates only Designs and Libraries already loaded in the current Session. It does not call `GetDesign`, `GetLib`, save, remove, or mutate any object.

Saving, overwriting, discarding changes, normal application exit and force termination require explicit user authorization. This foundation only allows a state transition into `closing` when the caller explicitly supplies authorization; it does not itself send an Exit command.

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
python -m unittest discover -s test -p "test_orcad_capture_requests.py" -v
python -m unittest discover -s test -p "test_orcad_capture_bridge_queue.py" -v
python -m unittest discover -s test -p "test_orcad_capture_design.py" -v
python -m py_compile skill/orcad-capture/scripts/airbridge_client.py skill/orcad-capture/scripts/capture_session.py skill/orcad-capture/scripts/capture_process.py skill/orcad-capture/scripts/capture_startup.py skill/orcad-capture/scripts/capture_requests.py skill/orcad-capture/scripts/capture_design.py
```

These tests validate the Skill foundation only. They do not emulate Capture, Tcl, Dbo APIs, capAutoLoad, the physical serial link or the two-host deployment.
