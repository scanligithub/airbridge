"""Small standard-library HTTP client for the existing AirBridge A-side API."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import GatewayConfig


class AirBridgeTransportError(RuntimeError):
    """The local HTTP endpoint could not return a trustworthy API response."""


class AirBridgeHttpClient:
    """Calls only the public HTTP endpoints implemented by AirBridge on Host A."""

    def __init__(self, config: GatewayConfig):
        self._config = config

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/api/v1/") or ".." in path:
            raise ValueError("Only fixed /api/v1 AirBridge endpoints are supported")
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(
            self._config.base_url + path,
            data=body,
            headers=headers,
            method=method.upper(),
        )
        timeout = (
            self._config.http_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return self._decode_response(response.status, response.read())
        except HTTPError as exc:
            return self._decode_response(exc.code, exc.read())
        except (URLError, TimeoutError, OSError) as exc:
            raise AirBridgeTransportError(str(exc)) from exc

    @staticmethod
    def _decode_response(status: int, raw: bytes) -> dict[str, Any]:
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AirBridgeTransportError(
                f"AirBridge returned non-JSON response (HTTP {status})"
            ) from exc
        if not isinstance(data, dict):
            raise AirBridgeTransportError(
                f"AirBridge returned a non-object JSON response (HTTP {status})"
            )
        result = dict(data)
        result["http_status"] = status
        return result

    def get_capabilities(self) -> dict[str, Any]:
        return self.request_json("GET", "/api/v1/runtime/capabilities")

    def get_eda_versions(self) -> dict[str, Any]:
        return self.request_json("GET", "/api/v1/eda/versions")

    def execute_eda(
        self,
        payload: dict[str, Any],
        *,
        http_timeout_seconds: float,
    ) -> dict[str, Any]:
        return self.request_json(
            "POST",
            "/api/v1/eda/execute",
            payload=payload,
            timeout_seconds=http_timeout_seconds,
        )

    def get_request_status(self, request_id: str) -> dict[str, Any]:
        encoded_id = quote(request_id, safe="")
        return self.request_json("GET", f"/api/v1/requests/{encoded_id}")
