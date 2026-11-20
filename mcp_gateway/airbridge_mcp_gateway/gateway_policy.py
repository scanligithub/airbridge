"""Fail-closed, per-call M2.5 STDIO Gateway policy.

The policy is stored per Windows user under %LOCALAPPDATA%/AirBridge/gateway.json.
The MCP process re-reads this file for every Tool invocation so turning Gateway
or an Agent OFF takes effect without relying on the MCP Host to reload settings.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


POLICY_SCHEMA_VERSION = 1
MAX_POLICY_BYTES = 64 * 1024
_AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    code: str
    message: str

    def as_tool_result(self) -> dict:
        return {
            "success": False,
            "state": "disabled" if self.code in {"GATEWAY_DISABLED", "AGENT_DISABLED"} else "unavailable",
            "error": {"code": self.code, "message": self.message},
        }


class GatewayPolicyStore:
    """Read/write the current user's versioned Gateway enablement policy."""

    def __init__(self, path: str | os.PathLike[str] | None):
        self.path = Path(path) if path is not None else None

    @classmethod
    def for_current_user(cls, environ: Mapping[str, str] | None = None) -> "GatewayPolicyStore":
        env = os.environ if environ is None else environ
        local_app_data = env.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            return cls(None)
        return cls(Path(local_app_data) / "AirBridge" / "gateway.json")

    def check(self, agent_id: str) -> PolicyDecision:
        """Re-read from disk on every call; absent/invalid policy denies access."""
        if self.path is None:
            return PolicyDecision(
                False,
                "GATEWAY_POLICY_UNAVAILABLE",
                "The current Windows user's LOCALAPPDATA path is unavailable; Gateway is disabled.",
            )
        if not isinstance(agent_id, str) or not _AGENT_ID_RE.fullmatch(agent_id.strip().lower()):
            return PolicyDecision(
                False,
                "AGENT_ID_REQUIRED",
                "A valid AIRBRIDGE_AGENT_ID is required for the M2.5 STDIO Gateway.",
            )

        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return PolicyDecision(
                False,
                "GATEWAY_DISABLED",
                "Gateway policy is not initialized. New installations default to OFF.",
            )
        except OSError:
            return PolicyDecision(
                False,
                "GATEWAY_POLICY_UNAVAILABLE",
                "Gateway policy cannot be read; access is denied.",
            )

        if len(raw) > MAX_POLICY_BYTES:
            return PolicyDecision(
                False,
                "GATEWAY_POLICY_INVALID",
                "Gateway policy exceeds the supported size; access is denied.",
            )

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return PolicyDecision(
                False,
                "GATEWAY_POLICY_INVALID",
                "Gateway policy is malformed; access is denied until it is repaired.",
            )

        schema_version = payload.get("schema_version") if isinstance(payload, dict) else None
        if type(schema_version) is not int or schema_version != POLICY_SCHEMA_VERSION:
            return PolicyDecision(
                False,
                "GATEWAY_POLICY_INVALID",
                "Gateway policy schema is missing or unsupported; access is denied.",
            )
        gateway = payload.get("gateway")
        if not isinstance(gateway, dict) or not isinstance(gateway.get("enabled"), bool):
            return PolicyDecision(
                False,
                "GATEWAY_POLICY_INVALID",
                "Gateway policy gateway.enabled must be a Boolean; access is denied.",
            )
        agents = payload.get("agents")
        if not isinstance(agents, dict):
            return PolicyDecision(
                False,
                "GATEWAY_POLICY_INVALID",
                "Gateway policy agents must be an object; access is denied.",
            )

        if not gateway["enabled"]:
            return PolicyDecision(
                False,
                "GATEWAY_DISABLED",
                "AirBridge Gateway is OFF. Enable it in the AirBridge GUI before using MCP tools.",
            )

        normalized_agent_id = agent_id.strip().lower()
        agent = agents.get(normalized_agent_id)
        if not isinstance(agent, dict) or not isinstance(agent.get("enabled"), bool):
            return PolicyDecision(
                False,
                "GATEWAY_POLICY_INVALID",
                f"Gateway policy for Agent '{normalized_agent_id}' is missing or malformed; access is denied.",
            )
        if not agent["enabled"]:
            return PolicyDecision(
                False,
                "AGENT_DISABLED",
                f"Agent '{normalized_agent_id}' is OFF in AirBridge Gateway settings.",
            )

        return PolicyDecision(True, "ALLOW", "Gateway and Agent are enabled.")

    def write(self, *, enabled: bool, agents: Mapping[str, bool]) -> None:
        """Atomically persist a non-secret policy snapshot for the GUI/settings layer."""
        if self.path is None:
            raise OSError("The current Windows user's LOCALAPPDATA path is unavailable")
        if not isinstance(enabled, bool):
            raise ValueError("Gateway enabled must be a Boolean")
        normalized_agents: dict[str, dict[str, bool]] = {}
        for raw_agent_id, agent_enabled in agents.items():
            if not isinstance(raw_agent_id, str):
                raise ValueError("Agent IDs must be strings")
            agent_id = raw_agent_id.strip().lower()
            if not _AGENT_ID_RE.fullmatch(agent_id):
                raise ValueError(f"Invalid Agent ID: {raw_agent_id!r}")
            if not isinstance(agent_enabled, bool):
                raise ValueError(f"Agent '{agent_id}' enabled must be a Boolean")
            normalized_agents[agent_id] = {"enabled": agent_enabled}

        payload = {
            "schema_version": POLICY_SCHEMA_VERSION,
            "gateway": {"enabled": enabled},
            "agents": normalized_agents,
        }
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_POLICY_BYTES:
            raise ValueError("Gateway policy exceeds the supported size")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".gateway-",
                suffix=".tmp",
                dir=self.path.parent,
                delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
