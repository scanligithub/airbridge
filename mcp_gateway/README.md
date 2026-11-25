# AirBridge MCP Gateway (M2.1 / M2.2)

> **M2.5 integration note (2026-11-20):** This README documents the currently deployed M2.1 / M2.2 standalone Python-package and virtual-environment setup. M2.5 retains the existing STDIO server, Agent ID → Windows Credential Manager lookup, and domain-neutral Tool layer, but the target is to integrate them into the unified AirBridge.exe / Python 3.11+ runtime. New M2.5 Antigravity / OpenCode adapters must not create a separate virtual environment or write AIRBRIDGE_AGENT_TOKEN; see [ADR-013](../doc/ADR-013_M2.5_Stdio本机MCP接入与客户端托管生命周期.md).



A standalone MCP stdio server for the existing AirBridge HTTP API on Host A.
It is intentionally separate from the AirBridge desktop application's Python
environment and does not import AirBridge internal modules.

## Scope

The first version exposes four domain-neutral tools:

| MCP tool | Existing AirBridge endpoint | Access |
|---|---|---|
| airbridge_get_capabilities | GET /api/v1/runtime/capabilities | Read-only |
| airbridge_get_eda_versions | GET /api/v1/eda/versions | Read-only |
| airbridge_eda_execute | POST /api/v1/eda/execute | Execution; disabled by default |
| airbridge_request_status | GET /api/v1/requests/{request_id} | Read-only |

The gateway treats script text as opaque. It does not know Tcl, Dbo, SKILL,
Capture design objects, Allegro objects, or professional workflows. Those belong
to their corresponding Agent Skills.

The gateway does not expose Remote Shell, Runtime Session creation/close APIs,
profiles, or per-agent session isolation. Those capabilities are not provided
by the current public HTTP API.

## Requirements and isolation

- Windows Host A (or another local machine that runs the AirBridge HTTP API).
- Python 3.11 or newer, in a dedicated virtual environment.
- MCP Python SDK pinned to mcp==2.3.0.
- AirBridge reachable at http://127.0.0.1:8888.

Do not install MCP dependencies into the existing AirBridge application
interpreter. The gateway is a separate process with a separate environment.

## Install on Windows

From this directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install .
```

## Configure an MCP host

Use the absolute path to the gateway virtual-environment Python executable.
An example client configuration is:

```json
{
  "mcpServers": {
    "airbridge": {
      "command": "E:\\path\\to\\airbridge\\mcp_gateway\\.venv\\Scripts\\python.exe",
      "args": ["-m", "airbridge_mcp_gateway.server"],
      "env": {
        "AIRBRIDGE_BASE_URL": "http://127.0.0.1:8888",
        "AIRBRIDGE_AGENT_ID": "opencode",
        "AIRBRIDGE_ENABLE_EDA_EXECUTE": "0"
      }
    }
  }
}
```

Each MCP host has its own configuration location and may launch its own gateway
subprocess. All of those processes call the same AirBridge HTTP API. No public
network listener is created by the stdio gateway.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| AIRBRIDGE_BASE_URL | http://127.0.0.1:8888 | Local AirBridge API base URL; loopback only |
| AIRBRIDGE_HTTP_TIMEOUT_SECONDS | 20 | Base HTTP timeout, 1–60 seconds; execute calls extend it to cover wait_timeout_ms |
| AIRBRIDGE_ENABLE_EDA_EXECUTE | 0 | Gateway-side execution switch; explicit opt-in required |
| AIRBRIDGE_AGENT_ID | optional (preferred on M2.4) | Non-secret Agent ID used to load that Agent's credential from Windows Credential Manager |
| AIRBRIDGE_AGENT_TOKEN | legacy compatibility | Per-Agent Bearer credential; use only for existing configurations during migration |

airbridge_eda_execute can run code in the remote EDA process. Keep it disabled
unless the MCP client is trusted and the user intends to grant execution access.
MCP tool annotations are only client hints, not authorization. Each Gateway
process must resolve its own per-Agent credential using `AIRBRIDGE_AGENT_ID`
(preferred) or the legacy `AIRBRIDGE_AGENT_TOKEN`. The AirBridge HTTP API
continues to validate the Bearer token and apply server-side scopes. The token
is never accepted as an MCP tool argument.

## M2.4: Tokenless Agent authentication on Windows

For a new Agent, provision its credential on Host A from the repository root:

```powershell
py -3.10 tools/airbridge_agent_admin.py provision --agent-id antigravity --label "Antigravity"
py -3.10 tools/airbridge_agent_admin.py provision --agent-id opencode --label "OpenCode"
```

The command generates a separate Token for each Agent, writes it to the current
Windows user's Credential Manager, reads it back to verify persistence, and
prints only non-secret metadata. The default scopes are read-only:
`runtime.read`, `eda.versions.read`, and `request.read`. Do not add write,
Shell, or EDA execution scopes unless separately authorized.

Set only the matching non-secret `AIRBRIDGE_AGENT_ID` in that MCP Host's
`env` block. Do not put a Token in the MCP JSON, environment, command line,
or logs. Do not set both `AIRBRIDGE_AGENT_ID` and
`AIRBRIDGE_AGENT_TOKEN`. The Credential Manager and Gateway must run under
the same Windows user account.

The CLI and AirBridge server must use the same
`AIRBRIDGE_AGENT_SECURITY_DB`. When not explicitly set, both use
`%LOCALAPPDATA%\\AirBridge\\agent_security.sqlite3`.

This M2.4.1 stage does not yet generate or rewrite MCP Host configuration,
rotate credentials automatically, or migrate existing Agent configurations.
Existing Hosts configured with `AIRBRIDGE_AGENT_TOKEN` continue to use the
legacy mode until migrated; do not revoke their current Tokens before the
corresponding Host has been switched and verified.

To inspect or revoke credentials, use the local administrator CLI:

```powershell
py -3.10 tools/airbridge_agent_admin.py list --agent-id opencode
py -3.10 tools/airbridge_agent_admin.py revoke --agent-id opencode
```

Agent-wide revocation disables server-side credentials (the authority for
access control) and attempts to remove the local Credential Manager entry.
The legacy manual issue path is still available only with explicit
`--reveal-token` opt-in; it is not the normal provisioning workflow.

EDA execution is separately gated by the local Gateway opt-in and the
Agent token's \`eda.execute\` scope. The initial Antigravity / OpenCode
credentials above are read-only and do not receive that scope.

Loopback binding is not an OS-level isolation boundary for processes running
under the same Windows account. Do not expose port 8888 to a LAN or the
Internet. Per-Agent tokens do not create independent Runtime Sessions or
Capture interpreters.


## AirBridge Agent security and rate-limit policy

Configure these variables in the environment of the AirBridge desktop process on Host A (not in the Gateway MCP host config):

| Variable | Default | Meaning |
|---|---:|---|
| AIRBRIDGE_AGENT_SECURITY_DB | `%LOCALAPPDATA%\\AirBridge\\agent_security.sqlite3` | Local credential, ownership, quota reservation and audit database |
| AIRBRIDGE_AGENT_READ_RPM | `60` | Per-Agent ordinary API requests per minute |
| AIRBRIDGE_AGENT_STATUS_RPM | `120` | Per-Agent request-status lookups per minute |
| AIRBRIDGE_AGENT_EXECUTE_RPM | `5` | Per-Agent EDA submission attempts per minute |
| AIRBRIDGE_AGENT_MAX_PENDING_EDA | `1` | Maximum simultaneous uncompleted EDA requests per Agent |

The sliding-window rate counters are in memory and reset when the AirBridge
HTTP service process restarts. In-flight EDA reservations and request ownership
records are stored in SQLite. Unknown execution outcomes retain the Agent's
in-flight reservation for a conservative 1,800 seconds. Audit records retain
90 days, capped at 200,000 rows. Admission audit must be stored before an API
handler can reach Runtime; an admission audit error returns HTTP 503 and blocks
the operation.

## Request behaviour

- The gateway creates a UUID request_id for every accepted execute call.
- The returned request_id must be retained by the caller.
- If the HTTP response is unconfirmed or the server reports `state=unknown`, call
  `airbridge_request_status` with the same ID. Never submit a new ID as a retry.
- Requests rejected before submission (for example 401 / 403 / 429 or payload
  validation) do not receive a pollable request ID.
- The gateway rejects script requests whose serialized JSON body exceeds
  60,000 bytes, leaving margin below the ABFP frame payload ceiling.
- Larger payloads require a future/explicit artifact-RFS integration; this
  gateway does not upload files.

## Run and test

```powershell
.\.venv\Scripts\python.exe -m airbridge_mcp_gateway.server
```

The process uses MCP stdio; stdout is reserved for MCP protocol messages.

From this directory, run the standard-library unit tests:

```powershell
py -3.11 -m unittest discover -s tests -v
```

The HTTP contract tests use a local mock HTTP server and do not require a live
Capture instance. The MCP SDK server-registration tests are run in CI after the
pinned SDK is installed. Live MCP-client discovery and Host-A/Host-B execution
remain separate integration acceptance steps.
