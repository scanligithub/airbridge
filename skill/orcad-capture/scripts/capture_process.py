# -*- coding: utf-8 -*-
"""Read-only remote Capture process inventory for the OrCAD Capture Skill.

This module never starts, injects into, terminates, or closes a process.  It
executes one bounded read-only PowerShell inventory command through the generic
AirBridge Shell API and compares returned identity evidence with a Session
Manifest.
"""
from __future__ import annotations

import json
import ntpath
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Mapping, Optional, Sequence


MAX_INVENTORY_OUTPUT_BYTES = 256 * 1024
MAX_CAPTURE_CANDIDATES = 128
INVENTORY_TASK_TIMEOUT_SECONDS = 30


class CaptureProcessInventoryError(RuntimeError):
    """The remote Capture process inventory could not be trusted."""


class CaptureProcessInventoryPending(CaptureProcessInventoryError):
    """The remote read-only Shell task was accepted but has no final result yet."""

    def __init__(self, request_id: Optional[str], state: str):
        self.request_id = request_id
        self.remote_state = state
        super().__init__(
            "Capture process inventory is not complete (state={}); "
            "reconcile the same request before submitting another command".format(
                state
            )
        )


class CaptureProcessDisposition(str, Enum):
    NO_PROCESS = "no_process"
    ORIGINAL_PROCESS_ABSENT = "original_process_absent"
    OWNED_INSTANCE = "owned_instance"
    UNOWNED_INSTANCE = "unowned_instance"
    MULTIPLE_INSTANCES = "multiple_instances"
    PID_REUSED_OR_IDENTITY_MISMATCH = "pid_reused_or_identity_mismatch"


@dataclass(frozen=True)
class CaptureProcessCandidate:
    """One observed capture.exe process; unavailable CIM properties stay None."""

    pid: int
    executable_path: Optional[str]
    started_at_utc: Optional[str]
    file_version: Optional[str]
    process_name: str = "capture.exe"

    def __post_init__(self) -> None:
        if isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 0:
            raise CaptureProcessInventoryError("Capture process PID must be positive")
        if str(self.process_name or "").casefold() != "capture.exe":
            raise CaptureProcessInventoryError("inventory returned a non-Capture process")
        if self.executable_path:
            path = str(self.executable_path).strip()
            if not ntpath.isabs(path):
                raise CaptureProcessInventoryError(
                    "Capture process image path is not an absolute Windows path"
                )
            object.__setattr__(self, "executable_path", path)
        if self.started_at_utc:
            _validate_utc_timestamp(self.started_at_utc, "started_at_utc")
        if self.file_version is not None:
            version = str(self.file_version).strip()
            object.__setattr__(self, "file_version", version or None)


@dataclass(frozen=True)
class CaptureProcessAssessment:
    disposition: CaptureProcessDisposition
    candidates: tuple[CaptureProcessCandidate, ...]
    owned_candidate: Optional[CaptureProcessCandidate] = None
    detail: str = ""


def _validate_utc_timestamp(value: str, field_name: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaptureProcessInventoryError(
            "{} must be an ISO-8601 timestamp".format(field_name)
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise CaptureProcessInventoryError(
            "{} must explicitly use UTC".format(field_name)
        )
    return raw


def _normalize_windows_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(str(value or "").strip()))


def build_capture_inventory_command() -> str:
    """Return a fixed read-only PowerShell command; no user data is interpolated."""
    return r"""
$ErrorActionPreference = 'Stop'
$rows = @(
    Get-CimInstance -ClassName Win32_Process -Filter "Name = 'capture.exe'" |
        ForEach-Object {
            $exePath = $null
            $startedUtc = $null
            $fileVersion = $null
            try {
                if ($_.ExecutablePath) {
                    $exePath = [string]$_.ExecutablePath
                    $fileVersion = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($exePath).FileVersion
                }
            } catch {
                $exePath = [string]$_.ExecutablePath
            }
            try {
                if ($_.CreationDate) {
                    $startedUtc = ([datetime]$_.CreationDate).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.ffffffZ')
                }
            } catch {
                $startedUtc = $null
            }
            [pscustomobject]@{
                pid = [int]$_.ProcessId
                process_name = [string]$_.Name
                executable_path = $exePath
                started_at_utc = $startedUtc
                file_version = $fileVersion
            }
        }
)
if ($rows.Count -eq 0) {
    Write-Output '[]'
} else {
    ConvertTo-Json -InputObject @($rows) -Depth 3 -Compress
}
""".strip()


def parse_capture_inventory_json(stdout: str) -> tuple[CaptureProcessCandidate, ...]:
    """Parse Shell stdout and fail closed on truncation, malformed or duplicate PID data."""
    raw = str(stdout or "")
    encoded = raw.encode("utf-8")
    if len(encoded) > MAX_INVENTORY_OUTPUT_BYTES:
        raise CaptureProcessInventoryError(
            "Capture process inventory exceeded the output size limit"
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CaptureProcessInventoryError(
            "Capture process inventory did not return valid JSON"
        ) from exc

    if isinstance(payload, dict):
        entries = [payload]
    elif isinstance(payload, list):
        entries = payload
    else:
        raise CaptureProcessInventoryError(
            "Capture process inventory must be a JSON array or object"
        )
    if len(entries) > MAX_CAPTURE_CANDIDATES:
        raise CaptureProcessInventoryError(
            "Capture process inventory exceeded the candidate count limit"
        )

    result = []
    seen_pids = set()
    for index, item in enumerate(entries):
        if not isinstance(item, Mapping):
            raise CaptureProcessInventoryError(
                "Capture process candidate {} is not an object".format(index)
            )
        pid = item.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise CaptureProcessInventoryError(
                "Capture process candidate {} has an invalid PID".format(index)
            )
        if pid in seen_pids:
            raise CaptureProcessInventoryError(
                "Capture process inventory contains duplicate PIDs"
            )
        seen_pids.add(pid)

        process_name = item.get("process_name")
        if (
            not isinstance(process_name, str)
            or process_name.casefold() != "capture.exe"
        ):
            raise CaptureProcessInventoryError(
                "Capture process candidate {} has no verified capture.exe name".format(
                    index
                )
            )
        executable_path = item.get("executable_path")
        started_at_utc = item.get("started_at_utc")
        file_version = item.get("file_version")
        if executable_path is not None and not isinstance(executable_path, str):
            raise CaptureProcessInventoryError(
                "Capture process executable_path must be a string or null"
            )
        if started_at_utc is not None and not isinstance(started_at_utc, str):
            raise CaptureProcessInventoryError(
                "Capture process started_at_utc must be a string or null"
            )
        if file_version is not None and not isinstance(file_version, str):
            raise CaptureProcessInventoryError(
                "Capture process file_version must be a string or null"
            )
        result.append(
            CaptureProcessCandidate(
                pid=pid,
                executable_path=executable_path,
                started_at_utc=started_at_utc,
                file_version=file_version,
                process_name=process_name,
            )
        )
    return tuple(result)


def assess_capture_processes(
    candidates: Sequence[CaptureProcessCandidate],
    manifest: Any,
) -> CaptureProcessAssessment:
    """Classify the observed process set without claiming ownership from PID alone."""
    observed = tuple(candidates)
    manifest_pid = getattr(manifest, "pid", None)

    if not observed:
        disposition = (
            CaptureProcessDisposition.ORIGINAL_PROCESS_ABSENT
            if manifest_pid is not None
            else CaptureProcessDisposition.NO_PROCESS
        )
        return CaptureProcessAssessment(disposition, observed)

    if len(observed) > 1:
        return CaptureProcessAssessment(
            CaptureProcessDisposition.MULTIPLE_INSTANCES,
            observed,
            detail=(
                "multiple capture.exe processes were found; do not select, inject "
                "a Hook into, or close any instance automatically"
            ),
        )

    candidate = observed[0]
    if manifest_pid is None:
        return CaptureProcessAssessment(
            CaptureProcessDisposition.UNOWNED_INSTANCE,
            observed,
            detail="no Session Manifest process identity owns this Capture instance",
        )

    same_pid = candidate.pid == manifest_pid
    same_path = bool(
        candidate.executable_path
        and getattr(manifest, "capture_exe_path", None)
        and _normalize_windows_path(candidate.executable_path)
        == _normalize_windows_path(manifest.capture_exe_path)
    )
    same_started = bool(
        candidate.started_at_utc
        and getattr(manifest, "process_started_at_utc", None)
        and candidate.started_at_utc == manifest.process_started_at_utc
    )
    same_version = bool(
        candidate.file_version
        and getattr(manifest, "expected_capture_version", None)
        and candidate.file_version == manifest.expected_capture_version
    )

    if same_pid and same_path and same_started and same_version:
        return CaptureProcessAssessment(
            CaptureProcessDisposition.OWNED_INSTANCE,
            observed,
            owned_candidate=candidate,
            detail="PID, executable path, process start time and file version match",
        )
    if same_pid:
        return CaptureProcessAssessment(
            CaptureProcessDisposition.PID_REUSED_OR_IDENTITY_MISMATCH,
            observed,
            detail=(
                "the recorded PID exists but its executable path, start time or "
                "file version does not match the Session Manifest"
            ),
        )
    return CaptureProcessAssessment(
        CaptureProcessDisposition.UNOWNED_INSTANCE,
        observed,
        detail="the observed Capture instance does not match the Session Manifest",
    )


class CaptureProcessInspector:
    """Read-only Capture process inventory over the generic AirBridge Shell API."""

    def __init__(self, airbridge_client: Any, shell_timeout_seconds: int = 30):
        if (
            isinstance(shell_timeout_seconds, bool)
            or not isinstance(shell_timeout_seconds, int)
            or not 1 <= shell_timeout_seconds <= 120
        ):
            raise ValueError("shell_timeout_seconds must be between 1 and 120")
        if airbridge_client is None or not callable(
            getattr(airbridge_client, "shell_execute", None)
        ):
            raise TypeError("airbridge_client must provide shell_execute()")
        self._client = airbridge_client
        self.shell_timeout_seconds = shell_timeout_seconds

    def inspect(self) -> tuple[CaptureProcessCandidate, ...]:
        response = self._client.shell_execute(
            build_capture_inventory_command(),
            shell="powershell",
            task_timeout_seconds=self.shell_timeout_seconds,
            async_mode=False,
        )
        if not isinstance(response, Mapping):
            raise CaptureProcessInventoryError(
                "AirBridge Shell returned a non-object response"
            )

        remote_state = str(response.get("state") or "").lower()
        if remote_state in {"accepted", "pending", "unknown"}:
            raise CaptureProcessInventoryPending(
                response.get("request_id"), remote_state
            )
        if remote_state and remote_state != "completed":
            raise CaptureProcessInventoryError(
                "AirBridge Shell request did not complete (state={})".format(
                    remote_state
                )
            )

        result = response.get("result", response)
        if not isinstance(result, Mapping):
            raise CaptureProcessInventoryError(
                "AirBridge Shell result is not a JSON object"
            )
        if result.get("stdout_truncated") or result.get("truncated"):
            raise CaptureProcessInventoryError(
                "AirBridge truncated the process inventory; refusing to use partial data"
            )
        if result.get("success") is False:
            raise CaptureProcessInventoryError(
                "Remote PowerShell inventory failed: {}".format(
                    result.get("error") or result.get("stderr") or "unknown error"
                )
            )
        exit_code = result.get("exit_code")
        if isinstance(exit_code, bool) or exit_code != 0:
            raise CaptureProcessInventoryError(
                "Remote PowerShell inventory did not exit successfully"
            )
        stdout = result.get("stdout")
        if not isinstance(stdout, str):
            raise CaptureProcessInventoryError(
                "Remote PowerShell inventory did not return stdout text"
            )
        return parse_capture_inventory_json(stdout)

    def inspect_session(self, manifest: Any) -> CaptureProcessAssessment:
        return assess_capture_processes(self.inspect(), manifest)
