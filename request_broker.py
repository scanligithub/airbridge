# request_broker.py
# -*- coding: utf-8 -*-
"""Durable B-side request admission and deduplication.

The broker is intentionally runtime-agnostic. It hashes opaque request
payloads, atomically claims request IDs, executes a supplied callback once,
and caches bounded results. It does not create Sessions or interpret Tcl,
SKILL, Capture, Allegro, or Shell commands.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from request_tracker import RequestTrackerError, encode_bounded_json_payload


class RequestBrokerError(ValueError):
    """Raised when a broker request or persisted result is invalid."""


class RequestBroker:
    DEFAULT_TTL_SECONDS = 24 * 60 * 60
    DEFAULT_MAX_RECORDS = 10000
    MAX_RESULT_BYTES = 64000

    def __init__(
        self,
        db_path: Optional[str] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_records: int = DEFAULT_MAX_RECORDS,
    ):
        self.db_path = str(db_path or self.default_db_path())
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_records = max(1, int(max_records))
        self._lock = threading.RLock()
        self._closed = False

        parent = Path(self.db_path).expanduser().resolve().parent
        parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.db_path,
            timeout=15.0,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 15000")
        if self.db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = FULL")
        self._initialize_schema()
        self._recover_interrupted_requests()

    @staticmethod
    def default_db_path() -> str:
        configured = os.environ.get("AIRBRIDGE_RUNTIME_DB", "").strip()
        if configured:
            return configured
        if os.name == "nt":
            root = os.environ.get("LOCALAPPDATA") or str(Path.home())
            return str(Path(root) / "AirBridge" / "runtime_broker.sqlite3")
        root = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        return str(Path(root) / "airbridge" / "runtime_broker.sqlite3")

    @staticmethod
    def validate_request_id(request_id: Any) -> str:
        if not isinstance(request_id, str):
            raise RequestBrokerError("request_id must be a UUID string")
        try:
            return str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise RequestBrokerError("request_id must be a valid UUID") from exc

    @staticmethod
    def payload_digest(payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            raise RequestBrokerError("request payload must be a JSON object")
        try:
            canonical = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise RequestBrokerError(f"request payload is not canonical JSON: {exc}") from exc
        return hashlib.sha256(canonical).hexdigest()

    @classmethod
    def _bounded_result(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(result, dict):
            raise RequestBrokerError("executor must return a JSON object")
        try:
            encoded = encode_bounded_json_payload(result, cls.MAX_RESULT_BYTES)
            bounded = json.loads(encoded.decode("utf-8"))
        except (RequestTrackerError, json.JSONDecodeError) as exc:
            raise RequestBrokerError(f"executor result cannot be stored safely: {exc}") from exc
        return bounded

    def _initialize_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS broker_requests (
                    request_id TEXT PRIMARY KEY,
                    request_kind TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    result_json TEXT,
                    error TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_broker_requests_updated "
                "ON broker_requests(state, updated_at)"
            )

    def _recover_interrupted_requests(self) -> int:
        """Mark persisted RUNNING work as UNKNOWN on broker process startup.

        The request may or may not have reached the external runtime before a
        crash. UNKNOWN is deliberately non-retryable with the same ID: the
        broker must not risk executing a side-effecting request a second time.
        """
        now = time.time()
        result = {
            "request_state": "unknown",
            "error": {
                "code": "BROKER_RESTART_RECOVERY",
                "message": (
                    "Broker restarted while this request was running; "
                    "remote execution outcome cannot be confirmed"
                ),
            },
        }
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """
                    UPDATE broker_requests
                    SET state = 'unknown', result_json = ?, error = ?, updated_at = ?
                    WHERE state = 'running'
                    """,
                    (encoded, result["error"]["message"], now),
                )
                count = cursor.rowcount
                self._conn.execute("COMMIT")
                return count
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def _prune_locked(self, now: float, reserve_slot: bool = False) -> None:
        cutoff = now - self.ttl_seconds
        self._conn.execute(
            """
            DELETE FROM broker_requests
            WHERE state IN ('completed', 'accepted', 'failed', 'unknown')
              AND updated_at <= ?
            """,
            (cutoff,),
        )

        count = int(
            self._conn.execute("SELECT COUNT(*) FROM broker_requests").fetchone()[0]
        )
        overflow = count - self.max_records + (1 if reserve_slot else 0)
        if overflow > 0:
            self._conn.execute(
                """
                DELETE FROM broker_requests
                WHERE request_id IN (
                    SELECT request_id
                    FROM broker_requests
                    WHERE state IN ('completed', 'accepted', 'failed', 'unknown')
                    ORDER BY updated_at ASC, request_id ASC
                    LIMIT ?
                )
                """,
                (overflow,),
            )

    def execute(
        self,
        request_id: str,
        request_kind: str,
        payload: Dict[str, Any],
        executor: Callable[[], Dict[str, Any]],
    ) -> Dict[str, Any]:
        normalized_id = self.validate_request_id(request_id)
        if not isinstance(request_kind, str) or not request_kind.strip():
            raise RequestBrokerError("request_kind must be a non-empty string")
        if not callable(executor):
            raise RequestBrokerError("executor must be callable")

        digest = self.payload_digest(payload)
        now = time.time()

        with self._lock:
            if self._closed:
                raise RequestBrokerError("request broker is closed")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._prune_locked(now, reserve_slot=False)
                row = self._conn.execute(
                    "SELECT * FROM broker_requests WHERE request_id = ?",
                    (normalized_id,),
                ).fetchone()

                if row is not None:
                    self._conn.execute("COMMIT")
                    if (
                        row["request_kind"] != request_kind.strip()
                        or row["payload_sha256"] != digest
                    ):
                        return {
                            "disposition": "conflict",
                            "state": row["state"],
                            "error": {
                                "code": "REQUEST_ID_CONFLICT",
                                "message": (
                                    "request_id was previously used with a different payload"
                                ),
                            },
                        }
                    if row["state"] == "running":
                        return {
                            "disposition": "in_progress",
                            "state": "running",
                            "result": None,
                        }
                    cached_result = (
                        json.loads(row["result_json"])
                        if row["result_json"]
                        else None
                    )
                    return {
                        "disposition": "cached",
                        "state": row["state"],
                        "result": cached_result,
                    }

                # A new admission may recycle the oldest terminal result, but
                # active RUNNING requests are never evicted.
                self._prune_locked(now, reserve_slot=True)
                count = int(
                    self._conn.execute(
                        "SELECT COUNT(*) FROM broker_requests"
                    ).fetchone()[0]
                )
                if count >= self.max_records:
                    self._conn.execute("COMMIT")
                    return {
                        "disposition": "capacity_rejected",
                        "state": "rejected",
                        "error": {
                            "code": "BROKER_CAPACITY",
                            "message": (
                                "request broker is full; active requests were not evicted"
                            ),
                        },
                    }

                self._conn.execute(
                    """
                    INSERT INTO broker_requests
                    (request_id, request_kind, payload_sha256, state,
                     created_at, updated_at, result_json, error)
                    VALUES (?, ?, ?, 'running', ?, ?, NULL, NULL)
                    """,
                    (
                        normalized_id,
                        request_kind.strip(),
                        digest,
                        now,
                        now,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        try:
            raw_result = executor()
            bounded_result = self._bounded_result(raw_result)
            terminal_state = (
                "accepted"
                if bounded_result.get("status") == "running"
                and bounded_result.get("async") is True
                else "completed"
            )
            error_text = None
        except Exception as exc:
            bounded_result = {
                "success": False,
                "error": {
                    "code": "BROKER_EXECUTOR_ERROR",
                    "message": str(exc),
                },
            }
            terminal_state = "failed"
            error_text = str(exc)

        result_json = json.dumps(
            bounded_result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        completed_at = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    UPDATE broker_requests
                    SET state = ?, result_json = ?, error = ?, updated_at = ?
                    WHERE request_id = ? AND state = 'running'
                    """,
                    (
                        terminal_state,
                        result_json,
                        error_text,
                        completed_at,
                        normalized_id,
                    ),
                )
                row = self._conn.execute(
                    "SELECT state, result_json FROM broker_requests WHERE request_id = ?",
                    (normalized_id,),
                ).fetchone()
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        if row is None:
            return {
                "disposition": "unknown",
                "state": "unknown",
                "result": {
                    "request_state": "unknown",
                    "error": {
                        "code": "BROKER_RECORD_LOST",
                        "message": "request record disappeared before result persistence",
                    },
                },
            }
        persisted_result = json.loads(row["result_json"]) if row["result_json"] else bounded_result
        return {
            "disposition": "executed",
            "state": row["state"],
            "result": persisted_result,
        }

    def get(self, request_id: str) -> Dict[str, Any]:
        normalized_id = self.validate_request_id(request_id)
        with self._lock:
            if self._closed:
                raise RequestBrokerError("request broker is closed")
            self._prune_locked(time.time())
            row = self._conn.execute(
                "SELECT * FROM broker_requests WHERE request_id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                return {"request_id": normalized_id, "state": "not_found"}
            return {
                "request_id": normalized_id,
                "kind": row["request_kind"],
                "state": row["state"],
                "payload_sha256": row["payload_sha256"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "result": json.loads(row["result_json"]) if row["result_json"] else None,
                "error": row["error"],
            }

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True
