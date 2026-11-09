#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a Capture Tcl export script locally; AirBridge does not execute it."""
import argparse
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "references" / "export-template.tcl"


def main():
    parser = argparse.ArgumentParser(
        description="Generate an OrCAD Capture PDF-export Tcl script"
    )
    parser.add_argument("--dsn", required=True, help="Absolute .dsn path on host B")
    parser.add_argument("--out-dir", default=r"E:\_airbridge_temp\pdf_out")
    parser.add_argument("--pdf-name", default="schematic.pdf")
    parser.add_argument("--gs", required=True, help="Absolute path to gswin64c.exe on host B")
    parser.add_argument("--ps-driver", default="OrCADPS_17.2")
    parser.add_argument("--output", default="export_pdf.tcl", help="Output Tcl file path")
    args = parser.parse_args()

    replacements = {
        "DSN_PATH": args.dsn.replace("\\", "/"),
        "OUT_DIR": args.out_dir.replace("\\", "/"),
        "PDF_NAME": args.pdf_name,
        "GS_EXE": args.gs.replace("\\", "/"),
        "PS_DRIVER": args.ps_driver,
    }
    script = TEMPLATE.read_text(encoding="utf-8")
    for key, value in replacements.items():
        script = script.replace(key, value)

    output_path = Path(args.output).expanduser().resolve()
    output_path.write_text(script, encoding="utf-8")
    print(f"Generated Capture Tcl script: {output_path}")
    print("AirBridge only transports this file; run it in Capture using your separate workflow.")


if __name__ == "__main__":
    main()