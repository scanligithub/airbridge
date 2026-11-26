# -*- coding: utf-8 -*-
"""Host-A Provider for AirBridge's generic HTTP Shell and request APIs."""
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
MAX_RESPONSE_BYTES = 2 *  1024 * 1024


class AirBridgeProviderError(RuntimeError):
    """AirBridge Provider configuration, HTTP, or protocol error."""


class _UncertainTransportError(Exception):
    """The HTTP exchange ended without a trustworthy response."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        # Never forward the Agent Bearer token to a redirect target.
        return None


def _validate_base_url(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
        parsed.port
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        parsed = None
        loopback = False
    if (
        parsed is None or parsed.scheme not in ("http", "https") or not loopback
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or parsed.path not in ("", "/")
        or not parsed.netloc
    ):
        raise AirBridgeProviderError(
            "AirBridge API URL must use a loopback IP literal"
        )
    return raw.rstrip("/")


class AirBridgeProvider:
    """Map TransportProvider calls to Host A's existing local AirBridge API."""

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
        selected_token = str(
            token if token is not None else source.get(TOKEN_ENV, "")
        ).strip()
        if not selected_token:
            raise AirBridgeProviderError(
                "{} must be provisioned for the calling Agent".format(TOKEN_ENV)
            )
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in selected_token):
            raise AirBridgeProviderError("Agent token contains invalid control characters")
        try:
            self.timeout_seconds = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise AirBridgeProviderError("timeout_seconds must be positive") from exc
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise AirBridgeProviderError("timeout_seconds must be positive")
        self._token = selected_token
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirectHandler()
        ).open
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProviderClosedError("Transport Provider is closed")

    def get_info(self) -> ProviderInfo:
        self._ensure_open()
        return ProviderInfo("airbridge", "0.1.0", CONTRACT_VERSION)

    def get_capabilities(self) -> list[Capability]:
        self._ensure_open()
        return [
            Capability("shell.execute", True),
            Capability("request.read", True),
        ]

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Mapping[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> tuple[int, dict[str, Any]]:
        self._ensure_open()
        body = None if payload is None else json.dumps(
            dict(payload), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self._token,
        }
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(
            self.base_url + path, data=body, headers=headers, method=method
        )
        try:
            response = self._opener(
                request, timeout=self.timeout_seconds if timeout is None else timeout
            )
            try:
                status, raw = int(response.getcode()), response.read(MAX_RESPONSE_BYTES + 1)
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read(MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise _UncertainTransportError from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AirBridgeProviderError("AirBridge response exceeded the size limit")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AirBridgeProviderError("AirBridge returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise AirBridgeProviderError("AirBridge response must be a JSON object")
        return status, result

    @staticmethod
    def _convert(payload: Mapping[str, Any], request_id: str) -> OperationResponse:
        request_id = normalize_request_id(request_id)
        response_id = payload.get("request_id")
        if response_id is not None and normalize_request_id(str(response_id)) != request_id:
            raise AirBridgeProviderError("AirBridge response request_id did not match")
        state = str(payload.get("state") or "").lower()
        if state in ("pending", "accepted"):
            return OperationResponse.accepted(request_id)
        if state in ("unknown", "not_found"):
            return OperationResponse.unknown(
                request_id, str(payload.get("error") or "AirBridge outcome is unknown")
            )
        if state not in ("completed", "failed"):
            raise AirBridgeProviderError("AirBridge returned an unsupported operation state")
        result = payload.get("result")
        result = result if isinstance(result, dict) else {}
        exit_code = result.get("exit_code")
        success = (
            exit_code == 0 and not result.get("killed", False)
            if exit_code is not None
            else bool(result.get("success", state == "completed"))
        )
        error = None if success else str(
            result.get("stderr") or payload.get("error") or "remote Shell command failed"
        )
        return OperationResponse.completed(request_id, success, result, error)

    def _call(self, method: str, path: str, request_id: str,
              payload: Optional[Mapping[str, Any]] = None,
              timeout: Optional[float] = None) -> OperationResponse:
        request_id = normalize_request_id(request_id)
        try:
            status, response = self._request(method, path, payload, timeout)
        except _UncertainTransportError:
            return OperationResponse.unknown(
                request_id, "AirBridge HTTP transport failed; remote outcome may be unknown"
            )
        if status == 503 and str(response.get("state", "")).lower() == "unknown":
            return OperationResponse.unknown(
                request_id, str(response.get("error") or "AirBridge reported UNKNOWN")
            )
        if status not in (200, 202):
            detail = response.get("error") or response.get("message") or "request failed"
            raise AirBridgeProviderError("AirBridge HTTP {}: {}".format(status, detail))
        return self._convert(response, request_id)

    def shell_execute(
        self, command: str, options: Optional[Mapping[str, Any]], request_id: str
    ) -> OperationResponse:
        self._ensure_open()
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        request_id = normalize_request_id(request_id)
        if options is not None and not isinstance(options, Mapping):
            raise TypeError("options must be a mapping or None")
        opts = dict(options or {})
        unknown = set(opts) - {"shell", "cwd", "timeout", "http_timeout_seconds"}
        if unknown:
            raise ValueError("unsupported Shell option: {}".format(sorted(unknown)[0]))
        shell = opts.get("shell", "powershell")
        task_timeout = opts.get("timeout", 60)
        if shell not in ("powershell", "cmd"):
            raise ValueError("shell must be 'powershell' or 'cmd'")
        if isinstance(task_timeout, bool) or not isinstance(task_timeout, int) or not 1 <= task_timeout <= 300:
            raise ValueError("timeout must be an integer between 1 and 300")
        cwd = opts.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise TypeError("cwd must be a string")
        body = {
            "command": command,
            "shell": shell,
            "timeout": task_timeout,
            "async": False,
            "request_id": request_id,
        }
        if cwd:
            body["cwd"] = cwd
        http_timeout = opts.get(
            "http_timeout_seconds", max(self.timeout_seconds, task_timeout + 15)
        )
        return self._call(
            "POST", "/api/v1/shell/exec", request_id, body, http_timeout
        )

    def get_operation(self, request_id: str) -> OperationResponse:
        """Read the original request record; this method never resubmits work."""
        request_id = normalize_request_id(request_id)
        return self._call(
            "GET", "/api/v1/requests/" + request_id, request_id
        )

    def close(self) -> None:
        self._closed = True
