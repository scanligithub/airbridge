# AirBridge M2.4.1 — Host A Tokenless Credential Acceptance Runbook

- Status: Host A Stages A–F accepted for artifact source `35dd48dd162bd47db882de9137c870b388a58601`; later PR #14 lifecycle / audit / token-revoke changes require a new build and Host A retest
- PR: #14 (Draft; not merged)
- Branch: `feature/m2-4-zero-touch-agent-auth`
- Scope: exact-revision Windows EXE build and SHA-256 verification, Windows Credential Manager provisioning, Gateway Agent-ID lookup, read-only HTTP/MCP verification, revocation, graceful desktop / HTTP lifecycle validation
- Explicit exclusions: no forced process termination, no Shell / EDA execution, no Capture / Allegro lifecycle operation, no manual Host B operation, and no formal OpenCode / Antigravity MCP Host migration

## 1. Acceptance stages

Run the stages separately. Do not treat a successful GitHub build as permission to replace the running Host A executable.

| Stage | Purpose | Host impact |
|---|---|---|
| A. GitHub artifact build | Build an EXE from the exact PR revision | GitHub only |
| B. Artifact inspection | Verify source commit, SHA-256, size and platform | Download only; do not launch |
| C. Credential provisioning | Create one isolated read-only Agent identity and save its Token in the current Windows user's Credential Manager | Writes one Agent row to the configured SQLite DB and one local vault credential |
| D. Gateway read-only test | Configure one test MCP Host with non-secret Agent ID and call read-only tools | Starts/restarts only the selected MCP Gateway / Host through its normal UI |
| E. Revocation | Revoke the test Agent and delete its local vault credential | Changes only the test Agent credential |
| F. EXE lifecycle acceptance | Verify the new EXE closes its owned HTTP server and SQLite Store cleanly | Requires a separately approved controlled replacement of the running AirBridge app |

Stages C–E can be tested against the currently running M2.3 AirBridge HTTP service because the M2.4 credential flow reuses the existing Bearer authentication and database schema. This does **not** validate the new EXE shutdown changes. Stage F is separate and must not be performed without explicit approval.

## 2. Stage A — build from the PR branch on GitHub

The existing workflow `.github/workflows/build-airbridge-exe.yml` supports `workflow_dispatch` and builds the exact selected source revision.

1. Open the repository's **Actions** tab.
2. Select **AirBridge Windows EXE Build**.
3. Choose **Run workflow**.
4. Select branch `feature/m2-4-zero-touch-agent-auth`.
5. Start the workflow and wait for the build job to complete successfully.

The workflow checks out the selected branch's exact commit, compiles the AirBridge modules, builds `AirBridge.exe` with Python 3.10.11 / PyInstaller, calculates SHA-256, and uploads an artifact named:

`AirBridge-Windows-x64-<source-commit-sha>`

The artifact contains:
- `AirBridge.exe`
- `BUILD_INFO.txt`

The artifact is retained by GitHub Actions for 30 days.

**Required verification:** the workflow run's source revision and `BUILD_INFO.txt` `source_commit` must match the PR head SHA selected for the build. If the branch advances during the build, do not assume the artifact represents the newest head; compare exact SHAs.

## 3. Stage B — inspect the artifact, do not deploy

Download the artifact to a separate temporary folder. Do not overwrite the existing AirBridge.exe, build directory, dist directory, or any running process.

Read `BUILD_INFO.txt` and verify:
- `platform=Windows x64`
- `source_commit` equals the intended PR head
- `sha256` is present
- `size_bytes` is greater than 1 MiB

On Windows, independently verify the downloaded executable:

```powershell
Get-FileHash .\AirBridge.exe -Algorithm SHA256
(Get-Item .\AirBridge.exe).Length
```

The independently calculated SHA-256 must match `BUILD_INFO.txt`. Keep the artifact in a separate directory. Do not launch or replace the currently running executable in this stage.

## 4. Stage C — provision one isolated test Agent

### 4.1 Confirm the authoritative database path

The admin CLI and AirBridge Runtime must use the same Windows user and the same Agent Security SQLite database. The historical Host A configuration used the default:

`%LOCALAPPDATA%\AirBridge\agent_security.sqlite3`

Before provisioning, confirm the file already exists. Do not let the CLI silently create a second, empty database.

Example PowerShell preflight when the runtime uses the default path:

```powershell
$dbPath = Join-Path $env:LOCALAPPDATA "AirBridge\agent_security.sqlite3"
if (-not (Test-Path -LiteralPath $dbPath -PathType Leaf)) {
    throw "Expected Agent Security database was not found; stop and confirm the runtime DB path."
}
$env:AIRBRIDGE_AGENT_SECURITY_DB = $dbPath
```

If the running AirBridge process was started with a custom `AIRBRIDGE_AGENT_SECURITY_DB`, use that exact path instead. Do not guess, copy, move, or replace a live SQLite database.

### 4.2 Provision a dedicated test identity

Use a new test Agent ID so no existing Antigravity / OpenCode credential is overwritten:

```powershell
py -3.10 tools/airbridge_agent_admin.py provision --agent-id m24-hosta-smoke --label "M2.4 Host A read-only acceptance"
```

The default scopes are exactly:
- `runtime.read`
- `eda.versions.read`
- `request.read`

The CLI should print only non-secret metadata. It must not print the Token. The Token is written to the current Windows user's Credential Manager and read back for verification.

Inspect metadata only:

```powershell
py -3.10 tools/airbridge_agent_admin.py list --agent-id m24-hosta-smoke
```

Confirm one enabled credential and the exact three read-only scopes. Do not use `issue --reveal-token`.

## 5. Stage D — verify Gateway's transparent credential lookup

Use the M2.4 Gateway code from the same PR branch, installed in its dedicated Python 3.11+ virtual environment. Do not install MCP dependencies into the AirBridge desktop application's Python environment.

In one test MCP Host configuration, set only these non-secret environment values:

```json
{
  "AIRBRIDGE_BASE_URL": "http://127.0.0.1:8888",
  "AIRBRIDGE_AGENT_ID": "m24-hosta-smoke",
  "AIRBRIDGE_ENABLE_EDA_EXECUTE": "0"
}
```

Do not include `AIRBRIDGE_AGENT_TOKEN`. Do not configure both Agent ID and legacy Token. The Gateway and Credential Manager must run under the same Windows user account.

Use the selected MCP Host's normal UI to restart its Gateway subprocess, then call only:
- `airbridge_get_capabilities`
- `airbridge_get_eda_versions` (only if the serial link is already available)
- `airbridge_request_status` with a random, non-existent UUID (expected not-found response)

Expected results:
- Capabilities: HTTP 200 / successful MCP result.
- EDA versions: HTTP 200 if the existing Runtime link is available; otherwise record the transport-unavailable result without attempting to reconnect or manipulate Host B.
- Unknown request ID: HTTP 404 / not_found, or the Gateway's documented structured not-found result.
- No Token in MCP JSON, command line, stdout, stderr, or audit records.
- EDA execution remains disabled at both Gateway and AirBridge server levels.

Do not call `airbridge_eda_execute`. Do not call Shell endpoints or tools. Do not send POST requests to execution routes.

## 6. Stage E — revoke and verify cleanup

Revoke only the isolated test Agent:

```powershell
py -3.10 tools/airbridge_agent_admin.py revoke --agent-id m24-hosta-smoke
```

Expected output:
- `revoked` indicates whether server credentials were newly revoked.
- `credential_cleanup=deleted` when the local vault entry was removed.
- `credential_cleanup=not_found` is acceptable only when the entry was already absent.
- `credential_cleanup=failed` is a failure; the command must return a non-zero exit code and cleanup must be retried.

Then verify metadata:

```powershell
py -3.10 tools/airbridge_agent_admin.py list --agent-id m24-hosta-smoke
```

All credentials for the test Agent must be disabled. If the Gateway process still has the Token cached in memory, the server-side revocation must nevertheless make subsequent HTTP requests fail with HTTP 401. Close/restart only the test MCP Host / Gateway through its normal UI, not by force-killing processes.

## 7. Stage F — EXE lifecycle acceptance (separate authorization required)

The new source adds:
- HTTP server shutdown;
- waiting for in-flight HTTP handlers;
- server thread join;
- closing the SQLite Store only when the IPC server owns it;
- preserving caller ownership of an injected Store;
- a desktop close event that refuses to close if the HTTP server cannot stop safely.

CI covers Store ownership and server stop behavior. A true desktop EXE acceptance still requires a controlled replacement of the running Host A AirBridge app. Do not perform that replacement as part of Stages A–E.

Before Stage F, the user must explicitly authorize:
1. normal graceful closure of the current AirBridge desktop app;
2. launching the downloaded EXE from the verified artifact directory;
3. confirming the expected loopback listener and database path;
4. normal GUI closure and confirmation that the process exits without force termination.

No Host B action is part of this acceptance.

## 8. Evidence to record

Record metadata only; never record Token values:

- PR head SHA used for the build
- GitHub Actions build run URL and artifact name
- `BUILD_INFO.txt` source commit, SHA-256 and size
- independent SHA-256 verification result
- Windows user context (do not record account secrets)
- Agent Security DB path (redact user-specific path segments if sharing)
- test Agent ID
- provision result and exact scopes
- Gateway capabilities result
- EDA versions result or unavailable reason
- unknown request ID result
- revocation result and `credential_cleanup` state
- confirmation that no EDA / Shell execution request was sent
- whether Stage F was explicitly authorized and completed

## 9. Stop conditions

Stop and report without trying alternative paths if:
- the database file is missing or its path is uncertain;
- CLI and Runtime cannot be confirmed to use the same database and Windows user;
- provisioning prints a Token or reports a vault verification failure;
- the Gateway cannot read the credential;
- both `AIRBRIDGE_AGENT_ID` and `AIRBRIDGE_AGENT_TOKEN` are set;
- any read-only call unexpectedly reaches an execution route;
- server or local credential revocation is incomplete;
- a process would need to be force-terminated.

This runbook does not authorize deployment, process termination, database replacement, Host B operation, or EDA / Shell execution.

## 10. Host A acceptance record — 2026-11-20

- Acceptance source revision: `c63f07ecda0e1ca0b5140a711fcc9ea2466f70be`, matching the tested PR #14 branch HEAD at the time of the run.
- Host: Windows, running as the same local user for the AirBridge desktop process, administrator CLI and MCP Gateway subprocess.
- Existing AirBridge HTTP service: `127.0.0.1:8888`; the listener was confirmed to belong to the AirBridge child process.
- Agent Security database: the default `%LOCALAPPDATA%\AirBridge\agent_security.sqlite3` file existed and contained the expected Agent Security tables. The acceptance CLI was explicitly pointed at this same database path. An authenticated request through the running HTTP service using the test credential in that database returned HTTP 200, confirming that the running service accepted the credential.
- The dedicated test identity `m24-hosta-smoke` was already present and enabled when the read-only database inspection began. Its metadata showed exactly `runtime.read`, `eda.versions.read`, and `request.read`, with label `M2.4 Host A read-only acceptance`. It was not provisioned a second time in this acceptance session.
- Windows Credential Manager listed the namespaced entry `AirBridge/Agent/m24-hosta-smoke`. The PR #14 Credential Manager provider read the credential successfully without displaying it.
- Direct authenticated HTTP check: `GET /api/v1/runtime/capabilities` returned HTTP 200.
- A dedicated Python 3.11.9 virtual environment was created under `mcp_gateway/.venv`; the Gateway project installed successfully with pinned `mcp==2.3.0`.
- A real Gateway subprocess was launched through MCP stdio with `AIRBRIDGE_AGENT_ID=m24-hosta-smoke`, loopback base URL and `AIRBRIDGE_ENABLE_EDA_EXECUTE=0`. The child environment did not contain `AIRBRIDGE_AGENT_TOKEN`.
- MCP `initialize` succeeded. Tool discovery returned the four expected tools: `airbridge_get_capabilities`, `airbridge_get_eda_versions`, `airbridge_eda_execute`, and `airbridge_request_status`.
- `airbridge_get_capabilities` succeeded through MCP. `airbridge_request_status` for the deliberately nonexistent UUID `00000000-0000-4000-8000-000000000000` returned the expected `state=not_found` and HTTP 404.
- No `airbridge_eda_execute` call, Shell operation, EDA script, Capture operation, Allegro operation, or Host B operation was performed. The EDA versions tool was discovered but not called.
- Revocation result for the isolated test Agent: `revoked=1`, `credential_deleted=true`, `credential_cleanup=deleted`, exit code 0.
- Final read-only verification showed the test credential disabled with a populated `revoked_at`, and the matching Windows Credential Manager entry absent. The formal Antigravity and OpenCode Agent records each still had one enabled read-only credential; neither formal Agent was revoked or re-provisioned.
- No existing Antigravity / OpenCode MCP Host configuration was edited. No Token value was recorded in this acceptance evidence.

### Acceptance result and remaining boundary

- **Stages C–E: PASS** for the isolated test Agent credential lookup, authenticated HTTP / MCP read-only checks, server-side revocation and local Credential Manager cleanup.
- **Stages A / B: PASS for source `35dd48dd162bd47db882de9137c870b388a58601` only**; see Section 11.
- **Stage F: PASS for the same source `35dd48dd162bd47db882de9137c870b388a58601` only**; see Section 11.
- The PR branch subsequently changed the HTTP shutdown implementation, final-audit failure handling, and single-token revocation cleanup. Those changes are newer than the verified Host A executable and have not yet been built or accepted on Host A. Do not treat the earlier Stage A / B / F evidence as acceptance of the latest PR HEAD.
- The acceptance does not cover Host B, Capture, Allegro, EDA execution, Shell execution, formal MCP Host migration, or production Agent migration.


## 11. Stage A / B / F acceptance update — 2026-11-20

### Stage A — exact-revision GitHub build

- Workflow: **AirBridge Windows EXE Build**, run #7.
- Run URL: https://github.com/scanligithub/airbridge/actions/runs/44661076364
- Source branch: `feature/m2-4-zero-touch-agent-auth`.
- Source commit: `35dd48dd162bd47db882de9137c870b388a58601`.
- Build result: `success`.
- Artifact name: `AirBridge-Windows-x64-35dd48dd162bd47db882de9137c870b388a58601`.
- Artifact ID: `14595437949`.
- Uploaded ZIP size: `48,911,885` bytes.
- Uploaded artifact ZIP SHA-256 digest: `71238dd1f4c03e033a8170c20a4dfaf9b5d67bd3967fd6e577ec256f1e28b379`.
- The build log recorded `BUILD_INFO.txt` with `source_commit=35dd48dd162bd47db882de9137c870b388a58601`, Python 3.10.11, PyInstaller 6.22.3, Windows x64, EXE size `49,245,414` bytes, and EXE SHA-256 `0f55cecd703801788093187191577a3588c4543f020e4bd1fb265dfc5671e631`.

### Stage B — independent Host A artifact verification

- Verified executable path: `E:\\download\\AirBridge-Windows-x64-35dd48dd162bd47db882de9137c870b388a58601\\AirBridge.exe`.
- Host A independently calculated EXE SHA-256: `0f55cecd703801788093187191577a3588c4543f020e4bd1fb265dfc5671e631`.
- Host A independently reported file size: `49,245,414` bytes.
- The adjacent `BUILD_INFO.txt` source commit, SHA-256, size, Python version, PyInstaller version and platform matched the build record.
- Result: **PASS**. The executable that was running on Host A was the same verified build artifact; no additional download or binary replacement was needed for this acceptance.

### Stage F — graceful shutdown and restart

All operations below were explicitly authorized by the user and were performed on Host A using the normal desktop close path. No forced process termination was used.

**Before close**

- The verified EXE ran from the path recorded in Stage B.
- Parent / launcher PID: `9608`, parent process `explorer.exe` PID `14128`.
- GUI / HTTP child PID: `8480`.
- GUI window handle: `589988`.
- TCP listener: `127.0.0.1:8888`, owned by PID `8480`.
- Unauthenticated `GET /api/v1/runtime/capabilities` returned HTTP 401.

**Graceful close**

- Sent Windows `WM_CLOSE` to the verified AirBridge GUI window for PID `8480`.
- Message delivery result: `1`; message result: `0`.
- GUI PID `8480` exited.
- Parent / launcher PID `9608` exited.
- TCP port `8888` was released.
- No `Stop-Process`, taskkill, or forced termination was used.
- Result: **PASS**.

**Restart and recovery**

- Rechecked the EXE SHA-256 before restart; it still matched the Stage B value.
- Relaunched the same verified EXE path using `Start-Process`.
- New parent / launcher PID: `9600`.
- New GUI / HTTP child PID: `19740`.
- GUI window handle: `591982`.
- TCP listener returned on `127.0.0.1:8888`, owned by PID `19740`; its executable path matched the verified EXE.
- An immediate first sample reported `Responding=False`; after a 10-second stabilization interval, the GUI reported `Responding=True`, and a non-mutating `WM_NULL` probe returned `RESPONDED`.
- Unauthenticated `GET /api/v1/runtime/capabilities` again returned HTTP 401.
- Result: **PASS**. The temporary early `Responding=False` sample cleared after startup stabilization and was not persistent.

### Stage F evidence boundary

The acceptance directly verified graceful GUI / parent process exit, release and recovery of TCP port 8888, successful relaunch, stable GUI responsiveness, and continued HTTP authentication enforcement. The SQLite Store shutdown / ownership behavior is additionally covered by the PR's Agent Security CI and implementation tests; this host run did not separately instrument or inspect an OS-level SQLite file handle. No database was deleted, replaced, copied, or modified during Stage F.

### Overall Host A result

- **Stages A–F: PASS for the deployed / tested EXE source `35dd48dd162bd47db882de9137c870b388a58601` only.** The latest PR branch contains later source changes that still require a fresh exact-head build and, for the lifecycle changes, a separately authorized Host A Stage F retest.
- The AirBridge desktop application was left running after successful restart, under PID `19740`, with its HTTP listener on `127.0.0.1:8888`.
- No manual Host B operation, EDA Execute request, Shell operation, Capture operation, Allegro operation, or formal MCP Host migration was performed.
- The existing desktop startup code schedules `auto_connect_serial` after 100 ms. That routine scans serial ports and may issue a read-only `eda_versions` probe to a discovered candidate. Because Stage F relaunched the desktop application, this built-in startup probe may have run; its outcome was not separately captured. No Host B execution or design modification was initiated.
- PR #14 remains Draft / Open and requires a separate merge decision.


## 12. Subsequent PR code changes — new artifact acceptance required

After the Host A tests recorded in Section 11, PR #14 added the following code changes:

- Bounded HTTP handler draining during GUI shutdown (default 5-second drain deadline); while draining, new accepted connections are closed. If an active handler does not finish before the deadline, normal request acceptance resumes, the listener and SQLite Store remain available, and the GUI close is rejected so shutdown can be retried safely. Only after a successful drain is the listener closed; the serve thread then receives a separate bounded join window.
- Final audit write failure now returns HTTP 503 / `AGENT_AUDIT_FINALIZATION_FAILED` and an `unknown` outcome instead of returning an apparently successful response. The durable admission audit remains the precondition for Runtime access.
- Single-token revocation removes the local Credential Manager entry only if its stored Token ID matches the target; another current credential for the same Agent is preserved as `not_current`.

These source changes are newer than the Host A binary tested in Section 11. The current running Host A EXE remains the previously verified build unless separately replaced. Before claiming these changes accepted on Host A:

1. Build an EXE from the exact current PR HEAD.
2. Independently verify `BUILD_INFO.txt`, executable SHA-256, size and source commit.
3. Obtain explicit authorization for the updated artifact's Host A replacement / normal shutdown / restart test.
4. Repeat Stage F and record the new parent / child PIDs, listener release and recovery, GUI responsiveness, and HTTP 401 behavior.

No new artifact deployment is authorized by this runbook update. PR #14 remains Draft / Open and unmerged.


## 13. Per-Agent credential lifecycle serialization

The current PR branch additionally serializes local Agent credential mutations with a Windows named mutex scoped to the current interactive session. The mutex name is derived from SHA-256 of the normalized Agent ID and does not expose the Agent ID. The legacy explicit-reveal issue command, tokenless provision command, single-token revoke, and Agent-wide revoke all use the same per-Agent lock, preventing cooperating administrator CLI processes from interleaving server credential changes and Credential Manager writes/deletes.

A Windows thread-level mutex integration test verifies that two operations for the same Agent cannot hold the lock simultaneously. This code is newer than the Stage A/B/F Host A executable recorded in Section 11; the updated EXE and Host A lifecycle still require a fresh build and separately authorized retest.
