# test_request_api_http.py
# -*- coding: utf-8 -*-
"""HTTP contract tests for the A-side correlated EDA and Shell endpoints."""
import json
import os
import sqlite3
import tempfile
import urllib.error
import urllib.request
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

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
        self.shell_task_finished = True
        self.shell_task_missing = False

    def is_serial_connected(self):
        return self.connected

    def send_fs_request_sync(self, req_dict, timeout=15.0):
        return {
            "req_id": req_dict.get("req_id", 1),
            "action": req_dict.get("action", ""),
            "success": True,
            "exists": True,
            "is_dir": False,
            "size": 5,
            "content": "hello",
        }

    def get_session_peer_status(self):
        return {
            "state": "negotiated",
            "features": ["handshake", "request.correlation", "request.deduplication", "shell.task.result-query"],
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
        elif frame_type == FrameType.SHELL_TASK_GET:
            if self.shell_task_missing:
                self.request_tracker.complete(
                    request_id,
                    {
                        "request_id": request_id,
                        "success": False,
                        "error": "任务不存在",
                        "request_state": "unknown",
                    },
                    state="unknown",
                )
                return
            finished = self.shell_task_finished
            result = {
                "request_id": request_id,
                "success": True,
                "task_id": data["task_id"],
                "finished": finished,
                "killed": False,
                "exit_code": 0 if finished else None,
                "stdout": "complete async shell output" if finished else "partial output",
                "stderr": "",
                "command": "Write-Output hello",
                "cwd": r"E:\\_airbridge_temp",
            }
            self.request_tracker.complete(
                request_id,
                result,
                state="completed" if finished else "accepted",
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

    def test_rfs_read_and_write_actions_require_separate_scopes(self):
        read_token = self.security.issue_token(
            "rfs-reader",
            ["filesystem.read"],
        )
        write_token = self.security.issue_token(
            "rfs-writer",
            ["filesystem.write"],
        )

        read_status, read_result = self._request_json(
            "POST",
            "/api/v1/fs/read",
            {"path": r"E:\_airbridge_temp\probe.txt"},
            token=read_token["token"],
        )
        self.assertEqual(read_status, 200, read_result)
        self.assertTrue(read_result["success"])

        read_denied_status, read_denied = self._request_json(
            "POST",
            "/api/v1/fs/write",
            {
                "path": r"E:\_airbridge_temp\probe.txt",
                "content": "should not write",
            },
            token=read_token["token"],
        )
        self.assertEqual(read_denied_status, 403, read_denied)
        self.assertEqual(
            read_denied["error"]["required_scope"],
            "filesystem.write",
        )

        write_denied_status, write_denied = self._request_json(
            "POST",
            "/api/v1/fs/read",
            {"path": r"E:\_airbridge_temp\probe.txt"},
            token=write_token["token"],
        )
        self.assertEqual(write_denied_status, 403, write_denied)
        self.assertEqual(
            write_denied["error"]["required_scope"],
            "filesystem.read",
        )

        write_status, write_result = self._request_json(
            "POST",
            "/api/v1/fs/write",
            {
                "path": r"E:\_airbridge_temp\probe.txt",
                "content": "write allowed",
            },
            token=write_token["token"],
        )
        self.assertEqual(write_status, 200, write_result)

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

    def test_uncertain_shell_serial_send_is_unknown_and_request_can_be_polled(self):
        self.worker.raise_on_send = True
        status, body = self._request_json(
            "POST",
            "/api/v1/shell/exec",
            {
                "command": "Write-Output uncertain",
                "timeout": 5,
                "async": False,
            },
        )

        self.assertEqual(status, 503, body)
        self.assertEqual(body["state"], "unknown")
        self.assertEqual(
            body["error"]["code"], "SHELL_FRAME_SUBMISSION_UNCONFIRMED"
        )
        self.assertIn("Query this request_id", body["error"]["next_step"])
        request_id = body["request_id"]
        self.assertEqual(len(self.worker.frames), 1)
        self.assertEqual(
            self.worker.request_tracker._records[request_id]["state"], "unknown"
        )

        poll_status, polled = self._request_json(
            "GET", f"/api/v1/requests/{request_id}"
        )
        self.assertEqual(poll_status, 503, polled)
        self.assertEqual(polled["state"], "unknown")
        self.assertEqual(len(self.worker.frames), 1)

        audit = next(
            (
                record
                for record in self.security.list_audit(limit=20)
                if record["path"] == "/api/v1/shell/exec"
                and record["reason_code"] == "SHELL_FRAME_SUBMISSION_UNCONFIRMED"
            ),
            None,
        )
        self.assertIsNotNone(audit)

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

    def test_async_shell_task_result_can_be_queried_by_original_request_id(self):
        submit_status, accepted = self._request_json(
            "POST",
            "/api/v1/shell/exec",
            {
                "command": "Write-Output hello",
                "timeout": 30,
                "async": True,
            },
        )
        self.assertEqual(submit_status, 202, accepted)
        self.assertEqual(accepted["state"], "accepted")
        original_request_id = accepted["request_id"]

        query_status, task = self._request_json(
            "GET",
            f"/api/v1/shell/tasks/by-request/{original_request_id}",
        )
        self.assertEqual(query_status, 200, task)
        self.assertEqual(task["request_id"], original_request_id)
        self.assertEqual(task["state"], "completed")
        self.assertEqual(task["result"]["task_id"], "shell-http-test")
        self.assertEqual(task["result"]["stdout"], "complete async shell output")

        sent_type, sent_payload = self.worker.frames[-1]
        self.assertEqual(sent_type, FrameType.SHELL_TASK_GET)
        query = json.loads(sent_payload.decode("utf-8"))
        self.assertEqual(query["task_id"], "shell-http-test")
        self.assertEqual(query["request_id"], task["query_request_id"])

        query_poll_status, query_poll = self._request_json(
            "GET", f"/api/v1/requests/{task['query_request_id']}"
        )
        self.assertEqual(query_poll_status, 200, query_poll)
        self.assertEqual(query_poll["kind"], "shell.task.get")
        self.assertTrue(query_poll["result"]["finished"])

    def test_async_shell_task_query_reports_missing_task_as_unknown(self):
        submit_status, accepted = self._request_json(
            "POST",
            "/api/v1/shell/exec",
            {
                "command": "Write-Output hello",
                "timeout": 30,
                "async": True,
            },
        )
        self.assertEqual(submit_status, 202, accepted)
        self.worker.shell_task_missing = True

        query_status, task = self._request_json(
            "GET",
            f"/api/v1/shell/tasks/by-request/{accepted['request_id']}",
        )
        self.assertEqual(query_status, 503, task)
        self.assertEqual(task["state"], "unknown")
        self.assertEqual(task["result"]["request_state"], "unknown")

    def test_async_shell_task_query_reports_running_state(self):
        self.worker.shell_task_finished = False
        submit_status, accepted = self._request_json(
            "POST",
            "/api/v1/shell/exec",
            {
                "command": "Start-LongRunningTask",
                "timeout": 120,
                "async": True,
            },
        )
        self.assertEqual(submit_status, 202, accepted)

        query_status, task = self._request_json(
            "GET",
            f"/api/v1/shell/tasks/by-request/{accepted['request_id']}",
        )
        self.assertEqual(query_status, 202, task)
        self.assertEqual(task["state"], "accepted")
        self.assertFalse(task["result"]["finished"])

    def test_async_shell_task_query_is_restricted_to_request_owner(self):
        submit_status, accepted = self._request_json(
            "POST",
            "/api/v1/shell/exec",
            {
                "command": "Write-Output private",
                "timeout": 30,
                "async": True,
            },
        )
        self.assertEqual(submit_status, 202, accepted)
        other = self.security.issue_token(
            "other-agent",
            ["shell.read", "request.read"],
        )

        query_status, result = self._request_json(
            "GET",
            f"/api/v1/shell/tasks/by-request/{accepted['request_id']}",
            token=other["token"],
        )
        self.assertEqual(query_status, 404, result)
        self.assertEqual(result["state"], "not_found")
        self.assertEqual(self.worker.frames[-1][0], FrameType.SHELL_EXEC)

    def test_async_shell_task_query_requires_peer_capability(self):
        submit_status, accepted = self._request_json(
            "POST",
            "/api/v1/shell/exec",
            {
                "command": "Write-Output hello",
                "timeout": 30,
                "async": True,
            },
        )
        self.assertEqual(submit_status, 202, accepted)
        self.worker.get_session_peer_status = lambda: {
            "state": "negotiated",
            "features": ["handshake", "request.correlation", "request.deduplication"],
        }

        query_status, result = self._request_json(
            "GET",
            f"/api/v1/shell/tasks/by-request/{accepted['request_id']}",
        )
        self.assertEqual(query_status, 503, result)
        self.assertEqual(result["state"], "unsupported")
        self.assertEqual(self.worker.frames[-1][0], FrameType.SHELL_EXEC)

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


    def test_stop_does_not_close_injected_security_store(self):
        self.server.stop()
        issued = self.security.issue_token("store-owner-check", ["runtime.read"])
        self.assertTrue(issued["token_id"])

    def test_stop_closes_server_owned_security_store(self):
        db_path = str(Path(self.temp.name) / "server-owned.sqlite3")
        with patch.dict(os.environ, {"AIRBRIDGE_AGENT_SECURITY_DB": db_path}):
            server = AntigravityIPCServer(FakeWorker(), port=0)
            owned_store = server.security_store
            server.start()
            server.stop()
        with self.assertRaises(sqlite3.ProgrammingError):
            owned_store.list_tokens()

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

        denial = next(
            (
                record
                for record in self.security.list_audit(limit=20)
                if record["path"] == "/api/v1/eda/execute"
                and record["reason_code"] == "AGENT_SCOPE_DENIED"
            ),
            None,
        )
        self.assertIsNotNone(denial)
        self.assertEqual(denial["agent_id"], "readonly-agent")
        self.assertEqual(denial["token_id"], read_only["token_id"])
        self.assertEqual(denial["http_status"], 403)

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
            self.assertNotIn("request_id", second)
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

    def test_audit_finalization_failure_never_returns_success_after_dispatch(self):
        original = self.security.audit_request
        request_id = str(uuid.uuid4())

        def fail_finalization(**kwargs):
            if kwargs.get("decision") == "admitted":
                return original(**kwargs)
            raise OSError("simulated final audit write failure")

        self.security.audit_request = fail_finalization
        try:
            status, body = self._request_json(
                "POST",
                "/api/v1/eda/execute",
                {"request_id": request_id, "script": "puts audit-finalization"},
            )
        finally:
            self.security.audit_request = original

        self.assertEqual(status, 503, body)
        self.assertEqual(body["error"]["code"], "AGENT_AUDIT_FINALIZATION_FAILED")
        self.assertEqual(body["state"], "unknown")
        self.assertEqual(body["request_id"], request_id)
        self.assertEqual(len(self.worker.frames), 1)

        poll_status, poll = self._request_json(
            "GET", f"/api/v1/requests/{request_id}"
        )
        self.assertEqual(poll_status, 200, poll)
        self.assertEqual(poll["state"], "completed")

    def test_stop_bounds_inflight_handlers_and_preserves_owned_store_until_drain(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingWorker(FakeWorker):
            def get_session_peer_status(self):
                entered.set()
                release.wait(timeout=5.0)
                return super().get_session_peer_status()

        db_path = str(Path(self.temp.name) / "owned-shutdown.sqlite3")
        with patch.dict(os.environ, {"AIRBRIDGE_AGENT_SECURITY_DB": db_path}):
            server = AntigravityIPCServer(BlockingWorker(), port=0)
        owned_store = server.security_store
        issued = owned_store.issue_token("shutdown-agent", ["runtime.read"])
        server._shutdown_timeout_seconds = 0.1
        server.start()

        request_result = []

        def send_request():
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.port}/api/v1/runtime/capabilities",
                headers={"Authorization": "Bearer " + issued["token"]},
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=4.0) as response:
                    request_result.append((response.status, response.read()))
            except Exception as exc:
                request_result.append(exc)

        client = threading.Thread(target=send_request, daemon=True)
        client.start()
        try:
            self.assertTrue(entered.wait(timeout=2.0), "handler did not enter the blocking worker")
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "in-flight HTTP handler"):
                server.stop()
            self.assertLess(time.monotonic() - started, 2.0)
            self.assertIsNotNone(server.security_store)
            self.assertEqual(len(owned_store.list_tokens("shutdown-agent")), 1)

            release.set()
            client.join(timeout=3.0)
            self.assertFalse(client.is_alive(), "HTTP client did not finish after handler release")
            server.stop()
            self.assertIsNone(server.security_store)
            with self.assertRaises(sqlite3.ProgrammingError):
                owned_store.list_tokens("shutdown-agent")
        finally:
            release.set()
            client.join(timeout=3.0)
            if server.security_store is not None:
                server._shutdown_timeout_seconds = 3.0
                server.stop()

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

    def test_eda_tracker_registration_failure_releases_owner_and_execution_slot(self):
        original_register = self.worker.request_tracker.register

        def fail_register(_request_id, _kind):
            raise RuntimeError("simulated tracker registration failure")

        self.worker.request_tracker.register = fail_register
        status, body = self._request_json(
            "POST",
            "/api/v1/eda/execute",
            {"script": "puts must_not_run"},
        )

        self.assertEqual(status, 503, body)
        self.assertEqual(body["state"], "not_submitted")
        self.assertEqual(len(self.worker.frames), 0)
        self.assertEqual(len(self.worker.request_tracker._records), 0)
        self.assertEqual(self.security.active_execution_count("test-agent"), 0)
        request_id = body["request_id"]
        principal = self.security.authenticate("Bearer " + self.token)
        self.assertFalse(self.security.can_read_request(request_id, principal))

        self.worker.request_tracker.register = original_register
        retry_status, retry = self._request_json(
            "POST",
            "/api/v1/eda/execute",
            {"request_id": request_id, "script": "puts retry"},
        )
        self.assertEqual(retry_status, 200, retry)
        self.assertEqual(retry["request_id"], request_id)
        self.assertEqual(retry["state"], "completed")
        self.assertEqual(len(self.worker.frames), 1)
        self.assertTrue(self.security.can_read_request(request_id, principal))

    def test_shell_tracker_registration_failure_releases_owner_without_unknown_state(self):
        original_register = self.worker.request_tracker.register

        def fail_register(_request_id, _kind):
            raise RuntimeError("simulated tracker registration failure")

        self.worker.request_tracker.register = fail_register
        status, body = self._request_json(
            "POST",
            "/api/v1/shell/exec",
            {"command": "Write-Output must_not_run", "timeout": 5},
        )

        self.assertEqual(status, 503, body)
        self.assertEqual(body["state"], "not_submitted")
        self.assertEqual(len(self.worker.frames), 0)
        self.assertEqual(len(self.worker.request_tracker._records), 0)
        request_id = body["request_id"]
        principal = self.security.authenticate("Bearer " + self.token)
        self.assertFalse(self.security.can_read_request(request_id, principal))
        self.assertFalse(
            any(
                record["path"] == "/api/v1/shell/exec"
                and record["reason_code"] == "SHELL_FRAME_SUBMISSION_UNCONFIRMED"
                for record in self.security.list_audit(limit=20)
            )
        )

        self.worker.request_tracker.register = original_register
        retry_status, retry = self._request_json(
            "POST",
            "/api/v1/shell/exec",
            {
                "request_id": request_id,
                "command": "Write-Output retry",
                "timeout": 5,
            },
        )
        self.assertEqual(retry_status, 200, retry)
        self.assertEqual(retry["request_id"], request_id)
        self.assertEqual(retry["state"], "completed")
        self.assertEqual(len(self.worker.frames), 1)
        self.assertTrue(self.security.can_read_request(request_id, principal))

if __name__ == "__main__":
    unittest.main()
