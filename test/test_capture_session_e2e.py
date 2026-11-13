# test_capture_session_e2e.py
# -*- coding: utf-8 -*-
"""Unit tests for the Host-A Capture Session E2E harness."""
import json
import unittest
from unittest import mock

from scripts import capture_session_e2e as e2e


class CaptureSessionE2ETests(unittest.TestCase):
    def test_first_probe_initializes_and_second_probe_only_checks_persistence(self):
        token = "0123456789abcdef0123456789abcdef"
        first = e2e.build_probe_script(token, initialize=True)
        second = e2e.build_probe_script(token, initialize=False)

        self.assertIn("namespace eval ::AirBridgeE2E", first)
        self.assertIn("proc ::AirBridgeE2E::probe", first)
        self.assertIn(token, first)
        self.assertIn("GetActivePMDesign", first)
        self.assertIn("GetActivePage", first)
        self.assertIn("OPage", first)

        self.assertNotIn("proc ::AirBridgeE2E::probe", second)
        self.assertNotIn(f'set ::AirBridgeE2E::token "{token}"', second)
        self.assertIn("persistent variable is missing", second)
        self.assertIn("persistent proc is missing", second)

    def test_report_parser_accepts_required_fields(self):
        value = (
            "pid=7476|tcl=8.6.0|token=abc123|proc=abc123|"
            "apis=GetActivePMDesign,GetActivePage,OPage|"
            "session_var=1|active_pm_rc=0"
        )
        result = e2e.parse_tcl_report(value)
        self.assertEqual(result["pid"], "7476")
        self.assertEqual(result["tcl"], "8.6.0")
        self.assertEqual(result["session_var"], "1")

    def test_report_parser_rejects_missing_or_malformed_fields(self):
        with self.assertRaises(e2e.E2EError):
            e2e.parse_tcl_report("pid=7476|tcl=8.6.0")
        with self.assertRaises(e2e.E2EError):
            e2e.parse_tcl_report("pid=7476|broken|tcl=8.6.0")
        with self.assertRaises(e2e.E2EError):
            e2e.parse_tcl_report("")

    def test_submit_uses_unique_request_id_and_startup_grace(self):
        response = {
            "request_id": None,
            "state": "completed",
            "result": {
                "success": True,
                "request_state": "completed",
                "returncode": 0,
            },
        }
        with mock.patch.object(
            e2e, "request_json", return_value=(200, response)
        ) as request_mock, mock.patch.object(
            e2e, "wait_for_request", return_value=response
        ) as wait_mock:
            result = e2e.submit_tcl_request(
                "http://127.0.0.1:8888",
                r"D:\\projects\\demo.dsn",
                "17.2",
                "puts ready",
                30,
            )

        self.assertTrue(result["success"])
        payload = request_mock.call_args.args[2]
        self.assertEqual(payload["target_file"], r"D:\\projects\\demo.dsn")
        self.assertTrue(payload["keep_open"])
        self.assertEqual(
            payload["wait_timeout_ms"],
            (30 + e2e.DEFAULT_STARTUP_GRACE_SECONDS + 15) * 1000,
        )
        self.assertEqual(
            request_mock.call_args.kwargs["timeout"],
            30 + e2e.DEFAULT_STARTUP_GRACE_SECONDS + 25,
        )
        self.assertEqual(payload["request_id"], wait_mock.call_args.args[1])

    def test_submit_rejects_wrong_request_id_or_non_completed_result(self):
        wrong_id_response = {
            "request_id": "00000000-0000-0000-0000-000000000001",
            "state": "completed",
            "result": {"success": True, "request_state": "completed"},
        }
        with mock.patch.object(
            e2e, "request_json", return_value=(200, wrong_id_response)
        ):
            with self.assertRaisesRegex(e2e.E2EError, "does not match"):
                e2e.submit_tcl_request(
                    "http://127.0.0.1:8888",
                    r"D:\\projects\\demo.dsn",
                    "17.2",
                    "puts ready",
                    10,
                )

        failed_response = {
            "request_id": None,
            "state": "failed",
            "result": {"success": False, "request_state": "failed"},
        }
        with mock.patch.object(
            e2e, "request_json", return_value=(502, failed_response)
        ), mock.patch.object(
            e2e, "wait_for_request", return_value=failed_response
        ):
            with self.assertRaisesRegex(e2e.E2EError, "ended in state failed"):
                e2e.submit_tcl_request(
                    "http://127.0.0.1:8888",
                    r"D:\\projects\\demo.dsn",
                    "17.2",
                    "error expected",
                    10,
                )

    def test_run_acceptance_requires_same_pid_and_persistent_tcl_state(self):
        token = "a" * 32
        peer = {
            "state": "negotiated",
            "features": [
                "handshake",
                "request.correlation",
                "request.deduplication",
            ],
            "profiles": [],
        }
        good_report = {
            "pid": "7476",
            "tcl": "8.6.0",
            "token": token,
            "proc": token,
            "apis": "GetActivePMDesign,GetActivePage,OPage",
            "session_var": "1",
            "active_pm_rc": "0",
        }
        responses = [
            {
                "success": True,
                "request_state": "completed",
                "capture_pid": 7476,
                "output": (
                    f"pid=7476|tcl=8.6.0|token={token}|proc={token}|"
                    "apis=GetActivePMDesign,GetActivePage,OPage|"
                    "session_var=1|active_pm_rc=0"
                ),
            },
            {
                "success": True,
                "request_state": "completed",
                "capture_pid": 7476,
                "output": (
                    f"pid=7476|tcl=8.6.0|token={token}|proc={token}|"
                    "apis=GetActivePMDesign,GetActivePage,OPage|"
                    "session_var=1|active_pm_rc=0"
                ),
            },
        ]
        with mock.patch.object(e2e, "uuid") as uuid_mock, mock.patch.object(
            e2e, "wait_for_negotiated_peer", return_value=peer
        ), mock.patch.object(
            e2e, "submit_tcl_request", side_effect=responses
        ) as submit_mock:
            uuid_mock.uuid4.return_value.hex = token
            result = e2e.run_acceptance(
                "http://127.0.0.1:8888",
                r"D:\\projects\\demo.dsn",
                version="17.2",
                timeout_seconds=15,
            )

        self.assertEqual(result["capture_pid"], 7476)
        self.assertTrue(result["persistent_variable"])
        self.assertTrue(result["persistent_proc"])
        self.assertTrue(result["same_capture_pid"])
        self.assertFalse(result["design_saved_or_closed"])
        self.assertEqual(submit_mock.call_count, 2)
        self.assertIn(token, submit_mock.call_args_list[0].args[3])
        self.assertNotIn("proc ::AirBridgeE2E::probe", submit_mock.call_args_list[1].args[3])

    def test_run_acceptance_rejects_pid_change_or_missing_capture_commands(self):
        token = "b" * 32
        peer = {"state": "negotiated", "features": list(e2e.REQUIRED_FEATURES)}
        first = {
            "success": True,
            "request_state": "completed",
            "capture_pid": 7476,
            "output": f"pid=7476|tcl=8.6|token={token}|proc={token}|apis=GetActivePMDesign,GetActivePage,OPage|session_var=1|active_pm_rc=0",
        }
        changed_pid = {
            "success": True,
            "request_state": "completed",
            "capture_pid": 7480,
            "output": f"pid=7480|tcl=8.6|token={token}|proc={token}|apis=GetActivePMDesign,GetActivePage,OPage|session_var=1|active_pm_rc=0",
        }
        with mock.patch.object(e2e.uuid, "uuid4") as uuid_mock, mock.patch.object(
            e2e, "wait_for_negotiated_peer", return_value=peer
        ), mock.patch.object(
            e2e, "submit_tcl_request", side_effect=[first, changed_pid]
        ):
            uuid_mock.return_value.hex = token
            with self.assertRaisesRegex(e2e.E2EError, "PID changed"):
                e2e.run_acceptance("http://127.0.0.1:8888", r"D:\\demo.dsn")

        missing_api = {
            "success": True,
            "request_state": "completed",
            "capture_pid": 7476,
            "output": f"pid=7476|tcl=8.6|token={token}|proc={token}|apis=GetActivePMDesign|session_var=1|active_pm_rc=0",
        }
        with mock.patch.object(e2e.uuid, "uuid4") as uuid_mock, mock.patch.object(
            e2e, "wait_for_negotiated_peer", return_value=peer
        ), mock.patch.object(
            e2e, "submit_tcl_request", side_effect=[first, missing_api]
        ):
            uuid_mock.return_value.hex = token
            with self.assertRaisesRegex(e2e.E2EError, r"command\(s\) unavailable"):
                e2e.run_acceptance("http://127.0.0.1:8888", r"D:\\demo.dsn")

    def test_run_acceptance_requires_host_b_dsn_path_and_positive_timeout(self):
        with self.assertRaisesRegex(e2e.E2EError, ".dsn"):
            e2e.run_acceptance("http://127.0.0.1:8888", "project.opj")
        with self.assertRaisesRegex(e2e.E2EError, "absolute Windows path"):
            e2e.run_acceptance(
                "http://127.0.0.1:8888",
                "relative\\demo.dsn",
            )
        with self.assertRaisesRegex(e2e.E2EError, "positive"):
            e2e.run_acceptance(
                "http://127.0.0.1:8888",
                r"D:\\demo.dsn",
                timeout_seconds=0,
            )


if __name__ == "__main__":
    unittest.main()
