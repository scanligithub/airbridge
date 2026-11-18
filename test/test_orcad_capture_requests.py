# -*- coding: utf-8 -*-
"""M3.2.4 Capture Tcl queue tests; no live Capture or Host-B is used."""
from __future__ import annotations

import base64
import hashlib
import json
import ntpath
import re
import sys
import tempfile
import uuid
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "skill"
    / "orcad-capture"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

from airbridge_client import AirBridgeTransportError  # noqa: E402
from capture_process import CaptureProcessDisposition  # noqa: E402
from capture_requests import (  # noqa: E402
    CaptureTclOutcomeUnknown,
    CaptureTclQueue,
    CaptureTclQueueError,
    build_capture_request_envelope,
    build_capture_result_envelope,
    build_publish_capture_request_command,
)
from capture_session import (  # noqa: E402
    CaptureAuthorizationError,
    CaptureConfig,
    CaptureSession,
    SessionManifest,
    SessionState,
)


CAPTURE_EXE = r"D:\Cadence\SPB_17.2\tools\bin\capture.exe"
AUTOLOAD = r"D:\Cadence\SPB_17.2\tools\capture\tclscripts\capAutoLoad"
REMOTE_DIR = r"C:\Users\Agent\AppData\Local\AirBridgeSkills\Capture\sessions\test-session"
PID = 7420
STARTED = "2026-11-18T08:00:00.000000Z"


class FakeInspector:
    def __init__(self, disposition=CaptureProcessDisposition.OWNED_INSTANCE):
        self.disposition = disposition
        self.calls = 0

    def inspect_session(self, _manifest):
        self.calls += 1
        return SimpleNamespace(disposition=self.disposition)


class FakeAirBridgeClient:
    def __init__(self, manifest, *, auto_result=True):
        self.manifest = manifest
        self.auto_result = auto_result
        self.remote_files = {}
        self.filesystem_calls = []
        self.shell_calls = []
        self.fail_publish_after_move = False

    def filesystem(self, action, payload=None, **_kwargs):
        request = dict(payload or {})
        self.filesystem_calls.append((action, request))
        path = request.get("path", "")
        if action == "write":
            raw = (
                base64.b64decode(request["content"].encode("ascii"), validate=True)
                if request.get("is_base64") is True
                else request["content"].encode("utf-8")
            )
            if path in self.remote_files:
                return {"success": False, "error": "destination exists"}
            self.remote_files[path] = raw
            return {"success": True, "path": path, "size": len(raw)}
        if action == "read":
            raw = self.remote_files.get(path)
            if raw is None:
                return {"success": False, "error": "File not found"}
            if request.get("as_base64") is True:
                return {
                    "success": True,
                    "path": path,
                    "size": len(raw),
                    "is_truncated": False,
                    "encoding": "base64",
                    "content": base64.b64encode(raw).decode("ascii"),
                }
            return {
                "success": True,
                "path": path,
                "size": len(raw),
                "is_truncated": False,
                "encoding": "text",
                "content": raw.decode("utf-8"),
            }
        raise AssertionError("unexpected RFS action: {}".format(action))

    def shell_execute(self, command, **kwargs):
        self.shell_calls.append((command, kwargs))
        request_id = kwargs["request_id"]
        encoded_paths = re.findall(
            r"FromBase64String\('([A-Za-z0-9+/=]+)'\)", command
        )
        self.assert_publish_command(command, encoded_paths)
        source, target = [
            base64.b64decode(item).decode("utf-8") for item in encoded_paths
        ]
        expected = re.search(r"\$expected = '([0-9a-f]{64})'", command).group(1)
        raw = self.remote_files[source]
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise AssertionError("publish command hash mismatch in test double")
        if target in self.remote_files:
            raise AssertionError("publish must not overwrite an existing request")
        self.remote_files[target] = self.remote_files.pop(source)

        request_lines = self.remote_files[target].decode("ascii").splitlines()
        fields = dict(line.split("=", 1) for line in request_lines[1:])
        if self.auto_result:
            request_id_from_file = fields["request_id"]
            result_path = ntpath.join(
                ntpath.dirname(ntpath.dirname(target)),
                "results",
                request_id_from_file + ".result",
            )
            self.remote_files[result_path] = build_capture_result_envelope(
                self.manifest.session_id,
                self.manifest.startup_nonce,
                request_id_from_file,
                fields["script_sha256"],
                success=True,
                value="DONE",
            )

        if self.fail_publish_after_move:
            raise AirBridgeTransportError(
                "simulated lost publish response", request_id=request_id
            )
        stdout = json.dumps({
            "published": True,
            "path": target,
            "sha256": actual,
        })
        return {
            "request_id": request_id,
            "state": "completed",
            "result": {
                "success": True,
                "exit_code": 0,
                "stdout": stdout,
                "stderr": "",
            },
        }

    @staticmethod
    def assert_publish_command(command, encoded_paths):
        if "[IO.File]::Move($source, $target)" not in command:
            raise AssertionError("publication must use atomic File.Move")
        if "Get-FileHash" not in command or "$actual -ne $expected" not in command:
            raise AssertionError("publication must verify SHA-256")
        if "refusing overwrite" not in command:
            raise AssertionError("publication must refuse overwrite")
        if len(encoded_paths) != 2:
            raise AssertionError("source and target paths must be Base64 encoded")


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        self.value += 1.0
        return self.value

    @staticmethod
    def sleep(_seconds):
        pass


class CaptureTclQueueTests(TestCase):
    def setUp(self):
        self.config = CaptureConfig(
            capture_exe_path=CAPTURE_EXE,
            cap_autoload_dir=AUTOLOAD,
            expected_capture_version="17.2-2016",
            expected_tcl_version="8.6.0",
            poll_interval_seconds=0.01,
            shell_timeout_seconds=5,
        )
        self.manifest = SessionManifest.create(self.config)
        self.manifest.state = SessionState.READY.value
        self.manifest.pid = PID
        self.manifest.process_started_at_utc = STARTED
        self.manifest.capture_version = "17.2-2016"
        self.manifest.tcl_version = "8.6.0"
        self.manifest.remote_session_dir = REMOTE_DIR
        self.manifest.bridge_sha256 = hashlib.sha256(b"bridge").hexdigest()
        self.session = CaptureSession(self.manifest, self.config)
        self.client = FakeAirBridgeClient(self.manifest)
        self.inspector = FakeInspector()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manifest_path = Path(self.temp.name) / "session.json"
        self.manifest.save(self.manifest_path)
        self.queue = CaptureTclQueue(
            self.client,
            self.session,
            self.config,
            self.manifest_path,
            inspector=self.inspector,
            sleep_fn=lambda _seconds: None,
        )

    def test_request_envelope_contains_identity_and_base64_not_raw_script(self):
        script = "set x 1\nputs $x"
        request_id = str(uuid.uuid4())
        raw, digest, crc = build_capture_request_envelope(
            self.manifest.session_id,
            self.manifest.startup_nonce,
            request_id,
            script,
        )
        text = raw.decode("ascii")
        self.assertIn("AB_CAPTURE_REQUEST_V1", text)
        self.assertIn("session_id=" + self.manifest.session_id, text)
        self.assertIn("request_id=" + request_id, text)
        self.assertIn("script_sha256=" + hashlib.sha256(script.encode()).hexdigest(), text)
        self.assertIn("script_crc32=" + format(zlib.crc32(script.encode()) & 0xFFFFFFFF, "08x"), text)
        self.assertEqual(digest, hashlib.sha256(script.encode()).hexdigest())
        self.assertEqual(crc, format(zlib.crc32(script.encode()) & 0xFFFFFFFF, "08x"))
        self.assertNotIn(script, text)

    def test_request_envelope_rejects_empty_nul_or_oversized_script(self):
        for script in ("", "   ", "puts {a\x00b}", "x" * (256 * 1024 + 1)):
            with self.subTest(length=len(script)):
                with self.assertRaises(CaptureTclQueueError):
                    build_capture_request_envelope(
                        self.manifest.session_id,
                        self.manifest.startup_nonce,
                        str(uuid.uuid4()),
                        script,
                    )

    def test_result_envelope_allows_worst_case_utf8_within_transport_limit(self):
        request_id = str(uuid.uuid4())
        script_hash = hashlib.sha256(b"unicode").hexdigest()
        value = "😀" * 100000
        raw = build_capture_result_envelope(
            self.manifest.session_id,
            self.manifest.startup_nonce,
            request_id,
            script_hash,
            success=True,
            value=value,
            error_info=value,
        )
        # The Bridge limits each Tcl string to 100,000 characters. Four-byte
        # UTF-8 characters plus Base64 must still fit the RFS read preview.
        self.assertLess(len(raw), 1_100_000)

    def test_publish_command_uses_sha_check_and_atomic_no_overwrite_move(self):
        staging = ntpath.join(REMOTE_DIR, "requests", "id.tmp")
        target = ntpath.join(REMOTE_DIR, "requests", "id.req")
        digest = hashlib.sha256(b"request").hexdigest()
        command = build_publish_capture_request_command(staging, target, digest)
        self.assertIn("Get-FileHash", command)
        self.assertIn("[IO.File]::Move($source, $target)", command)
        self.assertIn("refusing overwrite", command)
        self.assertNotIn(staging, command)
        self.assertNotIn(target, command)
        self.assertIn(base64.b64encode(staging.encode()).decode(), command)
        self.assertIn(base64.b64encode(target.encode()).decode(), command)

    def test_submit_requires_explicit_user_authorization_without_remote_side_effects(self):
        with self.assertRaises(CaptureAuthorizationError):
            self.queue.submit("puts ready")
        self.assertEqual(self.client.filesystem_calls, [])
        self.assertEqual(self.client.shell_calls, [])
        self.assertEqual(self.session.state, SessionState.READY)

    def test_execute_uploads_verifies_publishes_and_correlates_result(self):
        result = self.queue.execute(
            "set result READY",
            user_authorized=True,
            timeout_seconds=2,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.value, "DONE")
        self.assertEqual(self.session.state, SessionState.READY)
        self.assertIsNone(self.manifest.active_request_id)
        actions = [action for action, _payload in self.client.filesystem_calls]
        self.assertEqual(actions.count("write"), 1)
        self.assertEqual(actions.count("read"), 2)
        self.assertEqual(len(self.client.shell_calls), 1)
        manifest = SessionManifest.load(self.manifest_path)
        self.assertEqual(manifest.state, SessionState.READY.value)
        self.assertIsNone(manifest.active_request_id)
        command = self.client.shell_calls[0][0]
        self.assertIn("[IO.File]::Move($source, $target)", command)
        self.assertNotIn("/api/v1/eda/", command)

    def test_execution_error_is_a_completed_request_and_returns_session_to_ready(self):
        request = self.queue.submit("error test", user_authorized=True)
        raw = build_capture_result_envelope(
            self.manifest.session_id,
            self.manifest.startup_nonce,
            request.request_id,
            request.script_sha256,
            success=False,
            value="invalid command",
            error_info="stack trace",
        )
        self.client.remote_files[request.result_path] = raw
        result = self.queue.wait_for_result(request.request_id, timeout_seconds=1)
        self.assertFalse(result.success)
        self.assertEqual(result.value, "invalid command")
        self.assertEqual(result.error_info, "stack trace")
        self.assertEqual(self.session.state, SessionState.READY)

    def test_duplicate_request_id_is_rejected_without_second_remote_write(self):
        request_id = str(uuid.uuid4())
        self.queue.execute("first", user_authorized=True, request_id=request_id)
        before = len(self.client.filesystem_calls)
        with self.assertRaises(CaptureTclQueueError):
            self.queue.submit("must not run twice", user_authorized=True, request_id=request_id)
        self.assertEqual(len(self.client.filesystem_calls), before)
        self.assertEqual(self.session.state, SessionState.READY)

    def test_publish_transport_loss_is_unknown_and_never_republished(self):
        self.client.fail_publish_after_move = True
        with self.assertRaises(CaptureTclOutcomeUnknown) as raised:
            self.queue.submit("puts once", user_authorized=True)
        self.assertEqual(self.session.state, SessionState.UNKNOWN)
        self.assertEqual(raised.exception.request_id, self.manifest.active_request_id)
        self.assertEqual(len(self.client.shell_calls), 1)
        self.assertEqual(
            sum(1 for action, _ in self.client.filesystem_calls if action == "write"),
            1,
        )
        # Positive result plus original owned-process evidence permits read-only
        # reconciliation; no second upload or Shell publish is performed.
        result = self.queue.reconcile(raised.exception.request_id)
        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertEqual(self.session.state, SessionState.READY)
        self.assertEqual(self.inspector.calls, 1)
        self.assertEqual(len(self.client.shell_calls), 1)

    def test_unknown_result_without_owned_process_evidence_stays_unknown(self):
        self.client.fail_publish_after_move = True
        with self.assertRaises(CaptureTclOutcomeUnknown) as raised:
            self.queue.submit("puts once", user_authorized=True)
        self.queue.inspector = FakeInspector(CaptureProcessDisposition.UNOWNED_INSTANCE)
        with self.assertRaises(CaptureTclOutcomeUnknown):
            self.queue.reconcile(raised.exception.request_id)
        self.assertEqual(self.session.state, SessionState.UNKNOWN)
        self.assertEqual(len(self.client.shell_calls), 1)

    def test_result_timeout_marks_unknown_and_does_not_resubmit(self):
        self.client.auto_result = False
        clock = Clock()
        request = self.queue.submit("slow command", user_authorized=True)
        self.queue.monotonic_fn = clock.monotonic
        self.queue.sleep_fn = clock.sleep
        with self.assertRaises(CaptureTclOutcomeUnknown):
            self.queue.wait_for_result(request.request_id, timeout_seconds=1.5)
        self.assertEqual(self.session.state, SessionState.UNKNOWN)
        self.assertEqual(len(self.client.shell_calls), 1)
        self.assertEqual(
            sum(1 for action, _ in self.client.filesystem_calls if action == "write"),
            1,
        )

    def test_mismatched_result_hash_is_rejected_and_session_becomes_unknown(self):
        self.client.auto_result = False
        request = self.queue.submit("verify hash", user_authorized=True)
        self.client.remote_files[request.result_path] = build_capture_result_envelope(
            self.manifest.session_id,
            self.manifest.startup_nonce,
            request.request_id,
            hashlib.sha256(b"other script").hexdigest(),
            success=True,
            value="wrong",
        )
        with self.assertRaises(CaptureTclOutcomeUnknown):
            self.queue.wait_for_result(request.request_id, timeout_seconds=1)
        self.assertEqual(self.session.state, SessionState.UNKNOWN)

    def test_unknown_reconciliation_missing_result_is_read_only(self):
        self.client.fail_publish_after_move = True
        with self.assertRaises(CaptureTclOutcomeUnknown) as raised:
            self.queue.submit("puts once", user_authorized=True)
        request_id = raised.exception.request_id
        result_path = ntpath.join(REMOTE_DIR, "results", request_id + ".result")
        self.client.remote_files.pop(result_path, None)
        writes_before = len(self.client.filesystem_calls)
        self.assertIsNone(self.queue.reconcile(request_id))
        self.assertEqual(len(self.client.filesystem_calls), writes_before + 1)
        self.assertEqual(self.client.filesystem_calls[-1][0], "read")
        self.assertEqual(self.session.state, SessionState.UNKNOWN)
        self.assertEqual(len(self.client.shell_calls), 1)


if __name__ == "__main__":
    unittest.main()
