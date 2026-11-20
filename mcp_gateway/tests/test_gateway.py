from __future__ import annotations

import json
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from airbridge_mcp_gateway.config import GatewayConfig, GatewayConfigurationError
from airbridge_mcp_gateway.http_client import AirBridgeTransportError
from airbridge_mcp_gateway.service import AirBridgeGateway


class _State:
    requests: list[tuple[str, str, dict | None]] = []
    authorization_headers: list[str | None] = []
    redirect_target_authorization_headers: list[str | None] = []
    redirect_execute: bool = False


class _RedirectTargetHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):
        _State.redirect_target_authorization_headers.append(self.headers.get("Authorization"))
        raw = json.dumps({"unexpected": "redirect target reached"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _send(self, status: int, payload: dict):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlsplit(self.path).path
        _State.requests.append(("GET", path, None))
        _State.authorization_headers.append(self.headers.get("Authorization"))
        if path == "/api/v1/runtime/capabilities":
            self._send(200, {"state": "negotiated", "features": ["request.correlation"]})
        elif path == "/api/v1/eda/versions":
            self._send(200, {"success": True, "capture": ["17.2"]})
        elif path.startswith("/api/v1/requests/"):
            request_id = unquote(path.rsplit("/", 1)[-1])
            self._send(200, {"request_id": request_id, "state": "completed", "result": {"success": True}})
        elif path == "/api/v1/runtime/capabilities-redirect":
            self.send_response(302)
            self.send_header("Location", GatewayTests.redirect_target_url)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size).decode("utf-8"))
        _State.requests.append(("POST", path, payload))
        _State.authorization_headers.append(self.headers.get("Authorization"))
        if path == "/api/v1/eda/execute":
            if _State.redirect_execute:
                self.send_response(302)
                self.send_header("Location", GatewayTests.redirect_target_url)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send(200, {
                "request_id": payload["request_id"],
                "state": "completed",
                "result": {"success": True, "capture_pid": 3156},
            })
        else:
            self._send(404, {"error": "not found"})


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

        cls.redirect_target = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectTargetHandler)
        cls.redirect_target_thread = threading.Thread(
            target=cls.redirect_target.serve_forever, daemon=True
        )
        cls.redirect_target_thread.start()
        cls.redirect_target_url = f"http://127.0.0.1:{cls.redirect_target.server_address[1]}/target"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        cls.redirect_target.shutdown()
        cls.redirect_target.server_close()
        cls.redirect_target_thread.join(timeout=2)

    def setUp(self):
        _State.requests.clear()
        _State.authorization_headers.clear()
        _State.redirect_target_authorization_headers.clear()
        _State.redirect_execute = False
        self.config = GatewayConfig(
            base_url=self.base_url,
            enable_eda_execute=True,
            agent_token="test-agent-token",
        )
        self.gateway = AirBridgeGateway(self.config)

    def test_read_capabilities_and_versions_use_existing_endpoints(self):
        capabilities = self.gateway.get_capabilities()
        versions = self.gateway.get_eda_versions()
        self.assertEqual(capabilities["state"], "negotiated")
        self.assertEqual(versions["capture"], ["17.2"])
        self.assertEqual(
            _State.authorization_headers,
            ["Bearer test-agent-token", "Bearer test-agent-token"],
        )
        self.assertEqual(
            [item[1] for item in _State.requests],
            ["/api/v1/runtime/capabilities", "/api/v1/eda/versions"],
        )

    def test_http_client_rejects_redirect_without_forwarding_bearer_token(self):
        with self.assertRaisesRegex(AirBridgeTransportError, "redirects are disabled"):
            self.gateway.client.request_json(
                "GET", "/api/v1/runtime/capabilities-redirect"
            )

        # The configured local endpoint received the request, but the redirect target did not.
        self.assertEqual(_State.authorization_headers, ["Bearer test-agent-token"])
        self.assertEqual(_State.redirect_target_authorization_headers, [])
        self.assertEqual(len(_State.requests), 1)

    def test_execute_redirect_is_unconfirmed_and_does_not_forward_bearer_token(self):
        _State.redirect_execute = True
        result = self.gateway.execute_eda(script="puts redirect probe")

        self.assertEqual(result["state"], "unconfirmed")
        self.assertEqual(str(uuid.UUID(result["request_id"])), result["request_id"])
        self.assertEqual(_State.authorization_headers, ["Bearer test-agent-token"])
        self.assertEqual(_State.redirect_target_authorization_headers, [])
        self.assertEqual(len(_State.requests), 1)

    def test_execute_generates_uuid_and_preserves_opaque_script(self):
        script = 'puts "gateway probe"\nputs [pid]'
        result = self.gateway.execute_eda(script=script)
        self.assertEqual(result["state"], "completed")
        submitted = _State.requests[-1][2]
        self.assertEqual(submitted["script"], script)
        self.assertEqual(submitted["version"], "auto")
        self.assertTrue(submitted["keep_open"])
        self.assertEqual(result["request_id"], submitted["request_id"])
        self.assertEqual(str(uuid.UUID(result["request_id"])), result["request_id"])

    def test_execute_disabled_by_default_and_does_not_call_http_api(self):
        disabled = AirBridgeGateway(
            GatewayConfig(base_url=self.base_url, agent_token="test-agent-token")
        )
        result = disabled.execute_eda(script="puts [pid]")
        self.assertEqual(result["state"], "disabled")
        self.assertEqual(_State.requests, [])

    def test_execute_rejects_invalid_arguments_before_submission(self):
        result = self.gateway.execute_eda(script=" ", timeout=0)
        self.assertEqual(result["state"], "not_submitted")
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(_State.requests, [])

    def test_execute_rejects_payload_that_exceeds_frame_safe_limit(self):
        result = self.gateway.execute_eda(script="x" * 61_000)
        self.assertEqual(result["state"], "not_submitted")
        self.assertEqual(result["error"]["code"], "REQUEST_TOO_LARGE")
        self.assertNotIn("request_id", result)
        self.assertEqual(_State.requests, [])

    def test_request_status_requires_uuid_and_uses_existing_endpoint(self):
        bad = self.gateway.get_request_status("not-a-uuid")
        self.assertEqual(bad["error"]["code"], "INVALID_REQUEST_ID")
        request_id = str(uuid.uuid4())
        result = self.gateway.get_request_status(request_id)
        self.assertEqual(result["request_id"], request_id)
        self.assertEqual(_State.requests[-1][1], f"/api/v1/requests/{request_id}")

    def test_config_rejects_non_loopback_hosts(self):
        with self.assertRaises(GatewayConfigurationError):
            GatewayConfig(base_url="http://192.0.2.10:8888")
        with self.assertRaises(GatewayConfigurationError):
            GatewayConfig(base_url="https://127.0.0.1:8888")

    def test_env_flag_is_explicit_and_legacy_token_remains_compatible(self):
        config = GatewayConfig.from_env({
            "AIRBRIDGE_ENABLE_EDA_EXECUTE": "1",
            "AIRBRIDGE_AGENT_TOKEN": "test-token",
        })
        self.assertTrue(config.enable_eda_execute)
        self.assertEqual(config.agent_token, "test-token")
        self.assertFalse(
            GatewayConfig.from_env({"AIRBRIDGE_AGENT_TOKEN": "test-token"}).enable_eda_execute
        )
        with self.assertRaises(GatewayConfigurationError):
            GatewayConfig.from_env({})

    def test_m25_stdio_rejects_legacy_agent_token_environment(self):
        with self.assertRaisesRegex(
            GatewayConfigurationError,
            "AIRBRIDGE_AGENT_TOKEN is forbidden in M2.5 STDIO",
        ):
            GatewayConfig.from_env({
                "AIRBRIDGE_M2_5_GATEWAY_POLICY": "1",
                "AIRBRIDGE_AGENT_ID": "opencode",
                "AIRBRIDGE_AGENT_TOKEN": "must-not-be-accepted",
            })

    def test_m25_stdio_requires_agent_id(self):
        with self.assertRaisesRegex(
            GatewayConfigurationError,
            "AIRBRIDGE_AGENT_ID is required in M2.5 STDIO",
        ):
            GatewayConfig.from_env({
                "AIRBRIDGE_M2_5_GATEWAY_POLICY": "1",
                "AIRBRIDGE_AGENT_TOKEN": "",
            })

    def test_m25_stdio_uses_agent_id_credential_manager_path(self):
        class FakeCredentialStore:
            def __init__(self):
                self.requested_agent_ids = []

            def load(self, agent_id):
                self.requested_agent_ids.append(agent_id)
                return "vault-only-m25-secret"

            def store(self, _agent_id, _token):
                raise AssertionError("not used by gateway configuration")

            def delete(self, _agent_id):
                raise AssertionError("not used by gateway configuration")

        store = FakeCredentialStore()
        config = GatewayConfig.from_env(
            {
                "AIRBRIDGE_M2_5_GATEWAY_POLICY": "1",
                "AIRBRIDGE_AGENT_ID": "OpenCode",
            },
            credential_store=store,
        )

        self.assertTrue(config.enforce_gateway_policy)
        self.assertEqual(config.agent_id, "opencode")
        self.assertEqual(config.agent_token, "vault-only-m25-secret")
        self.assertEqual(store.requested_agent_ids, ["opencode"])
        self.assertNotIn("vault-only-m25-secret", repr(config))

    def test_agent_id_loads_token_from_injected_credential_store(self):
        class FakeCredentialStore:
            def __init__(self):
                self.requested_agent_ids = []

            def load(self, agent_id):
                self.requested_agent_ids.append(agent_id)
                return "vault-only-secret"

            def store(self, _agent_id, _token):
                raise AssertionError("not used by gateway configuration")

            def delete(self, _agent_id):
                raise AssertionError("not used by gateway configuration")

        store = FakeCredentialStore()
        config = GatewayConfig.from_env(
            {"AIRBRIDGE_AGENT_ID": "OpenCode"},
            credential_store=store,
        )
        self.assertEqual(config.agent_id, "opencode")
        self.assertEqual(config.agent_token, "vault-only-secret")
        self.assertEqual(store.requested_agent_ids, ["opencode"])
        self.assertNotIn("vault-only-secret", repr(config))

    def test_agent_id_and_legacy_token_cannot_be_configured_together(self):
        with self.assertRaisesRegex(GatewayConfigurationError, "Do not configure both"):
            GatewayConfig.from_env({
                "AIRBRIDGE_AGENT_ID": "opencode",
                "AIRBRIDGE_AGENT_TOKEN": "legacy-secret",
            }, credential_store=object())

    def test_agent_credential_store_failure_is_fail_closed(self):
        class MissingCredentialStore:
            def load(self, _agent_id):
                from airbridge_mcp_gateway.credential_store import CredentialStoreError
                raise CredentialStoreError("missing")

        with self.assertRaisesRegex(
            GatewayConfigurationError, "could not be loaded"
        ):
            GatewayConfig.from_env(
                {"AIRBRIDGE_AGENT_ID": "opencode"},
                credential_store=MissingCredentialStore(),
            )


    def test_server_rejection_does_not_fabricate_a_pollable_request_id(self):
        class RejectingClient:
            def execute_eda(self, *_args, **_kwargs):
                return {
                    "success": False,
                    "state": "rate_limited",
                    "error": {"code": "AGENT_RATE_LIMITED"},
                }

        gateway = AirBridgeGateway(self.config, client=RejectingClient())
        result = gateway.execute_eda(script="puts rejected")
        self.assertEqual(result["state"], "rate_limited")
        self.assertNotIn("request_id", result)

if __name__ == "__main__":
    unittest.main()
