# -*- coding: utf-8 -*-
"""Contract tests for the credential-managed AirBridge MCP STDIO Provider."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill"))

from transport import (  # noqa: E402
    AirBridgeProvider,
    AirBridgeProviderError,
    OperationStatus,
)


FAKE_MCP_SERVER = r"""
import json
import sys

def send(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    try:
        request = json.loads(line)
    except Exception:
        continue
    method = request.get("method")
    if "id" not in request:
        continue
    request_id = request["id"]
    if method == "initialize":
        send({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-airbridge", "version": "test"},
            },
        })
    elif method == "tools/list":
        send({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {"name": "airbridge_shell_execute", "inputSchema": {"type": "object"}},
                    {"name": "airbridge_request_status", "inputSchema": {"type": "object"}},
                ]
            },
        })
    elif method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        rid = args.get("request_id")
        if name == "airbridge_shell_execute":
            command = args.get("command", "")
            if command == "simulate-accepted":
                value = {"request_id": rid, "state": "accepted"}
            elif command == "simulate-unknown":
                value = {"request_id": rid, "state": "unknown", "error": {"code": "TEST_UNKNOWN"}}
            elif command == "simulate-mismatch":
                value = {"request_id": "7b2f8d18-03f6-4bca-8f63-bae7e7f39d88", "state": "completed", "result": {"success": True, "exit_code": 0}}
            else:
                value = {
                    "request_id": rid,
                    "kind": "shell.exec",
                    "state": "completed",
                    "result": {"success": True, "exit_code": 0, "stdout": command, "stderr": ""},
                }
        elif name == "airbridge_request_status":
            value = {
                "request_id": rid,
                "state": "completed",
                "result": {"success": True, "exit_code": 0, "stdout": "status result", "stderr": ""},
            }
        else:
            value = {"state": "not_submitted", "error": {"code": "UNKNOWN_TOOL"}}
        send({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(value)}],
                "structuredContent": value,
                "isError": False,
            },
        })
"""


class AirBridgeProviderTests(TestCase):
    def setUp(self):
        self.request_id = str(uuid.uuid4())
        self.provider = AirBridgeProvider(
            agent_id="antigravity",
            command=[sys.executable, "-u", "-c", FAKE_MCP_SERVER],
            timeout_seconds=3,
            environ={"PATH": ""},
        )

    def tearDown(self):
        self.provider.close()

    def test_provider_info_and_capabilities_use_registered_mcp_tools(self):
        info = self.provider.get_info()
        self.assertEqual(info.provider_name, "airbridge")
        self.assertEqual(info.contract_version, "1.0")
        capabilities = {
            item.name: item.supported
            for item in self.provider.get_capabilities()
        }
        self.assertTrue(capabilities["shell.execute"])
        self.assertTrue(capabilities["request.read"])

    def test_shell_execute_calls_generic_mcp_tool_and_preserves_original_uuid(self):
        command = "Get-Location | Select-Object -ExpandProperty Path"
        response = self.provider.shell_execute(
            command,
            {"shell": "powershell", "cwd": r"E:\_airbridge_temp", "timeout": 30},
            self.request_id,
        )
        self.assertEqual(response.status, OperationStatus.COMPLETED)
        self.assertEqual(response.request_id, self.request_id)
        self.assertTrue(response.success)
        self.assertEqual(response.result["stdout"], command)

    def test_accepted_and_unknown_states_remain_distinct(self):
        accepted = self.provider.shell_execute(
            "simulate-accepted", {}, self.request_id
        )
        self.assertEqual(accepted.status, OperationStatus.ACCEPTED)
        self.assertEqual(accepted.request_id, self.request_id)

        unknown = self.provider.shell_execute(
            "simulate-unknown", {}, self.request_id
        )
        self.assertEqual(unknown.status, OperationStatus.UNKNOWN)
        self.assertEqual(unknown.request_id, self.request_id)

    def test_get_operation_uses_read_only_status_tool_with_same_uuid(self):
        response = self.provider.get_operation(self.request_id)
        self.assertEqual(response.status, OperationStatus.COMPLETED)
        self.assertEqual(response.request_id, self.request_id)
        self.assertEqual(response.result["stdout"], "status result")

    def test_response_request_id_mismatch_is_rejected(self):
        with self.assertRaises(AirBridgeProviderError):
            self.provider.shell_execute(
                "simulate-mismatch", {}, self.request_id
            )

    def test_provider_does_not_require_token_environment_or_token_argument(self):
        response = self.provider.shell_execute("Get-Location", {}, self.request_id)
        self.assertTrue(response.success)

    def test_invalid_command_and_options_are_rejected(self):
        with self.assertRaises(ValueError):
            self.provider.shell_execute(" ", {}, self.request_id)
        with self.assertRaises(ValueError):
            self.provider.shell_execute(
                "Get-Location", {"unsupported": True}, self.request_id
            )
        with self.assertRaises(ValueError):
            self.provider.shell_execute(
                "Get-Location", {"timeout": 0}, self.request_id
            )

    def test_invalid_request_id_is_rejected_before_mcp_call(self):
        with self.assertRaises(ValueError):
            self.provider.shell_execute("Get-Location", {}, "not-a-uuid")

    def test_missing_agent_id_fails_closed(self):
        with self.assertRaises(AirBridgeProviderError):
            AirBridgeProvider(
                command=[sys.executable, "-u", "-c", FAKE_MCP_SERVER],
                environ={"PATH": ""},
            )

    def test_missing_executable_path_fails_closed(self):
        with self.assertRaises(AirBridgeProviderError):
            AirBridgeProvider(agent_id="antigravity", environ={"PATH": ""})

    def test_close_prevents_further_operations(self):
        self.provider.close()
        with self.assertRaises(RuntimeError):
            self.provider.get_info()


if __name__ == "__main__":
    main()
