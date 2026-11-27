"""SQLite audit for policy denials enforced inside the local MCP Gateway.

Only Gateway-local DENY decisions are recorded here. Allowed calls continue to
Host A, where AgentSecurityStore remains responsible for server-side auditing.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path


class GatewayPolicyAuditStore:
    """Persist minimal, non-sensitive Gateway-local denial events."""

    RETENTION_SECONDS = 90 * 24 * 60 * 60
    MAX_RECORDS = 200_000

    def __init__(self, path: str | Path | None, *, timeout_seconds: float = 5.0):
        self.path = Path(path) if path is not None else None
        self.timeout_seconds = timeout_seconds

    @classmethod
    def for_policy_store(cls, policy_store):
        policy_path = getattr(policy_store, "path", None)
        if policy_path is None:
            return cls(None)
        return cls(Path(policy_path).parent / "gateway_audit.sqlite3")

    def record_denial(self, *, agent_id: str, tool_name: str, reason_code: str) -> None:
        """Write a DENY event. Callers must treat every write error as non-fatal."""
        if self.path is None:
            raise OSError("Gateway audit path is unavailable")
        if not isinstance(agent_id, str) or not agent_id or len(agent_id) > 64:
            raise ValueError("agent_id is invalid")
        if not isinstance(tool_name, str) or not tool_name or len(tool_name) > 128:
            raise ValueError("tool_name is invalid")
        if not isinstance(reason_code, str) or not reason_code or len(reason_code) > 64:
            raise ValueError("reason_code is invalid")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        with closing(sqlite3.connect(
            self.path,
            timeout=self.timeout_seconds,
        )) as connection:
          with connection:
            connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gateway_policy_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    agent_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK (decision = 'DENY'),
                    reason_code TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO gateway_policy_audit
                    (timestamp, agent_id, tool_name, decision, reason_code)
                VALUES (?, ?, ?, 'DENY', ?)
                """,
                (now, agent_id, tool_name, reason_code),
            )
            connection.execute(
                "DELETE FROM gateway_policy_audit WHERE timestamp < ?",
                (now - self.RETENTION_SECONDS,),
            )
            connection.execute(
                """
                DELETE FROM gateway_policy_audit
                WHERE audit_id NOT IN (
                    SELECT audit_id FROM gateway_policy_audit
                    ORDER BY audit_id DESC LIMIT ?
                )
                """,
                (self.MAX_RECORDS,),
            )

    def list_events(self, *, limit: int = 100) -> list[dict]:
        """Read recent events for tests and local diagnostics."""
        if self.path is None:
            raise OSError("Gateway audit path is unavailable")
        limit = max(1, min(int(limit), 1000))
        with closing(sqlite3.connect(self.path, timeout=self.timeout_seconds)) as connection:
          connection.row_factory = sqlite3.Row
          rows = connection.execute(
                """
                SELECT timestamp, agent_id, tool_name, decision, reason_code
                FROM gateway_policy_audit
                ORDER BY audit_id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
