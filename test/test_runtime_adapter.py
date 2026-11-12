# test_runtime_adapter.py
# -*- coding: utf-8 -*-
"""Unit tests for generic Runtime Adapter / Profile contracts."""
import unittest
import uuid

from runtime_adapter import (
    RuntimeAdapter,
    RuntimeAdapterError,
    RuntimeAdapterRegistry,
    RuntimeExecutionContext,
    RuntimeExecutionRequest,
    RuntimeProfile,
    create_legacy_runtime_registry,
)


class FakeEDAEngine:
    def __init__(self, returncode=0, output="eda output"):
        self.returncode = returncode
        self.output = output
        self.calls = []

    def execute_script(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.returncode, self.output


class FakeShellEngine:
    def __init__(self, result=None):
        self.result = result or {
            "success": True,
            "task_id": "shell-unit",
            "exit_code": 0,
            "stdout": "shell output",
            "stderr": "",
        }
        self.calls = []

    def exec(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.result)


class RuntimeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.eda_engine = FakeEDAEngine()
        self.shell_engine = FakeShellEngine()
        self.registry = create_legacy_runtime_registry(
            lambda: self.eda_engine,
            lambda: self.shell_engine,
        )

    def test_factory_registers_distinct_profiles_and_adapters(self):
        profiles = self.registry.describe_profiles()
        self.assertEqual(
            [item["profile_id"] for item in profiles],
            ["legacy-cadence-oneshot", "legacy-shell-process"],
        )
        self.assertEqual(
            profiles[0]["operations"],
            ["execute"],
        )
        self.assertEqual(profiles[0]["execution_mode"], "one-shot-process")
        self.assertEqual(profiles[1]["execution_mode"], "process-per-request")

    def test_legacy_cadence_adapter_delegates_without_exposing_worker(self):
        messages = []
        request = RuntimeExecutionRequest(
            operation="execute",
            request_id=str(uuid.uuid4()),
            payload={
                "script": "puts hello",
                "hint": "probe.tcl",
                "timeout": 17,
                "version": "17.2",
                "target_file": r"D:\design\demo.dsn",
                "keep_open": True,
            },
        )
        result = self.registry.execute(
            "legacy-cadence-oneshot",
            request,
            RuntimeExecutionContext(log_callback=messages.append),
        )

        self.assertEqual(result, {
            "success": True,
            "returncode": 0,
            "output": "eda output",
        })
        args, kwargs = self.eda_engine.calls[0]
        self.assertEqual(args[0], "puts hello")
        self.assertEqual(args[1], "probe.tcl")
        self.assertEqual(kwargs["timeout"], 17)
        self.assertEqual(kwargs["version"], "17.2")
        self.assertEqual(kwargs["target_file"], r"D:\design\demo.dsn")
        self.assertTrue(kwargs["keep_open"])
        kwargs["log_callback"]("adapter log")
        self.assertEqual(messages, ["adapter log"])

    def test_legacy_cadence_nonzero_exit_is_structured_as_failure(self):
        self.eda_engine = FakeEDAEngine(returncode=3, output="script error")
        result = self.registry.execute(
            "legacy-cadence-oneshot",
            RuntimeExecutionRequest(
                operation="execute",
                payload={"script": "error expected"},
            ),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["returncode"], 3)

    def test_shell_adapter_delegates_and_preserves_structured_result(self):
        request = RuntimeExecutionRequest(
            operation="execute",
            request_id=str(uuid.uuid4()),
            payload={
                "command": "Write-Output hello",
                "shell": "powershell",
                "cwd": r"E:\_airbridge_temp",
                "timeout": 12,
                "async": True,
                "env": {"AB_TEST": "1"},
            },
        )
        result = self.registry.execute("legacy-shell-process", request)
        self.assertEqual(result["task_id"], "shell-unit")
        self.assertEqual(self.shell_engine.calls, [{
            "command": "Write-Output hello",
            "shell": "powershell",
            "cwd": r"E:\_airbridge_temp",
            "timeout": 12,
            "async_mode": True,
            "env": {"AB_TEST": "1"},
        }])

    def test_engine_provider_is_resolved_at_execution_time(self):
        replacement = FakeEDAEngine(output="replacement engine")
        self.eda_engine = replacement
        result = self.registry.execute(
            "legacy-cadence-oneshot",
            RuntimeExecutionRequest(operation="execute", payload={"script": "puts 1"}),
        )
        self.assertEqual(result["output"], "replacement engine")
        self.assertEqual(len(replacement.calls), 1)

    def test_unknown_profile_and_unsupported_operation_are_rejected(self):
        request = RuntimeExecutionRequest(operation="execute", payload={})
        with self.assertRaises(RuntimeAdapterError):
            self.registry.execute("missing-profile", request)
        with self.assertRaises(RuntimeAdapterError):
            self.registry.execute(
                "legacy-cadence-oneshot",
                RuntimeExecutionRequest(operation="shutdown", payload={}),
            )

    def test_disabled_profile_is_not_executable(self):
        adapter = _TestAdapter()
        registry = RuntimeAdapterRegistry()
        registry.register_adapter(adapter)
        registry.register_profile(
            RuntimeProfile(
                profile_id="disabled",
                adapter_id=adapter.adapter_id,
                display_name="Disabled test profile",
                operations=("execute",),
                capabilities=("test.execute",),
                enabled=False,
            )
        )
        with self.assertRaises(RuntimeAdapterError):
            registry.execute(
                "disabled",
                RuntimeExecutionRequest(operation="execute", payload={}),
            )
        self.assertEqual(adapter.calls, 0)

    def test_registry_rejects_duplicate_and_inconsistent_registrations(self):
        adapter = _TestAdapter()
        registry = RuntimeAdapterRegistry()
        registry.register_adapter(adapter)
        with self.assertRaises(RuntimeAdapterError):
            registry.register_adapter(adapter)
        with self.assertRaises(RuntimeAdapterError):
            registry.register_profile(
                RuntimeProfile(
                    profile_id="bad-operation",
                    adapter_id=adapter.adapter_id,
                    display_name="Bad operation",
                    operations=("close",),
                    capabilities=("test.execute",),
                )
            )
        with self.assertRaises(RuntimeAdapterError):
            registry.register_profile(
                RuntimeProfile(
                    profile_id="bad-capability",
                    adapter_id=adapter.adapter_id,
                    display_name="Bad capability",
                    operations=("execute",),
                    capabilities=("process.kill",),
                )
            )


    def test_request_payload_is_a_read_only_snapshot(self):
        source = {"value": 1}
        request = RuntimeExecutionRequest(
            operation="execute",
            payload=source,
        )
        source["value"] = 2
        self.assertEqual(request.payload["value"], 1)
        with self.assertRaises(TypeError):
            request.payload["value"] = 3

    def test_registry_rejects_duplicate_adapter_capabilities(self):
        adapter = _DuplicateCapabilityAdapter()
        registry = RuntimeAdapterRegistry()
        with self.assertRaises(RuntimeAdapterError):
            registry.register_adapter(adapter)

    def test_request_requires_json_object_and_valid_uuid_when_present(self):
        with self.assertRaises(RuntimeAdapterError):
            RuntimeExecutionRequest(operation="execute", payload=[])
        with self.assertRaises(RuntimeAdapterError):
            RuntimeExecutionRequest(
                operation="execute",
                payload={},
                request_id="not-a-uuid",
            )


class _DuplicateCapabilityAdapter(RuntimeAdapter):
    adapter_id = "duplicate-capability-adapter"
    operations = ("execute",)
    capabilities = ("test.execute", "test.execute")

    def execute(self, request, context):
        return {"success": True}


class _TestAdapter(RuntimeAdapter):
    adapter_id = "test-adapter"
    operations = ("execute",)
    capabilities = ("test.execute",)

    def __init__(self):
        self.calls = 0

    def execute(self, request, context):
        self.calls += 1
        return {"success": True}


if __name__ == "__main__":
    unittest.main()
