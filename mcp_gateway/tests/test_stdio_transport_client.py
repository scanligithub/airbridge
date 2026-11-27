from __future__ import annotations

import sys
import uuid
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2] / "skill"
sys.path.insert(0, str(SKILL_ROOT))

from transport.stdio_client import McpStdioClient  # noqa: E402


FAKE_GATEWAY_SERVER = r"""
from airbridge_mcp_gateway.server import create_server

class FakeGateway:
    def get_capabilities(self):
        return {"state": "negotiated"}

    def get_eda_versions(self):
        return {"success": True}

    def execute_shell(self, **kwargs):
        return {
            "request_id": kwargs["request_id"],
            "state": "completed",
            "result": {
                "success": True,
                "exit_code": 0,
                "stdout": kwargs["command"],
                "stderr": "",
            },
        }

    def execute_eda(self, **kwargs):
        return {"success": False, "state": "disabled"}

    def get_request_status(self, request_id):
        return {
            "request_id": request_id,
            "state": "completed",
            "result": {"success": True, "exit_code": 0, "stdout": "status"},
        }

create_server(FakeGateway()).run(transport="stdio")
"""


class McpStdioTransportClientTests(unittest.TestCase):
    def setUp(self):
        self.client = McpStdioClient(
            command=[sys.executable, "-u", "-c", FAKE_GATEWAY_SERVER],
            environ={"PATH": ""},
            timeout_seconds=5,
        )

    def tearDown(self):
        self.client.close()

    def test_initialize_tools_list_and_generic_shell_tool_call(self):
        self.assertIn("airbridge_shell_execute", self.client.tool_names)
        self.assertIn("airbridge_request_status", self.client.tool_names)

        request_id = str(uuid.uuid4())
        result = self.client.call_tool(
            "airbridge_shell_execute",
            {"request_id": request_id, "command": "Get-Location"},
        )
        self.assertEqual(result["request_id"], request_id)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["result"]["stdout"], "Get-Location")

    def test_request_status_tool_call_uses_original_request_id(self):
        request_id = str(uuid.uuid4())
        result = self.client.call_tool(
            "airbridge_request_status",
            {"request_id": request_id},
        )
        self.assertEqual(result["request_id"], request_id)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["result"]["stdout"], "status")


if __name__ == "__main__":
    unittest.main()
