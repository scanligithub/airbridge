"""Configuration for the standalone AirBridge MCP Gateway."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from urllib.parse import urlsplit


class GatewayConfigurationError(ValueError):
    """Raised when gateway configuration is unsafe or invalid."""


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise GatewayConfigurationError(
        "AIRBRIDGE_ENABLE_EDA_EXECUTE must be one of: 1/0, true/false, yes/no, on/off"
    )


def _validate_loopback_http_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "http":
        raise GatewayConfigurationError(
            "AIRBRIDGE_BASE_URL must use plain HTTP to the local AirBridge listener"
        )
    if not parsed.hostname or parsed.username or parsed.password:
        raise GatewayConfigurationError("AIRBRIDGE_BASE_URL must be a valid loopback URL")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise GatewayConfigurationError(
            "AIRBRIDGE_BASE_URL must contain only scheme, loopback host and optional port"
        )
    hostname = parsed.hostname.lower()
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise GatewayConfigurationError("AIRBRIDGE_BASE_URL has an invalid port") from exc
    try:
        is_loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname == "localhost"
    if not is_loopback:
        raise GatewayConfigurationError(
            "M2 stdio gateway is restricted to loopback; remote URLs are not supported"
        )
    if parsed_port is not None and not 1 <= parsed_port <= 65535:
        raise GatewayConfigurationError("AIRBRIDGE_BASE_URL port must be in 1..65535")
    return value.strip().rstrip("/")


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    """Runtime configuration; independent from AirBridge's Python environment."""

    base_url: str = "http://127.0.0.1:8888"
    http_timeout_seconds: float = 20.0
    enable_eda_execute: bool = False
    max_request_body_bytes: int = 60_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _validate_loopback_http_url(self.base_url))
        if not 1.0 <= self.http_timeout_seconds <= 60.0:
            raise GatewayConfigurationError(
                "AIRBRIDGE_HTTP_TIMEOUT_SECONDS must be between 1 and 60"
            )
        if not 1024 <= self.max_request_body_bytes <= 64_000:
            raise GatewayConfigurationError("max_request_body_bytes must be in 1024..64000")

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "GatewayConfig":
        env = os.environ if environ is None else environ
        raw_timeout = env.get("AIRBRIDGE_HTTP_TIMEOUT_SECONDS", "20")
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise GatewayConfigurationError(
                "AIRBRIDGE_HTTP_TIMEOUT_SECONDS must be numeric"
            ) from exc
        return cls(
            base_url=env.get("AIRBRIDGE_BASE_URL", "http://127.0.0.1:8888"),
            http_timeout_seconds=timeout,
            enable_eda_execute=_parse_bool(
                env.get("AIRBRIDGE_ENABLE_EDA_EXECUTE"), default=False
            ),
        )
