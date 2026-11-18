# -*- coding: utf-8 -*-
"""Execute the actual Capture Tcl Bridge under Tcl 8.6 via tkinter.Tcl()."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import TestCase, skipIf

try:
    import tkinter
except ImportError:  # pragma: no cover - some minimal Python builds omit Tcl/Tk
    tkinter = None

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "skill"
    / "orcad-capture"
    / "scripts"
)
BRIDGE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skill"
    / "orcad-capture"
    / "assets"
    / "bridge"
    / "capture_bridge.tcl"
)
sys.path.insert(0, str(SCRIPTS_DIR))

from capture_requests import CaptureTclQueue, build_capture_request_envelope  # noqa: E402
from capture_session import CaptureSession, SessionManifest  # noqa: E402


@skipIf(tkinter is None, "Python was built without Tcl/Tk")
class CaptureBridgeTclRuntimeTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        try:
            self.tcl = tkinter.Tcl()
            patchlevel = self.tcl.eval("info patchlevel")
        except Exception as exc:
            self.skipTest("Tcl runtime is unavailable: {}".format(exc))
        if not patchlevel.startswith("8.6."):
            self.skipTest("Capture Bridge validation requires Tcl 8.6")

        self.session_dir = Path(self.temp.name) / "capture-session"
        self.request_dir = self.session_dir / "requests"
        self.running_dir = self.session_dir / "running"
        self.result_dir = self.session_dir / "results"
        for path in (
            self.session_dir,
            self.request_dir,
            self.running_dir,
            self.result_dir,
            self.session_dir / "logs",
        ):
            path.mkdir(parents=True, exist_ok=True)

        self.session_id = str(uuid.uuid4())
        self.startup_nonce = "T" * 48
        self.bridge_hash = hashlib.sha256(BRIDGE_PATH.read_bytes()).hexdigest()
        self.tcl.call("source", str(BRIDGE_PATH))
        ready = self.tcl.call(
            "::AirBridgeSkill::bootstrap",
            self.session_id,
            self.startup_nonce,
            str(self.session_dir),
            self.bridge_hash,
        )
        self.assertEqual(self.tcl.call("dict", "get", ready, "state"), "ready")

        ready_payload = json.loads(
            (self.session_dir / "bridge.ready").read_text(encoding="utf-8")
        )
        self.assertEqual(ready_payload["session_id"], self.session_id)
        self.assertEqual(ready_payload["startup_nonce"], self.startup_nonce)
        self.assertEqual(ready_payload["bridge_sha256"], self.bridge_hash)

        self.manifest = SessionManifest(
            session_id=self.session_id,
            startup_nonce=self.startup_nonce,
        )
        self.session = CaptureSession(self.manifest)

    def tearDown(self):
        try:
            after_id = self.tcl.getvar("::AirBridgeSkill::poll_after_id")
            if after_id:
                self.tcl.call("after", "cancel", after_id)
        except Exception:
            pass

    def _publish_test_request(self, script, request_id=None):
        rid = str(uuid.UUID(request_id)) if request_id else str(uuid.uuid4())
        envelope, script_sha256, _crc = build_capture_request_envelope(
            self.session_id,
            self.startup_nonce,
            rid,
            script,
        )
        (self.request_dir / (rid + ".req")).write_bytes(envelope)
        return rid, script_sha256

    def _read_result(self, request_id, script_sha256):
        result_path = self.result_dir / (request_id + ".result")
        self.assertTrue(result_path.exists())
        queue = CaptureTclQueue.__new__(CaptureTclQueue)
        queue.session = self.session
        return queue._parse_result(
            result_path.read_bytes(),
            request_id,
            {"script_sha256": script_sha256},
        )

    def _poll_once(self):
        self.tcl.call("::AirBridgeSkill::poll")

    def test_real_tcl_bridge_executes_unicode_script_and_result_roundtrips(self):
        rid, digest = self._publish_test_request(
            'set ::capture_probe "Capture Tcl 8.6 / 中文 / 😀"'
        )
        self._poll_once()

        result = self._read_result(rid, digest)
        self.assertTrue(result.success)
        self.assertEqual(result.value, "Capture Tcl 8.6 / 中文 / 😀")
        self.assertEqual(result.error_info, "")
        self.assertFalse(result.result_truncated)
        self.assertFalse(result.error_info_truncated)
        self.assertFalse((self.running_dir / (rid + ".running")).exists())
        self.assertFalse((self.request_dir / (rid + ".req")).exists())

    def test_real_tcl_bridge_marks_large_result_and_error_truncation(self):
        rid, digest = self._publish_test_request(
            'error [string repeat "x" 100001]'
        )
        self._poll_once()

        result = self._read_result(rid, digest)
        self.assertFalse(result.success)
        self.assertTrue(result.result_truncated)
        self.assertTrue(result.error_info_truncated)
        self.assertEqual(len(result.value), 100000)
        self.assertEqual(len(result.error_info), 100000)

    def test_real_tcl_bridge_reports_script_errors_with_request_identity(self):
        rid, digest = self._publish_test_request('error "intentional Tcl failure"')
        self._poll_once()

        result = self._read_result(rid, digest)
        self.assertFalse(result.success)
        self.assertIn("intentional Tcl failure", result.value)
        self.assertIn("intentional Tcl failure", result.error_info)

    def test_real_tcl_bridge_serializes_requests_one_at_a_time(self):
        first_id, first_digest = self._publish_test_request("incr ::capture_counter")
        second_id, second_digest = self._publish_test_request("incr ::capture_counter")
        self._poll_once()

        completed = [
            rid for rid in (first_id, second_id)
            if (self.result_dir / (rid + ".result")).exists()
        ]
        pending = [
            rid for rid in (first_id, second_id)
            if (self.request_dir / (rid + ".req")).exists()
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(len(pending), 1)
        completed_digest = first_digest if completed[0] == first_id else second_digest
        self.assertEqual(self._read_result(completed[0], completed_digest).value, "1")

        self._poll_once()
        self.assertTrue((self.result_dir / (first_id + ".result")).exists())
        self.assertTrue((self.result_dir / (second_id + ".result")).exists())
        first_value = self._read_result(first_id, first_digest).value
        second_value = self._read_result(second_id, second_digest).value
        self.assertEqual({first_value, second_value}, {"1", "2"})

    def test_real_tcl_bridge_does_not_execute_duplicate_request_id(self):
        request_id = str(uuid.uuid4())
        rid, digest = self._publish_test_request(
            "incr ::capture_duplicate_counter",
            request_id=request_id,
        )
        self._poll_once()
        self.assertEqual(self._read_result(rid, digest).value, "1")

        # Re-introduce the same request bytes after a result already exists.
        # The Bridge must discard the duplicate rather than execute it again.
        envelope, _digest, _crc = build_capture_request_envelope(
            self.session_id,
            self.startup_nonce,
            rid,
            "incr ::capture_duplicate_counter",
        )
        (self.request_dir / (rid + ".req")).write_bytes(envelope)
        self._poll_once()

        self.assertEqual(self._read_result(rid, digest).value, "1")
        self.assertFalse((self.running_dir / (rid + ".running")).exists())


if __name__ == "__main__":
    import unittest
    unittest.main()
