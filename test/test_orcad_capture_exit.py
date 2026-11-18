# -*- coding: utf-8 -*-
"""M3.2.6 Capture Exit coordinator tests; no Cadence process is started."""
from __future__ import annotations

import base64
import hashlib
import sys
import tempfile
import uuid
import tkinter
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skill" / "orcad-capture" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from capture_exit import (  # noqa: E402
    PREPARE_MAGIC, SCHEDULE_MAGIC, CaptureExitCoordinator,
    CaptureExitError, CaptureExitOutcomeUnknown,
    build_exit_prepare_script, build_exit_schedule_script,
    parse_exit_prepare_report,
)
from capture_process import (  # noqa: E402
    CaptureProcessAssessment, CaptureProcessCandidate, CaptureProcessDisposition,
)
from capture_requests import build_capture_request_envelope  # noqa: E402
from capture_session import (  # noqa: E402
    CaptureAuthorizationError, CaptureSession, SessionManifest, SessionState,
)


PID = 7312
SESSION_ID = str(uuid.uuid4())
NONCE = "N" * 48
STARTED = "2026-11-18T10:00:00.000Z"
EXE = r"D:\Cadence\SPB_17.2\tools\bin\capture.exe"
VERSION = "17.2-2016"
TCL = "8.6.0"


def prepare_report(*, active=True):
    name = base64.b64encode("Board-A".encode()).decode() if active else ""
    return "\n".join([
        PREPARE_MAGIC, "session_id=" + SESSION_ID, "expected_pid=" + str(PID),
        "tcl_pid=" + str(PID), "tcl_version=" + TCL, "expected_tcl_version=" + TCL,
        "dbo_session_rc=0", "dbo_session_present=1", "active_design_rc=0",
        "active_design_present=" + ("1" if active else "0"),
        "active_design_name_rc=0", "active_design_name_b64=" + name,
    ])


class FakeQueue:
    def __init__(self, *, unknown_schedule=False):
        self.calls = []
        self.unknown_schedule = unknown_schedule

    def execute(self, script, **kwargs):
        self.calls.append((script, kwargs))
        if self.unknown_schedule and SCHEDULE_MAGIC in script:
            raise RuntimeError("transport lost after schedule publication")
        if PREPARE_MAGIC in script:
            value = prepare_report()
        elif SCHEDULE_MAGIC in script:
            value = "\n".join([
                SCHEDULE_MAGIC, "session_id=" + SESSION_ID, "pid=" + str(PID),
                "tcl_version=" + TCL, "exit_scheduled=1",
            ])
        else:
            raise AssertionError("unexpected Tcl script")
        return SimpleNamespace(success=True, value=value, request_id=kwargs["request_id"])


class FakeInspector:
    def __init__(self, *, exit_after=0, mismatch=False):
        self.calls = 0
        self.exit_after = exit_after
        self.mismatch = mismatch

    def inspect(self):
        self.calls += 1
        if self.calls > self.exit_after:
            return ()
        pid = PID + 1 if self.mismatch else PID
        return (CaptureProcessCandidate(
            pid=pid, executable_path=EXE, started_at_utc=STARTED,
            file_version=VERSION,
        ),)

    def inspect_session(self, manifest):
        candidates = self.inspect()
        if not candidates:
            return CaptureProcessAssessment(CaptureProcessDisposition.ORIGINAL_PROCESS_ABSENT, ())
        item = candidates[0]
        if item.pid == manifest.pid and not self.mismatch:
            return CaptureProcessAssessment(CaptureProcessDisposition.OWNED_INSTANCE, candidates, item)
        return CaptureProcessAssessment(CaptureProcessDisposition.PID_REUSED_OR_IDENTITY_MISMATCH, candidates)


class CaptureExitTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manifest_path = Path(self.temp.name) / "session.json"
        self.manifest = SessionManifest(
            session_id=SESSION_ID, startup_nonce=NONCE, state=SessionState.READY.value,
            capture_exe_path=EXE, cap_autoload_dir=r"D:\Cadence\SPB_17.2\tools\capture\tclscripts\capAutoLoad",
            expected_capture_version=VERSION, expected_tcl_version=TCL,
            capture_version=VERSION, tcl_version=TCL, pid=PID,
            process_started_at_utc=STARTED, remote_session_dir=r"C:\Temp\CaptureSession",
            bridge_sha256=hashlib.sha256(b"bridge").hexdigest(),
        )
        self.session = CaptureSession(self.manifest)
        self.queue = FakeQueue()
        self.inspector = FakeInspector(exit_after=2)
        self.coordinator = CaptureExitCoordinator(
            self.queue, self.inspector, self.session, self.manifest_path,
            sleep_fn=lambda _seconds: None,
            monotonic_fn=iter([0, 0, 0, 0, 0, 0, 0]).__next__,
        )

    def test_prepare_requires_authorization_without_side_effects(self):
        with self.assertRaises(CaptureAuthorizationError):
            self.coordinator.prepare_exit()
        self.assertEqual(self.queue.calls, [])
        self.assertFalse(self.coordinator.context_path.exists())

    def test_prepare_checks_tcl_pid_and_reports_active_design_read_only(self):
        report = self.coordinator.prepare_exit(user_authorized=True)
        self.assertTrue(report["active_design_present"])
        self.assertEqual(report["active_design_name"], "Board-A")
        self.assertTrue(self.coordinator.context_path.exists())
        self.assertEqual(self.session.state, SessionState.READY)

    def test_prepare_report_rejects_wrong_pid(self):
        value = prepare_report().replace("tcl_pid=" + str(PID), "tcl_pid=9999")
        with self.assertRaises(Exception):
            parse_exit_prepare_report(value, session_id=SESSION_ID, pid=PID, tcl_version=TCL)

    def test_schedule_requires_both_exit_and_design_confirmation(self):
        self.coordinator.prepare_exit(user_authorized=True)
        with self.assertRaises(CaptureAuthorizationError):
            self.coordinator.request_exit(user_authorized=True)
        with self.assertRaises(CaptureAuthorizationError):
            self.coordinator.request_exit(
                user_authorized=True, design_saved_or_no_changes_confirmed=False
            )
        self.assertEqual(len(self.queue.calls), 1)

    def test_exit_schedules_after_result_then_confirms_original_pid_absent(self):
        self.coordinator.prepare_exit(user_authorized=True)
        evidence = self.coordinator.request_exit(
            user_authorized=True, design_saved_or_no_changes_confirmed=True,
            timeout_seconds=5,
        )
        self.assertEqual(evidence.pid, PID)
        self.assertTrue(evidence.process_absent)
        self.assertEqual(self.session.state, SessionState.CLOSED)
        self.assertEqual(len(self.queue.calls), 2)
        self.assertIn("after 1000 [list Exit]", self.queue.calls[1][0])

    def test_ambiguous_schedule_is_unknown_and_never_retried(self):
        self.coordinator.prepare_exit(user_authorized=True)
        self.queue.unknown_schedule = True
        with self.assertRaises(CaptureExitOutcomeUnknown):
            self.coordinator.request_exit(
                user_authorized=True, design_saved_or_no_changes_confirmed=True
            )
        self.assertEqual(self.session.state, SessionState.UNKNOWN)
        with self.assertRaises(CaptureExitError):
            self.coordinator.request_exit(
                user_authorized=True, design_saved_or_no_changes_confirmed=True
            )
        self.assertEqual(len(self.queue.calls), 2)

    def test_pid_identity_mismatch_blocks_exit(self):
        self.inspector.mismatch = True
        with self.assertRaises(Exception):
            self.coordinator.prepare_exit(user_authorized=True)
        self.assertEqual(self.queue.calls, [])

    def test_exit_script_has_no_save_discard_or_force_kill_commands(self):
        script = build_exit_schedule_script(SESSION_ID, PID, TCL)
        for forbidden in ("SaveDesign", "SaveAs", "Discard", "taskkill", "Stop-Process"):
            self.assertNotIn(forbidden, script)



class CaptureExitBridgeOrderingTests(TestCase):
    """Run the real Tcl Bridge under Tcl 8.6 and prove result publication precedes Exit."""

    def test_bridge_publishes_schedule_result_before_delayed_exit_callback(self):
        scripts = Path(__file__).resolve().parents[1] / "skill" / "orcad-capture" / "assets" / "bridge"
        bridge_path = scripts / "capture_bridge.tcl"
        if not bridge_path.is_file():
            self.skipTest("Capture Tcl Bridge source is unavailable")
        try:
            tcl = tkinter.Tcl()
            if not tcl.eval("info patchlevel").startswith("8.6."):
                self.skipTest("Tcl 8.6 is required")
        except Exception as exc:
            self.skipTest("Tcl runtime is unavailable: {}".format(exc))

        with tempfile.TemporaryDirectory() as root:
            session_dir = Path(root)
            for name in ("requests", "running", "results"):
                (session_dir / name).mkdir()
            request_id = str(uuid.uuid4())
            envelope, _digest, _crc = build_capture_request_envelope(
                SESSION_ID, NONCE, request_id,
                build_exit_schedule_script(SESSION_ID, PID, TCL, delay_ms=250),
            )
            (session_dir / "requests" / (request_id + ".req")).write_bytes(envelope)

            tcl.call("source", str(bridge_path))
            tcl.call("set", "::test_result_dir", str(session_dir / "results"))
            tcl.call("set", "::test_request_id", request_id)
            tcl.eval("""
                set ::done ""
                set ::exit_result_present 0
                proc Exit {} {
                    set result_path [file join $::test_result_dir "$::test_request_id.result"]
                    set ::exit_result_present [file exists $result_path]
                    set ::done "exit_called"
                }
                after 3000 {
                    if {$::done eq ""} { set ::done "timeout" }
                }
            """)
            tcl.call(
                "::AirBridgeSkill::bootstrap", SESSION_ID, NONCE, str(session_dir),
                hashlib.sha256(b"bridge fixture").hexdigest(),
            )
            tcl.eval("vwait ::done")
            self.assertEqual(tcl.getvar("done"), "exit_called")
            self.assertEqual(tcl.getvar("exit_result_present"), "1")
            self.assertTrue((session_dir / "results" / (request_id + ".result")).is_file())


if __name__ == "__main__":
    import unittest
    unittest.main()
