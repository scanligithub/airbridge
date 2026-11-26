from __future__ import annotations

import asyncio
import unittest

from mcp import Client

from airbridge_mcp_gateway.server import create_server
from airbridge_mcp_gateway.service import AirBridgeGateway


class _FakeGateway:
    config = type("Config", (), {"base_url": "http://127.0.0.1:8888"})()

    def __init__(self):
        self.execute_calls = 0

    def get_capabilities(self):
        return {"state": "negotiated"}

    def get_eda_versions(self):
        return {"success": True}

    def execute_eda(self, **_kwargs):
        self.execute_calls += 1
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

    def test_generated_input_schemas_include_gateway_constraints(self):
        server = create_server(_FakeGateway())
        tools = asyncio.run(server.list_tools())
        by_name = {tool.name: tool for tool in tools}

        self.assertEqual(
            set(by_name),
            {
                "airbridge_get_capabilities",
                "airbridge_get_eda_versions",
                "airbridge_eda_execute",
                "airbridge_request_status",
            },
        )

        for name in ("airbridge_get_capabilities", "airbridge_get_eda_versions"):
            schema = by_name[name].input_schema
            self.assertEqual(schema["type"], "object")
            self.assertEqual(schema["properties"], {})
            self.assertNotIn("required", schema)

        request_schema = by_name["airbridge_request_status"].input_schema
        self.assertEqual(request_schema["required"], ["request_id"])
        request_id = request_schema["properties"]["request_id"]
        self.assertEqual(request_id["type"], "string")
        self.assertEqual(request_id["format"], "uuid")

        execute_schema = by_name["airbridge_eda_execute"].input_schema
        self.assertEqual(execute_schema["required"], ["script"])
        properties = execute_schema["properties"]

        for name in ("script", "hint", "version"):
            self.assertEqual(properties[name]["type"], "string")
            self.assertEqual(properties[name]["minLength"], 1)
            self.assertEqual(properties[name]["pattern"], r"\S")

        self.assertEqual(properties["script"]["title"], "Script")
        self.assertEqual(properties["hint"]["default"], "agent_request.tcl")
        self.assertEqual(properties["version"]["default"], "auto")

        self.assertEqual(properties["target_file"]["type"], "string")
        self.assertEqual(properties["target_file"]["default"], "")
        self.assertEqual(properties["timeout"]["type"], "integer")
        self.assertEqual(properties["timeout"]["default"], 60)
        self.assertEqual(properties["timeout"]["minimum"], 1)
        self.assertEqual(properties["timeout"]["maximum"], 300)
        self.assertEqual(properties["keep_open"]["type"], "boolean")
        self.assertEqual(properties["keep_open"]["default"], True)
        self.assertEqual(properties["wait_timeout_ms"]["type"], "integer")
        self.assertEqual(properties["wait_timeout_ms"]["default"], 60_000)
        self.assertEqual(properties["wait_timeout_ms"]["minimum"], 0)
        self.assertEqual(properties["wait_timeout_ms"]["maximum"], 300_000)

    def test_execution_tool_is_annotated_as_destructive(self):
        server = create_server(_FakeGateway())
        tools = asyncio.run(server.list_tools())
        execute_tool = next(t for t in tools if t.name == "airbridge_eda_execute")
        self.assertFalse(execute_tool.annotations.read_only_hint)
        self.assertTrue(execute_tool.annotations.destructive_hint)
        self.assertFalse(execute_tool.annotations.idempotent_hint)

    def test_in_process_mcp_client_can_list_and_call_tools(self):
        gateway = _FakeGateway()
        server = create_server(gateway)

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
        self.assertEqual(gateway.execute_calls, 1)

    def test_mcp_rejects_boolean_and_non_integer_numeric_arguments(self):
        gateway = _FakeGateway()
        server = create_server(gateway)
        invalid_arguments = [
            {"script": "x", "timeout": True},
            {"script": "x", "timeout": False},
            {"script": "x", "timeout": 1.5},
            {"script": "x", "wait_timeout_ms": True},
            {"script": "x", "wait_timeout_ms": 1.5},
            {"script": "x", "keep_open": 1},
            {"script": "x", "keep_open": "true"},
        ]

        async def exercise_client():
            async with Client(server) as client:
                results = []
                for arguments in invalid_arguments:
                    results.append(
                        await client.call_tool("airbridge_eda_execute", arguments)
                    )
                return results

        results = asyncio.run(exercise_client())
        self.assertEqual(len(results), len(invalid_arguments))
        for arguments, result in zip(invalid_arguments, results):
            with self.subTest(arguments=arguments):
                self.assertTrue(result.is_error)
        self.assertEqual(gateway.execute_calls, 0)

    def test_gateway_validation_rejects_boolean_integer_confusion(self):
        valid = ("puts 1", "agent_request.tcl", "auto", "", 60, True, 60_000)
        self.assertIsNone(AirBridgeGateway._validate_eda_arguments(*valid))

        invalid_cases = [
            ("timeout boolean true", ("puts 1", "agent_request.tcl", "auto", "", True, True, 60_000)),
            ("timeout boolean false", ("puts 1", "agent_request.tcl", "auto", "", False, True, 60_000)),
            ("timeout float", ("puts 1", "agent_request.tcl", "auto", "", 1.5, True, 60_000)),
            ("timeout below minimum", ("puts 1", "agent_request.tcl", "auto", "", 0, True, 60_000)),
            ("timeout above maximum", ("puts 1", "agent_request.tcl", "auto", "", 301, True, 60_000)),
            ("wait timeout boolean", ("puts 1", "agent_request.tcl", "auto", "", 60, True, True)),
            ("wait timeout float", ("puts 1", "agent_request.tcl", "auto", "", 60, True, 1.5)),
            ("wait timeout below minimum", ("puts 1", "agent_request.tcl", "auto", "", 60, True, -1)),
            ("wait timeout above maximum", ("puts 1", "agent_request.tcl", "auto", "", 60, True, 300_001)),
            ("keep_open integer", ("puts 1", "agent_request.tcl", "auto", "", 60, 1, 60_000)),
            ("keep_open string", ("puts 1", "agent_request.tcl", "auto", "", 60, "true", 60_000)),
        ]
        for name, arguments in invalid_cases:
            with self.subTest(name=name):
                result = AirBridgeGateway._validate_eda_arguments(*arguments)
                self.assertIsNotNone(result)
                self.assertEqual(result["state"], "not_submitted")
                self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
