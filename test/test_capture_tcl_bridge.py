# test_capture_tcl_bridge.py
# -*- coding: utf-8 -*-
"""Software tests for the persistent Capture Tcl Bridge contract.

These tests exercise the Python file-queue / lifecycle client against a
deterministic local Bridge simulator. They do not emulate Capture's Dbo API
or replace the required Windows / Capture 17.2 hardware acceptance.
"""
import base64
import hashlib
import os
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from capture_tcl_bridge import (
    CaptureTclAdapter,
    CaptureTclBridgeError,
    CaptureTclBridgeSession,
    _decode_record,
    _encode_record,
)
from runtime_adapter import RuntimeExecutionContext, RuntimeExecutionRequest


class FakeCaptureProcess:
    def __init__(self, pid=7476):
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode


class FakeBridgeSimulator:
    def __init__(self, session, process=None, status="SUCCEEDED"):
        self.session = session
        self.process = process or FakeCaptureProcess()
        self.status = status
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.session.attach_process(self.process)
        self.session.ready_path.write_text(
            _encode_record({
                "schema": "1",
                "session_id": self.session.session_id,
                "startup_nonce": self.session.startup_nonce,
                "runtime_pid": str(self.process.pid),
                "tcl_version": "8.6.0",
                "runtime_version": self.session.runtime_version,
                "ready_at_ms": str(int(time.time() * 1000)),
            }),
            encoding="ascii",
        )
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=1)

    def _run(self):
        while not self.stop_event.is_set():
            for request_path in sorted(self.session.requests_dir.glob("*.req")):
                running_path = self.session.running_dir / (request_path.name + ".run")
                try:
                    os.replace(request_path, running_path)
                except OSError:
                    continue
                try:
                    request = _decode_record(running_path)
                    script_bytes = base64.b64decode(request["script_b64"])
                    script = script_bytes.decode("utf-8")
                    payload_hash = hashlib.sha256(script_bytes).hexdigest()
                    success = self.status == "SUCCEEDED" and "error " not in script
                    record = {
                        "schema": "1",
                        "session_id": self.session.session_id,
                        "startup_nonce": self.session.startup_nonce,
                        "request_id": request["request_id"],
                        "payload_sha256": payload_hash,
                        "status": "SUCCEEDED" if success else "FAILED",
                        "return_code": "0" if success else "1",
                        "result": "42" if success else "",
                        "stdout": "bridge stdout",
                        "stderr": "",
                        "error_info": "" if success else "expected Tcl error",
                        "error_code": "" if success else "AB_EXPECTED_ERROR",
                        "runtime_pid": str(self.process.pid),
                        "tcl_version": "8.6.0",
                        "started_at_ms": str(int(time.time() * 1000) - 2),
                        "completed_at_ms": str(int(time.time() * 1000)),
                    }
                    final_path = self.session.results_dir / f"{request['request_id']}.result"
                    temp_path = final_path.with_suffix(".result.tmp")
                    temp_path.write_text(_encode_record(record), encoding="ascii")
                    os.replace(temp_path, final_path)
                finally:
                    running_path.unlink(missing_ok=True)
            self.stop_event.wait(0.01)


class CaptureTclBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.autoload_dir = self.root / "capAutoLoad"
        self.autoload_dir.mkdir()
        self.session = CaptureTclBridgeSession.create(
            session_root=str(self.root / "sessions"),
            autoload_dir=str(self.autoload_dir),
            runtime_version="17.2",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_installs_session_scoped_autoload_hook_and_bridge(self):
        self.assertTrue(self.session.bridge_script_path.is_file())
        self.assertTrue(self.session.hook_path.is_file())
        self.assertIn("after 200 ::ABBridge::poll", self.session.bridge_script_path.read_text(encoding="utf-8"))
        self.assertIn("startup_nonce", self.session.bridge_script_path.read_text(encoding="utf-8"))
        self.assertIn("source", self.session.hook_path.read_text(encoding="utf-8"))
        self.assertTrue(self.session.requests_dir.is_dir())
        self.assertTrue(self.session.results_dir.is_dir())

    def test_ready_requires_matching_session_nonce_and_process_pid(self):
        process = FakeCaptureProcess(pid=7476)
        simulator = FakeBridgeSimulator(self.session, process=process)
        simulator.start()
        try:
            ready = self.session.wait_ready(timeout_seconds=1, expected_pid=7476)
            self.assertEqual(ready["tcl_version"], "8.6.0")
            self.assertEqual(ready["runtime_pid"], "7476")
        finally:
            simulator.stop()

    def test_ready_rejects_wrong_startup_nonce(self):
        self.session.ready_path.write_text(
            _encode_record({
                "schema": "1",
                "session_id": self.session.session_id,
                "startup_nonce": "wrong-" + self.session.startup_nonce,
                "runtime_pid": "7476",
                "tcl_version": "8.6.0",
            }),
            encoding="ascii",
        )
        with self.assertRaisesRegex(CaptureTclBridgeError, "startup_nonce mismatch"):
            self.session.wait_ready(timeout_seconds=0.2, expected_pid=7476)

    def test_ready_rejects_pid_mismatch(self):
        self.session.ready_path.write_text(
            _encode_record({
                "schema": "1",
                "session_id": self.session.session_id,
                "startup_nonce": self.session.startup_nonce,
                "runtime_pid": "9999",
                "tcl_version": "8.6.0",
            }),
            encoding="ascii",
        )
        with self.assertRaisesRegex(CaptureTclBridgeError, "PID mismatch"):
            self.session.wait_ready(timeout_seconds=0.2, expected_pid=7476)

    def test_request_round_trip_uses_atomic_file_queue_and_returns_runtime_metadata(self):
        simulator = FakeBridgeSimulator(self.session)
        simulator.start()
        try:
            self.session.wait_ready(timeout_seconds=1, expected_pid=7476)
            request_id = str(uuid.uuid4())
            result = self.session.execute(
                "set answer 42\nputs $answer\nset answer",
                request_id=request_id,
                timeout_seconds=1,
            )
            self.assertTrue(result["success"], result)
            self.assertEqual(result["output"], "42")
            self.assertEqual(result["stdout"], "bridge stdout")
            self.assertEqual(result["capture_pid"], 7476)
            self.assertEqual(result["tcl_version"], "8.6.0")
            self.assertEqual(result["session_id"], self.session.session_id)
            self.assertEqual(result["request_id"], request_id)
            self.assertEqual(len(result["payload_sha256"]), 64)
        finally:
            simulator.stop()

    def test_tcl_execution_error_preserves_stack_as_structured_error(self):
        simulator = FakeBridgeSimulator(self.session)
        simulator.start()
        try:
            self.session.wait_ready(timeout_seconds=1, expected_pid=7476)
            result = self.session.execute(
                "error AB_EXPECTED_ERROR",
                request_id=str(uuid.uuid4()),
                timeout_seconds=1,
            )
            self.assertFalse(result["success"])
            self.assertEqual(result["request_state"], "failed")
            self.assertEqual(result["error"]["error_info"], "expected Tcl error")
            self.assertEqual(result["error"]["error_code"], "AB_EXPECTED_ERROR")
        finally:
            simulator.stop()

    def test_timeout_is_unknown_and_does_not_delete_queued_request(self):
        self.session.attach_process(FakeCaptureProcess())
        request_id = str(uuid.uuid4())
        result = self.session.execute(
            "after 5000; set x 1",
            request_id=request_id,
            timeout_seconds=0.1,
        )
        self.assertEqual(result["request_state"], "unknown")
        self.assertEqual(result["error"]["code"], "CAPTURE_REQUEST_TIMEOUT")
        self.assertTrue((self.session.requests_dir / f"{request_id}.req").is_file())

    def test_capture_process_exit_is_reported_as_unknown(self):
        process = FakeCaptureProcess()
        process.returncode = 0
        self.session.attach_process(process)
        result = self.session.execute(
            "puts never-run",
            request_id=str(uuid.uuid4()),
            timeout_seconds=1,
        )
        self.assertEqual(result["request_state"], "unknown")
        self.assertEqual(result["error"]["code"], "CAPTURE_SESSION_LOST")

    def test_payload_limit_is_checked_before_queueing(self):
        self.session.attach_process(FakeCaptureProcess())
        with self.assertRaisesRegex(CaptureTclBridgeError, "exceeds"):
            self.session.execute(
                "x" * 1024,
                request_id=str(uuid.uuid4()),
                max_script_bytes=128,
            )
        self.assertEqual(list(self.session.requests_dir.glob("*.req")), [])

    def test_adapter_uses_bridge_provider_and_preserves_request_id(self):
        simulator = FakeBridgeSimulator(self.session)
        simulator.start()
        logs = []
        try:
            self.session.wait_ready(timeout_seconds=1, expected_pid=7476)
            adapter = CaptureTclAdapter(lambda _payload, _context: self.session)
            request_id = str(uuid.uuid4())
            result = adapter.execute(
                RuntimeExecutionRequest(
                    operation="execute",
                    request_id=request_id,
                    payload={"script": "puts 42", "timeout": 1},
                ),
                RuntimeExecutionContext(log_callback=logs.append),
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["request_id"], request_id)
            self.assertEqual(len(logs), 2)
        finally:
            simulator.stop()

    def test_adapter_prepends_opj_open_command(self):
        class RecordingBridge:
            session_id = str(uuid.uuid4())
            target_file = ""
            def __init__(self):
                self.script = ""
            def execute(self, script, request_id, timeout_seconds):
                self.script = script
                return {
                    "success": True,
                    "request_state": "completed",
                    "returncode": 0,
                    "request_id": request_id,
                }

        bridge = RecordingBridge()
        adapter = CaptureTclAdapter(lambda _payload, _context: bridge)
        adapter.execute(
            RuntimeExecutionRequest(
                operation="execute",
                payload={
                    "script": "puts ready",
                    "target_file": r"D:\\work\\project.opj",
                    "timeout": 3,
                },
            ),
            RuntimeExecutionContext(),
        )
        self.assertTrue(bridge.script.startswith('Open "D:\\\\work\\\\project.opj"\n'))

    def test_result_retention_prunes_oldest_records_by_ttl_and_capacity(self):
        self.session.results_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for idx in range(3):
            path = self.session.results_dir / f"{idx}.result"
            path.write_text("x", encoding="ascii")
            os.utime(path, (time.time() - 100 - idx, time.time() - 100 - idx))
            paths.append(path)
        removed = self.session.prune_results(retention_seconds=50, max_records=2)
        self.assertEqual(removed, 3)


if __name__ == "__main__":
    unittest.main()
