from __future__ import annotations

import asyncio
import unittest

from airbridge_mcp_gateway.server import create_server


class _FakeGateway:
    config = type("Config", (), {"base_url": "http://127.0.0.1:8888"})()

    def get_capabilities(self):
        return {"state": "negotiated"}

    def get_eda_versions(self):
        return {"success": True}

    def execute_eda(self, **_kwargs):
        return {"state": "disabled"}

    def get_request_status(self, request_id):
        return {"request_id": request_id}


class McpServerRegistrationTests(unittest.TestCase):
    def test_server_registers_the_four_expected_tools(self):
        server = create_server(_FakeGateway())
        tools = asyncio.run(server.list_tools())
        names = {tool.name for tool in tools}
        self.assertEqual(
            names,
            {
                "airbridge_get_capabilities",
                "airbridge_get_eda_versions",
                "airbridge_eda_execute",
                "airbridge_request_status",
            },
        )

    def test_execution_tool_is_annotated_as_destructive(self):
        server = create_server(_FakeGateway())
        tools = asyncio.run(server.list_tools())
        execute_tool = next(t for t in tools if t.name == "airbridge_eda_execute")
        self.assertFalse(execute_tool.annotations.read_only_hint)
        self.assertTrue(execute_tool.annotations.destructive_hint)
        self.assertFalse(execute_tool.annotations.idempotent_hint)


if __name__ == "__main__":
    unittest.main()
