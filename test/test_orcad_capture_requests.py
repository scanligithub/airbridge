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
    RFS_CHUNK_BYTES,
    RESULT_PARTS_MAGIC,
    build_capture_request_envelope,
    build_capture_result_envelope,
    build_create_request_staging_command,
    build_publish_capture_request_command,
)
from capture_session import (  # noqa: E402
    CaptureAuthorizationError,
    CaptureConfig,
    CaptureSession,
    CaptureStateTransitionError,
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
        self.write_chunk_lengths = []
        self.read_chunk_lengths = []

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
            mode = request.get("mode", "w")
            if mode == "a":
                if path not in self.remote_files:
                    return {"success": False, "error": "append target is absent"}
                self.remote_files[path] += raw
            else:
                if path in self.remote_files:
                    return {"success": False, "error": "destination exists"}
                self.remote_files[path] = raw
            self.write_chunk_lengths.append(len(raw))
            return {"success": True, "path": path, "size": len(self.remote_files[path])}
        if action == "read":
            raw = self.remote_files.get(path)
            if raw is None:
                return {"success": False, "error": "File not found"}
            max_preview = int(request.get("max_preview_bytes", 65536))
            preview = raw[:max_preview]
            self.read_chunk_lengths.append(len(preview))
            if request.get("as_base64") is True:
                return {
                    "success": True,
                    "path": path,
                    "size": len(raw),
                    "is_truncated": len(raw) > max_preview,
                    "encoding": "base64",
                    "content": base64.b64encode(preview).decode("ascii"),
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
        if "[IO.File]::Open($path, [IO.FileMode]::CreateNew" in command:
            if len(encoded_paths) != 1:
                raise AssertionError("staging creation must contain one Base64 path")
            staging = base64.b64decode(encoded_paths[0]).decode("utf-8")
            if staging in self.remote_files:
                raise AssertionError("CreateNew must not overwrite the staging file")
            self.remote_files[staging] = b""
            stdout = json.dumps({"created": True, "path": staging, "length": 0})
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
        self.assert_publish_command(command, encoded_paths)
        source, target = [
            base64.b64decode(item).decode("utf-8") for item in encoded_paths
        ]
        expected = re.search(r"\$expected = '([0-9a-f]{64})'", command).group(1)
        expected_length = int(re.search(r"\$expectedLength = \[int64\](\d+)", command).group(1))
        raw = self.remote_files[source]
        actual = hashlib.sha256(raw).hexdigest()
        if len(raw) != expected_length:
            raise AssertionError("publish command length mismatch in test double")
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
            result_envelope = build_capture_result_envelope(
                self.manifest.session_id,
                self.manifest.startup_nonce,
                request_id_from_file,
                fields["script_sha256"],
                success=True,
                value="DONE",
            )
            self.store_result_parts(result_path, result_envelope)

        if self.fail_publish_after_move:
            raise AirBridgeTransportError(
                "simulated lost publish response", request_id=request_id
            )
        stdout = json.dumps({
            "published": True,
            "path": target,
            "sha256": actual,
            "length": len(raw),
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

    def store_result_parts(self, result_path, envelope):
        lines = envelope.decode("ascii").splitlines()
        fields = dict(line.split("=", 1) for line in lines[1:])
        chunks = [
            envelope[offset:offset + RFS_CHUNK_BYTES]
            for offset in range(0, len(envelope), RFS_CHUNK_BYTES)
        ]
        for index, chunk in enumerate(chunks):
            self.remote_files[result_path + ".part{:04d}".format(index)] = chunk
        manifest = "\n".join([
            RESULT_PARTS_MAGIC,
            "session_id=" + fields["session_id"],
            "startup_nonce=" + fields["startup_nonce"],
            "request_id=" + fields["request_id"],
            "part_count=" + str(len(chunks)),
            "total_length=" + str(len(envelope)),
            "envelope_crc32=" + format(zlib.crc32(envelope) & 0xFFFFFFFF, "08x"),
            "chunk_size=" + str(RFS_CHUNK_BYTES),
            "",
        ]).encode("ascii")
        self.remote_files[result_path] = manifest

    @staticmethod
    def assert_publish_command(command, encoded_paths):
        if "[IO.File]::Move($source, $target)" not in command:
            raise AssertionError("publication must use atomic File.Move")
        if (
            "Get-FileHash" not in command
            or "$actual -ne $expected" not in command
            or "$actualLength -ne $expectedLength" not in command
        ):
            raise AssertionError("publication must verify complete length and SHA-256")
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
        create_command = build_create_request_staging_command(staging)
        self.assertIn("[IO.FileMode]::CreateNew", create_command)
        self.assertIn("[IO.FileShare]::None", create_command)
        self.assertNotIn(staging, create_command)
        command = build_publish_capture_request_command(staging, target, digest, 123)
        self.assertIn("Get-FileHash", command)
        self.assertIn("[IO.File]::Move($source, $target)", command)
        self.assertIn("refusing overwrite", command)
        self.assertNotIn(staging, command)
        self.assertNotIn(target, command)
        self.assertIn(base64.b64encode(staging.encode()).decode(), command)
        self.assertIn(base64.b64encode(target.encode()).decode(), command)

    def test_existing_staging_path_is_never_overwritten(self):
        request_id = str(uuid.uuid4())
        staging_path = ntpath.join(REMOTE_DIR, "requests", request_id + ".tmp")
        self.client.remote_files[staging_path] = b"pre-existing"
        with self.assertRaises(CaptureTclOutcomeUnknown):
            self.queue.submit(
                "must not overwrite",
                user_authorized=True,
                request_id=request_id,
            )
        self.assertEqual(self.client.remote_files[staging_path], b"pre-existing")
        self.assertEqual(
            sum(1 for action, _payload in self.client.filesystem_calls if action == "write"),
            0,
        )
        self.assertEqual(len(self.client.shell_calls), 1)
        self.assertEqual(self.session.state, SessionState.UNKNOWN)

    def test_submit_uses_shell_create_new_then_rfs_append_only(self):
        request = self.queue.submit("set safe 1", user_authorized=True)
        self.assertIn("[IO.FileMode]::CreateNew", self.client.shell_calls[0][0])
        self.assertTrue(all(payload.get("mode") == "a" for action, payload in self.client.filesystem_calls if action == "write"))
        self.assertTrue(all(action == "write" for action, _payload in self.client.filesystem_calls))
        self.assertTrue(request.request_path.endswith(request.request_id + ".req"))

    def test_submit_requires_explicit_user_authorization_without_remote_side_effects(self):
        with self.assertRaises(CaptureAuthorizationError):
            self.queue.submit("puts ready")
        self.assertEqual(self.client.filesystem_calls, [])
        self.assertEqual(self.client.shell_calls, [])
        self.assertEqual(self.session.state, SessionState.READY)

    def test_unresolved_exit_intent_blocks_unrelated_tcl_submission(self):
        context_path = self.manifest_path.with_name(self.manifest_path.name + ".exit.json")
        schedule_request_id = str(uuid.uuid4())
        context_path.write_text(
            json.dumps({
                "schema_version": 1,
                "session_id": self.manifest.session_id,
                "state": "exit_scheduling",
                "schedule_request_id": schedule_request_id,
            }),
            encoding="utf-8",
        )

        with self.assertRaises(CaptureStateTransitionError):
            self.queue.submit("unrelated Tcl operation", user_authorized=True)

        self.assertEqual(self.session.state, SessionState.READY)
        self.assertEqual(self.client.shell_calls, [])
        self.assertEqual(self.client.filesystem_calls, [])

    def test_only_original_exit_request_id_can_pass_pending_intent_gate(self):
        context_path = self.manifest_path.with_name(self.manifest_path.name + ".exit.json")
        schedule_request_id = str(uuid.uuid4())
        context_path.write_text(
            json.dumps({
                "schema_version": 1,
                "session_id": self.manifest.session_id,
                "state": "exit_scheduling",
                "schedule_request_id": schedule_request_id,
            }),
            encoding="utf-8",
        )

        result = self.queue.execute(
            "after 1000 [list Exit]",
            user_authorized=True,
            request_id=schedule_request_id,
            exit_intent_request_id=schedule_request_id,
            timeout_seconds=1,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.request_id, schedule_request_id)
        self.assertEqual(self.session.state, SessionState.READY)

        another_request_id = str(uuid.uuid4())
        with self.assertRaises(CaptureStateTransitionError):
            self.queue.submit(
                "second Exit or unrelated operation",
                user_authorized=True,
                request_id=another_request_id,
                exit_intent_request_id=another_request_id,
            )
        self.assertEqual(len(self.client.shell_calls), 2)

    def test_read_result_is_read_only_and_does_not_require_live_session_state(self):
        request = self.queue.submit("read original result", user_authorized=True)
        self.session.mark_unknown(
            "TEST_UNKNOWN",
            "test read-only result access while Session is UNKNOWN",
        )
        self.manifest.save(self.manifest_path)
        shell_calls_before = len(self.client.shell_calls)
        filesystem_calls_before = len(self.client.filesystem_calls)

        result = self.queue.read_result(request.request_id)

        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertEqual(result.request_id, request.request_id)
        self.assertEqual(self.session.state, SessionState.UNKNOWN)
        self.assertEqual(self.manifest.active_request_id, request.request_id)
        self.assertEqual(len(self.client.shell_calls), shell_calls_before)
        new_actions = [
            action for action, _payload in self.client.filesystem_calls[filesystem_calls_before:]
        ]
        self.assertTrue(new_actions)
        self.assertTrue(all(action == "read" for action in new_actions))

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
        # Small request occupies one 16 KiB-or-smaller RFS frame; result
        # retrieval consists of one manifest plus one result part.
        self.assertEqual(actions.count("read"), 2)
        self.assertEqual(len(self.client.shell_calls), 2)
        manifest = SessionManifest.load(self.manifest_path)
        self.assertEqual(manifest.state, SessionState.READY.value)
        self.assertIsNone(manifest.active_request_id)
        command = self.client.shell_calls[1][0]
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
        self.client.store_result_parts(request.result_path, raw)
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
        self.assertEqual(len(self.client.shell_calls), 2)
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
        self.assertEqual(len(self.client.shell_calls), 2)

    def test_unknown_result_without_owned_process_evidence_stays_unknown(self):
        self.client.fail_publish_after_move = True
        with self.assertRaises(CaptureTclOutcomeUnknown) as raised:
            self.queue.submit("puts once", user_authorized=True)
        self.queue.inspector = FakeInspector(CaptureProcessDisposition.UNOWNED_INSTANCE)
        with self.assertRaises(CaptureTclOutcomeUnknown):
            self.queue.reconcile(raised.exception.request_id)
        self.assertEqual(self.session.state, SessionState.UNKNOWN)
        self.assertEqual(len(self.client.shell_calls), 2)

    def test_rfs_read_rejects_a_different_remote_path(self):
        remote_path = ntpath.join(REMOTE_DIR, "results", "expected.result")
        self.client.remote_files[remote_path] = b"test"
        original = self.client.filesystem

        def return_wrong_path(action, payload=None, **kwargs):
            response = original(action, payload, **kwargs)
            if action == "read" and response.get("success") is True:
                response["path"] = ntpath.join(REMOTE_DIR, "results", "other.result")
            return response

        self.client.filesystem = return_wrong_path
        with self.assertRaises(CaptureTclQueueError):
            self.queue._read_remote_bytes(remote_path)

    def test_rfs_read_rejects_non_integer_or_mismatched_declared_size(self):
        remote_path = ntpath.join(REMOTE_DIR, "results", "expected.result")
        self.client.remote_files[remote_path] = b"test"
        original = self.client.filesystem

        def return_bad_size(action, payload=None, **kwargs):
            response = original(action, payload, **kwargs)
            if action == "read" and response.get("success") is True:
                response["size"] = "not-an-integer"
            return response

        self.client.filesystem = return_bad_size
        with self.assertRaises(CaptureTclQueueError):
            self.queue._read_remote_bytes(remote_path)

    def test_large_request_is_uploaded_as_16k_chunks_before_hash_publish(self):
        script = 'set large "' + ("x" * 40000) + '"'
        expected_envelope, _digest, _crc = build_capture_request_envelope(
            self.manifest.session_id,
            self.manifest.startup_nonce,
            str(uuid.uuid4()),
            script,
        )
        request = self.queue.submit(script, user_authorized=True)
        uploaded = self.client.remote_files[request.request_path]
        self.assertEqual(len(uploaded), len(expected_envelope))
        self.assertEqual(
            hashlib.sha256(uploaded).hexdigest(),
            hashlib.sha256(build_capture_request_envelope(
                self.manifest.session_id,
                self.manifest.startup_nonce,
                request.request_id,
                script,
            )[0]).hexdigest(),
        )
        self.assertGreater(len(self.client.write_chunk_lengths), 1)
        self.assertLessEqual(max(self.client.write_chunk_lengths), RFS_CHUNK_BYTES)
        self.assertIn("$expectedLength = [int64]", self.client.shell_calls[1][0])

    def test_large_result_is_reassembled_from_16k_parts(self):
        self.client.auto_result = False
        request = self.queue.submit("large result", user_authorized=True)
        result_bytes = build_capture_result_envelope(
            self.manifest.session_id,
            self.manifest.startup_nonce,
            request.request_id,
            request.script_sha256,
            success=True,
            value="R" * 50000,
        )
        self.client.store_result_parts(request.result_path, result_bytes)
        result = self.queue.wait_for_result(request.request_id, timeout_seconds=1)
        self.assertTrue(result.success)
        self.assertEqual(result.value, "R" * 50000)
        self.assertGreater(len(self.client.read_chunk_lengths), 1)
        self.assertLessEqual(max(self.client.read_chunk_lengths), RFS_CHUNK_BYTES)

    def test_result_parts_manifest_missing_part_marks_unknown(self):
        self.client.auto_result = False
        request = self.queue.submit("missing part", user_authorized=True)
        result_bytes = build_capture_result_envelope(
            self.manifest.session_id,
            self.manifest.startup_nonce,
            request.request_id,
            request.script_sha256,
            success=True,
            value="P" * 50000,
        )
        self.client.store_result_parts(request.result_path, result_bytes)
        self.client.remote_files.pop(request.result_path + ".part0001")
        with self.assertRaises(CaptureTclOutcomeUnknown):
            self.queue.wait_for_result(request.request_id, timeout_seconds=1)
        self.assertEqual(self.session.state, SessionState.UNKNOWN)

    def test_result_parts_manifest_crc_detects_corrupt_assembly(self):
        self.client.auto_result = False
        request = self.queue.submit("bad part crc", user_authorized=True)
        result_bytes = build_capture_result_envelope(
            self.manifest.session_id,
            self.manifest.startup_nonce,
            request.request_id,
            request.script_sha256,
            success=True,
            value="C" * 50000,
        )
        self.client.store_result_parts(request.result_path, result_bytes)
        manifest = self.client.remote_files[request.result_path].decode("ascii")
        manifest = manifest.replace(
            "envelope_crc32=" + format(zlib.crc32(result_bytes) & 0xFFFFFFFF, "08x"),
            "envelope_crc32=00000000",
        )
        self.client.remote_files[request.result_path] = manifest.encode("ascii")
        with self.assertRaises(CaptureTclOutcomeUnknown):
            self.queue.wait_for_result(request.request_id, timeout_seconds=1)
        self.assertEqual(self.session.state, SessionState.UNKNOWN)

    def test_result_timeout_marks_unknown_and_does_not_resubmit(self):
        self.client.auto_result = False
        clock = Clock()
        request = self.queue.submit("slow command", user_authorized=True)
        self.queue.monotonic_fn = clock.monotonic
        self.queue.sleep_fn = clock.sleep
        with self.assertRaises(CaptureTclOutcomeUnknown):
            self.queue.wait_for_result(request.request_id, timeout_seconds=1.5)
        self.assertEqual(self.session.state, SessionState.UNKNOWN)
        self.assertEqual(len(self.client.shell_calls), 2)
        self.assertEqual(
            sum(1 for action, _ in self.client.filesystem_calls if action == "write"),
            1,
        )

    def test_mismatched_result_hash_is_rejected_and_session_becomes_unknown(self):
        self.client.auto_result = False
        request = self.queue.submit("verify hash", user_authorized=True)
        self.client.store_result_parts(
            request.result_path,
            build_capture_result_envelope(
                self.manifest.session_id,
                self.manifest.startup_nonce,
                request.request_id,
                hashlib.sha256(b"other script").hexdigest(),
                success=True,
                value="wrong",
            ),
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
        self.assertEqual(len(self.client.shell_calls), 2)


if __name__ == "__main__":
    unittest.main()
