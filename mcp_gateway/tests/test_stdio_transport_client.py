from __future__ import annotations

import os
import sys
import threading
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
        if kwargs["command"].startswith("slow:"):
            import time
            time.sleep(0.15)
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
            # Keep the Windows runtime environment (notably SystemRoot and
            # DLL search paths) while ensuring no Agent credential is inherited.
            environ={
                key: value
                for key, value in os.environ.items()
                if key.upper() not in {"AIRBRIDGE_AGENT_TOKEN"}
            },
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

    def test_concurrent_calls_do_not_steal_each_others_responses(self):
        calls = [
            ("slow:first", str(uuid.uuid4())),
            ("slow:second", str(uuid.uuid4())),
        ]
        results = {}
        errors = []

        def invoke(command, request_id):
            try:
                results[command] = self.client.call_tool(
                    "airbridge_shell_execute",
                    {"request_id": request_id, "command": command},
                )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=invoke, args=item)
            for item in calls
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        for command, request_id in calls:
            self.assertEqual(results[command]["request_id"], request_id)
            self.assertEqual(results[command]["result"]["stdout"], command)


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
