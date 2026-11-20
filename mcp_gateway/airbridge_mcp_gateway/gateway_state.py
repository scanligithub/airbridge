"""Fail-closed, per-call Gateway enablement state for local MCP STDIO workers.

The GUI owns this small, non-secret JSON state file. STDIO workers reread it
before every Tool handler so an already-running MCP child stops accepting new
requests as soon as the global Gateway or its Agent instance is disabled.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


class GatewayStateError(RuntimeError):
    """The local Gateway / Agent state does not permit Tool execution."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class GatewayStateStore:
    """Read the current user's Gateway state; missing or invalid state is OFF."""

    MAX_STATE_BYTES = 64 * 1024
    SCHEMA_VERSION = 1
    DEFAULT_FILENAME = "gateway.json"

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "GatewayStateStore":
        env = os.environ if environ is None else environ
        override = env.get("AIRBRIDGE_GATEWAY_STATE_PATH", "").strip()
        if override:
            return cls(override)

        local_app_data = env.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            raise GatewayStateError(
                "GATEWAY_STATE_UNAVAILABLE",
                "LOCALAPPDATA is unavailable; Gateway state cannot be verified.",
            )
        return cls(Path(local_app_data) / "AirBridge" / cls.DEFAULT_FILENAME)

    def assert_enabled(self, agent_id: str) -> None:
        normalized_agent_id = (agent_id or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", normalized_agent_id):
            raise GatewayStateError(
                "AGENT_ID_REQUIRED",
                "A valid AIRBRIDGE_AGENT_ID is required for the M2.5 STDIO Gateway.",
            )

        try:
            stat = self.path.stat()
            if stat.st_size <= 0 or stat.st_size > self.MAX_STATE_BYTES:
                raise GatewayStateError(
                    "GATEWAY_STATE_INVALID",
                    "Gateway state file is empty or exceeds the allowed size.",
                )
            raw = self.path.read_text(encoding="utf-8")
            state: Any = json.loads(raw)
        except FileNotFoundError as exc:
            raise GatewayStateError(
                "GATEWAY_CONFIG_MISSING",
                "Gateway state is missing; the default state is OFF.",
            ) from exc
        except GatewayStateError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise GatewayStateError(
                "GATEWAY_STATE_INVALID",
                "Gateway state cannot be read or parsed; access is disabled.",
            ) from exc

        if not isinstance(state, dict):
            self._invalid()
        version = state.get("schema_version")
        if isinstance(version, bool) or not isinstance(version, int) or version != self.SCHEMA_VERSION:
            raise GatewayStateError(
                "GATEWAY_STATE_UNSUPPORTED",
                "Gateway state schema is missing or unsupported; access is disabled.",
            )

        gateway = state.get("gateway")
        if not isinstance(gateway, dict) or not isinstance(gateway.get("enabled"), bool):
            self._invalid()
        if not gateway["enabled"]:
            raise GatewayStateError(
                "GATEWAY_DISABLED",
                "The local AirBridge Gateway is OFF.",
            )

        agents = state.get("agents")
        if not isinstance(agents, dict):
            self._invalid()
        agent_state = agents.get(normalized_agent_id)
        if not isinstance(agent_state, dict) or not isinstance(agent_state.get("enabled"), bool):
            raise GatewayStateError(
                "AGENT_DISABLED",
                f"Agent '{normalized_agent_id}' is not enabled in the local Gateway state.",
            )
        if not agent_state["enabled"]:
            raise GatewayStateError(
                "AGENT_DISABLED",
                f"Agent '{normalized_agent_id}' is disabled.",
            )

    @staticmethod
    def _invalid() -> None:
        raise GatewayStateError(
            "GATEWAY_STATE_INVALID",
            "Gateway state has an invalid structure; access is disabled.",
        )
