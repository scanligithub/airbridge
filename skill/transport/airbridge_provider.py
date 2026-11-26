# -*- coding: utf-8 -*-
"""Host-A Transport Provider for the existing generic AirBridge HTTP API.

This module is EDA-neutral. It talks only to the local Host-A API and never
opens the serial port or imports AirBridge Core implementation modules.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping, Optional

from .contract import (
    CONTRACT_VERSION,
    Capability,
    OperationResponse,
    ProviderClosedError,
    ProviderInfo,
    normalize_request_id,
)


DEFAULT_API_BASE_URL = "http://127.0.0.1:8888"
TOKEN_ENV = "AIRBRIDGE_AGENT_TOKEN"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class AirBridgeProviderError(RuntimeError):
    """A configuration, HTTP, or protocol error from the AirBridge Provider."""


class _AirBridgeHTTPError(AirBridgeProviderError):
    def __init__(self, status_code: int, payload: Mapping[str, Any]):
        self.status_code = int(status_code)
        self.payload = dict(payload)
        # Only include the server's structured error, never request headers.
        detail = self.payload.get("error") or self.payload.get("message") or "request failed"
        super().__init__("AirBridge HTTP {}: {}".format(status_code, detail))


class _AirBridgeTransportError(AirBridgeProviderError):
    """The HTTP exchange ended without a trustworthy response."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not forward the Agent Bearer token through an HTTP redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _validate_base_url(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
        parsed.port
        host_ip = ipaddress.ip_address(parsed.hostname or "")
        is_loopback = host_ip.is_loopback
    except ValueError:
        parsed = None
        is_loopback = False

    if (
        parsed is None
        or parsed.scheme not in ("http", "https")
        or not is_loopback
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or not parsed.netloc
    ):
        raise AirBridgeProviderError(
            "AirBridge API URL must use a loopback IP literal; "
            "the Agent token must not be sent to another host"
        )
    return raw.rstrip("/")


class AirBridgeProvider:
    """Map the generic Transport Contract to Host A's local AirBridge API."""

    def __init__(
        self,
        base_url: str = DEFAULT_API_BASE_URL,
        token: Optional[str] = None,
        timeout_seconds: float = 75.0,
        opener: Optional[Callable[..., Any]] = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.base_url = _validate_base_url(base_url)
        source = os.environ if environ is None else environ
        selected_token = token if token is not None else source.get(TOKEN_ENV, "")
        selected_token = str(selected_token or "").strip()
        if not selected_token:
            raise AirBridgeProviderError(
                "{} must be provisioned for the calling Agent".format(TOKEN_ENV)
            )
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in selected_token):
            raise AirBridgeProviderError("Agent token contains invalid control characters")
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise AirBridgeProviderError("timeout_seconds must be positive") from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise AirBridgeProviderError("timeout_seconds must be finite and positive")

        self._token = selected_token
        self.timeout_seconds = timeout
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        ).open
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProviderClosedError("Transport Provider is closed")

    def get_info(self) -> ProviderInfo:
        self._ensure_open()
        return ProviderInfo(
            provider_name="airbridge",
            provider_version="0.1.0",
            contract_version=CONTRACT_VERSION,
        )

    def get_capabilities(self) -> list[Capability]:
        self._ensure_open()
        return [
            Capability(name="shell.execute", supported=True),
            Capability(name="request.read", supported=True),
        ]

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Optional[Mapping[str, Any]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> dict[str, Any]:
        self._ensure_open()
        if not path.startswith("/api/v1/") or "//" in path:
            raise ValueError("path must be an AirBridge /api/v1/ endpoint")
        timeout = self.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be finite and positive")

        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self._token,
        }
        if payload is not None:
            body = json.dumps(
                dict(payload), ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            response = self._opener(request, timeout=timeout)
            try:
                status_code = int(response.getcode())
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(MAX_RESPONSE_BYTES + 1)
                parsed = json.loads(raw.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                parsed = {"error": "AirBridge returned an invalid HTTP error response"}
            if not isinstance(parsed, dict):
                parsed = {"error": "AirBridge HTTP error response was not an object"}
            raise _AirBridgeHTTPError(exc.code, parsed) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise _AirBridgeTransportError(
                "AirBridge HTTP transport failed; remote outcome may be unknown"
            ) from exc

        if len(raw) > MAX_RESPONSE_BYTES:
            raise AirBridgeProviderError("AirBridge response exceeded the size limit")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AirBridgeProviderError("AirBridge returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise AirBridgeProviderError("AirBridge response must be a JSON object")
        if not 200 <= status_code < 300:
            raise _AirBridgeHTTPError(status_code, result)
        return result

    @staticmethod
    def _to_operation_response(
        payload: Mapping[str, Any], request_id: str
    ) -> OperationResponse:
        normalized_id = normalize_request_id(request_id)
        response_id = payload.get("request_id")
        if response_id is not None and normalize_request_id(str(response_id)) != normalized_id:
            raise AirBridgeProviderError("AirBridge response request_id did not match")

        state = str(payload.get("state") or "").strip().lower()
        if state in ("pending", "accepted"):
            return OperationResponse.accepted(normalized_id)
        if state in ("unknown", "not_found"):
            return OperationResponse.unknown(
                normalized_id,
                str(payload.get("error") or "AirBridge could not determine the operation outcome"),
            )
        if state not in ("completed", "failed"):
            raise AirBridgeProviderError("AirBridge response contained an unsupported operation state")

        result = payload.get("result")
        if not isinstance(result, dict):
            result = {}
        exit_code = result.get("exit_code")
        if exit_code is None:
            success = bool(result.get("success", state == "completed"))
        else:
            success = exit_code == 0 and not bool(result.get("killed", False))
        error = None
        if not success:
            error = str(result.get("stderr") or payload.get("error") or "remote Shell command failed")
        return OperationResponse.completed(
            request_id=normalized_id,
            success=success,
            result=result,
            error=error,
        )

    def _map_http_error(
        self, exc: _AirBridgeHTTPError, request_id: str
    ) -> OperationResponse:
        if exc.status_code == 503 and str(exc.payload.get("state", "")).lower() == "unknown":
            return OperationResponse.unknown(
                request_id,
                str(exc.payload.get("error") or "AirBridge reported UNKNOWN"),
            )
        raise exc

    def shell_execute(
        self,
        command: str,
        options: Optional[Mapping[str, Any]],
        request_id: str,
    ) -> OperationResponse:
        self._ensure_open()
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        normalized_id = normalize_request_id(request_id)
        if options is not None and not isinstance(options, Mapping):
            raise TypeError("options must be a mapping or None")
        selected = dict(options or {})
        unknown_options = set(selected) - {
            "shell", "cwd", "timeout", "http_timeout_seconds"
        }
        if unknown_options:
            raise ValueError("unsupported Shell option: {}".format(sorted(unknown_options)[0]))

        shell = selected.get("shell", "powershell")
        if shell not in ("powershell", "cmd"):
            raise ValueError("shell must be 'powershell' or 'cmd'")
        task_timeout = selected.get("timeout", 60)
        if (
            isinstance(task_timeout, bool)
            or not isinstance(task_timeout, int)
            or not 1 <= task_timeout <= 300
        ):
            raise ValueError("timeout must be an integer between 1 and 300")
        cwd = selected.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise TypeError("cwd must be a string")
        http_timeout = selected.get(
            "http_timeout_seconds",
            max(self.timeout_seconds, task_timeout + 15),
        )

        body: dict[str, Any] = {
            "command": command,
            "shell": shell,
            "timeout": task_timeout,
            "async": False,
            "request_id": normalized_id,
        }
        if cwd:
            body["cwd"] = cwd

        try:
            payload = self._request_json(
                "POST",
                "/api/v1/shell/exec",
                body,
                timeout_seconds=http_timeout,
            )
        except _AirBridgeTransportError as exc:
            return OperationResponse.unknown(normalized_id, str(exc))
        except _AirBridgeHTTPError as exc:
            return self._map_http_error(exc, normalized_id)
        return self._to_operation_response(payload, normalized_id)

    def get_operation(self, request_id: str) -> OperationResponse:
        """Read the correlated request record; this never resubmits Shell work."""
        self._ensure_open()
        normalized_id = normalize_request_id(request_id)
        try:
            payload = self._request_json(
                "GET", "/api/v1/requests/" + normalized_id
            )
        except _AirBridgeTransportError as exc:
            return OperationResponse.unknown(normalized_id, str(exc))
        except _AirBridgeHTTPError as exc:
            return self._map_http_error(exc, normalized_id)
        return self._to_operation_response(payload, normalized_id)

    def close(self) -> None:
        self._closed = True
