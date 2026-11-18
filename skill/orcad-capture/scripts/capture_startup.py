# -*- coding: utf-8 -*-
"""Authorized Capture launch and Tcl READY orchestration owned by the Skill.

All Host-B operations use generic AirBridge Shell and RFS APIs.  This module
contains no Cadence-specific behavior in AirBridge Core and must never be
executed against a live Host B without an explicit user authorization.
"""
from __future__ import annotations

import base64
import hashlib
import json
import ntpath
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Mapping, Optional

from airbridge_client import (
    AirBridgeClientError,
    AirBridgeHTTPError,
    AirBridgeTransportError,
)
from capture_process import (
    CaptureProcessCandidate,
    CaptureProcessDisposition,
    CaptureProcessInspector,
)
from capture_session import (
    BridgeReadyEvidence,
    CaptureAuthorizationError,
    CaptureConfig,
    CaptureIdentityError,
    CaptureSession,
    CaptureSessionError,
    CaptureStateTransitionError,
    ProcessIdentity,
    SessionManifest,
    SessionState,
)


class CaptureStartupError(RuntimeError):
    """The authorized Capture startup workflow failed safely."""


class CaptureStartupOutcomeUnknown(CaptureStartupError):
    """A remote startup or file operation may have happened, but is unconfirmed."""

    def __init__(self, message: str, request_id: Optional[str] = None):
        self.request_id = request_id
        super().__init__(message)


@dataclass(frozen=True)
class CaptureStartupResult:
    session_id: str
    pid: int
    capture_version: str
    tcl_version: str
    remote_session_dir: str
    bridge_path: str
    hook_path: str
    bridge_sha256: str
    hook_sha256: str
    startup_request_id: str
    hook_cleanup_completed: bool


def _ps_literal(value: str) -> str:
    """Return a PowerShell single-quoted literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _utf8_base64(value: str) -> str:
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _normalize_windows_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(str(value or "").strip()))


def _parse_utc(value: str, field: str) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaptureStartupError("{} is not a valid UTC timestamp".format(field)) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise CaptureStartupError("{} must explicitly use UTC".format(field))
    return parsed


def build_remote_environment_command() -> str:
    """Return a fixed, read-only query for Host B LocalAppData."""
    return (
        "$ErrorActionPreference = 'Stop'; "
        "[Console]::WriteLine([Environment]::GetFolderPath('LocalApplicationData'))"
    )


def build_startup_preflight_command(
    capture_exe_path: str,
    cap_autoload_dir: str,
    remote_session_dir: str,
    hook_path: str,
) -> str:
    """Build a read-only preflight; no file or process is changed."""
    exe = _ps_literal(capture_exe_path)
    autoload = _ps_literal(cap_autoload_dir)
    session = _ps_literal(remote_session_dir)
    hook = _ps_literal(hook_path)
    return (
        "$ErrorActionPreference = 'Stop'; "
        "$exePath = " + exe + "; "
        "$autoloadPath = " + autoload + "; "
        "$sessionPath = " + session + "; "
        "$hookFile = " + hook + "; "
        "$exe = Get-Item -LiteralPath $exePath -ErrorAction SilentlyContinue; "
        "$autoload = Get-Item -LiteralPath $autoloadPath -ErrorAction SilentlyContinue; "
        "$capturePath = $null; $captureVersion = $null; "
        "if ($exe) { $capturePath = $exe.FullName; $captureVersion = $exe.VersionInfo.FileVersion }; "
        "$report = [pscustomobject]@{"
        "capture_exe_exists = [bool]$exe; "
        "capture_exe_path = $capturePath; "
        "capture_file_version = $captureVersion; "
        "autoload_dir_exists = [bool]($autoload -and $autoload.PSIsContainer); "
        "session_dir_exists = [bool](Test-Path -LiteralPath $sessionPath); "
        "hook_exists = [bool](Test-Path -LiteralPath $hookFile)"
        "}; "
        "$report | ConvertTo-Json -Compress -Depth 3"
    )


def build_remove_owned_hook_command(hook_path: str, expected_sha256: str) -> str:
    """Delete only the exact Hook whose bytes match this Session Manifest."""
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_sha256 or "").lower()):
        raise CaptureStartupError("invalid expected Hook SHA-256")
    path_b64 = _utf8_base64(hook_path)
    expected = str(expected_sha256).lower()
    return (
        "$ErrorActionPreference = 'Stop'; "
        "$path = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("
        + _ps_literal(path_b64) + ")); "
        "$expected = " + _ps_literal(expected) + "; "
        "if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { "
        "[pscustomobject]@{removed=$true; already_absent=$true; sha256=$expected} "
        "| ConvertTo-Json -Compress; return }; "
        "$actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant(); "
        "if ($actual -ne $expected) { throw 'Hook hash mismatch; refusing to delete a non-owned file' }; "
        "Remove-Item -LiteralPath $path -ErrorAction Stop; "
        "[pscustomobject]@{removed=$true; already_absent=$false; sha256=$actual} "
        "| ConvertTo-Json -Compress"
    )


def build_capture_start_command(
    capture_exe_path: str, session_id: str, startup_nonce: str
) -> str:
    """Start Capture with a one-child environment ticket for the capAutoLoad Hook."""
    path = _ps_literal(capture_exe_path)
    sid = _ps_literal(str(uuid.UUID(session_id)))
    nonce = _ps_literal(startup_nonce)
    return (
        "$ErrorActionPreference = 'Stop'; "
        "$exePath = " + path + "; "
        "$workingDirectory = Split-Path -Parent $exePath; "
        "$env:AB_CAPTURE_SKILL_SESSION_ID = " + sid + "; "
        "$env:AB_CAPTURE_SKILL_STARTUP_NONCE = " + nonce + "; "
        "try { "
        "$process = Start-Process -FilePath $exePath "
        "-WorkingDirectory $workingDirectory -PassThru -ErrorAction Stop; "
        "$started = $process.StartTime.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.ffffffZ'); "
        "[pscustomobject]@{pid=[int]$process.Id; started_at_utc=$started; process_name='capture.exe'} "
        "| ConvertTo-Json -Compress "
        "} finally { "
        "Remove-Item Env:AB_CAPTURE_SKILL_SESSION_ID -ErrorAction SilentlyContinue; "
        "Remove-Item Env:AB_CAPTURE_SKILL_STARTUP_NONCE -ErrorAction SilentlyContinue "
        "}"
    )


def build_create_new_hook_command(hook_path: str, hook_content: str) -> str:
    """Create one Hook with FileMode.CreateNew so an existing file is never overwritten."""
    path_b64 = _utf8_base64(hook_path)
    content_b64 = _utf8_base64(hook_content)
    return (
        "$ErrorActionPreference = 'Stop'; "
        "$path = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("
        + _ps_literal(path_b64) + ")); "
        "$bytes = [Convert]::FromBase64String(" + _ps_literal(content_b64) + "); "
        "$stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, "
        "[IO.FileAccess]::Write, [IO.FileShare]::None); "
        "try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } "
        "finally { $stream.Dispose() }; "
        "$hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant(); "
        "[pscustomobject]@{created=$true; path=$path; size=$bytes.Length; sha256=$hash} "
        "| ConvertTo-Json -Compress"
    )


def render_capture_hook(
    session_id: str,
    startup_nonce: str,
    remote_session_dir: str,
    bridge_sha256: str,
) -> str:
    """Render a per-session capAutoLoad Hook without Tcl string interpolation."""
    try:
        normalized_id = str(uuid.UUID(session_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise CaptureStartupError("invalid Session ID for Capture Hook") from exc
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", str(startup_nonce or "")):
        raise CaptureStartupError("invalid Startup Nonce for Capture Hook")
    if not re.fullmatch(r"[0-9a-f]{64}", str(bridge_sha256 or "").lower()):
        raise CaptureStartupError("invalid Bridge SHA-256 for Capture Hook")

    bridge_path = ntpath.join(remote_session_dir, "capture_bridge.tcl")
    # Base64 literals are safe Tcl words regardless of Windows path characters.
    encoded = {
        "session_id": _utf8_base64(normalized_id),
        "startup_nonce": _utf8_base64(startup_nonce),
        "session_dir": _utf8_base64(remote_session_dir),
        "bridge_path": _utf8_base64(bridge_path),
        "bridge_sha256": _utf8_base64(bridge_sha256.lower()),
    }
    return """# AirBridge Capture Skill Hook; owned by Session {sid}
set ::ab_skill_sid [encoding convertfrom utf-8 [binary decode base64 "{sid_b64}"]]
set ::ab_skill_nonce [encoding convertfrom utf-8 [binary decode base64 "{nonce}"]]

# The hook is shared by all Capture launches. Only the exact Start-Process
# child carrying this short-lived Session ID / Nonce may load the Bridge.
if {{![info exists ::env(AB_CAPTURE_SKILL_SESSION_ID)]}} {{ return }}
if {{![info exists ::env(AB_CAPTURE_SKILL_STARTUP_NONCE)]}} {{ return }}
if {{$::env(AB_CAPTURE_SKILL_SESSION_ID) ne $::ab_skill_sid}} {{ return }}
if {{$::env(AB_CAPTURE_SKILL_STARTUP_NONCE) ne $::ab_skill_nonce}} {{ return }}
unset ::env(AB_CAPTURE_SKILL_SESSION_ID)
unset ::env(AB_CAPTURE_SKILL_STARTUP_NONCE)

set ::ab_skill_session_dir [encoding convertfrom utf-8 [binary decode base64 "{dir}"]]
set ::ab_skill_bridge_path [encoding convertfrom utf-8 [binary decode base64 "{bridge}"]]
set ::ab_skill_bridge_sha256 [encoding convertfrom utf-8 [binary decode base64 "{hash}"]]

if {{[catch {{source $::ab_skill_bridge_path}} ab_skill_source_error]}} {{
    puts stderr "AirBridge Capture Skill: Bridge source failed: $ab_skill_source_error"
    return
}}
if {{![llength [info commands ::AirBridgeSkill::bootstrap]]}} {{
    puts stderr "AirBridge Capture Skill: Bridge bootstrap command is unavailable"
    return
}}
if {{[catch {{
    ::AirBridgeSkill::bootstrap \
        $::ab_skill_sid \
        $::ab_skill_nonce \
        $::ab_skill_session_dir \
        $::ab_skill_bridge_sha256
}} ab_skill_bootstrap_error]}} {{
    puts stderr "AirBridge Capture Skill: READY bootstrap failed: $ab_skill_bootstrap_error"
    return
}}
""".format(
        sid=normalized_id,
        sid_b64=encoded["session_id"],
        nonce=encoded["startup_nonce"],
        dir=encoded["session_dir"],
        bridge=encoded["bridge_path"],
        hash=encoded["bridge_sha256"],
    )


class CaptureStartupCoordinator:
    """Orchestrate one explicitly authorized Capture start and READY handshake."""

    def __init__(
        self,
        client: Any,
        config: CaptureConfig,
        *,
        inspector: Optional[CaptureProcessInspector] = None,
        bridge_path: Optional[str | Path] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        process_discovery_timeout_seconds: float = 10.0,
    ):
        if client is None or not callable(getattr(client, "shell_execute", None)):
            raise TypeError("client must provide shell_execute()")
        if not isinstance(config, CaptureConfig):
            raise TypeError("config must be a CaptureConfig")
        if not config.capture_exe_path or not config.cap_autoload_dir:
            raise CaptureSessionError(
                "explicit AIRBRIDGE_CAPTURE_EXE and AIRBRIDGE_CAPTURE_AUTOLOAD_DIR are required"
            )
        if not config.expected_capture_version:
            raise CaptureSessionError(
                "AIRBRIDGE_CAPTURE_VERSION must be selected before startup"
            )
        if (
            isinstance(process_discovery_timeout_seconds, bool)
            or not isinstance(process_discovery_timeout_seconds, (int, float))
            or process_discovery_timeout_seconds <= 0
            or process_discovery_timeout_seconds > 60
        ):
            raise ValueError("process_discovery_timeout_seconds must be in (0, 60]")
        self.client = client
        self.config = config
        self.inspector = inspector or CaptureProcessInspector(
            client, shell_timeout_seconds=config.shell_timeout_seconds
        )
        self.bridge_path = Path(bridge_path) if bridge_path else (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "bridge"
            / "capture_bridge.tcl"
        )
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.process_discovery_timeout_seconds = float(
            process_discovery_timeout_seconds
        )

    def start(
        self,
        session: CaptureSession,
        *,
        user_authorized: bool = False,
        manifest_path: Optional[str | Path] = None,
    ) -> CaptureStartupResult:
        """Install the unique Hook and launch Capture once after explicit authorization."""
        if user_authorized is not True:
            raise CaptureAuthorizationError(
                "starting Capture and installing its capAutoLoad Hook requires explicit user authorization"
            )
        if manifest_path is None:
            raise CaptureSessionError(
                "a local Manifest path is required so Host-A can persist startup ownership before side effects"
            )
        if not isinstance(session, CaptureSession):
            raise TypeError("session must be a CaptureSession")
        if session.state is not SessionState.ABSENT:
            raise CaptureStateTransitionError(
                "each Capture launch requires a fresh ABSENT Session ID / Nonce; "
                "create a new SessionManifest after a prior session is closed"
            )
        if session.manifest.pid is not None or session.manifest.reconciliation_required:
            raise CaptureStateTransitionError(
                "a Manifest with a prior PID or unresolved outcome cannot start a new Capture process"
            )

        session.begin_inspection()
        self._save_manifest(session, manifest_path)

        # A Capture process that already exists is never adopted implicitly.
        assessment = self.inspector.inspect_session(session.manifest)
        if assessment.disposition is not CaptureProcessDisposition.NO_PROCESS:
            raise CaptureStartupError(
                "Capture startup refused: {}".format(
                    assessment.detail or assessment.disposition.value
                )
            )

        local_app_data = self._get_remote_local_app_data()
        remote_session_root = self._resolve_remote_session_root(local_app_data)
        remote_session_dir = ntpath.join(
            remote_session_root, session.manifest.session_id
        )
        bridge_remote_path = ntpath.join(remote_session_dir, "capture_bridge.tcl")
        hook_name = "ab_skill_{}.tcl".format(session.manifest.session_id)
        hook_remote_path = ntpath.join(self.config.cap_autoload_dir, hook_name)

        preflight = self._run_shell_json(
            build_startup_preflight_command(
                self.config.capture_exe_path,
                self.config.cap_autoload_dir,
                remote_session_dir,
                hook_remote_path,
            ),
            request_id=str(uuid.uuid4()),
            label="Capture startup preflight",
        )
        self._validate_preflight(preflight)

        bridge_text = self.bridge_path.read_text(encoding="utf-8")
        bridge_bytes = bridge_text.encode("utf-8")
        bridge_sha256 = hashlib.sha256(bridge_bytes).hexdigest()
        hook_text = render_capture_hook(
            session.manifest.session_id,
            session.manifest.startup_nonce,
            remote_session_dir,
            bridge_sha256,
        )
        hook_sha256 = hashlib.sha256(hook_text.encode("utf-8")).hexdigest()

        session.manifest.remote_session_dir = remote_session_dir
        session.manifest.hook_path = hook_remote_path
        session.manifest.hook_sha256 = hook_sha256
        session.manifest.bridge_sha256 = bridge_sha256
        session.manifest.updated_at_utc = self._utc_now()
        self._save_manifest(session, manifest_path)

        # Create only this UUID-named session tree. No existing remote path is reused.
        self._mutate_remote(session, manifest_path, "mkdir", {"path": remote_session_dir})
        for child in ("requests", "running", "results", "logs"):
            self._mutate_remote(
                session,
                manifest_path,
                "mkdir",
                {"path": ntpath.join(remote_session_dir, child)},
            )

        self._mutate_remote(
            session,
            manifest_path,
            "write",
            {
                "path": bridge_remote_path,
                "content": _utf8_base64(bridge_text),
                "is_base64": True,
                "mode": "w",
            },
        )
        try:
            self._verify_remote_text_file(bridge_remote_path, bridge_bytes)
        except CaptureStartupOutcomeUnknown as exc:
            self._mark_unknown(session, "BRIDGE_UPLOAD_VERIFY_UNKNOWN", str(exc))
            self._save_manifest(session, manifest_path)
            raise

        # Hook creation uses .NET CreateNew to close the check/write race and
        # guarantee an existing Cadence/user file is never overwritten.
        hook_install_request_id = str(uuid.uuid4())
        session.manifest.hook_install_request_id = hook_install_request_id
        self._save_manifest(session, manifest_path)
        try:
            hook_receipt = self._run_shell_json(
                build_create_new_hook_command(hook_remote_path, hook_text),
                request_id=hook_install_request_id,
                label="Capture Hook installation",
            )
        except CaptureStartupOutcomeUnknown as exc:
            self._mark_unknown(
                session,
                "HOOK_INSTALL_OUTCOME_UNKNOWN",
                str(exc),
                request_id=hook_install_request_id,
            )
            self._save_manifest(session, manifest_path)
            raise
        except CaptureStartupError as exc:
            self._mark_unknown(
                session,
                "HOOK_INSTALL_RESULT_UNKNOWN",
                str(exc),
                request_id=hook_install_request_id,
            )
            self._save_manifest(session, manifest_path)
            raise CaptureStartupOutcomeUnknown(
                "Capture Hook may have been created but its receipt is not trustworthy",
                request_id=hook_install_request_id,
            ) from exc
        if hook_receipt.get("created") is not True:
            self._mark_failed(session, "HOOK_INSTALL_FAILED", "remote Hook was not created")
            raise CaptureStartupError("Capture Hook installation did not return a creation receipt")
        if (
            str(hook_receipt.get("path") or "").casefold()
            != hook_remote_path.casefold()
            or str(hook_receipt.get("sha256") or "").lower() != hook_sha256
            or int(hook_receipt.get("size", -1)) != len(hook_text.encode("utf-8"))
        ):
            self._mark_unknown(
                session,
                "HOOK_INSTALL_RECEIPT_MISMATCH",
                "Hook file may have been created but the returned identity/hash did not match",
                request_id=hook_install_request_id,
            )
            self._save_manifest(session, manifest_path)
            raise CaptureStartupOutcomeUnknown(
                "Capture Hook installation receipt did not match the intended file",
                request_id=hook_install_request_id,
            )

        session.begin_start()
        self._save_manifest(session, manifest_path)
        startup_request_id = str(uuid.uuid4())
        session.manifest.startup_request_id = startup_request_id
        self._save_manifest(session, manifest_path)
        try:
            start_receipt = self._run_shell_json(
                build_capture_start_command(
                    self.config.capture_exe_path,
                    session.manifest.session_id,
                    session.manifest.startup_nonce,
                ),
                request_id=startup_request_id,
                label="Capture process launch",
            )
        except CaptureStartupOutcomeUnknown as exc:
            self._mark_unknown(
                session,
                "CAPTURE_START_OUTCOME_UNKNOWN",
                str(exc),
                request_id=startup_request_id,
            )
            self._save_manifest(session, manifest_path)
            raise
        except CaptureStartupError as exc:
            # Start-Process may have succeeded before a later PowerShell/output
            # failure. Treat any untrusted post-submit result as indeterminate.
            self._mark_unknown(
                session,
                "CAPTURE_START_RESULT_UNTRUSTED",
                str(exc),
                request_id=startup_request_id,
            )
            self._save_manifest(session, manifest_path)
            raise CaptureStartupOutcomeUnknown(
                "Capture may have started but the Shell result was not trustworthy",
                request_id=startup_request_id,
            ) from exc

        pid = start_receipt.get("pid")
        started_at = start_receipt.get("started_at_utc")
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
            or not isinstance(started_at, str)
        ):
            self._mark_unknown(
                session,
                "CAPTURE_START_RECEIPT_INVALID",
                "Start-Process returned no valid PID / process start timestamp",
                request_id=startup_request_id,
            )
            self._save_manifest(session, manifest_path)
            raise CaptureStartupOutcomeUnknown(
                "Capture process may have started but its identity receipt is invalid",
                request_id=startup_request_id,
            )

        try:
            candidate = self._wait_for_started_process(pid, started_at)
            session.record_process_identity(
                ProcessIdentity(
                    pid=candidate.pid,
                    executable_path=str(candidate.executable_path or ""),
                    started_at_utc=str(candidate.started_at_utc or ""),
                    file_version=str(candidate.file_version or ""),
                )
            )
            session.mark_bridge_pending(
                remote_session_dir=remote_session_dir,
                hook_path=hook_remote_path,
                hook_sha256=hook_sha256,
                bridge_sha256=bridge_sha256,
            )
        except CaptureStartupOutcomeUnknown as exc:
            self._mark_unknown(
                session,
                "CAPTURE_PROCESS_IDENTITY_UNKNOWN",
                str(exc),
                request_id=startup_request_id,
            )
            self._save_manifest(session, manifest_path)
            raise
        except (CaptureIdentityError, CaptureSessionError) as exc:
            self._mark_unknown(
                session,
                "CAPTURE_PROCESS_IDENTITY_MISMATCH",
                str(exc),
                request_id=startup_request_id,
            )
            self._save_manifest(session, manifest_path)
            raise CaptureStartupOutcomeUnknown(
                "Capture may be running but its identity could not be verified",
                request_id=startup_request_id,
            ) from exc
        self._save_manifest(session, manifest_path)

        ready_path = ntpath.join(remote_session_dir, "bridge.ready")
        try:
            ready_payload = self._wait_for_ready(ready_path)
        except CaptureStartupOutcomeUnknown as exc:
            self._mark_unknown(session, "BRIDGE_READY_UNKNOWN", str(exc))
            self._save_manifest(session, manifest_path)
            raise
        except CaptureStartupError as exc:
            self._mark_failed(session, "BRIDGE_READY_INVALID", str(exc))
            self._save_manifest(session, manifest_path)
            raise
        try:
            if ready_payload.get("schema_version") != 1:
                raise CaptureIdentityError("READY schema version does not match")
            evidence = BridgeReadyEvidence(
                session_id=ready_payload.get("session_id", ""),
                startup_nonce=ready_payload.get("startup_nonce", ""),
                runtime_pid=ready_payload.get("runtime_pid", 0),
                tcl_version=ready_payload.get("tcl_version", ""),
                bridge_sha256=ready_payload.get("bridge_sha256", ""),
                schema_version=ready_payload.get("schema_version", 0),
            )
            session.mark_ready(evidence)
        except (CaptureIdentityError, CaptureSessionError, TypeError, ValueError) as exc:
            self._mark_failed(session, "BRIDGE_READY_IDENTITY_MISMATCH", str(exc))
            self._save_manifest(session, manifest_path)
            raise CaptureStartupError(
                "Capture Bridge READY failed identity verification: {}".format(exc)
            ) from exc

        hook_cleanup_completed = self._cleanup_owned_hook(
            session, manifest_path, hook_remote_path, hook_sha256
        )
        self._save_manifest(session, manifest_path)
        return CaptureStartupResult(
            session_id=session.manifest.session_id,
            pid=int(session.manifest.pid or 0),
            capture_version=str(session.manifest.capture_version or ""),
            tcl_version=str(session.manifest.tcl_version or ""),
            remote_session_dir=remote_session_dir,
            bridge_path=bridge_remote_path,
            hook_path=hook_remote_path,
            bridge_sha256=bridge_sha256,
            hook_sha256=hook_sha256,
            startup_request_id=startup_request_id,
            hook_cleanup_completed=hook_cleanup_completed,
        )

    def _cleanup_owned_hook(
        self,
        session: CaptureSession,
        manifest_path: str | Path,
        hook_path: str,
        hook_sha256: str,
    ) -> bool:
        request_id = str(uuid.uuid4())
        session.manifest.hook_cleanup_request_id = request_id
        self._save_manifest(session, manifest_path)
        try:
            receipt = self._run_shell_json(
                build_remove_owned_hook_command(hook_path, hook_sha256),
                request_id=request_id,
                label="Capture Hook cleanup",
            )
        except (CaptureStartupOutcomeUnknown, CaptureStartupError):
            # READY remains valid. The unique Hook is still gated by its
            # one-child launch ticket; do not retry an ambiguous delete.
            self._save_manifest(session, manifest_path)
            return False
        if (
            receipt.get("removed") is True
            and str(receipt.get("sha256") or "").lower() == hook_sha256
        ):
            session.manifest.hook_cleanup_completed = True
            session.manifest.hook_removed_at_utc = self._utc_now()
            self._save_manifest(session, manifest_path)
            return True
        self._save_manifest(session, manifest_path)
        return False

    def _get_remote_local_app_data(self) -> str:
        response = self._run_shell(
            build_remote_environment_command(),
            request_id=str(uuid.uuid4()),
            label="Host-B LocalAppData discovery",
        )
        stdout = str(response.get("stdout") or "").strip()
        # PowerShell may emit a leading blank line depending on host profile.
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise CaptureStartupError("Host B did not return LocalAppData")
        path = lines[-1]
        if not PureWindowsPath(path).is_absolute():
            raise CaptureStartupError("Host-B LocalAppData is not an absolute Windows path")
        return path

    def _resolve_remote_session_root(self, local_app_data: str) -> str:
        configured = str(self.config.remote_session_root or "").strip()
        if "%LOCALAPPDATA%" in configured.upper():
            marker = "%LOCALAPPDATA%"
            index = configured.upper().find(marker)
            suffix = configured[index + len(marker):].lstrip("\\/")
            root = ntpath.join(local_app_data, suffix) if suffix else local_app_data
        else:
            root = configured
        if not ntpath.isabs(root):
            raise CaptureSessionError(
                "remote_session_root must be absolute or begin with %LOCALAPPDATA%"
            )
        return ntpath.normpath(root)

    def _validate_preflight(self, report: Mapping[str, Any]) -> None:
        if report.get("capture_exe_exists") is not True:
            raise CaptureStartupError("configured capture.exe does not exist on Host B")
        if _normalize_windows_path(
            str(report.get("capture_exe_path") or "")
        ) != _normalize_windows_path(str(self.config.capture_exe_path)):
            raise CaptureStartupError("Host-B capture.exe path differs from configured path")
        if str(report.get("capture_file_version") or "").strip() != str(
            self.config.expected_capture_version
        ):
            raise CaptureStartupError("Host-B Capture file version differs from selected version")
        if report.get("autoload_dir_exists") is not True:
            raise CaptureStartupError("configured capAutoLoad directory does not exist")
        if report.get("session_dir_exists") is True:
            raise CaptureStartupError(
                "new Capture Session directory already exists; refusing to reuse it"
            )
        if report.get("hook_exists") is True:
            raise CaptureStartupError(
                "unique capAutoLoad Hook path already exists; refusing to overwrite it"
            )

    def _run_shell(
        self, command: str, *, request_id: str, label: str
    ) -> Mapping[str, Any]:
        try:
            response = self.client.shell_execute(
                command,
                shell="powershell",
                task_timeout_seconds=self.config.shell_timeout_seconds,
                async_mode=False,
                request_id=request_id,
            )
        except AirBridgeTransportError as exc:
            raise CaptureStartupOutcomeUnknown(
                "{} transport failed; request outcome is unknown".format(label),
                request_id=request_id,
            ) from exc
        except AirBridgeHTTPError as exc:
            if exc.status_code >= 500:
                raise CaptureStartupOutcomeUnknown(
                    "{} returned HTTP {}; remote outcome may be unknown".format(
                        label, exc.status_code
                    ),
                    request_id=request_id,
                ) from exc
            raise CaptureStartupError(
                "{} was rejected by AirBridge (HTTP {})".format(label, exc.status_code)
            ) from exc
        return self._unwrap_shell_result(response, request_id, label)

    def _run_shell_json(
        self, command: str, *, request_id: str, label: str
    ) -> Mapping[str, Any]:
        result = self._run_shell(command, request_id=request_id, label=label)
        stdout = str(result.get("stdout") or "").strip()
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise CaptureStartupError(
                "{} did not return valid JSON".format(label)
            ) from exc
        if not isinstance(value, Mapping):
            raise CaptureStartupError("{} JSON result is not an object".format(label))
        return value

    @staticmethod
    def _unwrap_shell_result(
        response: Any, request_id: str, label: str
    ) -> Mapping[str, Any]:
        if not isinstance(response, Mapping):
            raise CaptureStartupError("{} returned a non-object response".format(label))
        state = str(response.get("state") or "").lower()
        if state in {"accepted", "pending", "unknown"}:
            raise CaptureStartupOutcomeUnknown(
                "{} has indeterminate request state {}".format(label, state),
                request_id=request_id,
            )
        if state != "completed":
            raise CaptureStartupError(
                "{} did not complete (state={})".format(label, state or "missing")
            )
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise CaptureStartupError("{} did not include a Shell result".format(label))
        if result.get("success") is not True:
            raise CaptureStartupError(
                "{} failed: {}".format(label, result.get("error") or result.get("stderr") or "unknown error")
            )
        exit_code = result.get("exit_code")
        if isinstance(exit_code, bool) or exit_code != 0:
            raise CaptureStartupError(
                "{} returned a non-zero exit code".format(label)
            )
        return result

    def _mutate_remote(
        self,
        session: CaptureSession,
        manifest_path: str | Path,
        action: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        try:
            result = self._rfs_checked(action, payload)
        except CaptureStartupOutcomeUnknown as exc:
            self._mark_unknown(
                session,
                "RFS_{}_OUTCOME_UNKNOWN".format(action.upper()),
                str(exc),
            )
            self._save_manifest(session, manifest_path)
            raise
        self._save_manifest(session, manifest_path)
        return result

    def _rfs_checked(self, action: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            response = self.client.filesystem(
                action, payload, timeout_seconds=self.config.shell_timeout_seconds + 10
            )
        except (AirBridgeTransportError, AirBridgeHTTPError) as exc:
            raise CaptureStartupOutcomeUnknown(
                "RFS {} may have completed but its result was not confirmed".format(action)
            ) from exc
        if not isinstance(response, Mapping) or response.get("success") is not True:
            raise CaptureStartupOutcomeUnknown(
                "RFS {} returned no trustworthy success receipt".format(action)
            )
        return response

    def _verify_remote_text_file(self, remote_path: str, expected: bytes) -> None:
        expected_base64 = base64.b64encode(expected).decode("ascii")
        try:
            response = self.client.filesystem(
                "read",
                {"path": remote_path, "as_base64": True, "max_preview_bytes": 65536},
                timeout_seconds=self.config.shell_timeout_seconds + 10,
            )
        except (AirBridgeTransportError, AirBridgeHTTPError) as exc:
            raise CaptureStartupOutcomeUnknown(
                "RFS read-back for {} could not be confirmed".format(remote_path)
            ) from exc
        if (
            not isinstance(response, Mapping)
            or response.get("success") is not True
            or response.get("is_truncated") is True
            or response.get("encoding") != "base64"
            or response.get("content") != expected_base64
        ):
            raise CaptureStartupOutcomeUnknown(
                "RFS read-back did not exactly match the intended file {}".format(
                    remote_path
                )
            )

    def _wait_for_started_process(
        self, pid: int, started_at_utc: str
    ) -> CaptureProcessCandidate:
        expected_started = _parse_utc(started_at_utc, "Start-Process timestamp")
        deadline = self.monotonic_fn() + self.process_discovery_timeout_seconds
        while True:
            candidates = self.inspector.inspect()
            if len(candidates) > 1:
                raise CaptureStartupOutcomeUnknown(
                    "multiple capture.exe processes appeared during startup; no instance was adopted"
                )
            if candidates:
                candidate = candidates[0]
                if candidate.pid != pid:
                    raise CaptureStartupOutcomeUnknown(
                        "observed Capture PID differs from Start-Process PID"
                    )
                if (
                    not candidate.executable_path
                    or _normalize_windows_path(candidate.executable_path)
                    != _normalize_windows_path(str(self.config.capture_exe_path))
                    or not candidate.file_version
                    or candidate.file_version != self.config.expected_capture_version
                    or not candidate.started_at_utc
                ):
                    raise CaptureStartupOutcomeUnknown(
                        "Capture process path, version or start timestamp is incomplete"
                    )
                observed_started = _parse_utc(
                    candidate.started_at_utc, "CIM process timestamp"
                )
                if abs((observed_started - expected_started).total_seconds()) > 2.0:
                    raise CaptureStartupOutcomeUnknown(
                        "CIM process start timestamp does not match Start-Process receipt"
                    )
                return candidate
            if self.monotonic_fn() >= deadline:
                raise CaptureStartupOutcomeUnknown(
                    "Capture process did not become visible in the read-only inventory"
                )
            self.sleep_fn(min(self.config.poll_interval_seconds, 0.5))

    def _wait_for_ready(self, ready_path: str) -> Mapping[str, Any]:
        deadline = self.monotonic_fn() + self.config.bridge_ready_timeout_seconds
        last_missing = None
        while True:
            try:
                response = self.client.filesystem(
                    "read",
                    {"path": ready_path, "as_base64": False, "max_preview_bytes": 16384},
                    timeout_seconds=self.config.shell_timeout_seconds + 10,
                )
                if not isinstance(response, Mapping) or response.get("success") is not True:
                    raise CaptureStartupOutcomeUnknown(
                        "Bridge READY read returned no trustworthy response"
                    )
                if response.get("is_truncated") is True:
                    raise CaptureStartupError("Bridge READY file was truncated")
                raw = response.get("content")
                if not isinstance(raw, str):
                    raise CaptureStartupError("Bridge READY file did not contain text")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise CaptureStartupError("Bridge READY file is malformed JSON") from exc
                if not isinstance(payload, Mapping):
                    raise CaptureStartupError("Bridge READY payload is not an object")
                return payload
            except AirBridgeHTTPError as exc:
                error_text = json.dumps(exc.payload, ensure_ascii=False).casefold()
                if exc.status_code == 500 and any(
                    phrase in error_text
                    for phrase in ("文件不存在", "file not found", "no such file")
                ):
                    last_missing = exc
                else:
                    raise CaptureStartupOutcomeUnknown(
                        "Bridge READY could not be read; remote state is unknown"
                    ) from exc
            except AirBridgeTransportError as exc:
                last_missing = exc

            if self.monotonic_fn() >= deadline:
                raise CaptureStartupOutcomeUnknown(
                    "Capture Bridge READY timed out; keep the Hook and process for reconciliation"
                ) from last_missing
            self.sleep_fn(min(self.config.poll_interval_seconds, 1.0))

    def _save_manifest(
        self, session: CaptureSession, manifest_path: str | Path
    ) -> None:
        session.manifest.updated_at_utc = self._utc_now()
        session.manifest.save(manifest_path)

    @staticmethod
    def _mark_failed(session: CaptureSession, code: str, message: str) -> None:
        if session.state not in (SessionState.FAILED, SessionState.CLOSED):
            session.mark_failed(code, message)

    @staticmethod
    def _mark_unknown(
        session: CaptureSession,
        code: str,
        message: str,
        request_id: Optional[str] = None,
    ) -> None:
        if session.state not in (SessionState.UNKNOWN, SessionState.CLOSED):
            session.mark_unknown(code, message)

    @staticmethod
    def _utc_now() -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
