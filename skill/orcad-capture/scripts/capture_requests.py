# -*- coding: utf-8 -*-
"""Skill-owned, file-queue transport for one Capture Tcl interpreter.

The queue uses only generic AirBridge Shell and RFS operations.  Request and
result envelopes are plain, versioned ASCII records with Base64 UTF-8 payloads;
the Tcl Bridge does not need a JSON or SHA-256 package.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import ntpath
import os
import re
import tempfile
import time
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from airbridge_client import (
    AirBridgeHTTPError,
    AirBridgeTransportError,
)
from capture_process import CaptureProcessDisposition, CaptureProcessInspector
from capture_session import (
    CaptureAuthorizationError,
    CaptureConfig,
    CaptureIdentityError,
    CaptureSession,
    CaptureSessionError,
    CaptureStateTransitionError,
    SessionState,
)


MAX_SCRIPT_BYTES = 256 * 1024
MAX_RESULT_BYTES = 512 * 1024
MAX_RESULT_ENVELOPE_BYTES = 1_100_000
REQUEST_MAGIC = "AB_CAPTURE_REQUEST_V1"
RESULT_MAGIC = "AB_CAPTURE_RESULT_V1"


class CaptureTclQueueError(RuntimeError):
    """A Capture Tcl queue operation failed safely."""


class CaptureTclOutcomeUnknown(CaptureTclQueueError):
    """A request may have been accepted or executed; never resubmit it."""

    def __init__(self, message: str, request_id: str):
        self.request_id = request_id
        super().__init__(message)


@dataclass(frozen=True)
class CaptureTclRequest:
    request_id: str
    script_sha256: str
    script_length: int
    request_path: str
    result_path: str


@dataclass(frozen=True)
class CaptureTclResult:
    request_id: str
    script_sha256: str
    success: bool
    value: str
    error_info: str
    result_truncated: bool = False
    error_info_truncated: bool = False


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _uuid(value: Optional[str], field: str) -> str:
    try:
        return str(uuid.UUID(str(value))) if value else str(uuid.uuid4())
    except (ValueError, TypeError, AttributeError) as exc:
        raise CaptureTclQueueError("{} must be a UUID".format(field)) from exc


def _crc32_hex(value: bytes) -> str:
    return "{:08x}".format(zlib.crc32(value) & 0xFFFFFFFF)


def build_capture_request_envelope(
    session_id: str,
    startup_nonce: str,
    request_id: str,
    script: str,
) -> tuple[bytes, str, str]:
    """Create the exact bytes consumed by capture_bridge.tcl."""
    normalized_session = _uuid(session_id, "session_id")
    normalized_request = _uuid(request_id, "request_id")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", str(startup_nonce or "")):
        raise CaptureTclQueueError("startup_nonce is invalid")
    if not isinstance(script, str) or not script.strip():
        raise CaptureTclQueueError("Tcl script must be a non-empty string")
    if "\x00" in script:
        raise CaptureTclQueueError("Tcl script must not contain NUL characters")

    script_bytes = script.encode("utf-8")
    if len(script_bytes) > MAX_SCRIPT_BYTES:
        raise CaptureTclQueueError(
            "Tcl script exceeds the {} byte limit".format(MAX_SCRIPT_BYTES)
        )
    digest = hashlib.sha256(script_bytes).hexdigest()
    crc = _crc32_hex(script_bytes)
    encoded = base64.b64encode(script_bytes).decode("ascii")
    lines = [
        REQUEST_MAGIC,
        "session_id={}".format(normalized_session),
        "startup_nonce={}".format(startup_nonce),
        "request_id={}".format(normalized_request),
        "script_sha256={}".format(digest),
        "script_crc32={}".format(crc),
        "script_length={}".format(len(script_bytes)),
        "script_b64={}".format(encoded),
    ]
    return ("\n".join(lines) + "\n").encode("ascii"), digest, crc


def build_publish_capture_request_command(
    staging_path: str, request_path: str, expected_sha256: str
) -> str:
    """Verify uploaded bytes on Host B, then publish with no-overwrite File.Move."""
    digest = str(expected_sha256 or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise CaptureTclQueueError("expected request SHA-256 is invalid")
    source_b64 = base64.b64encode(staging_path.encode("utf-8")).decode("ascii")
    target_b64 = base64.b64encode(request_path.encode("utf-8")).decode("ascii")
    return (
        "$ErrorActionPreference = 'Stop'; "
        "$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("
        + "'" + source_b64 + "'" + ")); "
        "$target = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("
        + "'" + target_b64 + "'" + ")); "
        "$expected = '" + digest + "'; "
        "if (-not [IO.File]::Exists($source)) { throw 'Request staging file is absent' }; "
        "if ([IO.File]::Exists($target)) { throw 'Request destination already exists; refusing overwrite' }; "
        "$actual = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant(); "
        "if ($actual -ne $expected) { throw 'Request staging SHA-256 mismatch' }; "
        "[IO.File]::Move($source, $target); "
        "[pscustomobject]@{published=$true; path=$target; sha256=$actual} | ConvertTo-Json -Compress"
    )


def build_capture_result_envelope(
    session_id: str,
    startup_nonce: str,
    request_id: str,
    script_sha256: str,
    *,
    success: bool,
    value: str = "",
    error_info: str = "",
) -> bytes:
    """Reference serializer used by tests and local Bridge protocol fixtures."""
    result_bytes = str(value).encode("utf-8")
    error_bytes = str(error_info).encode("utf-8")
    if len(result_bytes) > MAX_RESULT_BYTES or len(error_bytes) > MAX_RESULT_BYTES:
        raise CaptureTclQueueError("fixture result exceeds the configured limit")
    lines = [
        RESULT_MAGIC,
        "session_id={}".format(_uuid(session_id, "session_id")),
        "startup_nonce={}".format(startup_nonce),
        "request_id={}".format(_uuid(request_id, "request_id")),
        "script_sha256={}".format(str(script_sha256).lower()),
        "status={}".format("ok" if success else "error"),
        "result_length={}".format(len(result_bytes)),
        "result_crc32={}".format(_crc32_hex(result_bytes)),
        "result_truncated=false",
        "result_b64={}".format(base64.b64encode(result_bytes).decode("ascii")),
        "error_info_length={}".format(len(error_bytes)),
        "error_info_crc32={}".format(_crc32_hex(error_bytes)),
        "error_info_truncated=false",
        "error_info_b64={}".format(base64.b64encode(error_bytes).decode("ascii")),
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


class CaptureTclQueue:
    """Submit one authorized Tcl script and reconcile only by its original ID."""

    def __init__(
        self,
        client: Any,
        session: CaptureSession,
        config: CaptureConfig,
        manifest_path: str | Path,
        *,
        inspector: Optional[CaptureProcessInspector] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ):
        if client is None or not callable(getattr(client, "filesystem", None)):
            raise TypeError("client must provide filesystem()")
        if not callable(getattr(client, "shell_execute", None)):
            raise TypeError("client must provide shell_execute()")
        if not isinstance(session, CaptureSession):
            raise TypeError("session must be a CaptureSession")
        if not isinstance(config, CaptureConfig):
            raise TypeError("config must be a CaptureConfig")
        if not str(manifest_path or "").strip():
            raise ValueError("manifest_path is required")
        self.client = client
        self.session = session
        self.config = config
        self.manifest_path = Path(manifest_path)
        self.inspector = inspector or CaptureProcessInspector(
            client, shell_timeout_seconds=config.shell_timeout_seconds
        )
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn

    def submit(
        self,
        script: str,
        *,
        user_authorized: bool = False,
        request_id: Optional[str] = None,
    ) -> CaptureTclRequest:
        """Persist local intent, upload a unique staging file, then publish once."""
        if user_authorized is not True:
            raise CaptureAuthorizationError(
                "executing Tcl in Capture requires explicit user authorization"
            )
        if self.session.state is not SessionState.READY:
            raise CaptureStateTransitionError(
                "Tcl submission requires a READY Capture session"
            )
        manifest = self.session.manifest
        if manifest.reconciliation_required or manifest.active_request_id:
            raise CaptureStateTransitionError(
                "reconcile the existing Request ID before submitting another Tcl script"
            )
        if not manifest.remote_session_dir or not manifest.pid:
            raise CaptureIdentityError(
                "a verified Capture process and remote session directory are required"
            )

        rid = _uuid(request_id, "request_id")
        envelope, script_sha256, _script_crc = build_capture_request_envelope(
            manifest.session_id, manifest.startup_nonce, rid, script
        )
        if len(envelope) > 400000:
            raise CaptureTclQueueError("request envelope exceeds the Tcl Bridge limit")

        queue_dir = ntpath.join(manifest.remote_session_dir, "requests")
        staging_path = ntpath.join(queue_dir, rid + ".tmp")
        request_path = ntpath.join(queue_dir, rid + ".req")
        result_path = ntpath.join(
            manifest.remote_session_dir, "results", rid + ".result"
        )
        upload_request_id = str(uuid.uuid4())
        publish_request_id = str(uuid.uuid4())
        record = {
            "schema_version": 1,
            "session_id": manifest.session_id,
            "request_id": rid,
            "script_sha256": script_sha256,
            "script_length": len(script.encode("utf-8")),
            "staging_path": staging_path,
            "request_path": request_path,
            "result_path": result_path,
            "upload_request_id": upload_request_id,
            "publish_request_id": publish_request_id,
            "state": "prepared",
            "created_at_utc": _utc_now(),
            "updated_at_utc": _utc_now(),
        }
        record_path = self._record_path(rid)
        self._create_record(record_path, record)

        try:
            self.session.begin_operation(rid)
            self._save_session()
        except Exception:
            try:
                record_path.unlink()
            except OSError:
                pass
            raise

        try:
            upload_receipt = self.client.filesystem(
                "write",
                {
                    "path": staging_path,
                    "content": base64.b64encode(envelope).decode("ascii"),
                    "is_base64": True,
                    "mode": "w",
                    "request_id": upload_request_id,
                },
                timeout_seconds=self.config.shell_timeout_seconds + 10,
            )
            if not isinstance(upload_receipt, Mapping) or upload_receipt.get("success") is not True:
                raise CaptureTclOutcomeUnknown(
                    "RFS request upload did not return a trustworthy receipt", rid
                )
            if str(upload_receipt.get("path") or "").casefold() != staging_path.casefold():
                raise CaptureTclOutcomeUnknown(
                    "RFS request upload receipt path did not match", rid
                )
            self._update_record(record_path, record, "uploaded")

            uploaded = self._read_remote_bytes(staging_path)
            if uploaded != envelope:
                raise CaptureTclOutcomeUnknown(
                    "RFS request read-back did not match the exact request bytes", rid
                )

            publish_command = build_publish_capture_request_command(
                staging_path, request_path, hashlib.sha256(envelope).hexdigest()
            )
            shell_response = self.client.shell_execute(
                publish_command,
                shell="powershell",
                task_timeout_seconds=self.config.shell_timeout_seconds,
                async_mode=False,
                request_id=publish_request_id,
            )
            receipt = self._unwrap_publish_response(
                shell_response, publish_request_id, request_path, hashlib.sha256(envelope).hexdigest()
            )
            if receipt.get("published") is not True:
                raise CaptureTclOutcomeUnknown(
                    "Host B did not confirm the atomic request publication", rid
                )
            self._update_record(record_path, record, "published")
            self._save_session()
            return CaptureTclRequest(
                request_id=rid,
                script_sha256=script_sha256,
                script_length=len(script.encode("utf-8")),
                request_path=request_path,
                result_path=result_path,
            )
        except CaptureTclOutcomeUnknown as exc:
            self._mark_unknown(record_path, record, rid, str(exc))
            raise
        except (AirBridgeTransportError, AirBridgeHTTPError, OSError, ValueError, TypeError) as exc:
            self._mark_unknown(record_path, record, rid, str(exc))
            raise CaptureTclOutcomeUnknown(
                "Capture Tcl request submission outcome is unknown; query only Request ID {}".format(rid),
                rid,
            ) from exc
        except Exception as exc:
            self._mark_unknown(record_path, record, rid, str(exc))
            raise CaptureTclOutcomeUnknown(
                "Capture Tcl request submission failed with an unconfirmed outcome; do not resubmit",
                rid,
            ) from exc

    def execute(
        self,
        script: str,
        *,
        user_authorized: bool = False,
        request_id: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ) -> CaptureTclResult:
        request = self.submit(
            script,
            user_authorized=user_authorized,
            request_id=request_id,
        )
        return self.wait_for_result(request.request_id, timeout_seconds=timeout_seconds)

    def wait_for_result(
        self, request_id: str, *, timeout_seconds: float = 60.0
    ) -> CaptureTclResult:
        rid = _uuid(request_id, "request_id")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
            or timeout_seconds > 3600
        ):
            raise ValueError("timeout_seconds must be in (0, 3600]")
        if self.session.manifest.active_request_id != rid:
            raise CaptureIdentityError("Request ID is not the active Capture operation")
        if self.session.state is SessionState.UNKNOWN:
            raise CaptureStateTransitionError(
                "UNKNOWN requests must use read-only reconcile(), not ordinary polling"
            )
        if self.session.state is not SessionState.BUSY:
            raise CaptureStateTransitionError(
                "result polling requires a BUSY Capture operation"
            )
        record_path, record = self._load_record(rid)
        deadline = self.monotonic_fn() + float(timeout_seconds)

        while True:
            try:
                raw = self._read_remote_bytes(record["result_path"])
            except (AirBridgeTransportError, AirBridgeHTTPError, CaptureTclQueueError) as exc:
                if self.session.state is not SessionState.UNKNOWN:
                    self._mark_unknown(record_path, record, rid, str(exc))
                raise CaptureTclOutcomeUnknown(
                    "Capture result read failed; the original Request ID remains UNKNOWN",
                    rid,
                ) from exc
            if raw is not None:
                try:
                    result = self._parse_result(raw, rid, record)
                except Exception as exc:
                    self._mark_unknown(
                        record_path, record, rid,
                        "Capture result envelope failed identity or integrity validation: {}".format(exc),
                    )
                    raise CaptureTclOutcomeUnknown(
                        "Capture result could not be trusted; keep the original Request ID UNKNOWN",
                        rid,
                    ) from exc
                self._complete(record_path, record, result, allow_unknown=False)
                return result
            if self.monotonic_fn() >= deadline:
                if self.session.state is not SessionState.UNKNOWN:
                    self._mark_unknown(
                        record_path,
                        record,
                        rid,
                        "Capture Tcl result timed out; no duplicate request was submitted",
                    )
                raise CaptureTclOutcomeUnknown(
                    "Capture Tcl request timed out; reconcile only the original Request ID",
                    rid,
                )
            self.sleep_fn(min(self.config.poll_interval_seconds, 1.0))

    def reconcile(self, request_id: str) -> Optional[CaptureTclResult]:
        """Read the original result and original process identity; never mutate Host B."""
        rid = _uuid(request_id, "request_id")
        if self.session.state is not SessionState.UNKNOWN:
            raise CaptureStateTransitionError(
                "read-only result reconciliation requires an UNKNOWN session"
            )
        if self.session.manifest.active_request_id != rid:
            raise CaptureIdentityError("Request ID is not the unresolved active operation")
        record_path, record = self._load_record(rid)

        try:
            raw = self._read_remote_bytes(record["result_path"])
        except (AirBridgeTransportError, AirBridgeHTTPError, CaptureTclQueueError) as exc:
            raise CaptureTclOutcomeUnknown(
                "read-only reconciliation could not retrieve the original result",
                rid,
            ) from exc
        if raw is None:
            return None

        try:
            result = self._parse_result(raw, rid, record)
        except Exception as exc:
            raise CaptureTclOutcomeUnknown(
                "original result failed identity or integrity validation",
                rid,
            ) from exc
        assessment = self.inspector.inspect_session(self.session.manifest)
        if getattr(assessment, "disposition", None) is not CaptureProcessDisposition.OWNED_INSTANCE:
            raise CaptureTclOutcomeUnknown(
                "result exists, but the original Capture process identity is not confirmed",
                rid,
            )
        self._complete(record_path, record, result, allow_unknown=True)
        return result

    def _read_remote_bytes(self, remote_path: str) -> Optional[bytes]:
        try:
            response = self.client.filesystem(
                "read",
                {
                    "path": remote_path,
                    "as_base64": True,
                    "max_preview_bytes": MAX_RESULT_ENVELOPE_BYTES,
                    "request_id": str(uuid.uuid4()),
                },
                timeout_seconds=self.config.shell_timeout_seconds + 10,
            )
        except AirBridgeHTTPError as exc:
            error_text = json.dumps(exc.payload, ensure_ascii=False).casefold()
            if exc.status_code in (404, 500) and any(
                term in error_text
                for term in ("file not found", "文件不存在", "no such file")
            ):
                return None
            raise
        if not isinstance(response, Mapping):
            raise CaptureTclQueueError("RFS read response is not an object")
        if response.get("success") is not True:
            error = json.dumps(response, ensure_ascii=False).casefold()
            if any(term in error for term in ("file not found", "文件不存在", "no such file")):
                return None
            raise CaptureTclQueueError("RFS read did not succeed")
        if response.get("is_truncated") is True:
            raise CaptureTclQueueError("RFS queue file was truncated")
        returned_path = response.get("path")
        if not isinstance(returned_path, str) or (
            ntpath.normcase(ntpath.normpath(returned_path))
            != ntpath.normcase(ntpath.normpath(remote_path))
        ):
            raise CaptureTclQueueError("RFS returned a different queue file path")
        content = response.get("content")
        if not isinstance(content, str) or response.get("encoding") != "base64":
            raise CaptureTclQueueError("RFS did not return Base64 queue bytes")
        try:
            raw = base64.b64decode(content.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise CaptureTclQueueError("RFS returned invalid Base64 queue bytes") from exc
        if len(raw) > MAX_RESULT_ENVELOPE_BYTES:
            raise CaptureTclQueueError("RFS queue file exceeded the transport size limit")
        declared_size = response.get("size")
        if declared_size is not None and (
            isinstance(declared_size, bool)
            or not isinstance(declared_size, int)
            or declared_size != len(raw)
        ):
            raise CaptureTclQueueError("RFS queue file size did not match its content")
        return raw

    def _parse_result(
        self, raw: bytes, request_id: str, record: Mapping[str, Any]
    ) -> CaptureTclResult:
        if len(raw) > MAX_RESULT_ENVELOPE_BYTES:
            raise CaptureTclQueueError("Capture result envelope exceeds the size limit")
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise CaptureTclQueueError("Capture result envelope is not ASCII") from exc
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        expected = [
            "session_id", "startup_nonce", "request_id", "script_sha256",
            "status", "result_length", "result_crc32", "result_truncated",
            "result_b64", "error_info_length", "error_info_crc32",
            "error_info_truncated", "error_info_b64",
        ]
        if not lines or lines[0] != RESULT_MAGIC or len(lines) != len(expected) + 1:
            raise CaptureTclQueueError("Capture result envelope shape is invalid")
        fields = {}
        for key, line in zip(expected, lines[1:]):
            prefix = key + "="
            if not line.startswith(prefix):
                raise CaptureTclQueueError("Capture result field order is invalid")
            fields[key] = line[len(prefix):]

        if fields["session_id"] != self.session.manifest.session_id:
            raise CaptureIdentityError("result Session ID does not match")
        if not hmac.compare_digest(fields["startup_nonce"], self.session.manifest.startup_nonce):
            raise CaptureIdentityError("result Startup Nonce does not match")
        if fields["request_id"] != request_id:
            raise CaptureIdentityError("result Request ID does not match")
        if not hmac.compare_digest(fields["script_sha256"], str(record["script_sha256"])):
            raise CaptureIdentityError("result script SHA-256 does not match the submitted script")
        if fields["status"] not in ("ok", "error"):
            raise CaptureTclQueueError("Capture result status is invalid")

        def decode_value(prefix: str) -> tuple[str, bool]:
            try:
                size = int(fields[prefix + "_length"])
            except (TypeError, ValueError) as exc:
                raise CaptureTclQueueError("Capture result length is invalid") from exc
            if size < 0 or size > MAX_RESULT_BYTES:
                raise CaptureTclQueueError("Capture result payload exceeds the size limit")
            encoded = fields[prefix + "_b64"]
            if not re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", encoded) or len(encoded) % 4:
                raise CaptureTclQueueError("Capture result Base64 is malformed")
            try:
                data = base64.b64decode(encoded.encode("ascii"), validate=True)
            except (ValueError, UnicodeEncodeError) as exc:
                raise CaptureTclQueueError("Capture result Base64 is invalid") from exc
            if len(data) != size:
                raise CaptureTclQueueError("Capture result payload length does not match")
            if _crc32_hex(data) != fields[prefix + "_crc32"]:
                raise CaptureTclQueueError("Capture result payload CRC-32 does not match")
            try:
                decoded = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CaptureTclQueueError("Capture result payload is not UTF-8") from exc
            truncated = fields[prefix + "_truncated"]
            if truncated not in ("true", "false"):
                raise CaptureTclQueueError("Capture result truncation flag is invalid")
            return decoded, truncated == "true"

        value, result_truncated = decode_value("result")
        error_info, error_truncated = decode_value("error_info")
        return CaptureTclResult(
            request_id=request_id,
            script_sha256=str(record["script_sha256"]),
            success=fields["status"] == "ok",
            value=value,
            error_info=error_info,
            result_truncated=result_truncated,
            error_info_truncated=error_truncated,
        )

    @staticmethod
    def _unwrap_publish_response(
        response: Any, request_id: str, expected_path: str, expected_sha256: str
    ) -> Mapping[str, Any]:
        if not isinstance(response, Mapping):
            raise CaptureTclOutcomeUnknown(
                "Shell publication returned no structured response", request_id
            )
        if response.get("request_id") != request_id or response.get("state") != "completed":
            raise CaptureTclOutcomeUnknown(
                "Shell publication was accepted but not confirmed complete", request_id
            )
        result = response.get("result")
        if (
            not isinstance(result, Mapping)
            or result.get("success") is not True
            or result.get("exit_code") != 0
        ):
            raise CaptureTclOutcomeUnknown(
                "Shell publication did not return a successful exit code", request_id
            )
        try:
            receipt = json.loads(str(result.get("stdout") or ""))
        except json.JSONDecodeError as exc:
            raise CaptureTclOutcomeUnknown(
                "Shell publication receipt is malformed", request_id
            ) from exc
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("published") is not True
            or str(receipt.get("path") or "").casefold() != expected_path.casefold()
            or str(receipt.get("sha256") or "").lower() != expected_sha256
        ):
            raise CaptureTclOutcomeUnknown(
                "Shell publication receipt identity/hash mismatch", request_id
            )
        return receipt

    def _complete(
        self,
        record_path: Path,
        record: dict[str, Any],
        result: CaptureTclResult,
        *,
        allow_unknown: bool,
    ) -> None:
        record["state"] = "completed" if result.success else "completed_with_error"
        record["updated_at_utc"] = _utc_now()
        self._save_record(record_path, record)
        if self.session.state is SessionState.UNKNOWN:
            if not allow_unknown:
                raise CaptureStateTransitionError("UNKNOWN result resolution was not authorized")
            self.session.resolve_unknown_operation(result.request_id)
        else:
            self.session.complete_operation(result.request_id)
        self._save_session()

    def _mark_unknown(
        self,
        record_path: Path,
        record: dict[str, Any],
        request_id: str,
        message: str,
    ) -> None:
        if self.session.state not in (SessionState.UNKNOWN, SessionState.CLOSED):
            self.session.mark_unknown("TCL_REQUEST_OUTCOME_UNKNOWN", message)
        record["state"] = "unknown"
        record["updated_at_utc"] = _utc_now()
        self._save_record(record_path, record)
        self._save_session()

    def _record_path(self, request_id: str) -> Path:
        return self.manifest_path.with_name(
            self.manifest_path.name + ".requests"
        ) / (request_id + ".json")

    def _create_record(self, path: Path, record: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(dict(record), stream, ensure_ascii=True, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise CaptureTclQueueError(
                "Request ID already has a local record; refusing reuse"
            ) from exc

    @staticmethod
    def _save_record(path: Path, record: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=str(path.parent),
                prefix=path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_name = stream.name
                json.dump(dict(record), stream, ensure_ascii=True, sort_keys=True, indent=2)
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

    def _update_record(
        self, path: Path, record: dict[str, Any], state: str
    ) -> None:
        record["state"] = state
        record["updated_at_utc"] = _utc_now()
        self._save_record(path, record)

    def _load_record(self, request_id: str) -> tuple[Path, dict[str, Any]]:
        path = self._record_path(request_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CaptureTclQueueError("local Request record is unavailable") from exc
        except json.JSONDecodeError as exc:
            raise CaptureTclQueueError("local Request record is malformed") from exc
        allowed = {
            "schema_version", "session_id", "request_id", "script_sha256",
            "script_length", "staging_path", "request_path", "result_path",
            "upload_request_id", "publish_request_id", "state",
            "created_at_utc", "updated_at_utc",
        }
        if not isinstance(value, Mapping) or set(value) != allowed:
            raise CaptureTclQueueError("local Request record fields are invalid")
        if (
            value["schema_version"] != 1
            or value["session_id"] != self.session.manifest.session_id
            or value["request_id"] != request_id
            or not re.fullmatch(r"[0-9a-f]{64}", str(value["script_sha256"]))
        ):
            raise CaptureIdentityError("local Request record identity is invalid")
        return path, dict(value)

    def _save_session(self) -> None:
        self.session.manifest.updated_at_utc = _utc_now()
        self.session.manifest.save(self.manifest_path)
