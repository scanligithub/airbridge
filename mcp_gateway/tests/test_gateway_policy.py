from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from airbridge_mcp_gateway.config import GatewayConfig
from airbridge_mcp_gateway.gateway_policy import GatewayPolicyStore
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

    def test_policy_writer_is_atomic_and_does_not_store_credentials(self):
        self.policy.write(enabled=True, agents={"Antigravity": True, "OpenCode": False})

        payload = json.loads(self.policy_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["agents"], {
            "antigravity": {"enabled": True},
            "opencode": {"enabled": False},
        })
        self.assertNotIn("test-only-token", self.policy_path.read_text(encoding="utf-8"))
        self.assertEqual(list(self.policy_path.parent.glob(".gateway-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
