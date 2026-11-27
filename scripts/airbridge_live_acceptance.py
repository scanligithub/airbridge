# -*- coding: utf-8 -*-
"""Opt-in Host-A -> Host-B AirBridge Transport live acceptance.

This script intentionally submits exactly one fixed, read-only PowerShell
command and then queries the same request UUID. It never retries execution.
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill"))

from transport import AirBridgeProvider, OperationStatus  # noqa: E402


LIVE_COMMAND = "Get-Location | Select-Object -ExpandProperty Path"


def _result(response):
    value = response.result
    return value if isinstance(value, dict) else {}


def run_live_acceptance() -> int:
    request_id = str(uuid.uuid4())
    provider = AirBridgeProvider(timeout_seconds=45.0)

    try:
        print("Host A -> Host B live Shell acceptance")
        print("request_id: {}".format(request_id))
        print("command: {}".format(LIVE_COMMAND))

        # This is the only command submission in this script.
        submitted = provider.shell_execute(
            LIVE_COMMAND,
            {
                "shell": "powershell",
                "timeout": 30,
                "http_timeout_seconds": 45,
            },
            request_id,
        )
        print("submit_status: {}".format(submitted.status.value))

        # Read only the original request record; never submit a replacement.
        observed = provider.get_operation(request_id)
        deadline = time.monotonic() + 20.0
        while observed.status == OperationStatus.ACCEPTED and time.monotonic() < deadline:
            time.sleep(1.0)
            observed = provider.get_operation(request_id)

        print("query_status: {}".format(observed.status.value))
        if observed.status != OperationStatus.COMPLETED:
            reason = observed.reason or submitted.reason or "operation did not reach a confirmed terminal state"
            print("FAIL: {}".format(reason))
            return 1

        result = _result(observed)
        exit_code = result.get("exit_code")
        remote_path = str(result.get("stdout") or "").strip()
        print("exit_code: {}".format(exit_code))
        print("Host B working directory: {}".format(remote_path or "(empty)"))

        if observed.request_id != request_id:
            print("FAIL: response request_id mismatch")
            return 1
        if observed.success is not True or exit_code != 0 or not remote_path:
            print("FAIL: Shell command did not return a successful working directory")
            return 1

        print("PASS: one remote command completed; original request was queried read-only")
        return 0
    finally:
        provider.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="explicitly permit the fixed read-only Host-B Shell command",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("live execution is disabled by default; pass --live explicitly")
    try:
        return run_live_acceptance()
    except Exception as exc:
        # Provider errors may contain useful HTTP status details, but never
        # print request headers, environment variables, or credential values.
        print("FAIL: {}: {}".format(type(exc).__name__, exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
