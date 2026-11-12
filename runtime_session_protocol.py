# runtime_session_protocol.py
# -*- coding: utf-8 -*-
"""Wire helpers for the generic AirBridge Runtime Session protocol.

This module intentionally contains no Capture, Allegro, Tcl, SKILL, or
Runtime-Adapter behavior. P1.2 implements only HELLO/CAPABILITIES.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from protocol import FrameType, MAX_PAYLOAD_LEN

SCHEMA_VERSION = 1
SESSION_PROTOCOL_ID = "runtime-session.v1"
CLIENT_VERSION = "airbridge-v2.4"
IMPLEMENTED_FEATURES = ("handshake", "request.correlation")


class SessionProtocolError(ValueError):
    """Raised when a Runtime Session message is malformed or unsupported."""


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _validate_uuid(value: Any, field: str, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str):
        raise SessionProtocolError(f"{field} must be a UUID string")
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise SessionProtocolError(f"{field} must be a valid UUID") from exc


def _validate_string_list(value: Any, field: str) -> None:
    if not isinstance(value, list):
        raise SessionProtocolError(f"{field} must be an array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise SessionProtocolError(f"{field} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise SessionProtocolError(f"{field} must not contain duplicates")


def create_envelope(
    payload: Dict[str, Any],
    correlation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "message_id": _new_uuid(),
        "correlation_id": correlation_id,
        "session_id": session_id,
        "request_id": request_id,
        "sent_at_utc": _utc_now(),
        "payload": payload,
    }
    return envelope


def make_session_hello(client_version: str = CLIENT_VERSION) -> Dict[str, Any]:
    return create_envelope({
        "supported_protocols": [SESSION_PROTOCOL_ID],
        "client_version": client_version,
        "max_frame_payload": MAX_PAYLOAD_LEN,
    })


def make_session_capabilities(
    correlation_id: str,
    server_version: str = CLIENT_VERSION,
) -> Dict[str, Any]:
    return create_envelope(
        {
            "supported_protocols": [SESSION_PROTOCOL_ID],
            # This build implements handshake only. Session creation and
            # runtime execution must not be advertised before later phases.
            "features": list(IMPLEMENTED_FEATURES),
            "profiles": [],
            "server_version": server_version,
            "max_frame_payload": MAX_PAYLOAD_LEN,
        },
        correlation_id=correlation_id,
    )


def validate_session_envelope(frame_type: int, envelope: Any) -> Dict[str, Any]:
    if frame_type not in (FrameType.SESSION_HELLO, FrameType.SESSION_CAPABILITIES):
        raise SessionProtocolError(f"unsupported Session frame type: {frame_type}")
    if not isinstance(envelope, dict):
        raise SessionProtocolError("Session envelope must be a JSON object")

    schema_version = envelope.get("schema_version")
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise SessionProtocolError(
            f"unsupported schema_version: {schema_version!r}"
        )

    _validate_uuid(envelope.get("message_id"), "message_id")
    _validate_uuid(envelope.get("correlation_id"), "correlation_id", optional=True)
    _validate_uuid(envelope.get("session_id"), "session_id", optional=True)
    _validate_uuid(envelope.get("request_id"), "request_id", optional=True)

    sent_at = envelope.get("sent_at_utc")
    if not isinstance(sent_at, str) or not sent_at.endswith("Z"):
        raise SessionProtocolError("sent_at_utc must be an ISO-8601 UTC timestamp")
    try:
        parsed_time = datetime.fromisoformat(sent_at[:-1] + "+00:00")
    except ValueError as exc:
        raise SessionProtocolError("sent_at_utc is invalid") from exc
    if parsed_time.tzinfo is None:
        raise SessionProtocolError("sent_at_utc must include a UTC timezone")

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise SessionProtocolError("payload must be a JSON object")

    supported = payload.get("supported_protocols")
    _validate_string_list(supported, "payload.supported_protocols")

    max_payload = payload.get("max_frame_payload")
    if type(max_payload) is not int or not 1 <= max_payload <= MAX_PAYLOAD_LEN:
        raise SessionProtocolError(
            f"payload.max_frame_payload must be between 1 and {MAX_PAYLOAD_LEN}"
        )

    if frame_type == FrameType.SESSION_HELLO:
        client_version = payload.get("client_version")
        if not isinstance(client_version, str) or not client_version.strip():
            raise SessionProtocolError("payload.client_version is required")
    else:
        _validate_uuid(
            envelope.get("correlation_id"),
            "correlation_id",
        )
        _validate_string_list(payload.get("features"), "payload.features")
        profiles = payload.get("profiles")
        if not isinstance(profiles, list):
            raise SessionProtocolError("payload.profiles must be an array")
        for index, profile in enumerate(profiles):
            if not isinstance(profile, dict):
                raise SessionProtocolError(
                    f"payload.profiles[{index}] must be an object"
                )
            for key in ("profile_id", "adapter_id"):
                if not isinstance(profile.get(key), str) or not profile[key].strip():
                    raise SessionProtocolError(
                        f"payload.profiles[{index}].{key} is required"
                    )
            _validate_string_list(
                profile.get("capabilities"),
                f"payload.profiles[{index}].capabilities",
            )
        server_version = payload.get("server_version")
        if not isinstance(server_version, str) or not server_version.strip():
            raise SessionProtocolError("payload.server_version is required")

    return envelope


def encode_session_message(frame_type: int, envelope: Dict[str, Any]) -> bytes:
    validate_session_envelope(frame_type, envelope)
    try:
        encoded = json.dumps(
            envelope,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SessionProtocolError(f"cannot encode Session envelope: {exc}") from exc

    if len(encoded) > MAX_PAYLOAD_LEN:
        raise SessionProtocolError(
            f"Session envelope exceeds ABFP payload limit ({len(encoded)} > "
            f"{MAX_PAYLOAD_LEN})"
        )
    return encoded


def decode_session_message(frame_type: int, payload: bytes) -> Dict[str, Any]:
    if not isinstance(payload, (bytes, bytearray)):
        raise SessionProtocolError("Session payload must be bytes")
    if len(payload) > MAX_PAYLOAD_LEN:
        raise SessionProtocolError("Session payload exceeds ABFP payload limit")
    try:
        decoded = json.loads(
            bytes(payload).decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                SessionProtocolError(f"invalid JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SessionProtocolError(f"invalid Session JSON: {exc}") from exc
    return validate_session_envelope(frame_type, decoded)
