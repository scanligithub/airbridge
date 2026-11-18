# -*- coding: utf-8 -*-
"""M3.2.3 authorized Capture startup tests; all remote operations are mocked."""
from __future__ import annotations

import base64
import hashlib
import json
import ntpath
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import TestCase

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "skill"
    / "orcad-capture"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

from airbridge_client import AirBridgeTransportError  # noqa: E402
from capture_process import (  # noqa: E402
    CaptureProcessAssessment,
    CaptureProcessCandidate,
    CaptureProcessDisposition,
)
from capture_session import (  # noqa: E402
    CaptureAuthorizationError,
    CaptureConfig,
    CaptureSession,
    SessionManifest,
    SessionState,
)
from capture_startup import (  # noqa: E402
    CaptureStartupCoordinator,
    CaptureStartupError,
    CaptureStartupOutcomeUnknown,
    _utf8_base64,
    build_capture_start_command,
    build_create_new_hook_command,
    build_remove_owned_hook_command,
    build_startup_preflight_command,
    render_capture_hook,
)


CAPTURE_EXE = r"D:\Cadence\SPB_17.2\tools\bin\capture.exe"
CAP_AUTOLOAD = (
    r"D:\Cadence\SPB_17.2\tools\capture\tclscripts\capAutoLoad"
)
LOCAL_APP_DATA = r"C:\Users\Agent\AppData\Local"
REMOTE_ROOT = ntpath.join(
    LOCAL_APP_DATA, "AirBridgeSkills", "Capture", "sessions"
)
STARTED_AT = "2026-11-18T10:00:00.000000Z"
CAPTURE_VERSION = "17.2-2016"
TCL_VERSION = "8.6.0"
PID = 7412


def make_config(**overrides):
    values = {
        "capture_exe_path": CAPTURE_EXE,
        "cap_autoload_dir": CAP_AUTOLOAD,
        "expected_capture_version": CAPTURE_VERSION,
        "expected_tcl_version": TCL_VERSION,
        "bridge_ready_timeout_seconds": 3,
        "poll_interval_seconds": 0.01,
        "shell_timeout_seconds": 5,
    }
    values.update(overrides)
    return CaptureConfig(**values)


class FakeInspector:
    def __init__(self, candidates=(), disposition=CaptureProcessDisposition.NO_PROCESS):
        self.candidates = tuple(candidates)
        self.disposition = disposition
        self.inspect_calls = 0
        self.assessment_calls = 0

    def inspect_session(self, manifest):
        self.assessment_calls += 1
        return CaptureProcessAssessment(self.disposition, self.candidates)

    def inspect(self):
        self.inspect_calls += 1
        return self.candidates


class FakeAirBridgeClient:
    def __init__(
        self,
        manifest,
        config,
        *,
        bridge_text,
        fail_start_transport=False,
        fail_rfs_write=False,
    ):
        self.manifest = manifest
        self.config = config
        self.bridge_text = bridge_text
        self.fail_start_transport = fail_start_transport
        self.fail_rfs_write = fail_rfs_write
        self.shell_calls = []
        self.filesystem_calls = []
        self.remote_files = {}
        self.remote_dirs = []
        self.remote_session_dir = ntpath.join(
            REMOTE_ROOT, manifest.session_id
        )
        self.hook_path = ntpath.join(
            config.cap_autoload_dir,
            "ab_skill_{}.tcl".format(manifest.session_id),
        )
        self.bridge_path = ntpath.join(
            self.remote_session_dir, "capture_bridge.tcl"
        )
        self.bridge_sha256 = hashlib.sha256(
            bridge_text.encode("utf-8")
        ).hexdigest()
        self.hook_text = render_capture_hook(
            manifest.session_id,
            manifest.startup_nonce,
            self.remote_session_dir,
            self.bridge_sha256,
        )
        self.hook_sha256 = hashlib.sha256(
            self.hook_text.encode("utf-8")
        ).hexdigest()
        self.ready_payload = {
            "schema_version": 1,
            "session_id": manifest.session_id,
            "startup_nonce": manifest.startup_nonce,
            "runtime_pid": PID,
            "tcl_version": TCL_VERSION,
            "bridge_sha256": self.bridge_sha256,
            "ready_at_utc": STARTED_AT,
        }

    def shell_execute(self, command, **kwargs):
        self.shell_calls.append((command, kwargs))
        request_id = kwargs.get("request_id") or str(uuid.uuid4())

        if "GetFolderPath('LocalApplicationData')" in command:
            stdout = LOCAL_APP_DATA
        elif "capture_exe_exists" in command and "$autoloadPath" in command:
            stdout = json.dumps({
                "capture_exe_exists": True,
                "capture_exe_path": CAPTURE_EXE,
                "capture_file_version": CAPTURE_VERSION,
                "autoload_dir_exists": True,
                "session_dir_exists": False,
                "hook_exists": False,
            })
        elif "[IO.FileMode]::CreateNew" in command:
            stdout = json.dumps({
                "created": True,
                "path": self.hook_path,
                "size": len(self.hook_text.encode("utf-8")),
                "sha256": self.hook_sha256,
            })
        elif "Start-Process -FilePath" in command:
            if self.fail_start_transport:
                raise AirBridgeTransportError(
                    "simulated Shell response loss",
                    request_id=request_id,
                )
            stdout = json.dumps({
                "pid": PID,
                "started_at_utc": STARTED_AT,
                "process_name": "capture.exe",
            })
        elif "Remove-Item -LiteralPath" in command and "Get-FileHash" in command:
            stdout = json.dumps({
                "removed": True,
                "already_absent": False,
                "sha256": self.hook_sha256,
            })
        else:
            raise AssertionError("unexpected remote Shell command: {}".format(command))

        return {
            "request_id": request_id,
            "kind": "shell.exec",
            "state": "completed",
            "result": {
                "success": True,
                "task_id": "shell-test",
                "exit_code": 0,
                "stdout": stdout,
                "stderr": "",
            },
        }

    def filesystem(self, action, payload=None, **kwargs):
        request = dict(payload or {})
        self.filesystem_calls.append((action, request, kwargs))

        if action == "mkdir":
            self.remote_dirs.append(request["path"])
            return {"success": True, "path": request["path"]}

        if action == "write":
            if self.fail_rfs_write:
                raise AirBridgeTransportError("simulated RFS write response loss")
            if request.get("is_base64") is True:
                raw = base64.b64decode(request["content"].encode("ascii"))
            else:
                raw = request["content"].encode("utf-8")
            self.remote_files[request["path"]] = raw
            return {
                "success": True,
                "path": request["path"],
                "size": len(raw),
            }

        if action == "read":
            path = request.get("path", "")
            if path.endswith("capture_bridge.tcl"):
                raw = self.remote_files[path]
            elif path.endswith("bridge.ready"):
                raw = json.dumps(
                    self.ready_payload, separators=(",", ":")
                ).encode("utf-8")
            else:
                raise AssertionError("unexpected RFS read path: {}".format(path))
            if request.get("as_base64"):
                returned_content = base64.b64encode(raw).decode("ascii")
                encoding = "base64"
            else:
                returned_content = raw.decode("utf-8")
                encoding = "text"
            return {
                "success": True,
                "path": path,
                "size": len(raw),
                "is_truncated": False,
                "encoding": encoding,
                "content": returned_content,
            }

        raise AssertionError("unexpected filesystem action: {}".format(action))


class CaptureStartupBuilderTests(TestCase):
    def test_hook_is_session_scoped_and_manual_capture_skips_it(self):
        sid = str(uuid.uuid4())
        nonce = "N" * 48
        remote_dir = ntpath.join(
            LOCAL_APP_DATA, "AirBridgeSkills", "Capture", "sessions", sid
        )
        bridge_hash = hashlib.sha256(b"bridge").hexdigest()

        hook = render_capture_hook(sid, nonce, remote_dir, bridge_hash)

        self.assertIn("AB_CAPTURE_SKILL_SESSION_ID", hook)
        self.assertIn("AB_CAPTURE_SKILL_STARTUP_NONCE", hook)
        self.assertIn("unset ::env(AB_CAPTURE_SKILL_SESSION_ID)", hook)
        self.assertIn("unset ::env(AB_CAPTURE_SKILL_STARTUP_NONCE)", hook)
        self.assertIn("return", hook)
        self.assertIn(_utf8_base64(remote_dir), hook)
        self.assertNotIn(remote_dir, hook)
        self.assertIn("::AirBridgeSkill::bootstrap", hook)

    def test_start_command_only_exports_one_child_launch_ticket(self):
        sid = str(uuid.uuid4())
        nonce = "A" * 48
        command = build_capture_start_command(CAPTURE_EXE, sid, nonce)

        self.assertIn("Start-Process -FilePath", command)
        self.assertIn("AB_CAPTURE_SKILL_SESSION_ID", command)
        self.assertIn("AB_CAPTURE_SKILL_STARTUP_NONCE", command)
        self.assertIn("Remove-Item Env:AB_CAPTURE_SKILL_SESSION_ID", command)
        self.assertIn("Remove-Item Env:AB_CAPTURE_SKILL_STARTUP_NONCE", command)
        self.assertNotIn("Stop-Process", command)
        self.assertNotIn("taskkill", command.lower())

    def test_hook_creation_uses_create_new_and_does_not_overwrite(self):
        path = ntpath.join(CAP_AUTOLOAD, "ab_skill_test.tcl")
        text = "puts {hello}"
        command = build_create_new_hook_command(path, text)

        self.assertIn("[IO.FileMode]::CreateNew", command)
        self.assertIn("[IO.FileShare]::None", command)
        self.assertIn("Get-FileHash", command)
        self.assertNotIn("Set-Content", command)
        self.assertNotIn("Out-File", command)
        self.assertNotIn(path, command)
        self.assertIn(_utf8_base64(path), command)

    def test_hook_cleanup_checks_hash_before_delete_and_never_uses_force(self):
        path = ntpath.join(CAP_AUTOLOAD, "ab_skill_test.tcl")
        digest = hashlib.sha256(b"owned hook").hexdigest()
        command = build_remove_owned_hook_command(path, digest)

        self.assertIn("Get-FileHash", command)
        self.assertIn("$actual -ne $expected", command)
        self.assertIn("Remove-Item -LiteralPath $path", command)
        self.assertNotIn("-Force", command)
        self.assertNotIn("Remove-Item -Recurse", command)

    def test_startup_preflight_is_read_only(self):
        command = build_startup_preflight_command(
            CAPTURE_EXE,
            CAP_AUTOLOAD,
            ntpath.join(REMOTE_ROOT, str(uuid.uuid4())),
            ntpath.join(CAP_AUTOLOAD, "ab_skill_test.tcl"),
        )
        self.assertIn("Get-Item -LiteralPath", command)
        self.assertIn("Test-Path -LiteralPath", command)
        self.assertNotIn("Start-Process", command)
        self.assertNotIn("Remove-Item", command)
        self.assertNotIn("/api/v1/eda/", command)


class CaptureStartupResponseTests(TestCase):
    def test_completed_shell_result_requires_success_and_exit_code_zero(self):
        result = CaptureStartupCoordinator._unwrap_shell_result({
            "state": "completed",
            "result": {"success": True, "exit_code": 0, "stdout": "{}"},
        }, str(uuid.uuid4()), "test")
        self.assertEqual(result["exit_code"], 0)

        with self.assertRaises(CaptureStartupError):
            CaptureStartupCoordinator._unwrap_shell_result({
                "state": "completed",
                "result": {"success": True, "exit_code": 1, "stdout": ""},
            }, str(uuid.uuid4()), "test")

    def test_accepted_or_unknown_shell_result_preserves_request_id(self):
        request_id = str(uuid.uuid4())
        for state in ("accepted", "pending", "unknown"):
            with self.subTest(state=state):
                with self.assertRaises(CaptureStartupOutcomeUnknown) as raised:
                    CaptureStartupCoordinator._unwrap_shell_result({
                        "state": state,
                        "request_id": request_id,
                    }, request_id, "test")
                self.assertEqual(raised.exception.request_id, request_id)


class CaptureStartupCoordinatorTests(TestCase):
    def setUp(self):
        self.config = make_config()
        self.manifest = SessionManifest.create(self.config)
        self.session = CaptureSession(self.manifest, self.config)
        self.bridge_path = (
            Path(__file__).resolve().parents[1]
            / "skill"
            / "orcad-capture"
            / "assets"
            / "bridge"
            / "capture_bridge.tcl"
        )
        self.bridge_text = self.bridge_path.read_text(encoding="utf-8")
        self.candidate = CaptureProcessCandidate(
            pid=PID,
            executable_path=CAPTURE_EXE,
            started_at_utc=STARTED_AT,
            file_version=CAPTURE_VERSION,
        )
        self.inspector = FakeInspector((self.candidate,))
        self.client = FakeAirBridgeClient(
            self.manifest,
            self.config,
            bridge_text=self.bridge_text,
        )
        self.coordinator = CaptureStartupCoordinator(
            self.client,
            self.config,
            inspector=self.inspector,
            bridge_path=self.bridge_path,
            sleep_fn=lambda _seconds: None,
        )

    def test_start_requires_explicit_authorization_before_any_remote_call(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.json"
            with self.assertRaises(CaptureAuthorizationError):
                self.coordinator.start(
                    self.session,
                    manifest_path=path,
                )

        self.assertEqual(self.session.state, SessionState.ABSENT)
        self.assertEqual(self.client.shell_calls, [])
        self.assertEqual(self.client.filesystem_calls, [])

    def test_existing_capture_process_is_not_adopted_or_modified(self):
        self.coordinator.inspector = FakeInspector(
            (self.candidate,),
            disposition=CaptureProcessDisposition.UNOWNED_INSTANCE,
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.json"
            with self.assertRaises(CaptureStartupError):
                self.coordinator.start(
                    self.session,
                    user_authorized=True,
                    manifest_path=path,
                )

        self.assertEqual(self.session.state, SessionState.INSPECTING)
        self.assertEqual(self.client.shell_calls, [])
        self.assertEqual(self.client.filesystem_calls, [])

    def test_authorized_start_deploys_bridge_verifies_ready_and_cleans_only_own_hook(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.json"
            result = self.coordinator.start(
                self.session,
                user_authorized=True,
                manifest_path=path,
            )
            persisted = SessionManifest.load(path)

        self.assertEqual(self.session.state, SessionState.READY)
        self.assertEqual(result.pid, PID)
        self.assertEqual(result.capture_version, CAPTURE_VERSION)
        self.assertEqual(result.tcl_version, TCL_VERSION)
        self.assertTrue(result.hook_cleanup_completed)
        self.assertTrue(persisted.hook_cleanup_completed)
        self.assertEqual(persisted.state, SessionState.READY.value)
        self.assertEqual(persisted.pid, PID)
        self.assertEqual(persisted.hook_sha256, result.hook_sha256)
        self.assertEqual(persisted.bridge_sha256, result.bridge_sha256)
        self.assertTrue(persisted.startup_request_id)
        self.assertTrue(persisted.hook_install_request_id)
        self.assertTrue(persisted.hook_cleanup_request_id)

        self.assertEqual(
            self.client.remote_files[result.bridge_path],
            self.bridge_text.encode("utf-8"),
        )
        self.assertEqual(len(self.client.shell_calls), 5)
        commands = [item[0] for item in self.client.shell_calls]
        self.assertTrue(any("GetFolderPath" in command for command in commands))
        self.assertTrue(any("capture_exe_exists" in command for command in commands))
        self.assertTrue(any("[IO.FileMode]::CreateNew" in command for command in commands))
        self.assertTrue(any("Start-Process -FilePath" in command for command in commands))
        self.assertTrue(any("Get-FileHash" in command and "Remove-Item -LiteralPath" in command for command in commands))
        self.assertTrue(all("/api/v1/eda/" not in command for command in commands))

        actions = [item[0] for item in self.client.filesystem_calls]
        self.assertEqual(actions.count("write"), 1)
        self.assertGreaterEqual(actions.count("mkdir"), 5)
        self.assertEqual(actions.count("read"), 2)

    def test_rfs_write_transport_loss_marks_session_unknown_without_retry(self):
        self.client.fail_rfs_write = True
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.json"
            with self.assertRaises(CaptureStartupOutcomeUnknown):
                self.coordinator.start(
                    self.session,
                    user_authorized=True,
                    manifest_path=path,
                )
            persisted = SessionManifest.load(path)

        self.assertEqual(self.session.state, SessionState.UNKNOWN)
        self.assertTrue(persisted.reconciliation_required)
        self.assertEqual(
            sum(1 for action, _payload, _kwargs in self.client.filesystem_calls if action == "write"),
            1,
        )
        self.assertFalse(
            any("Start-Process -FilePath" in command for command, _kwargs in self.client.shell_calls)
        )

    def test_start_transport_loss_persists_same_start_request_and_never_retries(self):
        self.client.fail_start_transport = True
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.json"
            with self.assertRaises(CaptureStartupOutcomeUnknown) as raised:
                self.coordinator.start(
                    self.session,
                    user_authorized=True,
                    manifest_path=path,
                )
            persisted = SessionManifest.load(path)

        self.assertEqual(self.session.state, SessionState.UNKNOWN)
        self.assertTrue(persisted.startup_request_id)
        self.assertEqual(raised.exception.request_id, persisted.startup_request_id)
        starts = [
            command for command, _kwargs in self.client.shell_calls
            if "Start-Process -FilePath" in command
        ]
        self.assertEqual(len(starts), 1)


if __name__ == "__main__":
    unittest.main()
