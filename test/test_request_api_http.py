# test_request_api_http.py
# -*- coding: utf-8 -*-
"""HTTP contract tests for the A-side correlated EDA and Shell endpoints."""
import json
import tempfile
import urllib.error
import urllib.request
import unittest
import uuid
from pathlib import Path

from agent_security import AgentSecurityStore
from airbridge_app import AntigravityIPCServer
from protocol import FrameType
from request_tracker import RequestTracker


class FakeWorker:
    def __init__(self):
        self.request_tracker = RequestTracker()
        self.connected = True
        self.auto_complete = True
        self.raise_on_send = False
        self.frames = []

    def is_serial_connected(self):
        return self.connected

    def get_session_peer_status(self):
        return {
            "state": "negotiated",
            "features": ["handshake", "request.correlation", "request.deduplication"],
        }

    def send_frame(self, frame_type, payload=b""):
        self.frames.append((frame_type, payload))
        if self.raise_on_send:
            raise OSError("serial write outcome is uncertain")
        if not self.auto_complete:
            return

        data = json.loads(payload.decode("utf-8"))
        request_id = data["request_id"]
        if frame_type == FrameType.EDA_TASK_SUBMIT:
            self.request_tracker.complete(
                request_id,
                {
                    "request_id": request_id,
                    "returncode": 0,
                    "output": "fake HTTP EDA result",
                },
            )
        elif frame_type == FrameType.SHELL_EXEC:
            result = {
                "request_id": request_id,
                "success": True,
                "task_id": "shell-http-test",
                "exit_code": 0,
                "stdout": "fake HTTP shell result",
                "stderr": "",
            }
            response_state = "completed"
            if data.get("async"):
                result.update({"status": "running", "async": True})
                response_state = "accepted"
            self.request_tracker.complete(
                request_id, result, state=response_state
            )


class CorrelatedRequestHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.security = AgentSecurityStore(
            str(Path(self.temp.name) / "agent_security.sqlite3")
        )
        issued = self.security.issue_token(
            "test-agent",
            [
                "runtime.read",
                "runtime.manage",
                "runtime.message",
                "eda.versions.read",
                "eda.execute",
                "request.read",
                "shell.read",
                "shell.execute",
                "shell.manage",
                "filesystem.read",
                "filesystem.write",
            ],
        )
        self.token = issued["token"]
        self.worker = FakeWorker()
        self.server = AntigravityIPCServer(
            self.worker,
            port=0,
            security_store=self.security,
            allow_eda_execute=True,
        )
        self.server.start()
        self.base_url = f"http://127.0.0.1:{self.server.port}"

    def tearDown(self):
        self.server.stop()
        self.security.close()
        self.temp.cleanup()

    def _request_json(self, method, path, payload=None, *, token="__default__", base_url=None):
        self.last_headers = {}
        body = (
            None
            if payload is None
            else json.dumps(payload).encode("utf-8")
        )
        headers = {"Content-Type": "application/json"}
        selected_token = self.token if token == "__default__" else token
        if selected_token:
            headers["Authorization"] = "Bearer " + selected_token
        request = urllib.request.Request(
            (base_url or self.base_url) + path,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=3.0) as response:
                self.last_headers = dict(response.headers.items())
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self.last_headers = dict(exc.headers.items())
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_eda_post_returns_correlated_structured_result(self):
        status, body = self._request_json(
            "POST",
            "/api/v1/eda/execute",
            {"script": "puts hello", "hint": "ci.tcl", "timeout": 5},
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(body["state"], "completed")
        self.assertEqual(body["kind"], "eda.execute")
        self.assertEqual(body["result"]["returncode"], 0)
        self.assertEqual(body["result"]["output"], "fake HTTP EDA result")
        sent_frame = self.worker.frames[-1]
        self.assertEqual(sent_frame[0], FrameType.EDA_TASK_SUBMIT)
        submitted = json.loads(sent_frame[1].decode("utf-8"))
        self.assertEqual(submitted["request_id"], body["request_id"])

    def test_http_wait_timeout_returns_202_and_result_can_be_polled(self):
        self.worker.auto_complete = False
        status, pending = self._request_json(
            "POST",
            "/api/v1/eda/execute",
            {
                "script": "puts hello",
                "timeout": 30,
                "wait_timeout_ms": 0,
            },
        )

        self.assertEqual(status, 202, pending)
        self.assertEqual(pending["state"], "pending")
        request_id = pending["request_id"]

        self.assertTrue(
            self.worker.request_tracker.complete(
                request_id,
                {"request_id": request_id, "returncode": 0, "output": "late result"},
            )
        )
        poll_status, result = self._request_json(
            "GET", f"/api/v1/requests/{request_id}"
        )
        self.assertEqual(poll_status, 200, result)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["result"]["output"], "late result")

    def test_shell_post_returns_correlated_structured_result(self):
        status, body = self._request_json(
            "POST",
            "/api/v1/shell/exec",
            {
                "command": "Write-Output hello",
                "shell": "powershell",
                "timeout": 5,
                "async": False,
            },
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(body["state"], "completed")
        self.assertEqual(body["kind"], "shell.exec")
        self.assertEqual(body["result"]["task_id"], "shell-http-test")
        self.assertEqual(body["result"]["stdout"], "fake HTTP shell result")
        sent_frame = self.worker.frames[-1]
        self.assertEqual(sent_frame[0], FrameType.SHELL_EXEC)
        submitted = json.loads(sent_frame[1].decode("utf-8"))
        self.assertEqual(submitted["request_id"], body["request_id"])

    def test_async_shell_returns_acceptance_not_false_completion(self):
        status, body = self._request_json(
            "POST",
            "/api/v1/shell/exec",
            {
                "command": "Write-Output hello",
                "timeout": 5,
                "async": True,
            },
        )

        self.assertEqual(status, 202, body)
        self.assertEqual(body["state"], "accepted")
        self.assertEqual(body["result"]["status"], "running")

        poll_status, polled = self._request_json(
            "GET", f"/api/v1/requests/{body['request_id']}"
        )
        self.assertEqual(poll_status, 202, polled)
        self.assertEqual(polled["state"], "accepted")
        self.assertEqual(polled["result"]["status"], "running")

    def test_caller_supplied_request_id_is_honored_and_not_submitted_twice(self):
        request_id = str(uuid.uuid4())
        payload = {
            "request_id": request_id,
            "script": "puts hello",
            "hint": "retry-safe.tcl",
            "timeout": 5,
        }

        first_status, first = self._request_json(
            "POST", "/api/v1/eda/execute", payload
        )
        self.assertEqual(first_status, 200, first)
        self.assertEqual(first["request_id"], request_id)
        self.assertEqual(len(self.worker.frames), 1)

        retry_status, retry = self._request_json(
            "POST", "/api/v1/eda/execute", payload
        )
        self.assertEqual(retry_status, 409, retry)
        self.assertEqual(retry["state"], "already_registered")
        self.assertEqual(retry["request_id"], request_id)
        self.assertEqual(len(self.worker.frames), 1)

        poll_status, polled = self._request_json(
            "GET", f"/api/v1/requests/{request_id}"
        )
        self.assertEqual(poll_status, 200, polled)
        self.assertEqual(polled["state"], "completed")

    def test_peer_with_correlation_but_without_deduplication_is_rejected(self):
        self.worker.get_session_peer_status = lambda: {
            "state": "negotiated",
            "features": ["handshake", "request.correlation"],
        }
        status, body = self._request_json(
            "POST",
            "/api/v1/eda/execute",
            {"script": "puts hello"},
        )

        self.assertEqual(status, 503, body)
        self.assertEqual(body["state"], "not_submitted")
        self.assertIn("request.deduplication", body["error"])
        self.assertEqual(len(self.worker.frames), 0)
        self.assertEqual(len(self.worker.request_tracker._records), 0)

    def test_disconnected_link_rejects_without_registering_request(self):
        self.worker.connected = False
        status, body = self._request_json(
            "POST",
            "/api/v1/eda/execute",
            {"script": "puts hello"},
        )

        self.assertEqual(status, 503, body)
        self.assertEqual(body["state"], "not_submitted")
        self.assertEqual(len(self.worker.request_tracker._records), 0)
        self.assertEqual(len(self.worker.frames), 0)

    def test_peer_without_result_correlation_is_rejected_before_submission(self):
        self.worker.get_session_peer_status = lambda: {
            "state": "negotiated",
            "features": ["handshake"],
        }
        status, body = self._request_json(
            "POST",
            "/api/v1/eda/execute",
            {"script": "puts hello"},
        )

        self.assertEqual(status, 503, body)
        self.assertEqual(body["state"], "not_submitted")
        self.assertIn("request.correlation", body["error"])
        self.assertEqual(len(self.worker.frames), 0)
        self.assertEqual(len(self.worker.request_tracker._records), 0)

    def test_invalid_shell_timeout_is_rejected_before_frame_submission(self):
        status, body = self._request_json(
            "POST",
            "/api/v1/shell/exec",
            {"command": "Write-Output hello", "timeout": "bad"},
        )

        self.assertEqual(status, 400, body)
        self.assertEqual(body["state"], "not_submitted")
        self.assertEqual(len(self.worker.frames), 0)
        self.assertEqual(len(self.worker.request_tracker._records), 0)



    def test_api_rejects_missing_bearer_before_any_operation(self):
        status, body = self._request_json(
            "GET", "/api/v1/runtime/capabilities", token=""
        )
        self.assertEqual(status, 401, body)
        self.assertEqual(body["error"]["code"], "INVALID_AGENT_TOKEN")
        self.assertEqual(self.worker.frames, [])

    def test_read_only_agent_cannot_submit_eda_execution(self):
        read_only = self.security.issue_token(
            "readonly-agent",
            ["runtime.read", "eda.versions.read", "request.read"],
        )
        status, body = self._request_json(
            "POST",
            "/api/v1/eda/execute",
            {"script": "puts should_not_run"},
            token=read_only["token"],
        )
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"]["code"], "AGENT_SCOPE_DENIED")
        self.assertEqual(self.worker.frames, [])

    def test_server_execution_kill_switch_overrides_agent_scope(self):
        blocked = AntigravityIPCServer(
            self.worker,
            port=0,
            security_store=self.security,
            allow_eda_execute=False,
        )
        blocked.start()
        try:
            status, body = self._request_json(
                "POST",
                "/api/v1/eda/execute",
                {"script": "puts should_not_run"},
                base_url=f"http://127.0.0.1:{blocked.port}",
            )
            self.assertEqual(status, 403, body)
            self.assertEqual(body["error"]["code"], "EDA_EXECUTION_DISABLED_SERVER")
            self.assertEqual(self.worker.frames, [])
        finally:
            blocked.stop()

    def test_request_status_is_not_visible_to_another_agent(self):
        self.worker.auto_complete = False
        status, pending = self._request_json(
            "POST",
            "/api/v1/eda/execute",
            {"script": "puts pending", "wait_timeout_ms": 0},
        )
        self.assertEqual(status, 202, pending)
        request_id = pending["request_id"]

        other = self.security.issue_token("other-agent", ["request.read"])
        other_status, hidden = self._request_json(
            "GET",
            f"/api/v1/requests/{request_id}",
            token=other["token"],
        )
        self.assertEqual(other_status, 404, hidden)
        self.assertEqual(hidden["state"], "not_found")

        owner_status, visible = self._request_json(
            "GET", f"/api/v1/requests/{request_id}"
        )
        self.assertEqual(owner_status, 202, visible)
        self.assertEqual(visible["state"], "pending")

    def test_authorization_attempts_are_audited_without_script_body(self):
        status, _ = self._request_json(
            "POST",
            "/api/v1/eda/execute",
            {"script": "secret-script-text"},
            token="",
        )
        self.assertEqual(status, 401)
        records = self.security.list_audit(limit=10)
        self.assertTrue(records)
        self.assertEqual(records[0]["decision"], "denied")
        self.assertNotIn("secret-script-text", str(records))

    def test_read_rate_limit_returns_429_and_retry_after_header(self):
        limited = AntigravityIPCServer(
            self.worker,
            port=0,
            security_store=self.security,
            read_requests_per_minute=1,
        )
        limited.start()
        try:
            base = f"http://127.0.0.1:{limited.port}"
            first_status, _ = self._request_json(
                "GET", "/api/v1/runtime/capabilities", base_url=base
            )
            second_status, second = self._request_json(
                "GET", "/api/v1/runtime/capabilities", base_url=base
            )
            self.assertEqual(first_status, 200)
            self.assertEqual(second_status, 429, second)
            self.assertEqual(second["error"]["code"], "AGENT_RATE_LIMITED")
            self.assertIn("Retry-After", self.last_headers)
        finally:
            limited.stop()

    def test_per_agent_inflight_eda_limit_rejects_second_pending_request(self):
        self.worker.auto_complete = False
        limited = AntigravityIPCServer(
            self.worker,
            port=0,
            security_store=self.security,
            allow_eda_execute=True,
            max_pending_eda_per_agent=1,
        )
        limited.start()
        try:
            base = f"http://127.0.0.1:{limited.port}"
            first_status, first = self._request_json(
                "POST",
                "/api/v1/eda/execute",
                {"script": "puts first", "wait_timeout_ms": 0},
                base_url=base,
            )
            self.assertEqual(first_status, 202, first)
            frame_count = len(self.worker.frames)
            second_status, second = self._request_json(
                "POST",
                "/api/v1/eda/execute",
                {"script": "puts second", "wait_timeout_ms": 0},
                base_url=base,
            )
            self.assertEqual(second_status, 429, second)
            self.assertEqual(
                second["error"]["code"], "AGENT_EXECUTION_IN_FLIGHT_LIMIT"
            )
            self.assertEqual(len(self.worker.frames), frame_count)
        finally:
            limited.stop()

    def test_audit_admission_failure_fails_closed_before_runtime_access(self):
        original = self.security.audit_request

        def fail_audit(**_kwargs):
            raise OSError("simulated audit database failure")

        self.security.audit_request = fail_audit
        try:
            status, body = self._request_json(
                "GET", "/api/v1/runtime/capabilities"
            )
            self.assertEqual(status, 503, body)
            self.assertEqual(body["error"]["code"], "AGENT_AUDIT_UNAVAILABLE")
            self.assertEqual(self.worker.frames, [])
        finally:
            self.security.audit_request = original

    def test_read_rate_limit_returns_429_and_retry_after_header(self):
        limited = AntigravityIPCServer(
            self.worker,
            port=0,
            security_store=self.security,
            read_requests_per_minute=1,
        )
        limited.start()
        try:
            base = f"http://127.0.0.1:{limited.port}"
            first_status, _ = self._request_json(
                "GET", "/api/v1/runtime/capabilities", base_url=base
            )
            second_status, second = self._request_json(
                "GET", "/api/v1/runtime/capabilities", base_url=base
            )
            self.assertEqual(first_status, 200)
            self.assertEqual(second_status, 429, second)
            self.assertEqual(second["error"]["code"], "AGENT_RATE_LIMITED")
        finally:
            limited.stop()

    def test_per_agent_inflight_eda_limit_rejects_second_pending_request(self):
        self.worker.auto_complete = False
        limited = AntigravityIPCServer(
            self.worker,
            port=0,
            security_store=self.security,
            allow_eda_execute=True,
            max_pending_eda_per_agent=1,
        )
        limited.start()
        try:
            base = f"http://127.0.0.1:{limited.port}"
            first_status, first = self._request_json(
                "POST",
                "/api/v1/eda/execute",
                {"script": "puts first", "wait_timeout_ms": 0},
                base_url=base,
            )
            self.assertEqual(first_status, 202, first)
            frame_count = len(self.worker.frames)
            second_status, second = self._request_json(
                "POST",
                "/api/v1/eda/execute",
                {"script": "puts second", "wait_timeout_ms": 0},
                base_url=base,
            )
            self.assertEqual(second_status, 429, second)
            self.assertEqual(
                second["error"]["code"], "AGENT_EXECUTION_IN_FLIGHT_LIMIT"
            )
            self.assertEqual(len(self.worker.frames), frame_count)
        finally:
            limited.stop()

    def test_audit_admission_failure_fails_closed_before_runtime_access(self):
        original = self.security.audit_request

        def fail_audit(**_kwargs):
            raise OSError("simulated audit database failure")

        self.security.audit_request = fail_audit
        try:
            status, body = self._request_json(
                "GET", "/api/v1/runtime/capabilities"
            )
            self.assertEqual(status, 503, body)
            self.assertEqual(body["error"]["code"], "AGENT_AUDIT_UNAVAILABLE")
            self.assertEqual(self.worker.frames, [])
        finally:
            self.security.audit_request = original

    def test_legacy_eda_execute_alias_requires_authentication(self):
        status, body = self._request_json(
            "POST",
            "/api/eda/exec",
            {"script": "puts must_not_run"},
            token="",
        )
        self.assertEqual(status, 401, body)
        self.assertEqual(body["error"]["code"], "INVALID_AGENT_TOKEN")
        self.assertEqual(self.worker.frames, [])

    def test_unclassified_api_route_requires_admin_scope(self):
        status, body = self._request_json(
            "GET", "/api/v1/future-sensitive-route"
        )
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"]["code"], "AGENT_SCOPE_DENIED")

    def test_uncertain_serial_send_is_unknown_and_keeps_inflight_reservation(self):
        self.worker.raise_on_send = True
        status, body = self._request_json(
            "POST",
            "/api/v1/eda/execute",
            {"script": "puts uncertain"},
        )
        self.assertEqual(status, 503, body)
        self.assertEqual(body["state"], "unknown")
        self.assertEqual(
            body["error"]["code"], "EDA_FRAME_SUBMISSION_UNCONFIRMED"
        )
        request_id = body["request_id"]
        self.assertEqual(self.security.active_execution_count("test-agent"), 1)

        poll_status, polled = self._request_json(
            "GET", f"/api/v1/requests/{request_id}"
        )
        self.assertEqual(poll_status, 503, polled)
        self.assertEqual(polled["state"], "unknown")

        second_status, second = self._request_json(
            "POST",
            "/api/v1/eda/execute",
            {"script": "puts must_wait"},
        )
        self.assertEqual(second_status, 429, second)
        self.assertEqual(
            second["error"]["code"], "AGENT_EXECUTION_IN_FLIGHT_LIMIT"
        )
        self.assertEqual(len(self.worker.frames), 1)

if __name__ == "__main__":
    unittest.main()
