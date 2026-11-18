# -*- coding: utf-8 -*-
"""M3.2.1 Capture Skill foundation tests; no Cadence process is started."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import TestCase, main

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "skill"
    / "orcad-capture"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

from airbridge_client import (  # noqa: E402
    AirBridgeClient,
    AirBridgeConfigurationError,
    AirBridgeHTTPError,
    AirBridgeProtocolError,
    AirBridgeTransportError,
)
from capture_session import (  # noqa: E402
    BridgeReadyEvidence,
    CaptureAuthorizationError,
    CaptureConfig,
    CaptureConfigurationError,
    CaptureIdentityError,
    CaptureSession,
    CaptureSessionError,
    CaptureStateTransitionError,
    ProcessExitEvidence,
    ProcessIdentity,
    SessionManifest,
    SessionState,
)


class AirBridgeClientTests(TestCase):
    def setUp(self):
        self.requests = []
        self.status_code = 200
        self.response_body = {"success": True, "state": "completed"}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self._respond()

            def do_POST(self):  # noqa: N802
                self._respond()

            def _respond(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.requests.append({
                    "method": self.command,
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "content_type": self.headers.get("Content-Type"),
                    "body": raw,
                })
                body = (
                    owner.response_body
                    if isinstance(owner.response_body, bytes)
                    else json.dumps(owner.response_body).encode("utf-8")
                )
                self.send_response(owner.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:{}".format(self.server.server_port)
        self.client = AirBridgeClient(
            base_url=self.base_url,
            environ={"AIRBRIDGE_AGENT_TOKEN": "test-agent-token"},
            timeout_seconds=2,
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_get_sends_bearer_and_accepts_json_object(self):
        result = self.client.get("/api/v1/runtime/capabilities")

        self.assertEqual(result["success"], True)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["method"], "GET")
        self.assertEqual(
            self.requests[0]["authorization"], "Bearer test-agent-token"
        )
        self.assertEqual(self.requests[0]["body"], b"")

    def test_shell_execute_preserves_202_acceptance_receipt(self):
        self.status_code = 202
        self.response_body = {
            "request_id": str(uuid.uuid4()),
            "kind": "shell.exec",
            "state": "accepted",
            "result": {"task_id": "shell-test", "async": True},
        }

        result = self.client.shell_execute(
            "Start-Process -FilePath 'D:\\Cadence\\SPB_17.2\\tools\\bin\\capture.exe'",
            async_mode=True,
            task_timeout_seconds=30,
        )

        self.assertEqual(result["state"], "accepted")
        request = self.requests[0]
        self.assertEqual(request["path"], "/api/v1/shell/exec")
        payload = json.loads(request["body"].decode("utf-8"))
        self.assertTrue(payload["async"])
        self.assertEqual(payload["timeout"], 30)

    def test_filesystem_actions_are_allowlisted(self):
        self.client.filesystem("read", {"path": r"E:\_airbridge_temp\probe.txt"})
        self.assertEqual(self.requests[0]["path"], "/api/v1/fs/read")
        with self.assertRaises(ValueError):
            self.client.filesystem("eda_execute", {"script": "puts unsafe"})

    def test_401_and_403_fail_closed_with_status_and_payload(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.status_code = status
                self.response_body = {
                    "error": {"code": "AGENT_SCOPE_DENIED", "message": "denied"}
                }
                with self.assertRaises(AirBridgeHTTPError) as raised:
                    self.client.get("/api/v1/runtime/capabilities")
                self.assertEqual(raised.exception.status_code, status)
                self.assertEqual(
                    raised.exception.payload["error"]["code"],
                    "AGENT_SCOPE_DENIED",
                )

    def test_invalid_json_and_non_object_json_are_rejected(self):
        self.response_body = b"not-json"
        with self.assertRaises(AirBridgeProtocolError):
            self.client.get("/api/v1/runtime/capabilities")

        self.response_body = [1, 2, 3]
        with self.assertRaises(AirBridgeProtocolError):
            self.client.get("/api/v1/runtime/capabilities")

    def test_transport_failure_is_unknown_and_is_not_retried(self):
        attempted = []

        def failing_opener(_request, timeout):
            attempted.append(timeout)
            raise urllib.error.URLError("simulated disconnect")

        client = AirBridgeClient(
            base_url=self.base_url,
            token="test-agent-token",
            opener=failing_opener,
        )
        with self.assertRaises(AirBridgeTransportError) as raised:
            client.post("/api/v1/shell/exec", {"command": "Start-Process"})
        self.assertTrue(raised.exception.outcome_unknown)
        self.assertEqual(len(attempted), 1)

    def test_token_must_exist_and_api_origin_must_be_loopback(self):
        with self.assertRaises(AirBridgeConfigurationError):
            AirBridgeClient(base_url=self.base_url, environ={})
        with self.assertRaises(AirBridgeConfigurationError):
            AirBridgeClient(
                base_url="https://example.com",
                token="must-not-leave-host-a",
            )
        with self.assertRaises(AirBridgeConfigurationError):
            AirBridgeClient(
                base_url="http://localhost:8888",
                token="loopback-name-must-not-be-resolved",
            )
        with self.assertRaises(AirBridgeConfigurationError):
            AirBridgeClient(
                base_url=self.base_url,
                token="invalid" + chr(9) + "control",
            )

    def test_endpoint_validation_prevents_external_urls_and_path_escape(self):
        for path in (
            "https://example.com/api/v1/runtime/capabilities",
            "//example.com/api/v1/runtime/capabilities",
            "/api/v1/../../outside",
            "/api/v1/runtime/capabilities?forward=1",
        ):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.client.get(path)


class CaptureSessionModelTests(TestCase):
    CAPTURE_EXE = r"D:\Cadence\SPB_17.2\tools\bin\capture.exe"
    BRIDGE_HASH = hashlib.sha256(b"skill-owned-capture-bridge").hexdigest()
    STARTED_AT = "2026-11-18T10:00:00Z"

    def setUp(self):
        self.config = CaptureConfig(
            capture_exe_path=self.CAPTURE_EXE,
            cap_autoload_dir=r"D:\\Cadence\\SPB_17.2\\tools\\capture\\tclscripts\\capAutoLoad",
            expected_capture_version="17.2-2016",
            expected_tcl_version="8.6.0",
        )
        self.manifest = SessionManifest.create(self.config)
        self.session = CaptureSession(self.manifest, self.config)

    def make_ready(self):
        self.session.begin_inspection()
        self.session.begin_start()
        self.session.record_process_identity(
            ProcessIdentity(
                pid=7412,
                executable_path=self.CAPTURE_EXE.lower(),
                started_at_utc=self.STARTED_AT,
                file_version="17.2-2016",
            )
        )
        self.session.mark_bridge_pending(
            remote_session_dir=r"C:\Users\Agent\AppData\Local\AirBridgeSkills\Capture\sessions\test",
            hook_path=r"D:\Cadence\SPB_17.2\tools\capture\tclscripts\capAutoLoad\ab_skill_test.tcl",
            hook_sha256=hashlib.sha256(b"capture-skill-hook").hexdigest(),
            bridge_sha256=self.BRIDGE_HASH,
        )
        self.session.mark_ready(
            BridgeReadyEvidence(
                session_id=self.manifest.session_id,
                startup_nonce=self.manifest.startup_nonce,
                runtime_pid=7412,
                tcl_version="8.6.0",
                bridge_sha256=self.BRIDGE_HASH,
            )
        )

    def test_manifest_rejects_non_integer_schema_version(self):
        values = self.manifest.to_dict()
        values["schema_version"] = 1.0
        with self.assertRaises(CaptureSessionError):
            SessionManifest.from_dict(values)

    def test_manifest_rejects_closed_state_without_process_exit_evidence(self):
        values = self.manifest.to_dict()
        values["state"] = SessionState.CLOSED.value
        with self.assertRaises(CaptureSessionError):
            SessionManifest.from_dict(values)

    def test_config_rejects_non_finite_poll_intervals(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(CaptureConfigurationError):
                    CaptureConfig(poll_interval_seconds=value)

    def test_manifest_rejects_invalid_startup_nonce_encoding(self):
        values = self.manifest.to_dict()
        values["startup_nonce"] = "contains+invalid/characters"
        with self.assertRaises(CaptureSessionError):
            SessionManifest.from_dict(values)

    def test_session_id_and_startup_nonce_are_generated_per_session(self):
        another = SessionManifest.create(self.config)
        self.assertNotEqual(self.manifest.session_id, another.session_id)
        self.assertNotEqual(self.manifest.startup_nonce, another.startup_nonce)
        self.assertGreaterEqual(len(self.manifest.startup_nonce), 32)
        self.assertEqual(self.session.state, SessionState.ABSENT)

    def test_process_image_path_and_file_version_are_verified(self):
        self.session.begin_inspection()
        self.session.begin_start()
        with self.assertRaises(CaptureIdentityError):
            self.session.record_process_identity(
                ProcessIdentity(
                    pid=7412,
                    executable_path=r"D:\OtherCadence\capture.exe",
                    started_at_utc=self.STARTED_AT,
                    file_version="17.2-2016",
                )
            )
        self.assertIsNone(self.manifest.pid)

        with self.assertRaises(CaptureIdentityError):
            self.session.record_process_identity(
                ProcessIdentity(
                    pid=7412,
                    executable_path=self.CAPTURE_EXE,
                    started_at_utc=self.STARTED_AT,
                    file_version="17.4",
                )
            )
        self.assertIsNone(self.manifest.pid)

    def test_start_requires_an_explicit_capture_executable_path(self):
        config = CaptureConfig(expected_capture_version="17.2-2016")
        manifest = SessionManifest.create(config)
        session = CaptureSession(manifest, config)
        session.begin_inspection()
        session.begin_start()

        with self.assertRaises(CaptureIdentityError):
            session.record_process_identity(
                ProcessIdentity(
                    pid=7412,
                    executable_path=self.CAPTURE_EXE,
                    started_at_utc=self.STARTED_AT,
                    file_version="17.2-2016",
                )
            )
        self.assertIsNone(manifest.pid)

    def test_start_requires_capture_version_selection_and_verified_file_version(self):
        config = CaptureConfig(
            capture_exe_path=self.CAPTURE_EXE,
            cap_autoload_dir=r"D:\\Cadence\\SPB_17.2\\tools\\capture\\tclscripts\\capAutoLoad",
        )
        manifest = SessionManifest.create(config)
        session = CaptureSession(manifest, config)
        session.begin_inspection()
        session.begin_start()

        identity = ProcessIdentity(
            pid=7412,
            executable_path=self.CAPTURE_EXE,
            started_at_utc=self.STARTED_AT,
            file_version="17.2-2016",
        )
        with self.assertRaises(CaptureIdentityError):
            session.record_process_identity(identity)
        self.assertIsNone(manifest.pid)

    def test_ready_requires_matching_session_nonce_pid_tcl_and_bridge_hash(self):
        self.session.begin_inspection()
        self.session.begin_start()
        self.session.record_process_identity(
            ProcessIdentity(
                pid=7412,
                executable_path=self.CAPTURE_EXE,
                started_at_utc=self.STARTED_AT,
                file_version="17.2-2016",
            )
        )
        self.session.mark_bridge_pending(bridge_sha256=self.BRIDGE_HASH)

        wrong_nonce = BridgeReadyEvidence(
            self.manifest.session_id,
            "x" * 48,
            7412,
            "8.6.0",
            self.BRIDGE_HASH,
        )
        with self.assertRaises(CaptureIdentityError):
            self.session.mark_ready(wrong_nonce)

        wrong_pid = BridgeReadyEvidence(
            self.manifest.session_id,
            self.manifest.startup_nonce,
            7413,
            "8.6.0",
            self.BRIDGE_HASH,
        )
        with self.assertRaises(CaptureIdentityError):
            self.session.mark_ready(wrong_pid)

        wrong_tcl = BridgeReadyEvidence(
            self.manifest.session_id,
            self.manifest.startup_nonce,
            7412,
            "8.5.0",
            self.BRIDGE_HASH,
        )
        with self.assertRaises(CaptureIdentityError):
            self.session.mark_ready(wrong_tcl)

        wrong_hash = BridgeReadyEvidence(
            self.manifest.session_id,
            self.manifest.startup_nonce,
            7412,
            "8.6.0",
            hashlib.sha256(b"another bridge").hexdigest(),
        )
        with self.assertRaises(CaptureIdentityError):
            self.session.mark_ready(wrong_hash)

        good = BridgeReadyEvidence(
            self.manifest.session_id,
            self.manifest.startup_nonce,
            7412,
            "8.6.0",
            self.BRIDGE_HASH,
        )
        self.session.mark_ready(good)
        self.assertEqual(self.session.state, SessionState.READY)
        self.assertEqual(self.manifest.tcl_version, "8.6.0")

    def test_tcl_operations_are_serialized_and_request_id_bound(self):
        self.make_ready()
        request_id = str(uuid.uuid4())
        self.session.begin_operation(request_id)
        self.assertEqual(self.session.state, SessionState.BUSY)
        self.assertEqual(self.manifest.active_request_id, request_id)

        with self.assertRaises(CaptureStateTransitionError):
            self.session.begin_operation(str(uuid.uuid4()))

        with self.assertRaises(CaptureIdentityError):
            self.session.complete_operation(str(uuid.uuid4()))
        self.session.complete_operation(request_id)
        self.assertEqual(self.session.state, SessionState.READY)
        self.assertIsNone(self.manifest.active_request_id)

    def test_unknown_preserves_request_identity_and_prevents_same_session_restart(self):
        self.make_ready()
        request_id = str(uuid.uuid4())
        self.session.begin_operation(request_id)
        self.session.mark_unknown("BRIDGE_TIMEOUT", "Tcl result was not received")

        self.assertEqual(self.session.state, SessionState.UNKNOWN)
        self.assertTrue(self.manifest.reconciliation_required)
        self.assertEqual(self.manifest.active_request_id, request_id)

        # Read-only inspection is permitted; starting again under the same
        # Session ID/Nonce is not. A new session must be created after review.
        self.session.begin_inspection()
        with self.assertRaises(CaptureStateTransitionError):
            self.session.begin_start()

    def test_manifest_requires_explicit_utc_timestamps_and_unknown_reconciliation(self):
        values = self.manifest.to_dict()
        values["created_at_utc"] = "2026-11-18T10:00:00"
        with self.assertRaises(CaptureSessionError):
            SessionManifest.from_dict(values)

        values = self.manifest.to_dict()
        values["state"] = "unknown"
        values["reconciliation_required"] = False
        with self.assertRaises(CaptureSessionError):
            SessionManifest.from_dict(values)

    def test_manifest_error_messages_redact_bearer_secrets(self):
        self.make_ready()
        secret = "abg1." + ("a" * 32) + ".topsecretvalue"
        self.session.mark_failed(
            "TEST_ERROR", "request failed with Authorization: Bearer " + secret
        )
        serialized = json.dumps(self.manifest.to_dict())
        self.assertNotIn(secret, serialized)
        self.assertIn("Bearer [REDACTED]", serialized)

    def test_public_transition_api_cannot_bypass_close_authorization(self):
        self.make_ready()
        with self.assertRaises(CaptureStateTransitionError):
            self.session.transition(SessionState.CLOSING)
        with self.assertRaises(CaptureStateTransitionError):
            self.session.transition(SessionState.CLOSED)
        self.assertEqual(self.session.state, SessionState.READY)

    def test_close_requires_authorization_and_original_process_exit_evidence(self):
        self.make_ready()
        with self.assertRaises(CaptureAuthorizationError):
            self.session.begin_close()
        self.assertEqual(self.session.state, SessionState.READY)

        self.session.begin_close(user_authorized=True)
        self.assertEqual(self.session.state, SessionState.CLOSING)

        checked_at = "2026-11-18T10:05:00Z"
        wrong_pid = ProcessExitEvidence(
            pid=7413,
            started_at_utc=self.STARTED_AT,
            process_absent=True,
            checked_at_utc=checked_at,
        )
        with self.assertRaises(CaptureIdentityError):
            self.session.mark_closed(wrong_pid)

        wrong_start = ProcessExitEvidence(
            pid=7412,
            started_at_utc="2026-11-18T10:00:01Z",
            process_absent=True,
            checked_at_utc=checked_at,
        )
        with self.assertRaises(CaptureIdentityError):
            self.session.mark_closed(wrong_start)

        still_running = ProcessExitEvidence(
            pid=7412,
            started_at_utc=self.STARTED_AT,
            process_absent=False,
            checked_at_utc=checked_at,
        )
        with self.assertRaises(CaptureIdentityError):
            self.session.mark_closed(still_running)
        self.assertEqual(self.session.state, SessionState.CLOSING)

        valid = ProcessExitEvidence(
            pid=7412,
            started_at_utc=self.STARTED_AT,
            process_absent=True,
            checked_at_utc=checked_at,
        )
        self.session.mark_closed(valid)
        self.assertEqual(self.session.state, SessionState.CLOSED)
        self.assertEqual(self.manifest.process_exit_confirmed_at_utc, checked_at)

    def test_close_exit_evidence_must_not_precede_process_start(self):
        self.make_ready()
        self.session.begin_close(user_authorized=True)
        too_early = ProcessExitEvidence(
            pid=7412,
            started_at_utc=self.STARTED_AT,
            process_absent=True,
            checked_at_utc="2026-11-18T09:59:59Z",
        )
        with self.assertRaises(CaptureIdentityError):
            self.session.mark_closed(too_early)

    def test_manifest_round_trip_is_atomic_and_contains_no_bearer_token(self):
        self.make_ready()
        self.session.begin_close(user_authorized=True)
        checked_at = "2026-11-18T10:05:00Z"
        self.session.mark_closed(
            ProcessExitEvidence(
                pid=7412,
                started_at_utc=self.STARTED_AT,
                process_absent=True,
                checked_at_utc=checked_at,
            )
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "nested" / "session.manifest.json"
            self.manifest.save(path)
            loaded = SessionManifest.load(path)

        self.assertEqual(loaded.session_id, self.manifest.session_id)
        self.assertEqual(loaded.startup_nonce, self.manifest.startup_nonce)
        self.assertEqual(loaded.pid, 7412)
        self.assertEqual(loaded.capture_version, "17.2-2016")
        self.assertEqual(loaded.tcl_version, "8.6.0")
        self.assertEqual(loaded.process_exit_confirmed_at_utc, checked_at)
        self.assertEqual(loaded.state, SessionState.CLOSED.value)
        serialized = json.dumps(loaded.to_dict())
        self.assertNotIn("AIRBRIDGE_AGENT_TOKEN", serialized)
        self.assertNotIn("test-agent-token", serialized)

    def test_configuration_from_env_does_not_absorb_token_value(self):
        config = CaptureConfig.from_env({
            "AIRBRIDGE_API_BASE_URL": "http://127.0.0.1:8888",
            "AIRBRIDGE_CAPTURE_EXE": self.CAPTURE_EXE,
            "AIRBRIDGE_CAPTURE_AUTOLOAD_DIR": r"D:\\Cadence\\SPB_17.2\\tools\\capture\\tclscripts\\capAutoLoad",
            "AIRBRIDGE_CAPTURE_VERSION": "17.2-2016",
            "AIRBRIDGE_AGENT_TOKEN": "do-not-copy-into-config",
        })
        self.assertEqual(config.capture_exe_path, self.CAPTURE_EXE)
        self.assertTrue(config.cap_autoload_dir.endswith("capAutoLoad"))
        self.assertFalse(hasattr(config, "token"))
        self.assertNotIn("do-not-copy-into-config", repr(config))

    def test_invalid_manifest_and_invalid_config_fail_closed(self):
        with self.assertRaises(CaptureSessionError):
            SessionManifest.from_dict({"schema_version": 1, "extra": "field"})
        with self.assertRaises(CaptureConfigurationError):
            CaptureConfig(capture_exe_path="capture.exe")
        with self.assertRaises(CaptureConfigurationError):
            CaptureConfig(api_base_url="https://example.com")
        with self.assertRaises(CaptureConfigurationError):
            CaptureConfig(api_base_url="http://[malformed")


if __name__ == "__main__":
    main()
