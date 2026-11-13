"""Local Agent authentication, scopes, request ownership, and audit storage.

This module is intentionally standard-library-only so the AirBridge desktop
application and its local administration tool can use it without MCP deps.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


VALID_SCOPES = {
    "runtime.read",
    "runtime.manage",
    "runtime.message",
    "eda.versions.read",
    "eda.execute",
    "request.read",
    "request.read:any",
    "shell.read",
    "shell.execute",
    "shell.manage",
    "filesystem.read",
    "filesystem.write",
    "api.admin",
}


class AgentSecurityError(RuntimeError):
    """Base class for local Agent security failures."""


class AgentAuthenticationError(AgentSecurityError):
    """Missing, malformed, expired, revoked, or invalid Bearer credential."""


class AgentAuthorizationError(AgentSecurityError):
    """Authenticated Agent does not own or have permission for a resource."""


@dataclass(frozen=True)
class AgentPrincipal:
    agent_id: str
    token_id: str
    scopes: tuple[str, ...]

    def allows(self, scope: str) -> bool:
        return "*" in self.scopes or "api.admin" in self.scopes or scope in self.scopes


class AgentSecurityStore:
    """SQLite-backed credential registry, request ownership, and audit log."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or self.default_db_path()
        self._lock = threading.RLock()
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().resolve().parent.mkdir(
                parents=True, exist_ok=True
            )
        self._conn = sqlite3.connect(
            self.db_path, timeout=15.0, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 15000")
        if self.db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = FULL")
        self._initialize_schema()

    @staticmethod
    def default_db_path() -> str:
        configured = os.environ.get("AIRBRIDGE_AGENT_SECURITY_DB", "").strip()
        if configured:
            return configured
        if os.name == "nt":
            root = os.environ.get("LOCALAPPDATA") or str(Path.home())
            return str(Path(root) / "AirBridge" / "agent_security.sqlite3")
        root = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        return str(Path(root) / "airbridge" / "agent_security.sqlite3")

    @classmethod
    def from_env(cls) -> "AgentSecurityStore":
        return cls()

    def _initialize_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_credentials (
                    token_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    secret_sha256 TEXT NOT NULL UNIQUE,
                    scopes_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    revoked_at TEXT,
                    label TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_agent_credentials_agent
                    ON agent_credentials(agent_id, enabled);
                CREATE TABLE IF NOT EXISTS agent_request_owners (
                    request_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_request_owners_agent
                    ON agent_request_owners(agent_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    agent_id TEXT,
                    token_id TEXT,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    reason_code TEXT,
                    request_id TEXT,
                    duration_ms INTEGER,
                    payload_bytes INTEGER,
                    payload_sha256 TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_audit_time
                    ON agent_audit(timestamp);
                CREATE INDEX IF NOT EXISTS idx_agent_audit_agent
                    ON agent_audit(agent_id, timestamp);
                """
            )

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )

    @staticmethod
    def _validate_agent_id(agent_id: str) -> str:
        normalized = (agent_id or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", normalized):
            raise AgentSecurityError(
                "agent_id must be 2..64 lowercase letters, digits, dot, underscore, or hyphen"
            )
        return normalized

    @staticmethod
    def _validate_scopes(scopes: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(scopes)))
        if not normalized:
            raise AgentSecurityError("at least one scope is required")
        unknown = sorted(set(normalized) - VALID_SCOPES)
        if unknown:
            raise AgentSecurityError("unknown scope(s): " + ", ".join(unknown))
        return normalized

    def issue_token(
        self,
        agent_id: str,
        scopes: list[str] | tuple[str, ...],
        *,
        label: str = "",
        expires_in_days: Optional[int] = 90,
    ) -> dict[str, Any]:
        normalized_agent = self._validate_agent_id(agent_id)
        normalized_scopes = self._validate_scopes(scopes)
        if expires_in_days is not None and (
            isinstance(expires_in_days, bool)
            or not isinstance(expires_in_days, int)
            or not 1 <= expires_in_days <= 3650
        ):
            raise AgentSecurityError("expires_in_days must be between 1 and 3650, or None")
        token_id = uuid.uuid4().hex
        secret = secrets.token_urlsafe(32)
        token = f"abg1.{token_id}.{secret}"
        digest = hashlib.sha256(secret.encode("ascii")).hexdigest()
        created = self._utc_now()
        expires = (
            self._timestamp(created + timedelta(days=int(expires_in_days)))
            if expires_in_days is not None
            else None
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agent_credentials
                    (token_id, agent_id, secret_sha256, scopes_json, enabled,
                     created_at, expires_at, label)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    token_id,
                    normalized_agent,
                    digest,
                    json.dumps(normalized_scopes),
                    self._timestamp(created),
                    expires,
                    str(label or "")[:128],
                ),
            )
        # The bearer secret is returned once. It is never stored in SQLite.
        return {
            "token_id": token_id,
            "agent_id": normalized_agent,
            "token": token,
            "scopes": list(normalized_scopes),
            "created_at": self._timestamp(created),
            "expires_at": expires,
        }

    def authenticate(self, authorization_header: Optional[str]) -> AgentPrincipal:
        if not isinstance(authorization_header, str):
            raise AgentAuthenticationError("Bearer token required")
        parts = authorization_header.strip().split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise AgentAuthenticationError("Bearer token required")
        token_parts = parts[1].split(".")
        if len(token_parts) != 3 or token_parts[0] != "abg1":
            raise AgentAuthenticationError("invalid Agent credential")
        token_id, secret = token_parts[1], token_parts[2]
        if not re.fullmatch(r"[0-9a-f]{32}", token_id) or not secret:
            raise AgentAuthenticationError("invalid Agent credential")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM agent_credentials WHERE token_id = ?", (token_id,)
            ).fetchone()
        if row is None or not row["enabled"]:
            raise AgentAuthenticationError("invalid Agent credential")
        if row["expires_at"] and self._timestamp(self._utc_now()) >= row["expires_at"]:
            raise AgentAuthenticationError("Agent credential expired")
        try:
            supplied_digest = hashlib.sha256(secret.encode("ascii")).hexdigest()
        except UnicodeEncodeError as exc:
            raise AgentAuthenticationError("invalid Agent credential") from exc
        if not hmac.compare_digest(supplied_digest, row["secret_sha256"]):
            raise AgentAuthenticationError("invalid Agent credential")
        try:
            scopes = tuple(json.loads(row["scopes_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise AgentSecurityError("stored Agent scopes are invalid") from exc
        return AgentPrincipal(
            agent_id=row["agent_id"], token_id=row["token_id"], scopes=scopes
        )

    def revoke_token(self, token_id: str) -> bool:
        now = self._timestamp(self._utc_now())
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE agent_credentials
                SET enabled = 0, revoked_at = ?
                WHERE token_id = ? AND enabled = 1
                """,
                (now, token_id),
            )
            return cursor.rowcount == 1

    def revoke_agent(self, agent_id: str) -> int:
        normalized = self._validate_agent_id(agent_id)
        now = self._timestamp(self._utc_now())
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE agent_credentials
                SET enabled = 0, revoked_at = ?
                WHERE agent_id = ? AND enabled = 1
                """,
                (now, normalized),
            )
            return cursor.rowcount

    def list_tokens(self, agent_id: Optional[str] = None) -> list[dict[str, Any]]:
        query = (
            "SELECT token_id, agent_id, scopes_json, enabled, created_at, "
            "expires_at, revoked_at, label FROM agent_credentials"
        )
        params: tuple[Any, ...] = ()
        if agent_id:
            query += " WHERE agent_id = ?"
            params = (self._validate_agent_id(agent_id),)
        query += " ORDER BY agent_id, created_at"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "token_id": row["token_id"],
                "agent_id": row["agent_id"],
                "scopes": json.loads(row["scopes_json"]),
                "enabled": bool(row["enabled"]),
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "revoked_at": row["revoked_at"],
                "label": row["label"],
            }
            for row in rows
        ]

    def bind_request_owner(self, request_id: str, agent_id: str) -> None:
        try:
            normalized_request_id = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise AgentSecurityError("request_id must be a UUID") from exc
        normalized_agent = self._validate_agent_id(agent_id)
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO agent_request_owners (request_id, agent_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        normalized_request_id,
                        normalized_agent,
                        self._timestamp(self._utc_now()),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise AgentSecurityError("request_id already has an owner") from exc

    def can_read_request(self, request_id: str, principal: AgentPrincipal) -> bool:
        if principal.allows("request.read:any"):
            return True
        try:
            normalized = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT agent_id FROM agent_request_owners WHERE request_id = ?",
                (normalized,),
            ).fetchone()
        return row is not None and row["agent_id"] == principal.agent_id

    def audit_request(
        self,
        *,
        agent_id: Optional[str],
        token_id: Optional[str],
        method: str,
        path: str,
        decision: str,
        http_status: int,
        reason_code: Optional[str] = None,
        request_id: Optional[str] = None,
        duration_ms: Optional[int] = None,
        payload_bytes: Optional[int] = None,
        payload_sha256: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agent_audit
                    (timestamp, agent_id, token_id, method, path, decision,
                     http_status, reason_code, request_id, duration_ms,
                     payload_bytes, payload_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._timestamp(self._utc_now()),
                    agent_id,
                    token_id,
                    method.upper(),
                    path[:512],
                    decision,
                    int(http_status),
                    reason_code,
                    request_id,
                    duration_ms,
                    payload_bytes,
                    payload_sha256,
                ),
            )

    def list_audit(self, *, agent_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        query = "SELECT * FROM agent_audit"
        params: tuple[Any, ...] = ()
        if agent_id:
            query += " WHERE agent_id = ?"
            params = (self._validate_agent_id(agent_id),)
        query += " ORDER BY audit_id DESC LIMIT ?"
        params += (limit,)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
