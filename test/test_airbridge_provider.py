# -*- coding: utf-8 -*-
"""Contract tests for the AirBridge Transport Provider using a local HTTP stub."""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill"))

from transport import (  # noqa: E402
    AirBridgeProvider,
    AirBridgeProviderError,
    OperationStatus,
)


class AirBridgeProviderTests(TestCase):
    def setUp(self):
        self.request_id = str(uuid.uuid4())
        self.requests = []
        self.post_status = 200
        self.post_payload = {
            "request_id": self.request_id,
            "kind": "shell.exec",
            "state": "completed",
            "result": {
                "success": True,
                "task_id": "shell-test",
                "exit_code": 0,
                "stdout": "hello from Host B",
                "stderr": "",
            },
        }
        self.get_status = 200
        self.get_payload = dict(self.post_payload)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.requests.append({
                    "method": "POST",
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(raw.decode("utf-8")),
                })
                self._respond(owner.post_status, owner.post_payload)

            def do_GET(self):  # noqa: N802
                owner.requests.append({
                    "method": "GET",
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                })
                self._respond(owner.get_status, owner.get_payload)

            def _respond(self, status, payload):
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:{}".format(self.server.server_port)
        self.provider = AirBridgeProvider(
            base_url=self.base_url,
            token="unit-test-token",
            timeout_seconds=2,
        )

    def tearDown(self):
        self.provider.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_provider_info_and_capabilities(self):
        info = self.provider.get_info()
        self.assertEqual(info.provider_name, "airbridge")
        self.assertEqual(info.contract_version, "1.0")
        capabilities = {item.name: item.supported for item in self.provider.get_capabilities()}
        self.assertTrue(capabilities["shell.execute"])
        self.assertTrue(capabilities["request.read"])

    def test_shell_execute_posts_to_existing_generic_api_and_preserves_id(self):
        response = self.provider.shell_execute(
            "Write-Output hello",
            {"shell": "powershell", "cwd": r"E:\_airbridge_temp", "timeout": 20},
            self.request_id,
        )
        self.assertEqual(response.status, OperationStatus.COMPLETED)
        self.assertEqual(response.request_id, self.request_id)
        self.assertTrue(response.success)
        self.assertEqual(response.result["stdout"], "hello from Host B")
        sent = self.requests[0]
        self.assertEqual(sent["path"], "/api/v1/shell/exec")
        self.assertEqual(sent["authorization"], "Bearer unit-test-token")
        self.assertEqual(sent["body"]["request_id"], self.request_id)
        self.assertFalse(sent["body"]["async"])
        self.assertEqual(sent["body"]["command"], "Write-Output hello")

    def test_nonzero_exit_code_is_completed_command_failure(self):
        self.post_payload["result"]["exit_code"] = 17
        self.post_payload["result"]["stderr"] = "command failed"
        response = self.provider.shell_execute("exit 17", {}, self.request_id)
        self.assertEqual(response.status, OperationStatus.COMPLETED)
        self.assertFalse(response.success)
        self.assertEqual(response.error, "command failed")

    def test_http_202_pending_maps_to_accepted(self):
        self.post_status = 202
        self.post_payload = {
            "request_id": self.request_id,
            "kind": "shell.exec",
            "state": "pending",
        }
        response = self.provider.shell_execute("long command", {}, self.request_id)
        self.assertEqual(response.status, OperationStatus.ACCEPTED)
        self.assertEqual(response.request_id, self.request_id)

    def test_get_operation_queries_original_request_without_resubmission(self):
        self.get_payload = {
            "request_id": self.request_id,
            "kind": "shell.exec",
            "state": "completed",
            "result": {"success": True, "exit_code": 0, "stdout": "late result"},
        }
        response = self.provider.get_operation(self.request_id)
        self.assertEqual(response.status, OperationStatus.COMPLETED)
        self.assertEqual(response.result["stdout"], "late result")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["method"], "GET")
        self.assertEqual(
            self.requests[0]["path"],
            "/api/v1/requests/" + self.request_id,
        )

    def test_http_503_unknown_maps_to_unknown(self):
        self.post_status = 503
        self.post_payload = {
            "request_id": self.request_id,
            "state": "unknown",
            "error": {"code": "SHELL_FRAME_SUBMISSION_UNCONFIRMED"},
        }
        response = self.provider.shell_execute("Write-Output uncertain", {}, self.request_id)
        self.assertEqual(response.status, OperationStatus.UNKNOWN)
        self.assertEqual(response.request_id, self.request_id)

    def test_http_502_failed_state_maps_to_completed_failure(self):
        self.post_status = 502
        self.post_payload = {
            "request_id": self.request_id,
            "kind": "shell.exec",
            "state": "failed",
            "error": {
                "code": "SHELL_LAUNCH_FAILED",
                "message": "PowerShell could not be started",
            },
        }
        response = self.provider.shell_execute("Get-Location", {}, self.request_id)
        self.assertEqual(response.status, OperationStatus.COMPLETED)
        self.assertFalse(response.success)
        self.assertEqual(
            response.error,
            "SHELL_LAUNCH_FAILED: PowerShell could not be started",
        )

    def test_http_404_not_found_maps_to_unknown(self):
        self.get_status = 404
        self.get_payload = {
            "request_id": self.request_id,
            "state": "not_found",
            "error": "request_id is unknown or expired",
        }
        response = self.provider.get_operation(self.request_id)
        self.assertEqual(response.status, OperationStatus.UNKNOWN)
        self.assertEqual(response.request_id, self.request_id)
        self.assertIn("unknown or expired", response.reason)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["method"], "GET")

    def test_http_transport_failure_maps_to_unknown_without_retry(self):
        calls = []

        def failed_opener(_request, timeout):
            calls.append(timeout)
            raise urllib.error.URLError("simulated connection failure")

        provider = AirBridgeProvider(
            base_url=self.base_url,
            token="unit-test-token",
            opener=failed_opener,
        )
        response = provider.shell_execute("Write-Output uncertain", {}, self.request_id)
        self.assertEqual(response.status, OperationStatus.UNKNOWN)
        self.assertEqual(response.request_id, self.request_id)
        self.assertEqual(len(calls), 1)

    def test_non_loopback_base_url_is_rejected(self):
        with self.assertRaises(AirBridgeProviderError):
            AirBridgeProvider(
                base_url="https://example.com",
                token="unit-test-token",
            )

    def test_missing_token_is_rejected_without_token_probing(self):
        with self.assertRaises(AirBridgeProviderError):
            AirBridgeProvider(
                base_url=self.base_url,
                environ={},
            )

    def test_request_id_mismatch_is_rejected(self):
        self.post_payload["request_id"] = str(uuid.uuid4())
        with self.assertRaises(AirBridgeProviderError):
            self.provider.shell_execute("Write-Output hello", {}, self.request_id)

    def test_close_prevents_calls(self):
        self.provider.close()
        with self.assertRaises(RuntimeError):
            self.provider.get_info()


if __name__ == "__main__":
    main()
