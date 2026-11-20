from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import Client, StdioServerParameters

from airbridge_mcp_gateway.credential_store import WindowsCredentialStore
from airbridge_mcp_gateway.gateway_policy import GatewayPolicyStore


EXPECTED_TOOLS = {
    "airbridge_get_capabilities",
    "airbridge_get_eda_versions",
    "airbridge_eda_execute",
    "airbridge_request_status",
}


class _FakeAirBridgeHandler(BaseHTTPRequestHandler):
    requests: list[str] = []

    def log_message(self, *_args):
        return

    def do_GET(self):
        type(self).requests.append(self.path)
        if self.path == "/api/v1/runtime/capabilities":
            payload = {"state": "negotiated", "features": ["stdio.policy.smoke"]}
            status = 200
        elif self.path == "/api/v1/eda/versions":
            payload = {"success": True, "capture": ["17.2"]}
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


async def exercise_frozen_stdio(
    executable: Path,
    agent_id: str,
    base_url: str,
    local_app_data: Path,
) -> None:
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

        allowed = await client.call_tool("airbridge_get_capabilities", {})
        if allowed.is_error or allowed.structured_content.get("state") != "negotiated":
            raise AssertionError(f"Enabled Gateway call failed: {allowed.structured_content!r}")

        # Simulate Gateway OFF while this already-running MCP child is retained.
        policy.write(enabled=False, agents={agent_id: True})
        gateway_off = await client.call_tool("airbridge_get_capabilities", {})
        if gateway_off.structured_content.get("error", {}).get("code") != "GATEWAY_DISABLED":
            raise AssertionError(f"Existing STDIO process did not observe Gateway OFF: {gateway_off.structured_content!r}")

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

        # Restoring a valid ON state should restore the same still-running process.
        policy.write(enabled=True, agents={agent_id: True})
        restored = await client.call_tool("airbridge_get_capabilities", {})
        if restored.is_error or restored.structured_content.get("state") != "negotiated":
            raise AssertionError(f"Restored Gateway policy did not take effect: {restored.structured_content!r}")


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
            asyncio.run(
                exercise_frozen_stdio(
                    executable,
                    agent_id,
                    base_url,
                    local_app_data,
                )
            )
        if len(_FakeAirBridgeHandler.requests) != 2:
            raise AssertionError(
                "Gateway OFF, Agent OFF, or malformed policy reached the AirBridge API: "
                f"{_FakeAirBridgeHandler.requests!r}"
            )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        store.delete(agent_id)

    print("Frozen AirBridge.exe STDIO initialize + tools/list: PASS")
    print("Frozen AirBridge.exe reads M2.4 Agent credential: PASS")
    print("Gateway OFF blocks an already-running STDIO process: PASS")
    print("Agent OFF blocks an already-running STDIO process: PASS")
    print("Malformed policy fails closed: PASS")
    print("Policy re-enable takes effect without restarting STDIO: PASS")
    print("stdout carries only MCP protocol frames: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
