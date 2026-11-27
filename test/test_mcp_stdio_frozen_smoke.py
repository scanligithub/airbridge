from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
import json
import os
import sqlite3
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import Client, StdioServerParameters

from airbridge_mcp_gateway.credential_store import WindowsCredentialStore
from airbridge_mcp_gateway.gateway_policy import GatewayPolicyStore

SKILL_ROOT = Path(__file__).resolve().parents[1] / "skill"
sys.path.insert(0, str(SKILL_ROOT))
from transport.stdio_client import McpStdioClient  # noqa: E402


EXPECTED_TOOLS = {
    "airbridge_get_capabilities",
    "airbridge_get_eda_versions",
    "airbridge_shell_execute",
    "airbridge_eda_execute",
    "airbridge_request_status",
}


class _FakeAirBridgeHandler(BaseHTTPRequestHandler):
    requests: list[str] = []

    def log_message(self, *_args):
        return

    def do_GET(self):
        type(self).requests.append("GET " + self.path)
        if self.path == "/api/v1/runtime/capabilities":
            payload = {"state": "negotiated", "features": ["stdio.policy.smoke"]}
            status = 200
        elif self.path == "/api/v1/eda/versions":
            payload = {"success": True, "capture": ["17.2"]}
            status = 200
        elif self.path.startswith("/api/v1/requests/"):
            request_id = self.path.rsplit("/", 1)[-1]
            payload = {
                "request_id": request_id,
                "state": "completed",
                "result": {"success": True, "exit_code": 0, "stdout": "status result"},
            }
            status = 200
        else:
            payload = {"error": "not found"}
            status = 404
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size).decode("utf-8"))
        type(self).requests.append("POST " + self.path)
        if self.path != "/api/v1/shell/exec":
            response = {"state": "not_submitted", "error": "unexpected path"}
            status = 404
        else:
            response = {
                "request_id": payload["request_id"],
                "kind": "shell.exec",
                "state": "completed",
                "result": {
                    "success": True,
                    "exit_code": 0,
                    "stdout": "Host B current directory",
                    "stderr": "",
                },
            }
            status = 200
        raw = json.dumps(response).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _get_tool_input_schema(tool):
    """Return the wire-level MCP inputSchema across SDK model naming styles."""
    if isinstance(tool, dict):
        schema = tool.get("inputSchema", tool.get("input_schema"))
    else:
        schema = getattr(tool, "inputSchema", None)
        if schema is None:
            schema = getattr(tool, "input_schema", None)
        if schema is None and hasattr(tool, "model_dump"):
            payload = tool.model_dump(by_alias=True)
            schema = payload.get("inputSchema", payload.get("input_schema"))
    if not isinstance(schema, dict):
        raise AssertionError(
            f"MCP tool {getattr(tool, 'name', '<unknown>')} has no inputSchema: "
            f"{schema!r}"
        )
    return schema


def _assert_m2_5_input_schemas(tools) -> None:
    """Verify constraints published by the frozen EXE through tools/list."""
    by_name = {tool.name: tool for tool in tools}

    for name in ("airbridge_get_capabilities", "airbridge_get_eda_versions"):
        schema = _get_tool_input_schema(by_name[name])
        if schema.get("type") != "object" or schema.get("properties") != {}:
            raise AssertionError(
                f"{name} must expose an empty object schema: {schema!r}"
            )
        if "required" in schema:
            raise AssertionError(
                f"{name} must not declare required arguments: {schema!r}"
            )

    request_schema = _get_tool_input_schema(
        by_name["airbridge_request_status"]
    )
    if request_schema.get("required") != ["request_id"]:
        raise AssertionError(
            f"request_status required fields mismatch: {request_schema!r}"
        )
    request_id = request_schema.get("properties", {}).get("request_id", {})
    if request_id.get("type") != "string" or request_id.get("format") != "uuid":
        raise AssertionError(
            f"request_id must be published as a UUID string: {request_id!r}"
        )

    shell_schema = _get_tool_input_schema(by_name["airbridge_shell_execute"])
    if shell_schema.get("required") != ["request_id", "command"]:
        raise AssertionError(f"Shell tool required fields mismatch: {shell_schema!r}")
    shell_properties = shell_schema.get("properties", {})
    shell_request_id = shell_properties.get("request_id", {})
    if shell_request_id.get("type") != "string" or shell_request_id.get("format") != "uuid":
        raise AssertionError(f"Shell request_id must be a UUID string: {shell_request_id!r}")
    shell_command = shell_properties.get("command", {})
    if shell_command.get("type") != "string" or shell_command.get("minLength") != 1:
        raise AssertionError(f"Shell command must be a non-empty string: {shell_command!r}")
    expected_shell = {
        "shell": {"type": "string", "default": "powershell"},
        "cwd": {"type": "string", "default": ""},
        "timeout": {"type": "integer", "default": 60, "minimum": 1, "maximum": 300},
        "wait_timeout_ms": {"type": "integer", "default": 0, "minimum": 0, "maximum": 300_000},
    }
    for name, constraints in expected_shell.items():
        field = shell_properties.get(name, {})
        for key, value in constraints.items():
            if field.get(key) != value:
                raise AssertionError(
                    f"Shell {name}.{key} expected {value!r}, got {field.get(key)!r}"
                )

    execute_schema = _get_tool_input_schema(by_name["airbridge_eda_execute"])
    if execute_schema.get("required") != ["script"]:
        raise AssertionError(
            f"eda_execute required fields mismatch: {execute_schema!r}"
        )
    properties = execute_schema.get("properties", {})

    for name in ("script", "hint", "version"):
        field = properties.get(name, {})
        if (
            field.get("type") != "string"
            or field.get("minLength") != 1
            or field.get("pattern") != r"\S"
        ):
            raise AssertionError(
                f"{name} must reject empty/whitespace-only strings: {field!r}"
            )

    expected = {
        "target_file": {"type": "string", "default": ""},
        "timeout": {
            "type": "integer",
            "default": 60,
            "minimum": 1,
            "maximum": 300,
        },
        "keep_open": {"type": "boolean", "default": True},
        "wait_timeout_ms": {
            "type": "integer",
            "default": 60_000,
            "minimum": 0,
            "maximum": 300_000,
        },
    }
    for name, constraints in expected.items():
        field = properties.get(name, {})
        for key, value in constraints.items():
            if field.get(key) != value:
                raise AssertionError(
                    f"{name}.{key} expected {value!r}, got {field.get(key)!r}; "
                    f"field={field!r}"
                )


async def exercise_frozen_stdio(
    executable: Path,
    agent_id: str,
    base_url: str,
    local_app_data: Path,
) -> str:
    policy = GatewayPolicyStore(local_app_data / "AirBridge" / "gateway.json")
    parameters = StdioServerParameters(
        command=str(executable.resolve()),
        args=["mcp", "stdio"],
        env={
            "AIRBRIDGE_AGENT_ID": agent_id,
            "AIRBRIDGE_BASE_URL": base_url,
            "AIRBRIDGE_ENABLE_EDA_EXECUTE": "0",
            "LOCALAPPDATA": str(local_app_data),
        },
        cwd=str(executable.resolve().parent),
    )

    # Use a real frozen child process and real OS stdin/stdout/stderr pipes.
    # The same child stays alive while the parent atomically changes its policy.
    policy.write(enabled=True, agents={agent_id: True})
    async with Client(parameters) as client:
        listed = await client.list_tools()
        actual_tools = {tool.name for tool in listed.tools}
        missing = EXPECTED_TOOLS - actual_tools
        extra = actual_tools - EXPECTED_TOOLS
        if missing or extra:
            raise AssertionError(
                f"Frozen STDIO tool registry mismatch; missing={sorted(missing)}, "
                f"extra={sorted(extra)}"
            )

        # Inspect the schema returned by the actual frozen child process,
        # not the source server's in-process tool objects.
        _assert_m2_5_input_schemas(listed.tools)

        shell_request_id = str(uuid.uuid4())
        shell_result = await client.call_tool(
            "airbridge_shell_execute",
            {
                "request_id": shell_request_id,
                "command": "Get-Location | Select-Object -ExpandProperty Path",
            },
        )
        if (
            shell_result.is_error
            or shell_result.structured_content.get("state") != "completed"
            or shell_result.structured_content.get("request_id") != shell_request_id
        ):
            raise AssertionError(
                f"Frozen generic Shell tool failed: {shell_result.structured_content!r}"
            )
        status_result = await client.call_tool(
            "airbridge_request_status",
            {"request_id": shell_request_id},
        )
        if (
            status_result.is_error
            or status_result.structured_content.get("state") != "completed"
            or status_result.structured_content.get("request_id") != shell_request_id
        ):
            raise AssertionError(
                f"Frozen Shell request status lookup failed: {status_result.structured_content!r}"
            )

        allowed = await client.call_tool("airbridge_get_capabilities", {})
        if allowed.is_error or allowed.structured_content.get("state") != "negotiated":
            raise AssertionError(f"Enabled Gateway call failed: {allowed.structured_content!r}")

        # Strict integer fields must reject JSON booleans instead of coercing
        # True to 1. The EDA execution feature remains disabled in this smoke test.
        for field_name in ("timeout", "wait_timeout_ms"):
            invalid_args = {"script": "schema validation smoke"}
            invalid_args[field_name] = True
            rejected = await client.call_tool("airbridge_eda_execute", invalid_args)
            if not rejected.is_error:
                raise AssertionError(
                    f"Boolean value was accepted for integer field {field_name}: "
                    f"{rejected.structured_content!r}"
                )

        # EDA execution remains disabled locally and creates a minimal DENY event.
        eda_disabled = await client.call_tool(
            "airbridge_eda_execute",
            {"script": "SENSITIVE_EDA_SCRIPT_MUST_NOT_BE_STORED"},
        )
        if eda_disabled.structured_content.get("error", {}).get("code") != "EDA_EXECUTION_DISABLED":
            raise AssertionError(f"EDA execution was not locally disabled: {eda_disabled.structured_content!r}")

        # Turn Gateway OFF while this already-running MCP child is retained.
        # Exercise every tool family with sensitive-looking arguments; none may
        # reach the HTTP API, and only metadata may enter the local audit DB.
        policy.write(enabled=False, agents={agent_id: True})
        gateway_off_calls = [
            ("airbridge_get_capabilities", {}),
            ("airbridge_shell_execute", {
                "request_id": str(uuid.uuid4()),
                "command": "SENSITIVE_SHELL_COMMAND_MUST_NOT_BE_STORED",
            }),
            ("airbridge_request_status", {"request_id": str(uuid.uuid4())}),
            ("airbridge_get_eda_versions", {}),
            ("airbridge_eda_execute", {
                "script": "SENSITIVE_EDA_SCRIPT_MUST_NOT_BE_STORED",
            }),
        ]
        for tool_name, arguments in gateway_off_calls:
            denied = await client.call_tool(tool_name, arguments)
            if denied.structured_content.get("error", {}).get("code") != "GATEWAY_DISABLED":
                raise AssertionError(
                    f"{tool_name} was not intercepted after Gateway OFF: "
                    f"{denied.structured_content!r}"
                )

        # Re-enable Gateway but disable only this Agent.
        policy.write(enabled=True, agents={agent_id: False})
        agent_off = await client.call_tool("airbridge_get_eda_versions", {})
        if agent_off.structured_content.get("error", {}).get("code") != "AGENT_DISABLED":
            raise AssertionError(f"Existing STDIO process did not observe Agent OFF: {agent_off.structured_content!r}")

        # Corrupt or unknown local policy must fail closed, never fall through.
        (local_app_data / "AirBridge" / "gateway.json").write_text(
            "{malformed policy", encoding="utf-8"
        )
        invalid = await client.call_tool(
            "airbridge_request_status",
            {"request_id": str(uuid.uuid4())},
        )
        if invalid.structured_content.get("error", {}).get("code") != "GATEWAY_POLICY_INVALID":
            raise AssertionError(f"Malformed policy was not rejected: {invalid.structured_content!r}")

        # Verify exact audit semantics and prove arguments/credentials are absent.
        audit_path = local_app_data / "AirBridge" / "gateway_audit.sqlite3"
        with closing(sqlite3.connect(audit_path)) as connection:
            columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(gateway_policy_audit)"
                ).fetchall()
            }
            expected_columns = {
                "audit_id", "timestamp", "agent_id", "tool_name",
                "decision", "reason_code",
            }
            if columns != expected_columns:
                raise AssertionError(f"Unexpected Gateway audit columns: {columns!r}")
            rows = connection.execute(
                """
                SELECT agent_id, tool_name, decision, reason_code
                FROM gateway_policy_audit
                ORDER BY audit_id
                """
            ).fetchall()
        expected_events = [
            (agent_id, "airbridge_eda_execute", "DENY", "EDA_EXECUTION_DISABLED"),
            (agent_id, "airbridge_get_capabilities", "DENY", "GATEWAY_DISABLED"),
            (agent_id, "airbridge_shell_execute", "DENY", "GATEWAY_DISABLED"),
            (agent_id, "airbridge_request_status", "DENY", "GATEWAY_DISABLED"),
            (agent_id, "airbridge_get_eda_versions", "DENY", "GATEWAY_DISABLED"),
            (agent_id, "airbridge_eda_execute", "DENY", "GATEWAY_DISABLED"),
            (agent_id, "airbridge_get_eda_versions", "DENY", "AGENT_DISABLED"),
            (agent_id, "airbridge_request_status", "DENY", "GATEWAY_POLICY_INVALID"),
        ]
        if sorted(rows) != sorted(expected_events):
            raise AssertionError(
                f"Gateway local denial audit events mismatch: {rows!r}"
            )
        audit_bytes = audit_path.read_bytes()
        for forbidden in (
            b"SENSITIVE_SHELL_COMMAND_MUST_NOT_BE_STORED",
            b"SENSITIVE_EDA_SCRIPT_MUST_NOT_BE_STORED",
            b"non-production-ci-stdio-smoke-token",
        ):
            if forbidden in audit_bytes:
                raise AssertionError("Gateway audit database contains sensitive input")

        # Restoring a valid ON state should restore the same still-running process.
        policy.write(enabled=True, agents={agent_id: True})
        restored = await client.call_tool("airbridge_get_capabilities", {})
        if restored.is_error or restored.structured_content.get("state") != "negotiated":
            raise AssertionError(f"Restored Gateway policy did not take effect: {restored.structured_content!r}")

    return shell_request_id


def exercise_custom_transport_client(
    executable: Path,
    agent_id: str,
    base_url: str,
    local_app_data: Path,
) -> str:
    # Exercise the exact lightweight client used by AirBridgeProvider against
    # the frozen Windows EXE, not only against a Python fake MCP server.
    child_env = {
        "AIRBRIDGE_AGENT_ID": agent_id,
        "AIRBRIDGE_BASE_URL": base_url,
        "AIRBRIDGE_ENABLE_EDA_EXECUTE": "0",
        "LOCALAPPDATA": str(local_app_data),
    }
    for name in (
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "APPDATA",
        "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "PROGRAMDATA",
    ):
        if name in os.environ:
            child_env[name] = os.environ[name]

    client = McpStdioClient(
        command=[str(executable.resolve()), "mcp", "stdio"],
        environ=child_env,
        timeout_seconds=15.0,
    )
    try:
        if not {
            "airbridge_shell_execute",
            "airbridge_request_status",
        }.issubset(client.tool_names):
            raise AssertionError(
                f"Custom STDIO client saw incomplete tools: {sorted(client.tool_names)}"
            )
        request_id = str(uuid.uuid4())
        submitted = client.call_tool(
            "airbridge_shell_execute",
            {
                "request_id": request_id,
                "command": "Get-Location | Select-Object -ExpandProperty Path",
                "wait_timeout_ms": 0,
            },
        )
        if submitted.get("request_id") != request_id:
            raise AssertionError(
                f"Custom STDIO client lost original request ID: {submitted!r}"
            )
        if submitted.get("state") not in ("completed", "pending", "accepted"):
            raise AssertionError(
                f"Custom STDIO Shell submission returned unexpected state: {submitted!r}"
            )
        status = client.call_tool(
            "airbridge_request_status",
            {"request_id": request_id},
        )
        if status.get("request_id") != request_id or status.get("state") != "completed":
            raise AssertionError(
                f"Custom STDIO client status lookup failed: {status!r}"
            )
        return request_id
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=Path)
    args = parser.parse_args()

    executable = args.exe.resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"Frozen executable not found: {executable}")
    if os.name != "nt":
        raise RuntimeError("This frozen STDIO smoke test must run on Windows")

    agent_id = f"ci-stdio-smoke-{uuid.uuid4().hex[:8]}"
    store = WindowsCredentialStore()
    store.store(agent_id, "non-production-ci-stdio-smoke-token")
    _FakeAirBridgeHandler.requests.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAirBridgeHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with TemporaryDirectory(prefix="airbridge-m2-5-stdio-") as temp:
            local_app_data = Path(temp)
            base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
            shell_request_id = asyncio.run(
                exercise_frozen_stdio(
                    executable,
                    agent_id,
                    base_url,
                    local_app_data,
                )
            )
            custom_client_request_id = exercise_custom_transport_client(
                executable,
                agent_id,
                base_url,
                local_app_data,
            )
        expected_requests = [
            "POST /api/v1/shell/exec",
            "GET /api/v1/requests/" + shell_request_id,
            "POST /api/v1/shell/exec",
            "GET /api/v1/requests/" + custom_client_request_id,
            "GET /api/v1/runtime/capabilities",
            "GET /api/v1/runtime/capabilities",
        ]
        if sorted(_FakeAirBridgeHandler.requests) != sorted(expected_requests):
            raise AssertionError(
                "Gateway OFF, Agent OFF, malformed policy, or invalid arguments "
                "reached the AirBridge API, or Shell request mapping was incorrect: "
                f"{_FakeAirBridgeHandler.requests!r}"
            )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        store.delete(agent_id)

    print("Frozen AirBridge.exe STDIO initialize + tools/list: PASS")
    print("Frozen AirBridge.exe publishes M2.5 and generic Shell parameter constraints: PASS")
    print("Frozen AirBridge.exe generic Shell submit preserves caller UUID: PASS")
    print("Frozen AirBridge.exe reads Shell status by original UUID: PASS")
    print("Transport McpStdioClient interoperates with frozen AirBridge.exe: PASS")
    print("Frozen AirBridge.exe rejects booleans for integer parameters: PASS")
    print("Frozen AirBridge.exe reads M2.4 Agent credential: PASS")
    print("Gateway OFF blocks all tool families in an already-running STDIO process: PASS")
    print("Gateway-local DENY audit records metadata only: PASS")
    print("Agent OFF blocks an already-running STDIO process: PASS")
    print("Malformed policy fails closed and is audited: PASS")
    print("Policy re-enable takes effect without restarting STDIO: PASS")
    print("stdout carries only MCP protocol frames: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
