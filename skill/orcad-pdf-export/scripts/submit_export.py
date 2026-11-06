#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fill export-template.tcl and POST to AirBridge EDA API (run on host A)."""
import argparse
import json
from pathlib import Path

try:
    import requests
except ImportError:
    raise SystemExit("pip install requests")

API = "http://127.0.0.1:8888"
TEMPLATE = Path(__file__).resolve().parent.parent / "references" / "export-template.tcl"


def main():
    p = argparse.ArgumentParser(description="Submit OrCAD schematic PDF export via AirBridge")
    p.add_argument("--dsn", required=True, help="Absolute path to .dsn on host B")
    p.add_argument("--out-dir", default=r"E:\_airbridge_temp\pdf_out")
    p.add_argument("--pdf-name", default="schematic.pdf")
    p.add_argument("--gs", required=True, help="Absolute path to gswin64c.exe on host B")
    p.add_argument("--ps-driver", default="OrCADPS_17.2")
    p.add_argument("--version", default="17.2")
    p.add_argument("--timeout", type=int, default=300)
    args = p.parse_args()

    tpl = TEMPLATE.read_text(encoding="utf-8")
    script = (
        tpl.replace("DSN_PATH", args.dsn.replace("\\", "/"))
        .replace("OUT_DIR", args.out_dir.replace("\\", "/"))
        .replace("PDF_NAME", args.pdf_name)
        .replace("GS_EXE", args.gs.replace("\\", "/"))
        .replace("PS_DRIVER", args.ps_driver)
    )

    body = {
        "script": script,
        "hint": "export_pdf.tcl",
        "version": args.version,
        "timeout": args.timeout,
    }
    r = requests.post(f"{API}/api/v1/eda/execute", json=body, timeout=60)
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))
    print("Watch AirBridge EDA console for [OK]/[ERROR]; then fs/ls the out dir.", flush=True)


if __name__ == "__main__":
    main()
