"""No-execution Host-A probe for synthetic cross-Agent request ownership.

The probe starts an isolated loopback HTTP server with a temporary SQLite
credential database and an in-memory RequestTracker. It sends GET requests
only. It never contacts the production AirBridge listener, opens a serial
port, submits EDA/Shell work, or emits ABFP frames.
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_security import AgentSecurityStore  # noqa: E402
from airbridge_app import AntigravityIPCServer  # noqa: E402
from request_tracker import RequestTracker  # noqa: E402


class SyntheticWorker:
    """In-memory request tracker; any attempted frame send is a hard failure."""

    def __init__(self) -> None:
        self.request_tracker = RequestTracker()
        self.frame_send_attempts = 0

    def send_frame(self, *_args: Any, **_kwargs: Any) -> None:
        self.frame_send_attempts += 1
        raise AssertionError("Synthetic ownership probe must never send an ABFP frame")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _get_json(
    base_url: str, path: str, token: str
) -> tuple[int, dict[str, Any], dict[str, str]]:
    request = urllib.request.Request(
        base_url + path,
        headers={"Accept": "application/json", "Authorization": "Bearer " + token},
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=5.0) as response:
            status = response.status
            raw = response.read()
            headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
        headers = dict(exc.headers.items())
    payload = json.loads(raw.decode("utf-8")) if raw else {}
    if not isinstance(payload, dict):
        raise RuntimeError("AirBridge returned a non-object JSON response")
    return status, payload, headers


def _require(condition: bool, label: str, details: str = "") -> None:
    if not condition:
        raise AssertionError(label + (": " + details if details else ""))
    print("[PASS] " + label + (": " + details if details else ""))


def run() -> int:
    with tempfile.TemporaryDirectory(prefix="airbridge-hosta-ownership-") as temp_dir:
        store = AgentSecurityStore(str(Path(temp_dir) / "synthetic-agent-security.sqlite3"))
        server: AntigravityIPCServer | None = None
        worker = SyntheticWorker()
        try:
            owner = store.issue_token("fixture-owner", ["request.read"], label="synthetic-owner")
            other = store.issue_token("fixture-other", ["request.read"], label="synthetic-non-owner")
            no_request_scope = store.issue_token(
                "fixture-no-request-read", ["runtime.read"], label="synthetic-scope-denial"
            )

            synthetic_request_id = str(uuid.uuid4())
            unknown_request_id = str(uuid.uuid4())
            rate_limited_request_id = str(uuid.uuid4())

            # Create ownership and a completed in-memory record without any POST.
            store.bind_request_owner(synthetic_request_id, "fixture-owner")
            worker.request_tracker.register(synthetic_request_id, "synthetic.fixture")
            _require(
                worker.request_tracker.complete(
                    synthetic_request_id,
                    {
                        "request_id": synthetic_request_id,
                        "fixture": "synthetic-no-execution",
                        "success": True,
                    },
                ),
                "synthetic request record created in memory",
            )

            server = AntigravityIPCServer(
                worker,
                port=0,
                security_store=store,
                allow_eda_execute=False,
                status_requests_per_minute=2,
            )
            server.start()
            base_url = "http://127.0.0.1:" + str(server.port)

            owner_status, owner_body, _ = _get_json(
                base_url, "/api/v1/requests/" + synthetic_request_id, owner["token"]
            )
            _require(
                owner_status == 200
                and owner_body.get("state") == "completed"
                and owner_body.get("request_id") == synthetic_request_id,
                "request owner can read the synthetic request",
                "HTTP " + str(owner_status) + ", state=" + repr(owner_body.get("state")),
            )

            foreign_status, foreign_body, _ = _get_json(
                base_url, "/api/v1/requests/" + synthetic_request_id, other["token"]
            )
            unknown_status, unknown_body, _ = _get_json(
                base_url, "/api/v1/requests/" + unknown_request_id, other["token"]
            )
            _require(
                foreign_status == 404 and foreign_body.get("state") == "not_found",
                "another Agent cannot read the synthetic request",
                "HTTP " + str(foreign_status) + ", state=" + repr(foreign_body.get("state")),
            )
            _require(
                unknown_status == 404
                and unknown_body.get("state") == "not_found"
                and foreign_body.get("error") == unknown_body.get("error"),
                "foreign and random-unknown UUIDs have the same public error",
                "both return HTTP 404 / not_found with identical error text",
            )

            forbidden_status, forbidden_body, _ = _get_json(
                base_url,
                "/api/v1/requests/" + synthetic_request_id,
                no_request_scope["token"],
            )
            forbidden_error = forbidden_body.get("error")
            _require(
                forbidden_status == 403
                and forbidden_body.get("state") == "forbidden"
                and isinstance(forbidden_error, dict)
                and forbidden_error.get("code") == "AGENT_SCOPE_DENIED",
                "missing request.read scope is denied",
                "HTTP " + str(forbidden_status),
            )

            limited_status, limited_body, limited_headers = _get_json(
                base_url,
                "/api/v1/requests/" + rate_limited_request_id,
                other["token"],
            )
            retry_after = limited_headers.get("Retry-After")
            _require(
                limited_status == 429
                and limited_body.get("state") == "rate_limited"
                and isinstance(limited_body.get("error"), dict)
                and limited_body["error"].get("code") == "AGENT_RATE_LIMITED"
                and retry_after is not None
                and retry_after.isdigit()
                and int(retry_after) >= 1,
                "status-query quota returns HTTP 429 and Retry-After",
                "HTTP " + str(limited_status) + ", Retry-After=" + repr(retry_after),
            )

            _require(
                worker.frame_send_attempts == 0,
                "no ABFP frame was sent",
                "frame send attempts=" + str(worker.frame_send_attempts),
            )
            _require(
                len(worker.request_tracker._records) == 1,
                "only the pre-seeded synthetic request exists",
                "tracked requests=" + str(len(worker.request_tracker._records)),
            )
            print("\nHost-A synthetic ownership probe: PASS.")
            print("Only loopback GET requests were sent to a temporary in-process server.")
            print("No production database, serial link, EDA/Shell POST, or ABFP frame was used.")
            print("The temporary credential database is discarded on exit.")
            return 0
        finally:
            if server is not None:
                server.stop()
            store.close()


def main() -> int:
    try:
        return run()
    except Exception as exc:
        print("Host-A synthetic ownership probe: FAIL: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
