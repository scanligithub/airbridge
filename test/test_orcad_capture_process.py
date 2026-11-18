# -*- coding: utf-8 -*-
"""M3.2.2 read-only Capture process inventory tests; no remote host is touched."""
from __future__ import annotations

import json
import sys
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

from capture_process import (  # noqa: E402
    CaptureProcessAssessment,
    CaptureProcessCandidate,
    CaptureProcessDisposition,
    CaptureProcessInspector,
    CaptureProcessInventoryError,
    CaptureProcessInventoryPending,
    assess_capture_processes,
    build_capture_inventory_command,
    parse_capture_inventory_json,
)


CAPTURE_EXE = r"D:\Cadence\SPB_17.2\tools\bin\capture.exe"
STARTED_AT = "2026-11-18T10:00:00.0000000Z"
EXPECTED_VERSION = "17.2-2016"


def make_candidate(
    pid=7412,
    path=CAPTURE_EXE,
    started_at=STARTED_AT,
    version=EXPECTED_VERSION,
):
    return CaptureProcessCandidate(
        pid=pid,
        executable_path=path,
        started_at_utc=started_at,
        file_version=version,
    )


def make_manifest(
    pid=7412,
    path=CAPTURE_EXE,
    started_at=STARTED_AT,
    expected_version=EXPECTED_VERSION,
):
    return SimpleNamespace(
        pid=pid,
        capture_exe_path=path,
        process_started_at_utc=started_at,
        expected_capture_version=expected_version,
    )


class CaptureInventoryParserTests(TestCase):
    def test_empty_inventory(self):
        self.assertEqual(parse_capture_inventory_json("[]"), ())

    def test_single_object_and_array_are_supported(self):
        item = {
            "pid": 7412,
            "process_name": "CAPTURE.EXE",
            "executable_path": CAPTURE_EXE,
            "started_at_utc": STARTED_AT,
            "file_version": EXPECTED_VERSION,
        }
        single = parse_capture_inventory_json(json.dumps(item))
        array = parse_capture_inventory_json(json.dumps([item]))

        self.assertEqual(len(single), 1)
        self.assertEqual(single[0].pid, 7412)
        self.assertEqual(single[0].process_name, "CAPTURE.EXE")
        self.assertEqual(array, single)

    def test_unavailable_process_properties_are_preserved_as_unknown(self):
        result = parse_capture_inventory_json(json.dumps({
            "pid": 7412,
            "process_name": "capture.exe",
            "executable_path": None,
            "started_at_utc": None,
            "file_version": None,
        }))
        self.assertIsNone(result[0].executable_path)
        self.assertIsNone(result[0].started_at_utc)
        self.assertIsNone(result[0].file_version)

    def test_invalid_json_shapes_and_duplicate_pids_fail_closed(self):
        for raw in ("", "not-json", "null", '"text"', "42"):
            with self.subTest(raw=raw):
                with self.assertRaises(CaptureProcessInventoryError):
                    parse_capture_inventory_json(raw)

        duplicated = [
            {"pid": 7412},
            {"pid": 7412},
        ]
        with self.assertRaises(CaptureProcessInventoryError):
            parse_capture_inventory_json(json.dumps(duplicated))

    def test_invalid_pid_path_name_and_timestamp_are_rejected(self):
        bad_items = [
            {"pid": True},
            {"pid": 0},
            {"pid": "7412"},
            {"pid": 7412, "process_name": "allegro.exe"},
            {"pid": 7412},
            {"pid": 7412, "executable_path": "capture.exe"},
            {"pid": 7412, "started_at_utc": "2026-11-18T10:00:00"},
        ]
        for item in bad_items:
            with self.subTest(item=item):
                with self.assertRaises(CaptureProcessInventoryError):
                    parse_capture_inventory_json(json.dumps(item))

    def test_candidate_count_and_output_size_are_bounded(self):
        too_many = [{"pid": index + 1} for index in range(129)]
        with self.assertRaises(CaptureProcessInventoryError):
            parse_capture_inventory_json(json.dumps(too_many))
        with self.assertRaises(CaptureProcessInventoryError):
            parse_capture_inventory_json(" " * (256 * 1024 + 1))


class CaptureProcessAssessmentTests(TestCase):
    def test_exact_pid_path_start_time_and_version_match_is_owned(self):
        candidate = make_candidate(path=CAPTURE_EXE.swapcase())
        result = assess_capture_processes([candidate], make_manifest())

        self.assertEqual(result.disposition, CaptureProcessDisposition.OWNED_INSTANCE)
        self.assertIs(result.owned_candidate, candidate)

    def test_process_presence_without_manifest_is_unowned_not_owned(self):
        result = assess_capture_processes([make_candidate()], make_manifest(pid=None))

        self.assertEqual(
            result.disposition, CaptureProcessDisposition.UNOWNED_INSTANCE
        )
        self.assertIsNone(result.owned_candidate)

    def test_path_start_time_or_version_mismatch_is_not_owned(self):
        candidates = [
            make_candidate(path=r"D:\OtherCadence\capture.exe"),
            make_candidate(started_at="2026-11-18T10:00:01.0000000Z"),
            make_candidate(version="17.4"),
            make_candidate(path=None),
        ]
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                result = assess_capture_processes([candidate], make_manifest())
                self.assertEqual(
                    result.disposition,
                    CaptureProcessDisposition.PID_REUSED_OR_IDENTITY_MISMATCH,
                )
                self.assertIsNone(result.owned_candidate)

    def test_different_pid_is_unowned_and_not_reused(self):
        result = assess_capture_processes(
            [make_candidate(pid=9999)], make_manifest(pid=7412)
        )
        self.assertEqual(
            result.disposition, CaptureProcessDisposition.UNOWNED_INSTANCE
        )

    def test_multiple_capture_processes_are_ambiguous(self):
        result = assess_capture_processes(
            [make_candidate(), make_candidate(pid=9999)],
            make_manifest(),
        )
        self.assertEqual(
            result.disposition, CaptureProcessDisposition.MULTIPLE_INSTANCES
        )
        self.assertIsNone(result.owned_candidate)

    def test_empty_inventory_distinguishes_no_process_from_original_absent(self):
        no_session = assess_capture_processes([], make_manifest(pid=None))
        previous_session = assess_capture_processes([], make_manifest(pid=7412))

        self.assertEqual(no_session.disposition, CaptureProcessDisposition.NO_PROCESS)
        self.assertEqual(
            previous_session.disposition,
            CaptureProcessDisposition.ORIGINAL_PROCESS_ABSENT,
        )


class CaptureProcessInspectorTests(TestCase):
    class FakeClient:
        def __init__(self, response):
            self.response = response
            self.calls = []

        def shell_execute(self, command, **kwargs):
            self.calls.append((command, kwargs))
            return self.response

    def test_inventory_command_is_fixed_read_only_and_capture_specific(self):
        command = build_capture_inventory_command()
        lowered = command.lower()
        self.assertIn("get-ciminstance", lowered)
        self.assertIn("win32_process", lowered)
        self.assertIn("capture.exe", lowered)
        self.assertIn("executablepath", lowered)
        self.assertIn("creationdate", lowered)
        self.assertIn("fileversioninfo", lowered)
        self.assertNotIn("start-process", lowered)
        self.assertNotIn("stop-process", lowered)
        self.assertNotIn("remove-process", lowered)
        self.assertNotIn("/api/v1/eda/", lowered)

    def test_inspector_uses_synchronous_generic_shell_once(self):
        payload = json.dumps([{
            "pid": 7412,
            "process_name": "capture.exe",
            "executable_path": CAPTURE_EXE,
            "started_at_utc": STARTED_AT,
            "file_version": EXPECTED_VERSION,
        }])
        client = self.FakeClient({
            "request_id": "request-capture-inventory",
            "kind": "shell.exec",
            "state": "completed",
            "result": {
                "success": True,
                "exit_code": 0,
                "stdout": payload,
                "stderr": "",
            },
        })
        inspector = CaptureProcessInspector(client)
        result = inspector.inspect()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].pid, 7412)
        self.assertEqual(len(client.calls), 1)
        command, options = client.calls[0]
        self.assertIn("Get-CimInstance", command)
        self.assertEqual(options["shell"], "powershell")
        self.assertFalse(options["async_mode"])

    def test_inspector_reconciles_response_with_manifest(self):
        payload = json.dumps([{
            "pid": 7412,
            "process_name": "capture.exe",
            "executable_path": CAPTURE_EXE,
            "started_at_utc": STARTED_AT,
            "file_version": EXPECTED_VERSION,
        }])
        client = self.FakeClient({
            "state": "completed",
            "result": {"success": True, "exit_code": 0, "stdout": payload},
        })
        assessment = CaptureProcessInspector(client).inspect_session(make_manifest())
        self.assertEqual(
            assessment.disposition, CaptureProcessDisposition.OWNED_INSTANCE
        )

    def test_pending_or_unknown_shell_result_is_not_resubmitted(self):
        client = self.FakeClient({
            "request_id": "original-inventory-request",
            "state": "accepted",
        })
        inspector = CaptureProcessInspector(client)
        with self.assertRaises(CaptureProcessInventoryPending) as raised:
            inspector.inspect()

        self.assertEqual(raised.exception.request_id, "original-inventory-request")
        self.assertEqual(raised.exception.remote_state, "accepted")
        self.assertEqual(len(client.calls), 1)

    def test_failed_shell_result_and_nonzero_exit_are_rejected(self):
        cases = [
            {"state": "failed", "error": "shell failed"},
            {
                "state": "completed",
                "result": {
                    "success": True,
                    "exit_code": 1,
                    "stdout": "[]",
                    "stderr": "CIM query denied",
                },
            },
            {
                "state": "completed",
                "result": {
                    "success": True,
                    "exit_code": 0,
                    "stdout": "[",
                    "stdout_truncated": True,
                },
            },
        ]
        for response in cases:
            with self.subTest(response=response):
                with self.assertRaises(CaptureProcessInventoryError):
                    CaptureProcessInspector(self.FakeClient(response)).inspect()


if __name__ == "__main__":
    unittest.main()
