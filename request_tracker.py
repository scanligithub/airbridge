# request_tracker.py
# -*- coding: utf-8 -*-
"""Bounded, thread-safe correlation of HTTP requests and ABFP responses.

This layer correlates one A-side HTTP request with one legacy ABFP response.
It does not implement Runtime Session queues, idempotent execution, or
exactly-once delivery; those belong to later Broker/Request phases.
"""
import copy
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class RequestTrackerError(ValueError):
    """Invalid, duplicate, or capacity-limited request registration."""


class RequestTracker:
    DEFAULT_TTL_SECONDS = 900
    DEFAULT_MAX_RECORDS = 500

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_records: int = DEFAULT_MAX_RECORDS,
    ):
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_records = max(1, int(max_records))
        self._lock = threading.RLock()
        self._records: Dict[str, Dict[str, Any]] = {}
        self._next_sequence = 0

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )

    @staticmethod
    def validate_request_id(request_id: Any) -> str:
        if not isinstance(request_id, str):
            raise RequestTrackerError("request_id must be a UUID string")
        try:
            parsed = uuid.UUID(request_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise RequestTrackerError("request_id must be a valid UUID") from exc
        return str(parsed)

    def _prune_locked(self, now: float, reserve_slot: bool = False) -> None:
        stale_pending = [
            record
            for record in self._records.values()
            if record["state"] == "pending"
            and now - record["_created_monotonic"] >= self.ttl_seconds
        ]
        for record in stale_pending:
            record["state"] = "unknown"
            record["error"] = (
                "request result retention deadline elapsed; remote execution status is unknown"
            )
            record["completed_at"] = self._utc_now()
            record["_created_monotonic"] = now
            record["event"].set()

        expired = [
            request_id
            for request_id, record in self._records.items()
            if record["state"] != "pending"
            and now - record["_created_monotonic"] >= self.ttl_seconds
        ]
        for request_id in expired:
            self._records.pop(request_id, None)

        overflow = len(self._records) - self.max_records + (1 if reserve_slot else 0)
        if overflow > 0:
            completed = sorted(
                (
                    (record["_created_monotonic"], record["_sequence"], request_id)
                    for request_id, record in self._records.items()
                    if record["state"] != "pending"
                )
            )
            for _, _, request_id in completed[:overflow]:
                self._records.pop(request_id, None)

    def register(self, request_id: str, kind: str) -> Dict[str, Any]:
        normalized = self.validate_request_id(request_id)
        if not isinstance(kind, str) or not kind.strip():
            raise RequestTrackerError("request kind must be a non-empty string")

        now = time.monotonic()
        with self._lock:
            self._prune_locked(now, reserve_slot=True)
            if normalized in self._records:
                raise RequestTrackerError(f"request_id already registered: {normalized}")
            if len(self._records) >= self.max_records:
                raise RequestTrackerError(
                    "request tracker is full; all retained records are still pending"
                )
            self._next_sequence += 1
            self._records[normalized] = {
                "request_id": normalized,
                "_sequence": self._next_sequence,
                "kind": kind.strip(),
                "state": "pending",
                "created_at": self._utc_now(),
                "_created_monotonic": now,
                "event": threading.Event(),
                "result": None,
                "error": None,
            }
            return self._snapshot_locked(self._records[normalized])

    def complete(
        self,
        request_id: str,
        result: Dict[str, Any],
        state: str = "completed",
    ) -> bool:
        if state not in ("completed", "accepted", "failed", "unknown"):
            raise ValueError(
                "state must be 'completed', 'accepted', 'failed', or 'unknown'"
            )
        try:
            normalized = self.validate_request_id(request_id)
        except RequestTrackerError:
            return False
        if not isinstance(result, dict):
            result = {"value": result}

        with self._lock:
            record = self._records.get(normalized)
            if record is None or record["state"] != "pending":
                return False
            record["state"] = state
            record["result"] = copy.deepcopy(result)
            record["completed_at"] = self._utc_now()
            record["_created_monotonic"] = time.monotonic()
            record["event"].set()
            return True

    def fail(self, request_id: str, error: str, state: str = "failed") -> bool:
        if state not in ("failed", "unknown"):
            raise ValueError("state must be 'failed' or 'unknown'")
        try:
            normalized = self.validate_request_id(request_id)
        except RequestTrackerError:
            return False

        with self._lock:
            record = self._records.get(normalized)
            if record is None or record["state"] != "pending":
                return False
            record["state"] = state
            record["error"] = str(error)
            record["completed_at"] = self._utc_now()
            record["_created_monotonic"] = time.monotonic()
            record["event"].set()
            return True

    def fail_all_pending(self, error: str) -> int:
        count = 0
        with self._lock:
            for record in self._records.values():
                if record["state"] == "pending":
                    record["state"] = "unknown"
                    record["error"] = str(error)
                    record["completed_at"] = self._utc_now()
                    record["_created_monotonic"] = time.monotonic()
                    record["event"].set()
                    count += 1
        return count

    def wait(self, request_id: str, timeout: Optional[float]) -> Dict[str, Any]:
        normalized = self.validate_request_id(request_id)
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            record = self._records.get(normalized)
            if record is None:
                return {
                    "request_id": normalized,
                    "state": "not_found",
                    "error": "request_id is unknown or expired",
                }
            event = record["event"]
            ttl_deadline = record["_created_monotonic"] + self.ttl_seconds
            ttl_remaining = max(0.0, ttl_deadline - now)
            requested_wait = (
                ttl_remaining
                if timeout is None
                else max(0.0, float(timeout))
            )
            wait_for_ttl_deadline = requested_wait >= ttl_remaining
            wait_seconds = min(requested_wait, ttl_remaining)

        event.wait(timeout=wait_seconds)
        if wait_for_ttl_deadline and not event.is_set():
            # Event.wait may return a fraction early due to OS timer
            # granularity. If this waiter consumed the entire retention
            # interval, prune at the exact monotonic deadline so callers
            # never observe a stale pending state at the expiry boundary.
            with self._lock:
                self._prune_locked(max(time.monotonic(), ttl_deadline))
                record = self._records.get(normalized)
                if record is None:
                    return {
                        "request_id": normalized,
                        "state": "not_found",
                        "error": "request_id is unknown or expired",
                    }
                return self._snapshot_locked(record)
        return self.get(normalized)

    def get(self, request_id: str) -> Dict[str, Any]:
        normalized = self.validate_request_id(request_id)
        with self._lock:
            self._prune_locked(time.monotonic())
            record = self._records.get(normalized)
            if record is None:
                return {
                    "request_id": normalized,
                    "state": "not_found",
                    "error": "request_id is unknown or expired",
                }
            return self._snapshot_locked(record)

    @staticmethod
    def _snapshot_locked(record: Dict[str, Any]) -> Dict[str, Any]:
        snapshot = {
            "request_id": record["request_id"],
            "kind": record["kind"],
            "state": record["state"],
            "created_at": record["created_at"],
        }
        if record.get("completed_at"):
            snapshot["completed_at"] = record["completed_at"]
        if record.get("result") is not None:
            snapshot["result"] = copy.deepcopy(record["result"])
        if record.get("error"):
            snapshot["error"] = record["error"]
        return snapshot


def encode_bounded_json_payload(data: Dict[str, Any], max_bytes: int = 65000) -> bytes:
    """Encode JSON while safely truncating large textual result fields.

    Large output remains available through streamed log frames where supported;
    the final correlated response stays within one ABFP frame.
    """
    if not isinstance(data, dict):
        raise RequestTrackerError("result payload must be a JSON object")
    result = copy.deepcopy(data)

    def _encode() -> bytes:
        import json
        return json.dumps(
            result, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")

    encoded = _encode()
    for field in ("output", "stdout", "stderr", "error"):
        if len(encoded) <= max_bytes:
            break
        value = result.get(field)
        if not isinstance(value, str) or not value:
            continue
        current = value.encode("utf-8")
        excess = len(encoded) - max_bytes
        target = max(0, len(current) - excess - 128)
        result[field] = current[:target].decode("utf-8", errors="ignore")
        result[field + "_truncated"] = True
        encoded = _encode()

    if len(encoded) > max_bytes:
        raise RequestTrackerError(
            f"result metadata exceeds ABFP payload limit ({len(encoded)} > {max_bytes})"
        )
    return encoded
