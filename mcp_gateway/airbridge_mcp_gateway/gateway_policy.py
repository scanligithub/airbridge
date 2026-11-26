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


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous JSON objects instead of silently accepting the last key."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key!r}")
        result[key] = value
    return result


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
        """Re-read and validate the complete policy on every call; invalid state denies access."""
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
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (UnicodeDecodeError, ValueError):
            return PolicyDecision(
                False,
                "GATEWAY_POLICY_INVALID",
                "Gateway policy is malformed or contains duplicate keys; access is denied until it is repaired.",
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

        normalized_agents: dict[str, bool] = {}
        for raw_agent_id, agent in agents.items():
            if not isinstance(raw_agent_id, str):
                return PolicyDecision(
                    False,
                    "GATEWAY_POLICY_INVALID",
                    "Gateway policy Agent IDs must be strings; access is denied.",
                )
            normalized_id = raw_agent_id.strip().lower()
            if not _AGENT_ID_RE.fullmatch(normalized_id) or normalized_id in normalized_agents:
                return PolicyDecision(
                    False,
                    "GATEWAY_POLICY_INVALID",
                    "Gateway policy contains an invalid or duplicate Agent ID; access is denied.",
                )
            if not isinstance(agent, dict) or not isinstance(agent.get("enabled"), bool):
                return PolicyDecision(
                    False,
                    "GATEWAY_POLICY_INVALID",
                    f"Gateway policy Agent '{normalized_id}' enabled must be a Boolean; access is denied.",
                )
            normalized_agents[normalized_id] = agent["enabled"]

        normalized_agent_id = agent_id.strip().lower()
        if normalized_agent_id not in normalized_agents:
            return PolicyDecision(
                False,
                "GATEWAY_POLICY_INVALID",
                f"Gateway policy for Agent '{normalized_agent_id}' is missing; access is denied.",
            )
        if not normalized_agents[normalized_agent_id]:
            return PolicyDecision(
                False,
                "AGENT_DISABLED",
                f"Agent '{normalized_agent_id}' is OFF in AirBridge Gateway settings.",
            )

        return PolicyDecision(True, "ALLOW", "Gateway and Agent are enabled.")

    def read_snapshot(self) -> tuple[bool, dict[str, bool]]:
        """Return the GUI snapshot; a missing file is the first-install OFF state."""
        if self.path is None:
            raise OSError("The current Windows user's LOCALAPPDATA path is unavailable")
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return False, {}
        if len(raw) > MAX_POLICY_BYTES:
            raise ValueError("Gateway policy exceeds the supported size")
        try:
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("Gateway policy is malformed or contains duplicate keys") from exc

        schema_version = payload.get("schema_version") if isinstance(payload, dict) else None
        if type(schema_version) is not int or schema_version != POLICY_SCHEMA_VERSION:
            raise ValueError("Gateway policy schema is missing or unsupported")
        gateway = payload.get("gateway")
        if not isinstance(gateway, dict) or not isinstance(gateway.get("enabled"), bool):
            raise ValueError("Gateway policy gateway.enabled must be a Boolean")
        raw_agents = payload.get("agents")
        if not isinstance(raw_agents, dict):
            raise ValueError("Gateway policy agents must be an object")

        agents: dict[str, bool] = {}
        for raw_agent_id, agent in raw_agents.items():
            if not isinstance(raw_agent_id, str):
                raise ValueError("Gateway policy Agent IDs must be strings")
            agent_id = raw_agent_id.strip().lower()
            if not _AGENT_ID_RE.fullmatch(agent_id) or agent_id in agents:
                raise ValueError("Gateway policy contains an invalid or duplicate Agent ID")
            if not isinstance(agent, dict) or not isinstance(agent.get("enabled"), bool):
                raise ValueError(f"Gateway policy Agent '{agent_id}' enabled must be a Boolean")
            agents[agent_id] = agent["enabled"]

        return gateway["enabled"], agents

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
            if agent_id in normalized_agents:
                raise ValueError(f"Duplicate Agent ID after normalization: {agent_id!r}")
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
