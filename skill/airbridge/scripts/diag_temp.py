#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnose host B AirBridge temp workspace via the authenticated host A API."""
import json
import os
import sys

try:
    import requests
except ImportError:
    print("Install dependency first: pip install requests", file=sys.stderr)
    sys.exit(1)

API = "http://127.0.0.1:8888"
TOKEN_ENV = "AIRBRIDGE_AGENT_TOKEN"
TEMP = r"E:\\_airbridge_temp"


def post(path, body, headers):
    response = requests.post(
        f"{API}{path}",
        json=body,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def main():
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise SystemExit(
            f"{TOKEN_ENV} must contain a valid AirBridge Agent Bearer token."
        )
    headers = {"Authorization": f"Bearer {token}"}

    print("=== drives ===")
    print(json.dumps(post("/api/v1/fs/drives", {"timeout": 10}, headers),
                     ensure_ascii=False, indent=2))

    print(f"\\n=== ls {TEMP} ===")
    data = post("/api/v1/fs/ls", {"path": TEMP, "timeout": 10}, headers)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if not data.get("success"):
        return

    for name in ("_cadence_stdout.log", "_bootstrap.tcl"):
        path = TEMP + "\\\\" + name
        print(f"\\n=== read {path} ===")
        log = post(
            "/api/v1/fs/read",
            {"path": path, "max_preview_bytes": 12000, "timeout": 15},
            headers,
        )
        if log.get("success"):
            print(log.get("content", "")[:8000])
        else:
            print(log.get("error", log))


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as exc:
        raise SystemExit(f"AirBridge HTTP request failed: {exc}")
