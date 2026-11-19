"""Configuration for the standalone AirBridge MCP Gateway."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .credential_store import CredentialStore, CredentialStoreError, default_credential_store


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


def _validate_agent_id(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", normalized):
        raise GatewayConfigurationError("AIRBRIDGE_AGENT_ID must be a valid Agent ID")
    return normalized


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    """Runtime configuration; independent from AirBridge's Python environment."""

    base_url: str = "http://127.0.0.1:8888"
    http_timeout_seconds: float = 20.0
    enable_eda_execute: bool = False
    max_request_body_bytes: int = 60_000
    agent_id: str = ""
    agent_token: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _validate_loopback_http_url(self.base_url))
        has_agent_id = bool(self.agent_id)
        has_token = bool(self.agent_token.strip()) if isinstance(self.agent_token, str) else False
        if has_agent_id == has_token:
            raise GatewayConfigurationError(
                "Configure exactly one credential source: AIRBRIDGE_AGENT_ID or legacy AIRBRIDGE_AGENT_TOKEN"
            )
        if has_agent_id:
            object.__setattr__(self, "agent_id", _validate_agent_id(self.agent_id))
        elif not isinstance(self.agent_token, str) or not self.agent_token.strip():
            raise GatewayConfigurationError("AIRBRIDGE_AGENT_TOKEN is required")
        if not 1.0 <= self.http_timeout_seconds <= 60.0:
            raise GatewayConfigurationError(
                "AIRBRIDGE_HTTP_TIMEOUT_SECONDS must be between 1 and 60"
            )
        if not 1024 <= self.max_request_body_bytes <= 64_000:
            raise GatewayConfigurationError("max_request_body_bytes must be in 1024..64000")

    @classmethod
    def from_env(
        cls,
        environ: dict[str, str] | None = None,
        *,
        credential_store: CredentialStore | None = None,
    ) -> "GatewayConfig":
        env = os.environ if environ is None else environ
        raw_timeout = env.get("AIRBRIDGE_HTTP_TIMEOUT_SECONDS", "20")
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise GatewayConfigurationError(
                "AIRBRIDGE_HTTP_TIMEOUT_SECONDS must be numeric"
            ) from exc
        agent_id = env.get("AIRBRIDGE_AGENT_ID", "").strip()
        agent_token = env.get("AIRBRIDGE_AGENT_TOKEN", "").strip()
        if agent_id and agent_token:
            raise GatewayConfigurationError(
                "Do not configure both AIRBRIDGE_AGENT_ID and legacy AIRBRIDGE_AGENT_TOKEN"
            )
        if agent_id:
            try:
                provider = credential_store or default_credential_store()
                agent_token = provider.load(_validate_agent_id(agent_id))
            except CredentialStoreError as exc:
                raise GatewayConfigurationError(
                    "Agent credential could not be loaded from the system credential store"
                ) from exc
        return cls(
            base_url=env.get("AIRBRIDGE_BASE_URL", "http://127.0.0.1:8888"),
            http_timeout_seconds=timeout,
            enable_eda_execute=_parse_bool(
                env.get("AIRBRIDGE_ENABLE_EDA_EXECUTE"), default=False
            ),
            agent_id=agent_id,
            agent_token=agent_token,
        )
