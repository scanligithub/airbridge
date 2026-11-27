from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from airbridge_mcp_gateway.config import GatewayConfig
from airbridge_mcp_gateway.gateway_policy import GatewayPolicyStore
from airbridge_mcp_gateway.gateway_policy_audit import GatewayPolicyAuditStore
from airbridge_mcp_gateway.service import AirBridgeGateway


class _FakeClient:
    def __init__(self):
        self.calls: list[str] = []

    def get_capabilities(self):
        self.calls.append("get_capabilities")
        return {"state": "negotiated"}

    def get_eda_versions(self):
        self.calls.append("get_eda_versions")
        return {"success": True}

    def execute_shell(self, payload, *, http_timeout_seconds):
        self.calls.append(f"execute_shell:{payload['request_id']}")
        return {"request_id": payload["request_id"], "state": "completed"}

    def get_request_status(self, request_id: str):
        self.calls.append(f"get_request_status:{request_id}")
        return {"request_id": request_id, "state": "completed"}


class GatewayPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.policy_path = Path(self.temp_dir.name) / "AirBridge" / "gateway.json"
        self.policy = GatewayPolicyStore(self.policy_path)
        self.client = _FakeClient()
        self.config = GatewayConfig(
            base_url="http://127.0.0.1:8888",
            agent_id="antigravity",
            agent_token="test-only-token",
        )
        self.gateway = AirBridgeGateway(
            self.config,
            client=self.client,
            policy_store=self.policy,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_policy_defaults_to_gateway_off(self):
        result = self.gateway.get_capabilities()

        self.assertEqual(result["state"], "disabled")
        self.assertEqual(result["error"]["code"], "GATEWAY_DISABLED")
        self.assertEqual(self.client.calls, [])

    def test_global_gateway_off_denies_tool_call_before_http(self):
        self.policy.write(enabled=False, agents={"antigravity": True})

        result = self.gateway.get_eda_versions()

        self.assertEqual(result["state"], "disabled")
        self.assertEqual(result["error"]["code"], "GATEWAY_DISABLED")
        self.assertEqual(self.client.calls, [])

    def test_agent_off_denies_only_that_agent(self):
        self.policy.write(enabled=True, agents={"antigravity": False, "opencode": True})

        result = self.gateway.get_capabilities()

        self.assertEqual(result["state"], "disabled")
        self.assertEqual(result["error"]["code"], "AGENT_DISABLED")
        self.assertEqual(self.client.calls, [])

    def test_existing_stdio_process_reloads_policy_for_every_tool_call(self):
        # The same AirBridgeGateway object represents a long-lived STDIO child.
        self.policy.write(enabled=True, agents={"antigravity": True})
        allowed = self.gateway.get_capabilities()
        self.assertEqual(allowed["state"], "negotiated")
        self.assertEqual(self.client.calls, ["get_capabilities"])

        # Simulate GUI switching Gateway OFF while the MCP Host keeps this
        # process alive. The next request must be rejected without HTTP traffic.
        self.policy.write(enabled=False, agents={"antigravity": True})
        denied = self.gateway.get_capabilities()
        self.assertEqual(denied["error"]["code"], "GATEWAY_DISABLED")
        self.assertEqual(self.client.calls, ["get_capabilities"])

    def test_shell_execute_is_denied_when_gateway_is_off(self):
        result = self.gateway.execute_shell(
            request_id="f9d9d4f2-8b73-4b13-b3d6-9a09a10dcb3a",
            command="Get-Location",
        )
        self.assertEqual(result["state"], "disabled")
        self.assertEqual(result["error"]["code"], "GATEWAY_DISABLED")
        self.assertEqual(self.client.calls, [])

    def test_agent_can_be_disabled_without_revoking_credential(self):
        self.policy.write(enabled=True, agents={"antigravity": True})
        self.assertEqual(self.gateway.get_capabilities()["state"], "negotiated")

        self.policy.write(enabled=True, agents={"antigravity": False})
        denied = self.gateway.get_eda_versions()

        self.assertEqual(denied["error"]["code"], "AGENT_DISABLED")
        self.assertEqual(self.client.calls, ["get_capabilities"])

    def test_corrupt_policy_fails_closed(self):
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy_path.write_text("{not-json", encoding="utf-8")

        result = self.gateway.get_capabilities()

        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["error"]["code"], "GATEWAY_POLICY_INVALID")
        self.assertEqual(self.client.calls, [])

    def test_unsupported_policy_schema_fails_closed(self):
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy_path.write_text(
            json.dumps({
                "schema_version": 999,
                "enabled": True,
                "agents": {"antigravity": {"enabled": True}},
            }),
            encoding="utf-8",
        )

        result = self.gateway.get_capabilities()

        self.assertEqual(result["error"]["code"], "GATEWAY_POLICY_INVALID")
        self.assertEqual(self.client.calls, [])

    def test_boolean_schema_version_is_rejected_as_invalid(self):
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy_path.write_text(
            json.dumps({
                "schema_version": True,
                "enabled": True,
                "agents": {"antigravity": {"enabled": True}},
            }),
            encoding="utf-8",
        )

        result = self.gateway.get_capabilities()

        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["error"]["code"], "GATEWAY_POLICY_INVALID")
        self.assertEqual(self.client.calls, [])

    def test_missing_agent_entry_fails_closed(self):
        self.policy.write(enabled=True, agents={"opencode": True})

        result = self.gateway.get_capabilities()

        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["error"]["code"], "GATEWAY_POLICY_INVALID")
        self.assertEqual(self.client.calls, [])

    def test_policy_over_size_limit_fails_closed(self):
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy_path.write_bytes(b" " * (64 * 1024 + 1))

        result = self.gateway.get_capabilities()

        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["error"]["code"], "GATEWAY_POLICY_INVALID")
        self.assertEqual(self.client.calls, [])

    def test_read_snapshot_defaults_to_first_install_off(self):
        enabled, agents = self.policy.read_snapshot()
        self.assertFalse(enabled)
        self.assertEqual(agents, {})

    def test_read_snapshot_returns_global_and_per_agent_settings(self):
        self.policy.write(
            enabled=True,
            agents={"Antigravity": True, "OpenCode": False},
        )

        enabled, agents = self.policy.read_snapshot()

        self.assertTrue(enabled)
        self.assertEqual(agents, {"antigravity": True, "opencode": False})

    def test_read_snapshot_rejects_malformed_agent_entries(self):
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy_path.write_text(
            json.dumps({
                "schema_version": 1,
                "gateway": {"enabled": True},
                "agents": {"antigravity": {"enabled": "yes"}},
            }),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "enabled must be a Boolean"):
            self.policy.read_snapshot()

    def test_malformed_unrelated_agent_entry_fails_closed_for_current_agent(self):
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy_path.write_text(
            json.dumps({
                "schema_version": 1,
                "gateway": {"enabled": True},
                "agents": {
                    "antigravity": {"enabled": True},
                    "opencode": {"enabled": "yes"},
                },
            }),
            encoding="utf-8",
        )

        result = self.gateway.get_capabilities()

        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["error"]["code"], "GATEWAY_POLICY_INVALID")
        self.assertEqual(self.client.calls, [])

    def test_case_insensitive_duplicate_agent_ids_fail_closed(self):
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy_path.write_text(
            json.dumps({
                "schema_version": 1,
                "gateway": {"enabled": True},
                "agents": {
                    "antigravity": {"enabled": True},
                    "ANTIGRAVITY": {"enabled": False},
                },
            }),
            encoding="utf-8",
        )

        result = self.gateway.get_capabilities()

        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["error"]["code"], "GATEWAY_POLICY_INVALID")
        self.assertEqual(self.client.calls, [])

    def test_duplicate_json_object_keys_fail_closed(self):
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy_path.write_text(
            '{"schema_version":1,"gateway":{"enabled":true},'
            '"agents":{"antigravity":{"enabled":false},'
            '"antigravity":{"enabled":true}}}',
            encoding="utf-8",
        )

        result = self.gateway.get_capabilities()

        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["error"]["code"], "GATEWAY_POLICY_INVALID")
        self.assertEqual(self.client.calls, [])

    def test_policy_writer_is_atomic_and_does_not_store_credentials(self):
        self.policy.write(enabled=True, agents={"Antigravity": True, "OpenCode": False})

        payload = json.loads(self.policy_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["gateway"]["enabled"])
        self.assertEqual(payload["agents"], {
            "antigravity": {"enabled": True},
            "opencode": {"enabled": False},
        })
        self.assertNotIn("test-only-token", self.policy_path.read_text(encoding="utf-8"))
        self.assertEqual(list(self.policy_path.parent.glob(".gateway-*.tmp")), [])


    def test_local_policy_denial_is_persisted_without_tool_arguments(self):
        self.policy.write(enabled=False, agents={"antigravity": True})
        result = self.gateway.execute_shell(
            request_id="f9d9d4f2-8b73-4b13-b3d6-9a09a10dcb3a",
            command="SENSITIVE_COMMAND_MUST_NOT_BE_STORED",
        )

        self.assertEqual(result["error"]["code"], "GATEWAY_DISABLED")
        self.assertEqual(self.client.calls, [])
        audit_path = self.policy_path.parent / "gateway_audit.sqlite3"
        events = GatewayPolicyAuditStore(audit_path).list_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(
            {key: events[0][key] for key in ("agent_id", "tool_name", "decision", "reason_code")},
            {
                "agent_id": "antigravity",
                "tool_name": "airbridge_shell_execute",
                "decision": "DENY",
                "reason_code": "GATEWAY_DISABLED",
            },
        )
        self.assertGreater(events[0]["timestamp"], 0)
        raw = audit_path.read_bytes()
        self.assertNotIn(b"SENSITIVE_COMMAND_MUST_NOT_BE_STORED", raw)
        self.assertNotIn(b"test-only-token", raw)

    def test_allowed_calls_do_not_write_gateway_local_audit(self):
        self.policy.write(enabled=True, agents={"antigravity": True})

        result = self.gateway.get_capabilities()

        self.assertEqual(result["state"], "negotiated")
        self.assertEqual(self.client.calls, ["get_capabilities"])
        self.assertFalse((self.policy_path.parent / "gateway_audit.sqlite3").exists())

    def test_eda_execution_disabled_is_audited_as_local_denial(self):
        self.policy.write(enabled=True, agents={"antigravity": True})

        result = self.gateway.execute_eda(script="not executed")

        self.assertEqual(result["error"]["code"], "EDA_EXECUTION_DISABLED")
        events = GatewayPolicyAuditStore(
            self.policy_path.parent / "gateway_audit.sqlite3"
        ).list_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["tool_name"], "airbridge_eda_execute")
        self.assertEqual(events[0]["reason_code"], "EDA_EXECUTION_DISABLED")
        self.assertEqual(events[0]["decision"], "DENY")
        self.assertEqual(self.client.calls, [])

    def test_audit_write_failure_never_changes_denial_or_forwards_request(self):
        class _FailingAuditStore:
            def record_denial(self, **_kwargs):
                raise OSError("simulated audit storage failure")

        gateway = AirBridgeGateway(
            self.config,
            client=self.client,
            policy_store=self.policy,
            audit_store=_FailingAuditStore(),
        )
        self.policy.write(enabled=False, agents={"antigravity": True})

        result = gateway.get_capabilities()

        self.assertEqual(result["error"]["code"], "GATEWAY_DISABLED")
        self.assertEqual(result["state"], "disabled")
        self.assertEqual(self.client.calls, [])

    def test_sqlite_audit_store_handles_concurrent_writers(self):
        audit_path = self.policy_path.parent / "gateway_audit.sqlite3"
        stores = [GatewayPolicyAuditStore(audit_path) for _ in range(8)]

        def write_event(index):
            stores[index % len(stores)].record_denial(
                agent_id="antigravity",
                tool_name="airbridge_get_capabilities",
                reason_code="GATEWAY_DISABLED",
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write_event, range(40)))

        events = GatewayPolicyAuditStore(audit_path).list_events(limit=100)
        self.assertEqual(len(events), 40)
        self.assertTrue(all(event["decision"] == "DENY" for event in events))


if __name__ == "__main__":
    unittest.main()
