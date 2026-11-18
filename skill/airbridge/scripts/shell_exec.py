#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Submit and optionally wait for a remote Shell command through AirBridge."""
import argparse
import json
import os
import sys
import time

try:
    import requests
except ImportError:
    print("Install dependency first: pip install requests", file=sys.stderr)
    sys.exit(1)

API = "http://127.0.0.1:8888"
TOKEN_ENV = "AIRBRIDGE_AGENT_TOKEN"


def main():
    p = argparse.ArgumentParser(description="AirBridge remote Shell")
    p.add_argument("command", help="PowerShell or cmd command string")
    p.add_argument("--shell", default="powershell", choices=["powershell", "cmd"])
    p.add_argument("--cwd", default=r"E:\\_airbridge_temp")
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--async", dest="async_mode", action="store_true",
                   help="Return after the remote Shell task is accepted.")
    p.add_argument("--wait", action="store_true",
                   help="Run asynchronously, then poll for the final task result.")
    p.add_argument("--poll-interval", type=float, default=1.0)
    args = p.parse_args()

    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        p.error(f"{TOKEN_ENV} must contain a valid AirBridge Agent Bearer token.")

    if args.timeout < 0:
        p.error("--timeout must be >= 0")
    if args.poll_interval <= 0:
        p.error("--poll-interval must be > 0")

    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "command": args.command,
        "shell": args.shell,
        "cwd": args.cwd,
        "timeout": args.timeout,
        "async": bool(args.async_mode or args.wait),
    }

    response = requests.post(
        f"{API}/api/v1/shell/exec",
        json=body,
        headers=headers,
        timeout=body["timeout"] + 15 if not body["async"] and body["timeout"] > 0 else 30,
    )
    response.raise_for_status()
    result = response.json()
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not args.wait:
        if result.get("state") == "accepted":
            print(
                f"Task accepted. Query /api/v1/shell/tasks/by-request/{result.get('request_id')} "
                "for its final result.",
                file=sys.stderr,
            )
        return

    request_id = result.get("request_id")
    if not request_id:
        raise RuntimeError("AirBridge response did not include request_id.")

    query_url = f"{API}/api/v1/shell/tasks/by-request/{request_id}"
    while True:
        query = requests.get(query_url, headers=headers, timeout=30)
        if query.status_code == 202:
            time.sleep(args.poll_interval)
            continue
        query.raise_for_status()
        final_result = query.json()
        print(json.dumps(final_result, ensure_ascii=False, indent=2))
        task_result = final_result.get("result") or {}
        if final_result.get("state") != "completed":
            raise RuntimeError(
                f"Shell task did not complete successfully: {final_result.get('state')}"
            )
        if task_result.get("killed") or task_result.get("exit_code") != 0:
            raise RuntimeError(
                f"Remote command failed with exit_code={task_result.get('exit_code')}"
            )
        return


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as exc:
        raise SystemExit(f"AirBridge HTTP request failed: {exc}")
    except Exception as exc:
        raise SystemExit(str(exc))
