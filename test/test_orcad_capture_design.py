# -*- coding: utf-8 -*-
"""M3.2.5 OPJ open and Dbo read-only tests."""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import TestCase, skipIf

try:
    import tkinter
except ImportError:  # pragma: no cover
    tkinter = None

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "skill"
    / "orcad-capture"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

from capture_design import (  # noqa: E402
    ACTIVE_MAGIC,
    INVENTORY_MAGIC,
    OPEN_MAGIC,
    CaptureActiveDesign,
    CaptureDboProbeError,
    CaptureDesignCoordinator,
    CaptureDesignError,
    CaptureDesignOutcomeUnknown,
    CaptureDboInventory,
    build_active_design_probe_script,
    build_dbo_session_inventory_script,
    build_open_project_script,
    build_remote_opj_preflight_command,
    parse_active_design_report,
    parse_dbo_session_inventory,
    parse_open_project_report,
    validate_remote_opj_path,
)
from capture_requests import CaptureTclOutcomeUnknown, CaptureTclResult  # noqa: E402
from capture_session import (  # noqa: E402
    CaptureAuthorizationError,
    CaptureSession,
    SessionManifest,
    SessionState,
)


CAPTURE_EXE = r"D:\Cadence\SPB_17.2\tools\bin\capture.exe"
AUTOLOAD = r"D:\Cadence\SPB_17.2\tools\capture\tclscripts\capAutoLoad"
OPJ = r"E:\_airbridge_temp\test_design\test_design.opj"


def _b64(value):
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _active_report(*, active=False, name="TestDesign", same=True):
    return "\n".join([
        ACTIVE_MAGIC,
        "tcl_pid=1234",
        "tcl_version=8.6.0",
        "session_present=1",
        "session_wrapper_rc=0",
        "active_design_rc=0",
        "active_design_present=" + ("1" if active else "0"),
        "active_design_name_rc=0",
        "active_design_name_b64=" + (_b64(name) if active else ""),
        "project_manager_rc=0",
        "project_manager_present=" + ("1" if active else "0"),
        "project_manager_name_rc=0",
        "project_manager_name_b64=" + (_b64(name) if active else ""),
        "same_design_object=" + ("1" if active and same else "0"),
    ])


def _open_report(path, *, open_rc=0, active=True, same=True):
    return "\n".join([
        OPEN_MAGIC,
        "requested_path_b64=" + _b64(path),
        "open_rc=" + str(open_rc),
        "open_result_b64=" + _b64("OPENED" if open_rc == 0 else "open failed"),
        "tcl_pid=1234",
        "tcl_version=8.6.0",
        "session_present=1",
        "session_wrapper_rc=0",
        "active_design_rc=0",
        "active_design_present=" + ("1" if active else "0"),
        "active_design_name_rc=0",
        "active_design_name_b64=" + (_b64("TestDesign") if active else ""),
        "project_manager_rc=0",
        "project_manager_present=" + ("1" if active else "0"),
        "project_manager_name_rc=0",
        "project_manager_name_b64=" + (_b64("TestDesign") if active else ""),
        "same_design_object=" + ("1" if active and same else "0"),
    ])


def _queue_result(value, request_id=None, success=True):
    return CaptureTclResult(
        request_id=request_id or str(uuid.uuid4()),
        script_sha256=hashlib.sha256(value.encode("utf-8")).hexdigest(),
        success=success,
        value=value,
        error_info="" if success else "script failed",
    )


class FakeClient:
    def __init__(self):
        self.shell_calls = []

    def shell_execute(self, command, **kwargs):
        self.shell_calls.append((command, kwargs))
        token = command.split("FromBase64String('", 1)[1].split("')", 1)[0]
        path = base64.b64decode(token).decode("utf-8")
        payload = json.dumps({
            "exists": True,
            "path": path,
            "length": 4096,
            "last_write_utc": "2026-11-18T08:00:00.0000000Z",
        })
        return {
            "request_id": kwargs["request_id"],
            "state": "completed",
            "result": {
                "success": True,
                "exit_code": 0,
                "stdout": payload,
                "stderr": "",
            },
        }


class FakeQueue:
    def __init__(self, *, active_before=False, unknown_open=False, same_object=True):
        self.active_before = active_before
        self.unknown_open = unknown_open
        self.same_object = same_object
        self.calls = []
        self.reconcile_result = None

    def execute(self, script, **kwargs):
        self.calls.append((script, kwargs))
        if script.startswith("set __lines [list \"AB_CAPTURE_ACTIVE_DESIGN_V1\""):
            return _queue_result(_active_report(active=self.active_before))
        if script.startswith("set __path [encoding convertfrom utf-8"):
            request_id = kwargs["request_id"]
            if self.unknown_open:
                raise CaptureTclOutcomeUnknown("lost request result", request_id)
            return _queue_result(
                _open_report(OPJ, same=self.same_object),
                request_id=request_id,
            )
        raise AssertionError("unknown Tcl script requested")

    def reconcile(self, request_id):
        return self.reconcile_result


class CaptureDesignCoordinatorTests(TestCase):
    def setUp(self):
        config = None
        self.manifest = SessionManifest(
            session_id=str(uuid.uuid4()),
            startup_nonce="S" * 48,
            state=SessionState.READY.value,
            capture_exe_path=CAPTURE_EXE,
            cap_autoload_dir=AUTOLOAD,
            expected_capture_version="17.2-2016",
            expected_tcl_version="8.6.0",
            capture_version="17.2-2016",
            tcl_version="8.6.0",
            pid=1234,
            process_started_at_utc="2026-11-18T08:00:00.000000Z",
            remote_session_dir=r"C:\Users\Agent\AppData\Local\AirBridgeSkills\Capture\sessions\test",
            bridge_sha256=hashlib.sha256(b"bridge").hexdigest(),
        )
        self.session = CaptureSession(self.manifest, config)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manifest_path = Path(self.temp.name) / "session.json"
        self.manifest.save(self.manifest_path)
        self.client = FakeClient()
        self.queue = FakeQueue()
        self.coordinator = CaptureDesignCoordinator(
            self.client, self.queue, self.session, self.manifest_path
        )

    def test_open_requires_explicit_authorization_before_shell_or_tcl(self):
        with self.assertRaises(CaptureAuthorizationError):
            self.coordinator.open_project(OPJ)
        self.assertEqual(self.client.shell_calls, [])
        self.assertEqual(self.queue.calls, [])

    def test_only_absolute_opj_paths_are_supported(self):
        for value in ("test_design.opj", r"E:\design.dsn", ""):
            with self.subTest(path=value):
                with self.assertRaises(CaptureDesignError):
                    validate_remote_opj_path(value)
        with self.assertRaises(CaptureDesignError):
            self.coordinator.open_project(r"E:\design.dsn", user_authorized=True)
        self.assertEqual(self.client.shell_calls, [])
        self.assertEqual(self.queue.calls, [])

    def test_preflight_command_uses_base64_path_and_only_reads_file_metadata(self):
        command = build_remote_opj_preflight_command(OPJ)
        self.assertNotIn(OPJ, command)
        self.assertIn(base64.b64encode(OPJ.encode()).decode(), command)
        self.assertIn("Get-Item -LiteralPath", command)
        self.assertIn("PSIsContainer", command)
        self.assertNotIn("Remove-Item", command)
        self.assertNotIn("Copy-Item", command)

    def test_open_checks_existing_active_design_and_refuses_to_switch(self):
        self.queue.active_before = True
        with self.assertRaises(CaptureDesignError):
            self.coordinator.open_project(OPJ, user_authorized=True)
        self.assertEqual(len(self.client.shell_calls), 0)
        self.assertEqual(len(self.queue.calls), 1)
        self.assertFalse(self.coordinator.context_path.exists())

    def test_open_project_verifies_dbo_and_project_manager_identity(self):
        result = self.coordinator.open_project(OPJ, user_authorized=True)
        self.assertEqual(result.opj_path, OPJ)
        self.assertEqual(result.active_design_name, "TestDesign")
        self.assertEqual(result.project_manager_name, "TestDesign")
        self.assertTrue(result.same_design_object)
        self.assertEqual(len(self.client.shell_calls), 1)
        self.assertEqual(len(self.queue.calls), 2)
        self.assertIn("Open $__path", self.queue.calls[1][0])
        context = json.loads(self.coordinator.context_path.read_text(encoding="utf-8"))
        self.assertEqual(context["state"], "opened")
        self.assertEqual(context["request_id"], result.request_id)

    def test_open_refuses_a_second_design_in_same_session(self):
        self.coordinator.open_project(OPJ, user_authorized=True)
        shell_calls = len(self.client.shell_calls)
        queue_calls = len(self.queue.calls)
        with self.assertRaises(CaptureDesignError):
            self.coordinator.open_project(OPJ, user_authorized=True)
        self.assertEqual(len(self.client.shell_calls), shell_calls)
        self.assertEqual(len(self.queue.calls), queue_calls)

    def test_identity_mismatch_is_not_reported_as_open_success(self):
        self.queue.same_object = False
        with self.assertRaises(CaptureDesignError):
            self.coordinator.open_project(OPJ, user_authorized=True)
        context = json.loads(self.coordinator.context_path.read_text(encoding="utf-8"))
        self.assertEqual(context["state"], "verification_failed")

    def test_unknown_open_persists_request_id_and_blocks_duplicate_open(self):
        self.queue.unknown_open = True
        with self.assertRaises(CaptureDesignOutcomeUnknown) as raised:
            self.coordinator.open_project(OPJ, user_authorized=True)
        context = json.loads(self.coordinator.context_path.read_text(encoding="utf-8"))
        self.assertEqual(context["state"], "unknown")
        self.assertEqual(context["request_id"], raised.exception.request_id)
        queue_calls = len(self.queue.calls)
        with self.assertRaises(CaptureDesignError):
            self.coordinator.open_project(OPJ, user_authorized=True)
        self.assertEqual(len(self.queue.calls), queue_calls)


@skipIf(tkinter is None, "Python was built without Tcl/Tk")
class CaptureDesignTclRuntimeTests(TestCase):
    def setUp(self):
        try:
            self.tcl = tkinter.Tcl()
            patchlevel = self.tcl.eval("info patchlevel")
        except Exception as exc:
            self.skipTest("Tcl runtime is unavailable: {}".format(exc))
        if not patchlevel.startswith("8.6."):
            self.skipTest("Capture Tcl scripts require Tcl 8.6")
        self.tcl.eval(r"""
set ::DboSession_s_pDboSession mockSession
set ::mockCStringValue ""
set ::mockActiveDesign mockDesignA
set ::mockDesignIndex 0
set ::mockLibraryIndex 0
set ::mockOpenPath ""
proc DboSession {args} { return "" }
proc DboState {} { return mockDboState }
proc mockDboState {args} { return "" }
proc DboTclHelper_sMakeCString {} { return mockCString }
proc DboTclHelper_sGetConstCharPtr {obj} { return $::mockCStringValue }
proc mockDesignA {method args} {
    if {$method eq "GetName"} { set ::mockCStringValue "Design A"; return "" }
    error "unexpected Design A method: $method"
}
proc mockDesignB {method args} {
    if {$method eq "GetName"} { set ::mockCStringValue "Design B"; return "" }
    error "unexpected Design B method: $method"
}
proc mockLibraryA {method args} {
    if {$method eq "GetName"} { set ::mockCStringValue "Library A"; return "" }
    error "unexpected Library A method: $method"
}
proc mockDesignIter {method args} {
    if {$method ne "NextDesign"} { error "unexpected design iterator method" }
    incr ::mockDesignIndex
    if {$::mockDesignIndex == 1} { return mockDesignA }
    if {$::mockDesignIndex == 2} { return mockDesignB }
    return NULL
}
proc mockLibraryIter {method args} {
    if {$method ne "NextLib"} { error "unexpected library iterator method" }
    incr ::mockLibraryIndex
    if {$::mockLibraryIndex == 1} { return mockLibraryA }
    return NULL
}
proc mockSession {method args} {
    switch -- $method {
        GetActiveDesign { return $::mockActiveDesign }
        NewDesignsIter { set ::mockDesignIndex 0; return mockDesignIter }
        NewLibsIter { set ::mockLibraryIndex 0; return mockLibraryIter }
        default { error "unexpected session method: $method" }
    }
}
proc delete_DboSessionDesignsIter {iter} { return "" }
proc delete_DboSessionLibsIter {iter} { return "" }
proc GetActivePMDesign {} {
    if {$::mockActiveDesign eq "NULL"} {
        error "GetActivePMDesign must not be queried when no Dbo Design is active"
    }
    return $::mockActiveDesign
}
proc Open {path} {
    set ::mockOpenPath $path
    set ::mockActiveDesign mockDesignA
    return "OPENED"
}
""")

    def test_active_design_probe_reads_dbo_and_project_manager_same_object(self):
        value = self.tcl.eval(build_active_design_probe_script())
        report = parse_active_design_report(value)
        self.assertTrue(report.session_present)
        self.assertEqual(report.session_wrapper_rc, 0)
        self.assertTrue(report.active_design_present)
        self.assertEqual(report.active_design_name, "Design A")
        self.assertEqual(report.project_manager_name, "Design A")
        self.assertTrue(report.same_design_object)
        self.assertTrue(report.verified)

    def test_dbo_inventory_script_is_read_only_and_does_not_load_designs_or_libraries(self):
        script = build_dbo_session_inventory_script()
        self.assertIn("NewDesignsIter", script)
        self.assertIn("NewLibsIter", script)
        self.assertIn("delete_DboSessionDesignsIter", script)
        self.assertIn("delete_DboSessionLibsIter", script)
        self.assertIn("-delete", script)
        self.assertNotIn("GetDesign ", script)
        self.assertNotIn("GetLib ", script)
        self.assertNotIn("SaveDesign", script)
        self.assertNotIn("SetEffectiveProp", script)
        self.assertNotIn("RemoveDesign", script)

    def test_start_page_active_design_probe_does_not_call_project_manager_api(self):
        self.tcl.setvar("mockActiveDesign", "NULL")
        value = self.tcl.eval(build_active_design_probe_script())
        report = parse_active_design_report(value)
        self.assertFalse(report.active_design_present)
        self.assertEqual(report.active_design_rc, 0)
        self.assertEqual(report.project_manager_rc, 0)
        self.assertFalse(report.project_manager_present)
        self.assertFalse(report.verified)

    def test_session_inventory_enumerates_only_loaded_designs_and_libraries(self):
        value = self.tcl.eval(build_dbo_session_inventory_script())
        report = parse_dbo_session_inventory(value)
        self.assertEqual(report.design_names, ("Design A", "Design B"))
        self.assertEqual(report.library_names, ("Library A",))
        self.assertTrue(report.complete)
        self.assertTrue(report.resources_released)

    def test_open_project_script_roundtrips_path_without_tcl_interpolation(self):
        path = r"E:\Test {Project}\capture ; [do-not-run].opj"
        script = build_open_project_script(path)
        self.assertNotIn(path, script)
        value = self.tcl.eval(script)
        active, open_rc, open_result, returned_path = parse_open_project_report(value)
        self.assertEqual(self.tcl.getvar("mockOpenPath"), path)
        self.assertEqual(open_rc, 0)
        self.assertEqual(open_result, "OPENED")
        self.assertEqual(returned_path, path)
        self.assertTrue(active.verified)

    def test_all_design_reports_use_full_tcl_patchlevel(self):
        expected_patchlevel = self.tcl.eval("info patchlevel")

        active_value = self.tcl.eval(build_active_design_probe_script())
        active = parse_active_design_report(active_value)
        self.assertEqual(active.tcl_version, expected_patchlevel)

        inventory_value = self.tcl.eval(build_dbo_session_inventory_script())
        inventory = parse_dbo_session_inventory(inventory_value)
        self.assertEqual(inventory.tcl_version, expected_patchlevel)

        open_value = self.tcl.eval(build_open_project_script(OPJ))
        opened, open_rc, _open_result, _returned_path = parse_open_project_report(open_value)
        self.assertEqual(open_rc, 0)
        self.assertEqual(opened.tcl_version, expected_patchlevel)

if __name__ == "__main__":
    import unittest
    unittest.main()
