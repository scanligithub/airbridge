from __future__ import annotations

import asyncio
import unittest

from mcp import Client

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

    def test_in_process_mcp_client_can_list_and_call_tools(self):
        server = create_server(_FakeGateway())

        async def exercise_client():
            async with Client(server) as client:
                listed = await client.list_tools()
                capabilities = await client.call_tool(
                    "airbridge_get_capabilities", {}
                )
                execution = await client.call_tool(
                    "airbridge_eda_execute", {"script": "puts [pid]"}
                )
                return listed, capabilities, execution

        listed, capabilities, execution = asyncio.run(exercise_client())
        self.assertIn(
            "airbridge_get_capabilities",
            {tool.name for tool in listed.tools},
        )
        self.assertFalse(capabilities.is_error)
        self.assertEqual(capabilities.structured_content["state"], "negotiated")
        self.assertFalse(execution.is_error)
        self.assertEqual(execution.structured_content["state"], "disabled")


if __name__ == "__main__":
    unittest.main()
