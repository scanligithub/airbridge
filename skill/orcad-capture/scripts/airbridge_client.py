# -*- coding: utf-8 -*-
"""Small, fail-closed HTTP client for the Capture Skill.

This module intentionally depends only on the Python standard library.  It
communicates with the Host-A AirBridge HTTP API; it never opens a serial port
or imports AirBridge Core implementation modules.
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping, Optional


DEFAULT_API_BASE_URL = "http://127.0.0.1:8888"
TOKEN_ENV = "AIRBRIDGE_AGENT_TOKEN"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ALLOWED_FILESYSTEM_ACTIONS = frozenset(
    {"drives", "ls", "read", "write", "mkdir", "rm", "stat", "pull", "mv"}
)


class AirBridgeClientError(RuntimeError):
    """Base class for errors returned by the Capture Skill HTTP client."""


class AirBridgeConfigurationError(AirBridgeClientError):
    """The local client configuration is missing or unsafe."""


class AirBridgeTransportError(AirBridgeClientError):
    """The HTTP exchange ended without a trustworthy response.

    For a submitted POST, the remote operation may already have happened.
    Callers must query the same request/session identity before deciding
    whether another submission is safe.  This client never retries.
    """

    outcome_unknown = True


class AirBridgeProtocolError(AirBridgeClientError):
    """The server response was not a bounded JSON object."""


class AirBridgeHTTPError(AirBridgeClientError):
    """AirBridge returned a non-success HTTP status."""

    def __init__(self, status_code: int, payload: Any):
        self.status_code = int(status_code)
        self.payload = payload
        if isinstance(payload, dict):
            error = payload.get("error") or payload.get("message") or payload
        else:
            error = payload
        # Never include request headers or the Bearer token in an exception.
        super().__init__("AirBridge HTTP {}: {}".format(status_code, error))


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not forward an Agent Bearer token through an HTTP redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _validate_local_base_url(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port  # Force invalid-port validation.
    except ValueError as exc:
        raise AirBridgeConfigurationError("invalid AirBridge API base URL") from exc

    if (
        parsed.scheme not in ("http", "https")
        or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or not parsed.netloc
    ):
        raise AirBridgeConfigurationError(
            "AirBridge API URL must be a loopback HTTP(S) origin; "
            "the Agent token must not be sent to another host"
        )
    return raw.rstrip("/")


def _decode_json_object(raw: bytes, status_code: int) -> dict[str, Any]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AirBridgeProtocolError("AirBridge response exceeded the size limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AirBridgeProtocolError(
            "AirBridge returned a non-JSON or invalid UTF-8 response"
        ) from exc
    if not isinstance(value, dict):
        raise AirBridgeProtocolError("AirBridge JSON response must be an object")
    if not 200 <= int(status_code) < 300:
        raise AirBridgeHTTPError(status_code, value)
    return value


class AirBridgeClient:
    """Authenticated client for the generic Host-A AirBridge HTTP API."""

    def __init__(
        self,
        base_url: str = DEFAULT_API_BASE_URL,
        token: Optional[str] = None,
        timeout_seconds: float = 20.0,
        opener: Optional[Callable[..., Any]] = None,
        environ: Optional[Mapping[str, str]] = None,
    ):
        self.base_url = _validate_local_base_url(base_url).rstrip("/")
        source = os.environ if environ is None else environ
        selected_token = token if token is not None else source.get(TOKEN_ENV, "")
        selected_token = str(selected_token or "").strip()
        if not selected_token:
            raise AirBridgeConfigurationError(
                "{} is required; provision a scoped token for this Agent".format(
                    TOKEN_ENV
                )
            )
        if any(char in selected_token for char in "\r\n"):
            raise AirBridgeConfigurationError("Agent token contains invalid characters")
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise AirBridgeConfigurationError(
                "timeout_seconds must be a positive number"
            ) from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise AirBridgeConfigurationError("timeout_seconds must be a finite positive number")

        self._token = selected_token
        self.timeout_seconds = timeout
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        ).open

    @classmethod
    def from_env(cls, **kwargs: Any) -> "AirBridgeClient":
        """Construct the client using AIRBRIDGE_AGENT_TOKEN without logging it."""
        return cls(**kwargs)

    def request_json(
        self,
        method: str,
        path: str,
        payload: Optional[Mapping[str, Any]] = None,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> dict[str, Any]:
        verb = str(method or "").strip().upper()
        if verb not in ("GET", "POST"):
            raise ValueError("only GET and POST are supported")
        endpoint = str(path or "")
        if (
            not re.fullmatch(r"/api/v1/[A-Za-z0-9_/-]+", endpoint)
            or "//" in endpoint
        ):
            raise ValueError("path must be an AirBridge /api/v1/ endpoint")
        if payload is not None and not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping or None")
        if verb == "GET" and payload is not None:
            raise ValueError("GET requests cannot include a JSON body")

        timeout = self.timeout_seconds if timeout_seconds is None else float(
            timeout_seconds
        )
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")

        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self._token,
        }
        if payload is not None:
            try:
                body = json.dumps(
                    dict(payload), ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ValueError("payload must be JSON serializable") from exc
            headers["Content-Type"] = "application/json; charset=utf-8"

        request = urllib.request.Request(
            self.base_url + endpoint,
            data=body,
            headers=headers,
            method=verb,
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
            except Exception:
                raw = b""
            try:
                payload_value = _decode_json_object(raw, 200)
            except AirBridgeClientError:
                payload_value = {"error": "HTTP error response was not valid JSON"}
            raise AirBridgeHTTPError(exc.code, payload_value) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Do not retry a POST: the remote side may have accepted it.
            raise AirBridgeTransportError(
                "AirBridge transport failed; remote outcome may be unknown. "
                "Inspect the same request/session identity before retrying."
            ) from exc

        return _decode_json_object(raw, status_code)

    def get(self, path: str, *, timeout_seconds: Optional[float] = None):
        return self.request_json("GET", path, timeout_seconds=timeout_seconds)

    def post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: Optional[float] = None
    ):
        return self.request_json(
            "POST", path, payload, timeout_seconds=timeout_seconds
        )

    def shell_execute(
        self,
        command: str,
        *,
        shell: str = "powershell",
        cwd: Optional[str] = None,
        task_timeout_seconds: int = 60,
        async_mode: bool = False,
        http_timeout_seconds: Optional[float] = None,
    ) -> dict[str, Any]:
        command_text = str(command or "")
        if not command_text.strip():
            raise ValueError("command must not be empty")
        if shell not in ("powershell", "cmd"):
            raise ValueError("shell must be 'powershell' or 'cmd'")
        if (
            isinstance(task_timeout_seconds, bool)
            or not isinstance(task_timeout_seconds, int)
            or not 1 <= task_timeout_seconds <= 300
        ):
            raise ValueError("task_timeout_seconds must be between 1 and 300")

        body: dict[str, Any] = {
            "command": command_text,
            "shell": shell,
            "timeout": task_timeout_seconds,
            "async": bool(async_mode),
        }
        if cwd:
            body["cwd"] = str(cwd)

        request_timeout = http_timeout_seconds
        if request_timeout is None:
            request_timeout = (
                self.timeout_seconds
                if async_mode
                else max(self.timeout_seconds, task_timeout_seconds + 15)
            )
        return self.post(
            "/api/v1/shell/exec",
            body,
            timeout_seconds=request_timeout,
        )

    def filesystem(
        self,
        action: str,
        payload: Optional[Mapping[str, Any]] = None,
        *,
        timeout_seconds: Optional[float] = None
    ) -> dict[str, Any]:
        selected = str(action or "").strip().lower()
        if selected not in ALLOWED_FILESYSTEM_ACTIONS:
            raise ValueError("unsupported AirBridge filesystem action")
        return self.post(
            "/api/v1/fs/" + selected,
            payload or {},
            timeout_seconds=timeout_seconds,
        )
