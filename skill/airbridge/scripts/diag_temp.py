#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnose host B AirBridge temp workspace via host A API."""
import json
import sys

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

API = "http://127.0.0.1:8888"
TEMP = r"E:\_airbridge_temp"


def post(path, body):
    r = requests.post(f"{API}{path}", json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    print("=== drives ===")
    print(json.dumps(post("/api/v1/fs/drives", {"timeout": 10}), ensure_ascii=False, indent=2))

    print(f"\n=== ls {TEMP} ===")
    data = post("/api/v1/fs/ls", {"path": TEMP, "timeout": 10})
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if not data.get("success"):
        return

    for name in ("_cadence_stdout.log", "_bootstrap.tcl"):
        path = TEMP + "\\" + name
        print(f"\n=== read {path} ===")
        log = post(
            "/api/v1/fs/read",
            {"path": path, "max_preview_bytes": 12000, "timeout": 15},
        )
        if log.get("success"):
            print(log.get("content", "")[:8000])
        else:
            print(log.get("error", log))


if __name__ == "__main__":
    main()
