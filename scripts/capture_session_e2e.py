# scripts/capture_session_e2e.py
# -*- coding: utf-8 -*-
"""Host-A acceptance harness for a persistent Capture Tcl Session.

Run this from Host A while AirBridge HTTP/serial connectivity is active.
The target DSN path is interpreted on Host B; this script deliberately does
not test for that path on Host A. It does not save, close, or modify the design.
"""
import argparse
import json
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import PureWindowsPath
from typing import Any, Dict, Mapping, Optional, Tuple


REQUIRED_FEATURES = ("request.correlation", "request.deduplication")
REQUIRED_CAPTURE_COMMANDS = ("GetActivePMDesign", "GetActivePage", "OPage")


class E2EError(RuntimeError):
    """Raised when the live Capture Session acceptance contract fails."""


def request_json(
    method: str,
    url: str,
    payload: Optional[Mapping[str, Any]] = None,
    timeout: float = 10.0,
) -> Tuple[int, Dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"error": raw}
        return exc.code, data
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise E2EError(f"HTTP {method} {url} failed: {exc}") from exc


def wait_for_negotiated_peer(base_url: str, timeout_seconds: float = 20.0) -> Dict[str, Any]:
    capabilities_url = base_url.rstrip("/") + "/api/v1/runtime/capabilities"
    negotiate_url = base_url.rstrip("/") + "/api/v1/runtime/negotiate"

    status, peer = request_json("GET", capabilities_url, timeout=5)
    if (
        peer.get("state") != "negotiated"
        or not set(REQUIRED_FEATURES).issubset(set(peer.get("features", [])))
    ):
        request_json("POST", negotiate_url, {}, timeout=5)

    deadline = time.monotonic() + timeout_seconds
    latest = peer
    while time.monotonic() < deadline:
        status, latest = request_json("GET", capabilities_url, timeout=5)
        features = set(latest.get("features", []))
        if latest.get("state") == "negotiated" and set(REQUIRED_FEATURES).issubset(features):
            return latest
        time.sleep(0.25)

    raise E2EError(
        "Runtime Session peer did not negotiate the required capabilities: "
        + json.dumps(latest, ensure_ascii=False)
    )


def build_probe_script(token: str, initialize: bool) -> str:
    """Build a read-only Capture API / persistent-interpreter Tcl probe."""
    if initialize:
        prelude = f'''namespace eval ::AirBridgeE2E {{}}
set ::AirBridgeE2E::token "{token}"
proc ::AirBridgeE2E::probe {{}} {{
    if {{![info exists ::AirBridgeE2E::token]}} {{
        error "AirBridge E2E persistent variable is missing"
    }}
    return $::AirBridgeE2E::token
}}
'''
    else:
        prelude = '''if {![info exists ::AirBridgeE2E::token]} {
    error "AirBridge E2E persistent variable is missing"
}
if {![llength [info commands ::AirBridgeE2E::probe]]} {
    error "AirBridge E2E persistent proc is missing"
}
'''

    return prelude + '''set ::AirBridgeE2E::api_commands {}
foreach command {GetActivePMDesign GetActivePage OPage} {
    if {[llength [info commands $command]] > 0} {
        lappend ::AirBridgeE2E::api_commands $command
    }
}
set ::AirBridgeE2E::active_pm_rc -1
if {[llength [info commands GetActivePMDesign]] > 0} {
    set ::AirBridgeE2E::active_pm_rc [catch {GetActivePMDesign} ::AirBridgeE2E::active_pm]
}
set ::AirBridgeE2E::session_var [expr {[info exists ::DboSession_s_pD] ? 1 : 0}]
set ::AirBridgeE2E::report [format "pid=%s|tcl=%s|token=%s|proc=%s|apis=%s|session_var=%s|active_pm_rc=%s" \
    [pid] $::tcl_version $::AirBridgeE2E::token \
    [::AirBridgeE2E::probe] [join $::AirBridgeE2E::api_commands ","] \
    $::AirBridgeE2E::session_var $::AirBridgeE2E::active_pm_rc]
puts $::AirBridgeE2E::report
set ::AirBridgeE2E::report
'''


def parse_tcl_report(value: Any) -> Dict[str, str]:
    if not isinstance(value, str) or not value:
        raise E2EError("Capture Bridge returned an empty Tcl report")
    fields: Dict[str, str] = {}
    for item in value.strip().split("|"):
        key, sep, field_value = item.partition("=")
        if not sep or not key:
            raise E2EError(f"malformed Capture Tcl report field: {item!r}")
        fields[key] = field_value
    required = ("pid", "tcl", "token", "proc", "apis", "session_var", "active_pm_rc")
    missing = [field for field in required if field not in fields]
    if missing:
        raise E2EError(f"Capture Tcl report is missing fields: {missing}")
    return fields


def wait_for_request(
    base_url: str,
    request_id: str,
    initial_status: int,
    initial_body: Dict[str, Any],
    deadline: float,
) -> Dict[str, Any]:
    body = initial_body
    state = str(body.get("state", ""))
    if state in ("completed", "failed", "unknown"):
        return body
    if state in ("not_submitted", "not_found", "already_registered"):
        raise E2EError(
            f"EDA request was not accepted (HTTP {initial_status}): "
            + json.dumps(body, ensure_ascii=False)
        )

    result_url = base_url.rstrip("/") + "/api/v1/requests/" + request_id
    latest_status = initial_status
    while time.monotonic() < deadline:
        latest_status, body = request_json("GET", result_url, timeout=10)
        state = str(body.get("state", ""))
        if state in ("completed", "failed", "unknown"):
            return body
        if state in ("not_found", "not_submitted"):
            raise E2EError(
                "request disappeared while polling: "
                + json.dumps(body, ensure_ascii=False)
            )
        time.sleep(0.25)

    raise E2EError(
        f"request {request_id} did not finish before the E2E deadline; "
        f"last HTTP status={latest_status}, body="
        + json.dumps(body, ensure_ascii=False)
    )


def submit_tcl_request(
    base_url: str,
    target_dsn: str,
    version: str,
    script: str,
    timeout_seconds: int,
) -> Dict[str, Any]:
    request_id = str(uuid.uuid4())
    wait_timeout_ms = (timeout_seconds + 30) * 1000
    payload = {
        "request_id": request_id,
        "script": script,
        "hint": "capture_session_e2e.tcl",
        "version": version,
        "target_file": target_dsn,
        "timeout": timeout_seconds,
        "keep_open": True,
        "wait_timeout_ms": wait_timeout_ms,
    }
    url = base_url.rstrip("/") + "/api/v1/eda/execute"
    status, body = request_json(
        "POST",
        url,
        payload,
        timeout=timeout_seconds + 40,
    )
    if body.get("request_id") not in (None, request_id):
        raise E2EError("HTTP result request_id does not match submitted UUID")

    deadline = time.monotonic() + timeout_seconds + 35
    result = wait_for_request(base_url, request_id, status, body, deadline)
    if result.get("state") != "completed":
        raise E2EError(
            f"EDA request {request_id} ended in state {result.get('state')}: "
            + json.dumps(result, ensure_ascii=False)
        )
    actual = result.get("result") or {}
    if actual.get("success") is not True or actual.get("request_state") != "completed":
        raise E2EError(
            f"Capture Tcl request did not complete successfully: "
            + json.dumps(actual, ensure_ascii=False)
        )
    return actual


def run_acceptance(
    base_url: str,
    target_dsn: str,
    version: str = "17.2",
    timeout_seconds: int = 60,
) -> Dict[str, Any]:
    if PureWindowsPath(target_dsn).suffix.lower() != ".dsn":
        raise E2EError("target_dsn must be a .dsn path on Host B")
    if timeout_seconds < 1:
        raise E2EError("timeout_seconds must be positive")

    peer = wait_for_negotiated_peer(base_url)
    token = uuid.uuid4().hex

    first = submit_tcl_request(
        base_url,
        target_dsn,
        version,
        build_probe_script(token, initialize=True),
        timeout_seconds,
    )
    first_report = parse_tcl_report(first.get("output"))
    if first_report["token"] != token or first_report["proc"] != token:
        raise E2EError("first request did not establish the expected persistent Tcl state")

    first_pid = int(first_report["pid"])
    if first.get("capture_pid") != first_pid:
        raise E2EError(
            f"first request PID mismatch: result={first.get('capture_pid')}, "
            f"Tcl={first_pid}"
        )
    if first_report["active_pm_rc"] != "0":
        raise E2EError(
            "GetActivePMDesign command is missing or returned an error; "
            f"active_pm_rc={first_report['active_pm_rc']}"
        )

    second = submit_tcl_request(
        base_url,
        target_dsn,
        version,
        build_probe_script(token, initialize=False),
        timeout_seconds,
    )
    second_report = parse_tcl_report(second.get("output"))
    second_pid = int(second_report["pid"])
    if second_pid != first_pid or second.get("capture_pid") != first_pid:
        raise E2EError(
            f"Capture PID changed between requests: first={first_pid}, second={second_pid}"
        )
    if second_report["token"] != token or second_report["proc"] != token:
        raise E2EError(
            "global Tcl variable / procedure did not survive across requests"
        )
    if second_report["active_pm_rc"] != "0":
        raise E2EError(
            "GetActivePMDesign failed on the second request: "
            + second_report["active_pm_rc"]
        )

    required_commands = set(REQUIRED_CAPTURE_COMMANDS)
    available_commands = set(second_report["apis"].split(","))
    missing_commands = sorted(required_commands - available_commands)
    if missing_commands:
        raise E2EError(f"Capture Dbo command(s) unavailable: {missing_commands}")

    return {
        "peer": peer,
        "capture_pid": first_pid,
        "tcl_version": second_report["tcl"],
        "target_dsn": target_dsn,
        "persistent_variable": True,
        "persistent_proc": True,
        "same_capture_pid": True,
        "capture_api_commands": sorted(available_commands),
        "dbo_session_variable_present": second_report["session_var"] == "1",
        "get_active_pm_design_call_succeeded": True,
        "design_saved_or_closed": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify two consecutive Tcl requests execute in one persistent "
            "Capture Session. The target DSN path belongs to Host B."
        )
    )
    parser.add_argument("target_dsn", help="absolute .dsn path on Host B")
    parser.add_argument("--base-url", default="http://127.0.0.1:8888")
    parser.add_argument("--version", default="17.2")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args(argv)

    try:
        result = run_acceptance(
            base_url=args.base_url,
            target_dsn=args.target_dsn,
            version=args.version,
            timeout_seconds=args.timeout,
        )
    except E2EError as exc:
        print(f"Capture Session E2E FAIL: {exc}", file=sys.stderr)
        return 2

    print("Capture Session E2E PASS")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Capture remains open; this harness does not save or exit the design.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
