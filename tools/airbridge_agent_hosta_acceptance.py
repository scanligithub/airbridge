"""Safe, read-only Host-A smoke test for M2.3 Agent credentials.

This probe sends GET requests only. It never submits EDA or Shell work and
never prints or persists the supplied Bearer tokens.
"""
from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_security import (  # noqa: E402
    AgentAuthenticationError,
    AgentSecurityStore,
)


READ_ONLY_SCOPES = {
    "runtime.read",
    "eda.versions.read",
    "request.read",
}


class _NoRedirectHandler(HTTPRedirectHandler):
    """Never forward an Authorization header to a redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, msg, headers, fp)


def validate_loopback_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Base URL must be a plain HTTP loopback origin.")
    hostname = parsed.hostname.lower()
    try:
        is_loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname == "localhost"
    if not is_loopback:
        raise ValueError("This acceptance probe is restricted to loopback.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Base URL has an invalid port.") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Base URL port must be in 1..65535.")
    return value.strip().rstrip("/")


class ReadOnlyApiProbe:
    def __init__(self, base_url: str):
        self.base_url = validate_loopback_base_url(base_url)
        self._opener = build_opener(_NoRedirectHandler())

    def get(self, path: str, token: str | None = None) -> tuple[int, dict[str, Any]]:
        if not path.startswith("/api/v1/") or ".." in path:
            raise ValueError("Only fixed /api/v1 paths are supported.")
        headers = {"Accept": "application/json"}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        request = Request(
            self.base_url + path,
            headers=headers,
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=5.0) as response:
                status = response.status
                raw = response.read()
        except HTTPError as exc:
            status = exc.code
            raw = exc.read()
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("AirBridge HTTP request failed: " + str(exc)) from exc
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return status, payload


def check(
    label: str,
    actual_status: int,
    payload: dict[str, Any],
    expected_status: int,
    *,
    expected_code: str | None = None,
    expected_state: str | None = None,
) -> bool:
    okay = actual_status == expected_status
    if expected_code is not None:
        error = payload.get("error")
        actual_code = error.get("code") if isinstance(error, dict) else None
        okay = okay and actual_code == expected_code
    if expected_state is not None:
        okay = okay and payload.get("state") == expected_state
    status = "PASS" if okay else "FAIL"
    details = f"HTTP {actual_status}, expected {expected_status}"
    if expected_code is not None:
        error = payload.get("error")
        actual_code = error.get("code") if isinstance(error, dict) else None
        details += f"; code={actual_code!r}, expected {expected_code!r}"
    if expected_state is not None:
        details += f"; state={payload.get('state')!r}, expected {expected_state!r}"
    print(f"[{status}] {label}: {details}")
    return okay


def authenticate_local_token(
    store: AgentSecurityStore,
    agent_id: str,
    token: str,
) -> bool:
    try:
        principal = store.authenticate("Bearer " + token)
    except AgentAuthenticationError:
        print(f"[FAIL] {agent_id}: token is not active in the local credential database.")
        return False
    if principal.agent_id != agent_id:
        print(f"[FAIL] {agent_id}: token belongs to a different Agent ID.")
        return False
    if set(principal.scopes) != READ_ONLY_SCOPES:
        print(
            f"[FAIL] {agent_id}: scopes must be exactly "
            "runtime.read, eda.versions.read, request.read."
        )
        return False
    print(f"[PASS] {agent_id}: local token is active and has only the three read-only scopes.")
    return True


def run(args: argparse.Namespace) -> int:
    probe = ReadOnlyApiProbe(args.base_url)
    store = AgentSecurityStore.from_env()
    failures = 0
    try:
        status, payload = probe.get("/api/v1/runtime/capabilities")
        failures += not check(
            "missing Authorization is rejected",
            status,
            payload,
            401,
            expected_code="INVALID_AGENT_TOKEN",
        )

        fake_token = "abg1." + ("0" * 32) + ".not-a-real-secret"
        status, payload = probe.get("/api/v1/runtime/capabilities", fake_token)
        failures += not check(
            "invalid Authorization is rejected",
            status,
            payload,
            401,
            expected_code="INVALID_AGENT_TOKEN",
        )

        for agent_id in ("antigravity", "opencode"):
            token = getpass.getpass(f"Enter {agent_id} read-only token (input hidden): ")
            if not authenticate_local_token(store, agent_id, token):
                failures += 1
                continue

            status, payload = probe.get("/api/v1/runtime/capabilities", token)
            failures += not check(
                f"{agent_id} can read Runtime capabilities",
                status,
                payload,
                200,
            )

            unknown_id = str(uuid.uuid4())
            status, payload = probe.get(
                f"/api/v1/requests/{unknown_id}",
                token,
            )
            failures += not check(
                f"{agent_id} has request.read scope (random, unowned UUID)",
                status,
                payload,
                404,
                expected_state="not_found",
            )

            if args.check_eda_versions:
                status, payload = probe.get("/api/v1/eda/versions", token)
                failures += not check(
                    f"{agent_id} can read EDA versions (requires Host-B link)",
                    status,
                    payload,
                    200,
                )

        if args.check_revoked:
            revoked_token = getpass.getpass(
                "Enter a token that has already been revoked (input hidden): "
            )
            try:
                store.authenticate("Bearer " + revoked_token)
            except AgentAuthenticationError:
                status, payload = probe.get(
                    "/api/v1/runtime/capabilities",
                    revoked_token,
                )
                failures += not check(
                    "revoked token is rejected by the running HTTP service",
                    status,
                    payload,
                    401,
                    expected_code="INVALID_AGENT_TOKEN",
                )
            else:
                print("[FAIL] revocation probe: the local credential database still accepts this token.")
                failures += 1
    finally:
        store.close()

    if failures:
        print(f"\nM2.3 read-only Host-A probe: FAILED ({failures} check(s)).")
        return 1
    print("\nM2.3 read-only Host-A probe: PASS.")
    print("Only loopback GET requests were sent; no EDA/Shell POST or ABFP frame was submitted.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only M2.3 Host-A Agent-authentication smoke test."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8888",
        help="Loopback-only AirBridge HTTP origin (default: http://127.0.0.1:8888).",
    )
    parser.add_argument(
        "--check-eda-versions",
        action="store_true",
        help="Also test EDA versions; requires a functioning Host-A/Host-B link.",
    )
    parser.add_argument(
        "--check-revoked",
        action="store_true",
        help="Prompt for one already-revoked token and verify HTTP 401.",
    )
    args = parser.parse_args()
    try:
        return run(args)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"M2.3 read-only Host-A probe could not run: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
