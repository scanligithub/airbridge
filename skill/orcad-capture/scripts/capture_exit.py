# -*- coding: utf-8 -*-
"""M3.2.6 explicitly authorized Capture Exit and process-exit confirmation.

Capture-specific lifecycle policy stays in this Skill. Remote communication uses
only the existing generic Tcl request queue and read-only Shell inventory.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from capture_process import (
    CaptureProcessDisposition,
    CaptureProcessInspector,
)
from capture_session import (
    CaptureAuthorizationError,
    CaptureIdentityError,
    CaptureSession,
    CaptureSessionError,
    CaptureStateTransitionError,
    ProcessExitEvidence,
    SessionState,
)
from capture_requests import CaptureTclOutcomeUnknown


PREPARE_MAGIC = "AB_CAPTURE_EXIT_PREPARE_V1"
SCHEDULE_MAGIC = "AB_CAPTURE_EXIT_SCHEDULED_V1"


class CaptureExitError(RuntimeError):
    """A Capture Exit workflow failed closed."""


class CaptureExitOutcomeUnknown(CaptureExitError):
    """Exit may have been scheduled or completed; never send Exit again."""

    def __init__(self, message: str, request_id: Optional[str] = None):
        self.request_id = request_id
        super().__init__(message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _tcl_quote(value: str) -> str:
    """Return a Tcl-safe braced literal, rejecting brace forms we cannot quote."""
    raw = str(value)
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", raw):
        raise CaptureExitError("Tcl version must be a numeric version literal")
    return "{" + raw + "}"


def build_exit_prepare_script(session_id: str, pid: int, tcl_version: str) -> str:
    """Read-only Tcl probe using the verified Capture DboSession wrapper."""
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", str(session_id)):
        raise CaptureExitError("Session ID must be a UUID")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise CaptureExitError("Capture PID must be positive")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", str(tcl_version)):
        raise CaptureExitError("Tcl version must be a numeric version literal")
    return r"""
set __ab_lines [list "%s"]
lappend __ab_lines "session_id=%s"
lappend __ab_lines "expected_pid=%s"
lappend __ab_lines "tcl_pid=[pid]"
lappend __ab_lines "tcl_version=[info patchlevel]"
set __ab_session_exists [info exists ::DboSession_s_pDboSession]
set __ab_session ""
set __ab_session_rc -1
set __ab_wrapper_rc -1
set __ab_active ""
set __ab_active_rc -1
set __ab_name ""
set __ab_name_rc 0
if {$__ab_session_exists && $::DboSession_s_pDboSession ne "" && $::DboSession_s_pDboSession ne "NULL"} {
    set __ab_session $::DboSession_s_pDboSession
    set __ab_session_rc 0
    set __ab_wrapper_rc [catch {DboSession -this $__ab_session}]
    if {$__ab_wrapper_rc == 0} {
        set __ab_active_rc [catch {set __ab_active [$__ab_session GetActiveDesign]}]
        if {$__ab_active_rc == 0 && $__ab_active ne "" && $__ab_active ne "NULL"} {
            set __ab_name_rc [catch {
                set __ab_cstr [DboTclHelper_sMakeCString]
                $__ab_active GetName $__ab_cstr
                set __ab_name [DboTclHelper_sGetConstCharPtr $__ab_cstr]
            }]
        }
    }
}
lappend __ab_lines "session_present=$__ab_session_exists"
lappend __ab_lines "session_wrapper_rc=$__ab_wrapper_rc"
lappend __ab_lines "active_design_rc=$__ab_active_rc"
lappend __ab_lines "active_design_present=[expr {$__ab_active ne "" && $__ab_active ne "NULL"}]"
lappend __ab_lines "active_design_name_rc=$__ab_name_rc"
lappend __ab_lines "active_design_name_b64=[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__ab_name]]"
join $__ab_lines "\n"
""" % (
        PREPARE_MAGIC,
        session_id,
        pid,
    )


def parse_exit_prepare_report(value: str, *, session_id: str, pid: int,
                              tcl_version: str) -> Mapping[str, Any]:
    lines = str(value or "").splitlines()
    if not lines or lines[0] != PREPARE_MAGIC:
        raise CaptureExitError("Capture Exit preparation report magic is invalid")
    fields = {}
    for line in lines[1:]:
        if "=" not in line:
            raise CaptureExitError("Capture Exit preparation report is malformed")
        key, item = line.split("=", 1)
        if key in fields:
            raise CaptureExitError("Capture Exit preparation report has duplicate fields")
        fields[key] = item
    required = {
        "session_id", "expected_pid", "tcl_pid", "tcl_version",
        "session_present", "session_wrapper_rc",
        "active_design_rc", "active_design_present", "active_design_name_rc",
        "active_design_name_b64",
    }
    if set(fields) != required:
        raise CaptureExitError("Capture Exit preparation report fields are incomplete")
    if (
        fields["session_id"] != session_id
        or fields["expected_pid"] != str(pid)
        or fields["tcl_pid"] != str(pid)
        or fields["tcl_version"] != tcl_version
        or fields["session_present"] != "1"
        or fields["session_wrapper_rc"] != "0"
        or fields["active_design_rc"] != "0"
        or fields["active_design_present"] not in ("0", "1")
        or fields["active_design_name_rc"] != "0"
    ):
        raise CaptureIdentityError("Capture Tcl / Dbo identity did not match the READY Session")
    import base64
    try:
        name = base64.b64decode(fields["active_design_name_b64"], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise CaptureExitError("Active Design name encoding is invalid") from exc
    return {
        "session_id": session_id,
        "pid": pid,
        "tcl_version": tcl_version,
        "active_design_present": fields["active_design_present"] == "1",
        "active_design_name": name,
    }


def build_exit_schedule_script(session_id: str, pid: int, tcl_version: str,
                               delay_ms: int = 1000) -> str:
    """Schedule Exit for a later Tcl event-loop turn, after this request is published."""
    if isinstance(delay_ms, bool) or not isinstance(delay_ms, int) or not 250 <= delay_ms <= 10000:
        raise ValueError("delay_ms must be between 250 and 10000")
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", str(session_id)):
        raise CaptureExitError("Session ID must be a UUID")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise CaptureExitError("Capture PID must be positive")
    return r"""
if {[pid] != %s} { error "Capture PID mismatch" }
if {[info patchlevel] ne %s} { error "Tcl version mismatch" }
after %s [list Exit]
join [list "%s" "session_id=%s" "pid=[pid]" "tcl_version=[info patchlevel]" "exit_scheduled=1"] "\n"
""" % (pid, _tcl_quote(tcl_version), delay_ms, SCHEDULE_MAGIC, session_id)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=str(path.parent),
            prefix=path.name + ".", suffix=".tmp", delete=False
        ) as stream:
            temp_name = stream.name
            json.dump(dict(value), stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass


class CaptureExitCoordinator:
    """Two-phase, user-authorized Exit with independent PID absence confirmation."""

    def __init__(self, queue: Any, inspector: CaptureProcessInspector,
                 session: CaptureSession, manifest_path: str | Path, *,
                 sleep_fn=time.sleep, monotonic_fn=time.monotonic):
        if queue is None or not callable(getattr(queue, "execute", None)):
            raise TypeError("queue must provide execute()")
        if (
            not callable(getattr(inspector, "inspect", None))
            or not callable(getattr(inspector, "inspect_session", None))
        ):
            raise TypeError("inspector must provide inspect() and inspect_session()")
        if not isinstance(session, CaptureSession):
            raise TypeError("session must be a CaptureSession")
        self.queue = queue
        self.inspector = inspector
        self.session = session
        self.manifest_path = Path(manifest_path)
        self.context_path = self.manifest_path.with_name(self.manifest_path.name + ".exit.json")
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn

    def prepare_exit(self, *, user_authorized: bool = False) -> Mapping[str, Any]:
        if user_authorized is not True:
            raise CaptureAuthorizationError("Capture Exit preparation requires explicit authorization")
        if self.session.state is not SessionState.READY:
            raise CaptureStateTransitionError("Exit preparation requires a READY Session")
        if self.context_path.exists():
            raise CaptureExitError("An Exit context already exists; refusing a second Exit workflow")
        self._assert_owned_process()
        manifest = self.session.manifest
        request_id = str(uuid.uuid4())
        try:
            result = self.queue.execute(
                build_exit_prepare_script(manifest.session_id, int(manifest.pid), str(manifest.tcl_version)),
                user_authorized=True, request_id=request_id,
            )
        except CaptureTclOutcomeUnknown as exc:
            raise CaptureExitOutcomeUnknown("Exit preparation outcome is UNKNOWN; do not retry Tcl", exc.request_id) from exc
        if not result.success:
            raise CaptureExitError("Capture Exit preparation Tcl probe failed")
        report = parse_exit_prepare_report(
            result.value, session_id=manifest.session_id, pid=int(manifest.pid),
            tcl_version=str(manifest.tcl_version),
        )
        context = {
            "schema_version": 1, "session_id": manifest.session_id,
            "startup_nonce": manifest.startup_nonce, "pid": manifest.pid,
            "process_started_at_utc": manifest.process_started_at_utc,
            "capture_exe_path": manifest.capture_exe_path,
            "capture_version": manifest.capture_version, "tcl_version": manifest.tcl_version,
            "prepare_request_id": request_id, "active_design_present": report["active_design_present"],
            "active_design_name": report["active_design_name"], "state": "prepared",
            "created_at_utc": _utc_now(), "updated_at_utc": _utc_now(),
        }
        if self.context_path.exists():
            raise CaptureExitError("An Exit context already exists; refusing a second Exit workflow")
        _atomic_json(self.context_path, context)
        return report

    def request_exit(self, *, user_authorized: bool = False,
                     design_saved_or_no_changes_confirmed: bool = False,
                     timeout_seconds: float = 30.0, delay_ms: int = 1000) -> ProcessExitEvidence:
        if user_authorized is not True:
            raise CaptureAuthorizationError("Capture Exit requires explicit user authorization")
        if design_saved_or_no_changes_confirmed is not True:
            raise CaptureAuthorizationError(
                "user must explicitly confirm the design is saved or has no unsaved changes"
            )
        if self.session.state is SessionState.UNKNOWN:
            raise CaptureExitOutcomeUnknown(
                "Capture Exit Session is UNKNOWN; only read-only reconciliation is allowed"
            )
        if self.session.state is not SessionState.READY:
            raise CaptureStateTransitionError("Capture Exit requires a READY Session")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be between 1 and 600")
        context = self._load_context()
        manifest = self.session.manifest
        self._assert_context_identity(context)
        self._assert_owned_process()
        if context.get("state") != "prepared":
            raise CaptureExitError("Exit context is not in prepared state; Exit will not be retried")
        context["state"] = "exit_scheduling"
        context["schedule_request_id"] = str(uuid.uuid4())
        context["updated_at_utc"] = _utc_now()
        _atomic_json(self.context_path, context)
        try:
            result = self.queue.execute(
                build_exit_schedule_script(manifest.session_id, int(manifest.pid),
                                           str(manifest.tcl_version), delay_ms),
                user_authorized=True, request_id=context["schedule_request_id"],
            )
        except Exception as exc:
            self._mark_unknown("CAPTURE_EXIT_SCHEDULE_UNKNOWN", str(exc))
            context["state"] = "unknown"
            context["updated_at_utc"] = _utc_now()
            _atomic_json(self.context_path, context)
            raise CaptureExitOutcomeUnknown(
                "Exit may have been scheduled; do not send another Exit or force-kill Capture",
                context["schedule_request_id"],
            ) from exc
        expected = "\n".join([
            SCHEDULE_MAGIC, "session_id=" + manifest.session_id,
            "pid=" + str(manifest.pid), "tcl_version=" + str(manifest.tcl_version),
            "exit_scheduled=1",
        ])
        if not result.success or result.value.strip() != expected:
            self._mark_unknown("CAPTURE_EXIT_SCHEDULE_UNVERIFIED", "Exit schedule receipt did not match")
            context["state"] = "unknown"
            context["updated_at_utc"] = _utc_now()
            _atomic_json(self.context_path, context)
            raise CaptureExitOutcomeUnknown("Exit schedule receipt is not trustworthy; do not retry",
                                            context["schedule_request_id"])
        context["state"] = "exit_scheduled"
        context["updated_at_utc"] = _utc_now()
        _atomic_json(self.context_path, context)
        try:
            self.session.begin_close(user_authorized=True)
            self.session.manifest.active_request_id = None
            self.session.manifest.save(self.manifest_path)
        except Exception as exc:
            self._mark_unknown("CAPTURE_EXIT_LOCAL_COMMIT_UNKNOWN", str(exc))
            raise CaptureExitOutcomeUnknown("Exit was scheduled but local close state could not be committed",
                                            context["schedule_request_id"]) from exc
        return self._wait_for_original_process_exit(timeout_seconds)

    def _wait_for_original_process_exit(self, timeout_seconds: float) -> ProcessExitEvidence:
        manifest = self.session.manifest
        deadline = self.monotonic_fn() + float(timeout_seconds)
        while True:
            try:
                candidates = self.inspector.inspect()
            except Exception as exc:
                self._mark_unknown("CAPTURE_EXIT_PROCESS_CHECK_UNKNOWN", str(exc))
                raise CaptureExitOutcomeUnknown("Could not verify Capture process exit; no retry or force-kill") from exc
            matches = [item for item in candidates if item.pid == manifest.pid]
            if not matches:
                evidence = ProcessExitEvidence(
                    pid=int(manifest.pid), started_at_utc=str(manifest.process_started_at_utc),
                    process_absent=True, checked_at_utc=_utc_now(),
                )
                try:
                    self.session.mark_closed(evidence)
                    self.session.manifest.save(self.manifest_path)
                    context = self._load_context()
                    context["state"] = "closed"
                    context["process_exit_confirmed_at_utc"] = evidence.checked_at_utc
                    context["updated_at_utc"] = _utc_now()
                    _atomic_json(self.context_path, context)
                except Exception as exc:
                    self._mark_unknown("CAPTURE_EXIT_CLOSE_COMMIT_UNKNOWN", str(exc))
                    raise CaptureExitOutcomeUnknown("PID absence observed but CLOSED state could not be persisted") from exc
                return evidence
            assessment = self.inspector.inspect_session(manifest) if callable(getattr(self.inspector, "inspect_session", None)) else None
            if assessment is None or assessment.disposition is not CaptureProcessDisposition.OWNED_INSTANCE:
                self._mark_unknown("CAPTURE_EXIT_PID_REUSE_OR_IDENTITY_MISMATCH",
                                   "recorded PID remains but its original process identity cannot be verified")
                raise CaptureExitOutcomeUnknown("Capture PID remains with mismatched identity; no further action")
            if self.monotonic_fn() >= deadline:
                self._mark_unknown("CAPTURE_EXIT_TIMEOUT", "original Capture process remains after Exit scheduling")
                context = self._load_context()
                context["state"] = "unknown"
                context["updated_at_utc"] = _utc_now()
                _atomic_json(self.context_path, context)
                raise CaptureExitOutcomeUnknown("Capture did not exit before timeout; do not retry Exit or force-kill")
            self.sleep_fn(0.25)

    def _assert_owned_process(self) -> None:
        try:
            assessment = self.inspector.inspect_session(self.session.manifest)
        except Exception as exc:
            raise CaptureExitOutcomeUnknown("Capture process identity inspection is indeterminate") from exc
        if assessment.disposition is not CaptureProcessDisposition.OWNED_INSTANCE:
            raise CaptureIdentityError("the exact Session-owned Capture process is not confirmed")

    def _assert_context_identity(self, context: Mapping[str, Any]) -> None:
        manifest = self.session.manifest
        expected = {
            "session_id": manifest.session_id, "startup_nonce": manifest.startup_nonce,
            "pid": manifest.pid, "process_started_at_utc": manifest.process_started_at_utc,
            "capture_exe_path": manifest.capture_exe_path,
            "capture_version": manifest.capture_version, "tcl_version": manifest.tcl_version,
        }
        if any(context.get(key) != value for key, value in expected.items()):
            raise CaptureIdentityError("Exit context does not belong to the current Session identity")

    def _load_context(self) -> dict[str, Any]:
        try:
            value = json.loads(self.context_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CaptureExitError("Exit preparation context is unavailable") from exc
        except json.JSONDecodeError as exc:
            raise CaptureExitError("Exit preparation context is malformed") from exc
        if not isinstance(value, Mapping) or value.get("schema_version") != 1:
            raise CaptureExitError("Exit preparation context schema is invalid")
        return dict(value)

    def _mark_unknown(self, code: str, message: str) -> None:
        if self.session.state not in (SessionState.UNKNOWN, SessionState.CLOSED):
            self.session.mark_unknown(code, message)
        try:
            self.session.manifest.save(self.manifest_path)
        except Exception:
            pass
        try:
            if self.context_path.exists():
                context = self._load_context()
                context["state"] = "unknown"
                context["updated_at_utc"] = _utc_now()
                _atomic_json(self.context_path, context)
        except Exception:
            pass
