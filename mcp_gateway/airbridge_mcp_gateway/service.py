"""MCP-facing operations over AirBridge's existing HTTP API."""

from __future__ import annotations

import json
import uuid
from typing import Any

from .config import GatewayConfig
from .http_client import AirBridgeHttpClient, AirBridgeTransportError


class AirBridgeGateway:
    """Domain-neutral gateway; it does not interpret Tcl or EDA business semantics."""

    def __init__(
        self,
        config: GatewayConfig | None = None,
        client: AirBridgeHttpClient | None = None,
    ):
        self.config = config or GatewayConfig.from_env()
        self.client = client or AirBridgeHttpClient(self.config)

    def get_capabilities(self) -> dict[str, Any]:
        try:
            return self.client.get_capabilities()
        except AirBridgeTransportError as exc:
            return self._unavailable(exc)

    def get_eda_versions(self) -> dict[str, Any]:
        try:
            return self.client.get_eda_versions()
        except AirBridgeTransportError as exc:
            return self._unavailable(exc)

    def execute_eda(
        self,
        script: str,
        hint: str = "agent_request.tcl",
        version: str = "auto",
        target_file: str = "",
        timeout: int = 60,
        keep_open: bool = True,
        wait_timeout_ms: int = 60_000,
    ) -> dict[str, Any]:
        """Submit opaque script text; this layer never parses or rewrites it."""
        if not self.config.enable_eda_execute:
            return {
                "success": False,
                "state": "disabled",
                "error": {
                    "code": "EDA_EXECUTION_DISABLED",
                    "message": (
                        "EDA execution is disabled. Set AIRBRIDGE_ENABLE_EDA_EXECUTE=1 "
                        "only for a trusted local MCP client."
                    ),
                },
            }
        validation = self._validate_eda_arguments(
            script, hint, version, target_file, timeout, keep_open, wait_timeout_ms
        )
        if validation is not None:
            return validation

        request_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "request_id": request_id,
            "script": script,
            "hint": hint,
            "version": version,
            "target_file": target_file,
            "timeout": timeout,
            "keep_open": keep_open,
            "wait_timeout_ms": wait_timeout_ms,
        }
        encoded_size = len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        if encoded_size > self.config.max_request_body_bytes:
            return {
                "success": False,
                "state": "not_submitted",
                "error": {
                    "code": "REQUEST_TOO_LARGE",
                    "message": (
                        f"Serialized HTTP request is {encoded_size} bytes; gateway limit is "
                        f"{self.config.max_request_body_bytes}. Use the existing artifact/RFS "
                        "path for larger payloads; this gateway does not upload artifacts."
                    ),
                },
            }

        http_timeout = max(
            self.config.http_timeout_seconds,
            wait_timeout_ms / 1000.0 + 15.0,
        )
        try:
            result = self.client.execute_eda(
                payload,
                http_timeout_seconds=http_timeout,
            )
            if result.get("state") not in {
                "disabled",
                "not_submitted",
                "rate_limited",
                "forbidden",
                "unauthorized",
                "unavailable",
                "not_found",
            }:
                result.setdefault("request_id", request_id)
            return result
        except AirBridgeTransportError as exc:
            return {
                "success": False,
                "state": "unconfirmed",
                "request_id": request_id,
                "error": {
                    "code": "AIRBRIDGE_RESPONSE_UNCONFIRMED",
                    "message": str(exc),
                    "next_step": "Call airbridge_request_status with this request_id; do not resubmit.",
                },
            }

    def get_request_status(self, request_id: str) -> dict[str, Any]:
        try:
            parsed = uuid.UUID(request_id)
        except (AttributeError, TypeError, ValueError):
            return {
                "success": False,
                "state": "not_submitted",
                "error": {
                    "code": "INVALID_REQUEST_ID",
                    "message": "request_id must be a UUID returned by airbridge_eda_execute",
                },
            }
        canonical_id = str(parsed)
        try:
            result = self.client.get_request_status(canonical_id)
            result.setdefault("request_id", canonical_id)
            return result
        except AirBridgeTransportError as exc:
            return self._unavailable(exc, request_id=canonical_id)

    @staticmethod
    def _validate_eda_arguments(
        script: str,
        hint: str,
        version: str,
        target_file: str,
        timeout: int,
        keep_open: bool,
        wait_timeout_ms: int,
    ) -> dict[str, Any] | None:
        if not isinstance(script, str) or not script.strip():
            return AirBridgeGateway._invalid("script must be a non-empty string")
        if not isinstance(hint, str) or not hint.strip():
            return AirBridgeGateway._invalid("hint must be a non-empty string")
        if not isinstance(version, str) or not version.strip():
            return AirBridgeGateway._invalid("version must be a non-empty string")
        if not isinstance(target_file, str):
            return AirBridgeGateway._invalid("target_file must be a string")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
            return AirBridgeGateway._invalid("timeout must be an integer in 1..300 seconds")
        if not isinstance(keep_open, bool):
            return AirBridgeGateway._invalid("keep_open must be a boolean")
        if (
            isinstance(wait_timeout_ms, bool)
            or not isinstance(wait_timeout_ms, int)
            or not 0 <= wait_timeout_ms <= 300_000
        ):
            return AirBridgeGateway._invalid("wait_timeout_ms must be an integer in 0..300000")
        return None

    @staticmethod
    def _invalid(message: str) -> dict[str, Any]:
        return {
            "success": False,
            "state": "not_submitted",
            "error": {"code": "INVALID_ARGUMENT", "message": message},
        }

    @staticmethod
    def _unavailable(
        exc: AirBridgeTransportError, *, request_id: str | None = None
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "success": False,
            "state": "unavailable",
            "error": {"code": "AIRBRIDGE_UNAVAILABLE", "message": str(exc)},
        }
        if request_id is not None:
            result["request_id"] = request_id
        return result
