#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Submit a remote shell command via AirBridge host A API."""
import argparse
import json
import sys

try:
    import requests
except ImportError:
    print("pip install requests")
    sys.exit(1)

API = "http://127.0.0.1:8888"


def main():
    p = argparse.ArgumentParser(description="AirBridge remote shell")
    p.add_argument("command", help="PowerShell or cmd command string")
    p.add_argument("--shell", default="powershell", choices=["powershell", "cmd"])
    p.add_argument("--cwd", default=r"E:\_airbridge_temp")
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--async", dest="async_mode", action="store_true")
    args = p.parse_args()

    body = {
        "command": args.command,
        "shell": args.shell,
        "cwd": args.cwd,
        "timeout": args.timeout,
        "async": args.async_mode,
    }
    r = requests.post(f"{API}/api/v1/shell/exec", json=body, timeout=30)
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))
    print(
        "\nNote: full stdout/stderr usually appear in the AirBridge console as SHELL_RESULT.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
