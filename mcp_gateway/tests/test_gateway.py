from __future__ import annotations

import json
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from airbridge_mcp_gateway.config import GatewayConfig, GatewayConfigurationError
from airbridge_mcp_gateway.service import AirBridgeGateway


class _State:
    requests: list[tuple[str, str, dict | None]] = []
    authorization_headers: list[str | None] = []


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
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size).decode("utf-8"))
        _State.requests.append(("POST", path, payload))
        _State.authorization_headers.append(self.headers.get("Authorization"))
        if path == "/api/v1/eda/execute":
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

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        _State.requests.clear()
        _State.authorization_headers.clear()
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

    def test_env_flag_is_explicit(self):
        config = GatewayConfig.from_env({
            "AIRBRIDGE_ENABLE_EDA_EXECUTE": "1",
            "AIRBRIDGE_AGENT_TOKEN": "test-token",
        })
        self.assertTrue(config.enable_eda_execute)
        self.assertFalse(
            GatewayConfig.from_env({"AIRBRIDGE_AGENT_TOKEN": "test-token"}).enable_eda_execute
        )
        with self.assertRaises(GatewayConfigurationError):
            GatewayConfig.from_env({})


if __name__ == "__main__":
    unittest.main()
