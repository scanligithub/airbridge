# AirBridge MCP Gateway (M2.1 / M2.2)

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
| AIRBRIDGE_ENABLE_EDA_EXECUTE | 0 | Explicit opt-in required to expose remote script execution |

airbridge_eda_execute can run code in the remote EDA process. Keep it disabled
unless the MCP client is trusted and the user intends to grant execution access.
MCP tool annotations are only client hints, not authorization. This first
iteration does not add authentication to AirBridge's HTTP API; loopback binding
is not a per-agent authorization boundary. Do not expose port 8888 to a LAN or
the Internet. Agent identity, server-side authorization, and audit records are
M2.3 work.

## Request behaviour

- The gateway creates a UUID request_id for every accepted execute call.
- The returned request_id must be retained by the caller.
- If the HTTP response is unconfirmed, call airbridge_request_status with the
  same ID. Do not submit a new execution merely because the caller timed out.
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
