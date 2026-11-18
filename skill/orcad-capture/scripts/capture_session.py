# -*- coding: utf-8 -*-
"""Capture Skill-owned session configuration, manifest and state model.

No Capture process is launched or controlled by this module.  It models the
decisions that the Capture Skill will make in later M3.2 phases.
"""
from __future__ import annotations

import hmac
import json
import math
import ntpath
import os
import re
import secrets
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import PureWindowsPath
from pathlib import Path
from typing import Any, Mapping, Optional


DEFAULT_API_BASE_URL = "http://127.0.0.1:8888"
DEFAULT_REMOTE_SESSION_ROOT = (
    r"%LOCALAPPDATA%\AirBridgeSkills\Capture\sessions"
)


class CaptureSessionError(RuntimeError):
    """Base class for Capture Skill session errors."""


class CaptureConfigurationError(CaptureSessionError):
    """Invalid or incomplete Capture Skill configuration."""


class CaptureStateTransitionError(CaptureSessionError):
    """A state transition is not allowed by the Capture Skill state machine."""


class CaptureIdentityError(CaptureSessionError):
    """Process or Bridge identity evidence does not match this session."""


class CaptureAuthorizationError(CaptureSessionError):
    """A lifecycle operation requires explicit user authorization."""


class SessionState(str, Enum):
    ABSENT = "absent"
    INSPECTING = "inspecting"
    STARTING = "starting"
    BRIDGE_PENDING = "bridge_pending"
    READY = "ready"
    BUSY = "busy"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"
    UNKNOWN = "unknown"


_ALLOWED_TRANSITIONS = {
    SessionState.ABSENT: {SessionState.INSPECTING, SessionState.FAILED},
    SessionState.INSPECTING: {
        SessionState.STARTING,
        SessionState.BRIDGE_PENDING,
        SessionState.FAILED,
        SessionState.UNKNOWN,
    },
    SessionState.STARTING: {
        SessionState.BRIDGE_PENDING,
        SessionState.FAILED,
        SessionState.UNKNOWN,
    },
    SessionState.BRIDGE_PENDING: {
        SessionState.READY,
        SessionState.FAILED,
        SessionState.UNKNOWN,
    },
    SessionState.READY: {
        SessionState.BUSY,
        SessionState.CLOSING,
        SessionState.FAILED,
        SessionState.UNKNOWN,
    },
    SessionState.BUSY: {
        SessionState.READY,
        SessionState.FAILED,
        SessionState.UNKNOWN,
    },
    SessionState.CLOSING: {
        SessionState.CLOSED,
        SessionState.FAILED,
        SessionState.UNKNOWN,
    },
    SessionState.CLOSED: {SessionState.INSPECTING},
    SessionState.FAILED: {SessionState.INSPECTING, SessionState.UNKNOWN},
    # UNKNOWN may only return to read-only inspection. It cannot be restarted
    # or resubmitted until the Skill has reconciled the original outcome.
    SessionState.UNKNOWN: {SessionState.INSPECTING},
}


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _validate_timestamp(value: str, field_name: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError("timestamp must be explicitly UTC")
    except ValueError as exc:
        raise CaptureSessionError("{} must be an ISO-8601 timestamp".format(
            field_name
        )) from exc
    return raw


def _normalize_windows_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(str(value).strip()))

def _sanitize_error_message(value: Any) -> str:
    """Avoid persisting common Bearer/token forms in a Session Manifest."""
    message = str(value or "")[:1024]
    message = re.sub(
        r"(?i)Bearer\s+[^\s,;]+",
        "Bearer [REDACTED]",
        message,
    )
    message = re.sub(
        r"(?i)abg1\.[0-9a-f]{32}\.[A-Za-z0-9_-]+",
        "[REDACTED_AGENT_TOKEN]",
        message,
    )
    return message[:512]


@dataclass(frozen=True)
class CaptureConfig:
    """Non-secret Skill configuration; the Agent token is never stored here."""

    api_base_url: str = DEFAULT_API_BASE_URL
    capture_exe_path: Optional[str] = None
    cap_autoload_dir: Optional[str] = None
    expected_capture_version: Optional[str] = None
    expected_tcl_version: Optional[str] = "8.6.0"
    remote_session_root: str = DEFAULT_REMOTE_SESSION_ROOT
    shell_timeout_seconds: int = 60
    bridge_ready_timeout_seconds: int = 45
    poll_interval_seconds: float = 0.5

    def __post_init__(self) -> None:
        # Import locally to keep the state model independently usable.
        from airbridge_client import (
            AirBridgeConfigurationError,
            _validate_local_base_url,
        )

        try:
            _validate_local_base_url(self.api_base_url)
        except AirBridgeConfigurationError as exc:
            raise CaptureConfigurationError(
                "api_base_url must be a valid loopback HTTP(S) origin"
            ) from exc
        if self.capture_exe_path:
            win_path = PureWindowsPath(self.capture_exe_path)
            if not win_path.is_absolute() or win_path.name.casefold() != "capture.exe":
                raise CaptureConfigurationError(
                    "capture_exe_path must be an absolute path to capture.exe"
                )
        if self.cap_autoload_dir:
            autoload_path = PureWindowsPath(self.cap_autoload_dir)
            if not autoload_path.is_absolute():
                raise CaptureConfigurationError(
                    "cap_autoload_dir must be an absolute Windows path"
                )
        if bool(self.capture_exe_path) != bool(self.cap_autoload_dir):
            raise CaptureConfigurationError(
                "capture_exe_path and cap_autoload_dir must be configured together"
            )
        if not str(self.remote_session_root or "").strip():
            raise CaptureConfigurationError("remote_session_root must not be empty")
        if (
            isinstance(self.shell_timeout_seconds, bool)
            or not isinstance(self.shell_timeout_seconds, int)
            or not 1 <= self.shell_timeout_seconds <= 300
        ):
            raise CaptureConfigurationError(
                "shell_timeout_seconds must be between 1 and 300"
            )
        if (
            isinstance(self.bridge_ready_timeout_seconds, bool)
            or not isinstance(self.bridge_ready_timeout_seconds, int)
            or not 1 <= self.bridge_ready_timeout_seconds <= 600
        ):
            raise CaptureConfigurationError(
                "bridge_ready_timeout_seconds must be between 1 and 600"
            )
        if (
            isinstance(self.poll_interval_seconds, bool)
            or not isinstance(self.poll_interval_seconds, (int, float))
            or not math.isfinite(float(self.poll_interval_seconds))
            or self.poll_interval_seconds <= 0
        ):
            raise CaptureConfigurationError(
                "poll_interval_seconds must be a finite positive number"
            )

    @classmethod
    def from_env(
        cls, environ: Optional[Mapping[str, str]] = None
    ) -> "CaptureConfig":
        """Load non-secret configuration from the Host-A Agent environment."""
        source = os.environ if environ is None else environ

        def optional(name: str) -> Optional[str]:
            value = str(source.get(name, "") or "").strip()
            return value or None

        def integer(name: str, default: int) -> int:
            raw = source.get(name, str(default))
            try:
                return int(raw)
            except (TypeError, ValueError) as exc:
                raise CaptureConfigurationError(
                    "{} must be an integer".format(name)
                ) from exc

        def number(name: str, default: float) -> float:
            raw = source.get(name, str(default))
            try:
                return float(raw)
            except (TypeError, ValueError) as exc:
                raise CaptureConfigurationError(
                    "{} must be a number".format(name)
                ) from exc

        return cls(
            api_base_url=source.get("AIRBRIDGE_API_BASE_URL", DEFAULT_API_BASE_URL),
            capture_exe_path=optional("AIRBRIDGE_CAPTURE_EXE"),
            cap_autoload_dir=optional("AIRBRIDGE_CAPTURE_AUTOLOAD_DIR"),
            expected_capture_version=optional("AIRBRIDGE_CAPTURE_VERSION"),
            expected_tcl_version=str(
                source.get("AIRBRIDGE_CAPTURE_TCL_VERSION", "8.6.0") or ""
            ).strip() or None,
            remote_session_root=source.get(
                "AIRBRIDGE_CAPTURE_SESSION_ROOT", DEFAULT_REMOTE_SESSION_ROOT
            ),
            shell_timeout_seconds=integer("AIRBRIDGE_CAPTURE_SHELL_TIMEOUT", 60),
            bridge_ready_timeout_seconds=integer(
                "AIRBRIDGE_CAPTURE_READY_TIMEOUT", 45
            ),
            poll_interval_seconds=number("AIRBRIDGE_CAPTURE_POLL_INTERVAL", 0.5),
        )


@dataclass
class SessionManifest:
    """Persistable identity record for one Skill-owned Capture session."""

    session_id: str
    startup_nonce: str
    state: str = SessionState.ABSENT.value
    schema_version: int = 1
    created_at_utc: str = ""
    updated_at_utc: str = ""
    capture_exe_path: Optional[str] = None
    cap_autoload_dir: Optional[str] = None
    expected_capture_version: Optional[str] = None
    expected_tcl_version: Optional[str] = None
    capture_version: Optional[str] = None
    tcl_version: Optional[str] = None
    pid: Optional[int] = None
    process_started_at_utc: Optional[str] = None
    process_exit_confirmed_at_utc: Optional[str] = None
    remote_session_dir: Optional[str] = None
    hook_path: Optional[str] = None
    hook_sha256: Optional[str] = None
    bridge_sha256: Optional[str] = None
    active_request_id: Optional[str] = None
    reconciliation_required: bool = False
    last_error_code: Optional[str] = None
    last_error_message: Optional[str] = None

    def __post_init__(self) -> None:
        try:
            self.session_id = str(uuid.UUID(self.session_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise CaptureSessionError("session_id must be a UUID") from exc
        if (
            not isinstance(self.startup_nonce, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", self.startup_nonce)
        ):
            raise CaptureSessionError(
                "startup_nonce must be a 32-128 character base64url token"
            )
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != 1
        ):
            raise CaptureSessionError("unsupported Session Manifest schema version")
        try:
            self.state = SessionState(self.state).value
        except ValueError as exc:
            raise CaptureSessionError("invalid Capture session state") from exc
        if not self.created_at_utc:
            self.created_at_utc = _utc_now()
        if not self.updated_at_utc:
            self.updated_at_utc = self.created_at_utc
        self.created_at_utc = _validate_timestamp(
            self.created_at_utc, "created_at_utc"
        )
        self.updated_at_utc = _validate_timestamp(
            self.updated_at_utc, "updated_at_utc"
        )
        if self.capture_exe_path and (
            not PureWindowsPath(self.capture_exe_path).is_absolute()
            or PureWindowsPath(self.capture_exe_path).name.casefold() != "capture.exe"
        ):
            raise CaptureSessionError(
                "capture_exe_path must be an absolute path to capture.exe"
            )
        if self.cap_autoload_dir and not PureWindowsPath(self.cap_autoload_dir).is_absolute():
            raise CaptureSessionError("cap_autoload_dir must be an absolute Windows path")
        if bool(self.capture_exe_path) != bool(self.cap_autoload_dir):
            raise CaptureSessionError(
                "capture_exe_path and cap_autoload_dir must be configured together"
            )
        if not isinstance(self.reconciliation_required, bool):
            raise CaptureSessionError("reconciliation_required must be a boolean")
        if self.state == SessionState.UNKNOWN.value and not self.reconciliation_required:
            raise CaptureSessionError(
                "UNKNOWN sessions must require explicit reconciliation"
            )
        if self.pid is not None and (
            isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 0
        ):
            raise CaptureSessionError("pid must be a positive integer")
        if self.process_started_at_utc:
            self.process_started_at_utc = _validate_timestamp(
                self.process_started_at_utc, "process_started_at_utc"
            )
        if self.process_exit_confirmed_at_utc:
            self.process_exit_confirmed_at_utc = _validate_timestamp(
                self.process_exit_confirmed_at_utc,
                "process_exit_confirmed_at_utc",
            )
        if self.process_exit_confirmed_at_utc and not self.process_started_at_utc:
            raise CaptureSessionError(
                "process exit confirmation requires an original process start time"
            )
        if self.state == SessionState.CLOSED.value and not self.process_exit_confirmed_at_utc:
            raise CaptureSessionError(
                "CLOSED sessions require confirmed process-exit evidence"
            )
        if (
            self.process_exit_confirmed_at_utc
            and self.state != SessionState.CLOSED.value
        ):
            raise CaptureSessionError(
                "process-exit evidence is only valid for a CLOSED session"
            )
        if self.reconciliation_required and self.state not in {
            SessionState.UNKNOWN.value,
            SessionState.INSPECTING.value,
            SessionState.FAILED.value,
        }:
            raise CaptureSessionError(
                "unresolved sessions cannot advance beyond read-only inspection"
            )
        if self.hook_sha256:
            self.hook_sha256 = self.hook_sha256.lower()
            if not re.fullmatch(r"[0-9a-f]{64}", self.hook_sha256):
                raise CaptureSessionError("hook_sha256 must be a SHA-256 hex digest")
        if self.bridge_sha256:
            self.bridge_sha256 = self.bridge_sha256.lower()
            if not re.fullmatch(r"[0-9a-f]{64}", self.bridge_sha256):
                raise CaptureSessionError("bridge_sha256 must be a SHA-256 hex digest")
        if self.active_request_id:
            try:
                self.active_request_id = str(uuid.UUID(self.active_request_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise CaptureSessionError("active_request_id must be a UUID") from exc
        for name in ("last_error_code", "last_error_message"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, str(value)[:512])

    @classmethod
    def create(cls, config: CaptureConfig) -> "SessionManifest":
        return cls(
            session_id=str(uuid.uuid4()),
            startup_nonce=secrets.token_urlsafe(32),
            capture_exe_path=config.capture_exe_path,
            cap_autoload_dir=config.cap_autoload_dir,
            expected_capture_version=config.expected_capture_version,
            expected_tcl_version=config.expected_tcl_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "startup_nonce": self.startup_nonce,
            "state": self.state,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "capture_exe_path": self.capture_exe_path,
            "cap_autoload_dir": self.cap_autoload_dir,
            "expected_capture_version": self.expected_capture_version,
            "expected_tcl_version": self.expected_tcl_version,
            "capture_version": self.capture_version,
            "tcl_version": self.tcl_version,
            "pid": self.pid,
            "process_started_at_utc": self.process_started_at_utc,
            "process_exit_confirmed_at_utc": self.process_exit_confirmed_at_utc,
            "remote_session_dir": self.remote_session_dir,
            "hook_path": self.hook_path,
            "hook_sha256": self.hook_sha256,
            "bridge_sha256": self.bridge_sha256,
            "active_request_id": self.active_request_id,
            "reconciliation_required": self.reconciliation_required,
            "last_error_code": self.last_error_code,
            "last_error_message": self.last_error_message,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SessionManifest":
        if not isinstance(value, Mapping):
            raise CaptureSessionError("Session Manifest must be a JSON object")
        allowed = {
            "schema_version", "session_id", "startup_nonce", "state",
            "created_at_utc", "updated_at_utc", "capture_exe_path",
            "cap_autoload_dir", "expected_capture_version", "expected_tcl_version", "capture_version",
            "tcl_version", "pid", "process_started_at_utc",
            "process_exit_confirmed_at_utc", "remote_session_dir", "hook_path", "hook_sha256", "bridge_sha256", "active_request_id",
            "reconciliation_required", "last_error_code",
            "last_error_message",
        }
        unexpected = set(value) - allowed
        if unexpected:
            raise CaptureSessionError(
                "Session Manifest contains unknown fields: {}".format(
                    ", ".join(sorted(str(item) for item in unexpected))
                )
            )
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise CaptureSessionError("Session Manifest fields are invalid") from exc

    @classmethod
    def load(cls, path: str | Path) -> "SessionManifest":
        manifest_path = Path(path)
        try:
            raw = manifest_path.read_text(encoding="utf-8")
            value = json.loads(raw)
        except OSError as exc:
            raise CaptureSessionError(
                "cannot read Session Manifest: {}".format(exc)
            ) from exc
        except json.JSONDecodeError as exc:
            raise CaptureSessionError("Session Manifest is not valid JSON") from exc
        return cls.from_dict(value)

    def save(self, path: str | Path) -> None:
        """Atomically persist a local Host-A Manifest; never stores Agent tokens."""
        manifest_path = Path(path)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=str(manifest_path.parent),
                prefix=manifest_path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_name = stream.name
                json.dump(
                    self.to_dict(), stream, ensure_ascii=False,
                    sort_keys=True, indent=2
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, manifest_path)
        except OSError as exc:
            raise CaptureSessionError(
                "cannot atomically save Session Manifest: {}".format(exc)
            ) from exc
        finally:
            if temp_name and os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    executable_path: str
    started_at_utc: str
    file_version: Optional[str] = None


@dataclass(frozen=True)
class BridgeReadyEvidence:
    session_id: str
    startup_nonce: str
    runtime_pid: int
    tcl_version: str
    bridge_sha256: str
    schema_version: int = 1


@dataclass(frozen=True)
class ProcessExitEvidence:
    """Proof that the exact process recorded by this Session is no longer running."""

    pid: int
    started_at_utc: str
    process_absent: bool
    checked_at_utc: str


class CaptureSession:
    """State transition guard around one Session Manifest."""

    def __init__(
        self, manifest: SessionManifest, config: Optional[CaptureConfig] = None
    ):
        self.manifest = manifest
        self.config = config

    @property
    def state(self) -> SessionState:
        return SessionState(self.manifest.state)

    def transition(self, target: SessionState | str) -> None:
        """Allow callers to request read-only inspection, not bypass guards."""
        next_state = SessionState(target)
        if next_state is not SessionState.INSPECTING:
            raise CaptureStateTransitionError(
                "use the corresponding CaptureSession method for guarded transitions"
            )
        self._move_to(next_state)

    def _move_to(self, next_state: SessionState) -> None:
        current = self.state
        if (
            next_state is SessionState.STARTING
            and self.manifest.reconciliation_required
        ):
            raise CaptureStateTransitionError(
                "this session has an unresolved outcome; create a new Session ID "
                "after read-only reconciliation instead of restarting it"
            )
        if next_state not in _ALLOWED_TRANSITIONS[current]:
            raise CaptureStateTransitionError(
                "invalid Capture session transition: {} -> {}".format(
                    current.value, next_state.value
                )
            )
        self.manifest.state = next_state.value
        self.manifest.updated_at_utc = _utc_now()
        if next_state not in (SessionState.FAILED, SessionState.UNKNOWN):
            self.manifest.last_error_code = None
            self.manifest.last_error_message = None

    def begin_inspection(self) -> None:
        self.transition(SessionState.INSPECTING)

    def begin_start(self) -> None:
        self._move_to(SessionState.STARTING)

    def record_process_identity(self, identity: ProcessIdentity) -> None:
        if self.state is not SessionState.STARTING:
            raise CaptureStateTransitionError(
                "process identity can only be recorded while starting"
            )
        if isinstance(identity.pid, bool) or not isinstance(identity.pid, int) or identity.pid <= 0:
            raise CaptureIdentityError("Capture PID must be a positive integer")
        actual_path = str(identity.executable_path or "").strip()
        if not PureWindowsPath(actual_path).is_absolute():
            raise CaptureIdentityError(
                "Capture process image path must be absolute"
            )
        expected_path = self.manifest.capture_exe_path
        if not expected_path:
            raise CaptureIdentityError(
                "an explicit Capture executable path must be selected before start"
            )
        if _normalize_windows_path(actual_path) != _normalize_windows_path(expected_path):
            raise CaptureIdentityError(
                "Capture process image path does not match configured capture.exe"
            )
        actual_version = str(identity.file_version or "").strip() or None
        expected_version = self.manifest.expected_capture_version
        if not expected_version:
            raise CaptureIdentityError(
                "an explicit Capture file version must be selected before start"
            )
        if not actual_version or actual_version != expected_version:
            raise CaptureIdentityError(
                "Capture file version is missing or does not match the configured version"
            )
        process_started_at = _validate_timestamp(
            identity.started_at_utc, "process started_at_utc"
        )
        self.manifest.pid = identity.pid
        self.manifest.capture_exe_path = actual_path
        self.manifest.capture_version = actual_version
        self.manifest.process_started_at_utc = process_started_at
        self.manifest.process_exit_confirmed_at_utc = None
        self.manifest.updated_at_utc = _utc_now()

    def mark_bridge_pending(
        self,
        *,
        bridge_sha256: str,
        remote_session_dir: Optional[str] = None,
        hook_path: Optional[str] = None,
    ) -> None:
        if self.manifest.pid is None or not self.manifest.process_started_at_utc:
            raise CaptureIdentityError(
                "verified Capture process identity is required before Bridge setup"
            )
        digest = str(bridge_sha256 or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CaptureIdentityError("Bridge SHA-256 must be a 64-character hex digest")
        self.manifest.bridge_sha256 = digest
        if remote_session_dir:
            self.manifest.remote_session_dir = str(remote_session_dir)
        if hook_path:
            self.manifest.hook_path = str(hook_path)
        self._move_to(SessionState.BRIDGE_PENDING)

    def mark_ready(self, evidence: BridgeReadyEvidence) -> None:
        if self.manifest.reconciliation_required:
            raise CaptureStateTransitionError(
                "this session has an unresolved prior outcome and cannot become READY"
            )
        if self.state is not SessionState.BRIDGE_PENDING:
            raise CaptureStateTransitionError(
                "Bridge READY is only valid while bridge_pending"
            )
        try:
            evidence_session_id = str(uuid.UUID(evidence.session_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise CaptureIdentityError("READY record has an invalid Session ID") from exc
        if (
            isinstance(evidence.schema_version, bool)
            or not isinstance(evidence.schema_version, int)
            or evidence.schema_version != 1
        ):
            raise CaptureIdentityError("READY record schema version is unsupported")
        if evidence_session_id != self.manifest.session_id:
            raise CaptureIdentityError("READY Session ID does not match this session")
        if not hmac.compare_digest(
            str(evidence.startup_nonce), self.manifest.startup_nonce
        ):
            raise CaptureIdentityError("READY Startup Nonce does not match this session")
        if (
            isinstance(evidence.runtime_pid, bool)
            or not isinstance(evidence.runtime_pid, int)
            or self.manifest.pid is None
            or evidence.runtime_pid != self.manifest.pid
        ):
            raise CaptureIdentityError("READY PID does not match the verified Capture PID")
        actual_tcl = str(evidence.tcl_version or "").strip()
        expected_tcl = self.manifest.expected_tcl_version
        if not actual_tcl or (expected_tcl and actual_tcl != expected_tcl):
            raise CaptureIdentityError("READY Tcl version does not match the Skill policy")
        digest = str(evidence.bridge_sha256 or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CaptureIdentityError("READY Bridge hash is invalid")
        expected_digest = self.manifest.bridge_sha256
        if expected_digest and not hmac.compare_digest(digest, expected_digest):
            raise CaptureIdentityError("READY Bridge hash does not match the deployed file")
        self.manifest.tcl_version = actual_tcl
        self._move_to(SessionState.READY)

    def begin_operation(self, request_id: str) -> None:
        if self.manifest.reconciliation_required:
            raise CaptureStateTransitionError(
                "resolve the previous UNKNOWN request before submitting another Tcl operation"
            )
        if self.state is not SessionState.READY:
            raise CaptureStateTransitionError(
                "Tcl operation requires a READY Capture session"
            )
        try:
            normalized = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise CaptureSessionError("operation request_id must be a UUID") from exc
        self._move_to(SessionState.BUSY)
        self.manifest.active_request_id = normalized

    def complete_operation(self, request_id: str) -> None:
        if self.state is not SessionState.BUSY:
            raise CaptureStateTransitionError("there is no active Tcl operation")
        try:
            normalized = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise CaptureSessionError("operation request_id must be a UUID") from exc
        if self.manifest.active_request_id != normalized:
            raise CaptureIdentityError("result Request ID does not match active operation")
        self.manifest.active_request_id = None
        self._move_to(SessionState.READY)

    def mark_unknown(self, code: str, message: str) -> None:
        if self.state in (SessionState.ABSENT, SessionState.CLOSED):
            raise CaptureStateTransitionError(
                "an absent or closed session cannot become unknown"
            )
        self._move_to(SessionState.UNKNOWN)
        self.manifest.reconciliation_required = True
        self.manifest.last_error_code = str(code or "OUTCOME_UNKNOWN")[:128]
        self.manifest.last_error_message = _sanitize_error_message(
            message or "Remote outcome is unknown"
        )
        # Keep active_request_id intact so a later read-only reconciliation can
        # query the same Request ID rather than submitting a duplicate operation.

    def mark_failed(self, code: str, message: str) -> None:
        self._move_to(SessionState.FAILED)
        self.manifest.last_error_code = str(code or "CAPTURE_SESSION_FAILED")[:128]
        self.manifest.last_error_message = _sanitize_error_message(
            message or "Capture session failed"
        )

    def begin_close(self, *, user_authorized: bool = False) -> None:
        if user_authorized is not True:
            raise CaptureAuthorizationError(
                "closing Capture requires explicit user authorization"
            )
        if self.state is not SessionState.READY:
            raise CaptureStateTransitionError(
                "Capture can only begin graceful close from READY"
            )
        self._move_to(SessionState.CLOSING)

    def mark_closed(self, evidence: ProcessExitEvidence) -> None:
        if self.state is not SessionState.CLOSING:
            raise CaptureStateTransitionError("session is not closing")
        if not isinstance(evidence, ProcessExitEvidence):
            raise CaptureIdentityError("structured Capture process-exit evidence is required")
        if (
            isinstance(evidence.pid, bool)
            or not isinstance(evidence.pid, int)
            or self.manifest.pid is None
            or evidence.pid != self.manifest.pid
        ):
            raise CaptureIdentityError("exit evidence PID does not match the original Capture PID")
        if evidence.started_at_utc != self.manifest.process_started_at_utc:
            raise CaptureIdentityError(
                "exit evidence process start time does not match the original Capture instance"
            )
        _validate_timestamp(evidence.started_at_utc, "exit evidence started_at_utc")
        checked_at = _validate_timestamp(
            evidence.checked_at_utc, "exit evidence checked_at_utc"
        )
        if evidence.process_absent is not True:
            raise CaptureIdentityError(
                "the original Capture PID is still running; session cannot be closed"
            )
        started = datetime.fromisoformat(
            evidence.started_at_utc.replace("Z", "+00:00")
        )
        checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        if checked < started:
            raise CaptureIdentityError(
                "process exit check time cannot precede the original process start time"
            )
        self.manifest.process_exit_confirmed_at_utc = checked_at
        self._move_to(SessionState.CLOSED)
