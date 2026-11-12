# test_request_api_http.py
# -*- coding: utf-8 -*-
"""HTTP contract tests for the A-side correlated EDA and Shell endpoints."""
import json
import urllib.error
import urllib.request
import unittest

from airbridge_app import AntigravityIPCServer
from protocol import FrameType
from request_tracker import RequestTracker


class FakeWorker:
    def __init__(self):
        self.request_tracker = RequestTracker()
        self.connected = True
        self.auto_complete = True
        self.frames = []

    def is_serial_connected(self):
        return self.connected

    def send_frame(self, frame_type, payload=b""):
        self.frames.append((frame_type, payload))
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
        self.worker = FakeWorker()
        self.server = AntigravityIPCServer(self.worker, port=0)
        self.server.start()
        self.base_url = f"http://127.0.0.1:{self.server.port}"

    def tearDown(self):
        self.server.stop()

    def _request_json(self, method, path, payload=None):
        body = (
            None
            if payload is None
            else json.dumps(payload).encode("utf-8")
        )
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=3.0) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
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


if __name__ == "__main__":
    unittest.main()
