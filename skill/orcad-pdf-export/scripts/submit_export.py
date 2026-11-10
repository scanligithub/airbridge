#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Submit and supervise an OrCAD Capture PDF export through AirBridge.

Run on host A. Capture is started on host B with the DSN as a native command-
line argument. The Tcl export runs inside that Capture GUI session, then
AirBridge requests a normal WM_CLOSE because keep_open=False.
"""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path, PureWindowsPath

try:
    import requests
except ImportError:
    raise SystemExit("Install dependency first: pip install requests")

API = "http://127.0.0.1:8888"
DEFAULT_WORKSPACE = r"E:\_airbridge_temp"
TEMPLATE = Path(__file__).resolve().parent.parent / "references" / "export-template.tcl"


def remote_path(path: str) -> str:
    """Normalize a Windows path for Tcl / AirBridge without touching host A."""
    return str(path).replace("\\", "/")


def tcl_quote(value: str) -> str:
    """Quote a Tcl value without allowing substitutions or command injection."""
    value = str(value)
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def request_json(method: str, path: str, *, body=None, timeout=30):
    url = f"{API}{path}"
    response = requests.request(method, url, json=body, timeout=timeout)
    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError(
            f"AirBridge returned non-JSON response ({response.status_code}): "
            f"{response.text[:500]}"
        )
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"AirBridge returned an unexpected JSON type ({response.status_code}): "
            f"{type(payload).__name__}"
        )
    return payload


def require_remote_file(path: str, label: str):
    result = request_json(
        "POST", "/api/v1/fs/stat",
        body={"path": path, "timeout": 15},
    )
    if not result.get("success") or not result.get("exists") or result.get("is_dir"):
        raise RuntimeError(
            f"{label} does not exist as a file on host B: {path}\n"
            f"AirBridge response: {json.dumps(result, ensure_ascii=False)}"
        )
    return result


def parse_result(text: str):
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return values


def read_remote_result(path: str):
    result = request_json(
        "POST", "/api/v1/fs/read",
        body={"path": path, "max_preview_bytes": 8192, "timeout": 15},
    )
    if not result.get("success"):
        return None
    return parse_result(result.get("content", ""))


def render_template(template_text: str, config_file: str, result_file: str):
    replacements = {
        "@CONFIG_FILE@": tcl_quote(remote_path(config_file)),
        "@RESULT_FILE@": tcl_quote(remote_path(result_file)),
    }
    script = template_text
    for marker, value in replacements.items():
        script = script.replace(marker, value)
    if "@CONFIG_FILE@" in script or "@RESULT_FILE@" in script:
        raise RuntimeError("Unresolved Tcl template marker.")
    return script


def main():
    parser = argparse.ArgumentParser(
        description="Export an OrCAD DSN to PDF through AirBridge on host B."
    )
    parser.add_argument("--dsn", required=True, help="Absolute .dsn path on host B")
    parser.add_argument("--out-dir", default=r"E:\_airbridge_temp\pdf_out")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE, help="Remote AirBridge workspace; match AIRBRIDGE_WORKSPACE_DIR if customized")
    parser.add_argument("--pdf-name", default=None, help="PDF filename (default: DSN basename.pdf)")
    parser.add_argument("--overwrite", action="store_true", help="Replace the target PDF if it already exists")
    parser.add_argument("--gs", required=True, help="Absolute gswin64c.exe path on host B")
    parser.add_argument("--ps-driver", default="OrCADPS_17.2")
    parser.add_argument("--version", default="17.2")
    parser.add_argument("--timeout", type=int, default=600, help="Capture task timeout in seconds")
    parser.add_argument("--wait-timeout", type=int, default=720, help="Host A wait for result marker")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args()

    dsn_path = args.dsn.strip()
    if not PureWindowsPath(dsn_path).is_absolute():
        raise SystemExit("--dsn must be an absolute Windows path on host B.")
    if PureWindowsPath(dsn_path).suffix.lower() != ".dsn":
        raise SystemExit("--dsn must point to a .dsn file on host B.")
    if not PureWindowsPath(args.gs.strip()).is_absolute():
        raise SystemExit("--gs must be an absolute Windows path on host B.")
    if not args.gs.strip().lower().endswith(".exe"):
        raise SystemExit("--gs must point to gswin64c.exe on host B.")
    if args.timeout < 30 or args.wait_timeout < args.timeout:
        raise SystemExit("Set --timeout >= 30 and --wait-timeout >= --timeout.")

    pdf_name = args.pdf_name or f"{PureWindowsPath(dsn_path).stem}.pdf"
    if (
        not pdf_name.lower().endswith(".pdf")
        or pdf_name in (".pdf", "..")
        or "/" in pdf_name
        or "\\" in pdf_name
        or "\r" in pdf_name
        or "\n" in pdf_name
    ):
        raise SystemExit("--pdf-name must be a plain filename ending in .pdf.")

    out_dir = args.out_dir.strip()
    gs_exe = args.gs.strip()
    ps_driver = args.ps_driver.strip()
    if not out_dir or not ps_driver:
        raise SystemExit("--out-dir and --ps-driver cannot be empty.")
    if not PureWindowsPath(out_dir).is_absolute():
        raise SystemExit("--out-dir must be an absolute Windows path on host B.")

    # 1. Verify AirBridge and the selected remote inputs.
    health = request_json("GET", "/api/v1/eda/versions", timeout=20)
    if not health.get("success"):
        raise RuntimeError(f"AirBridge EDA health check failed: {health}")
    versions = health.get("versions") or {}
    if args.version != "auto" and versions and args.version not in versions:
        raise RuntimeError(
            f"Cadence version {args.version} is unavailable on host B. "
            f"Available versions: {', '.join(versions.keys())}"
        )

    dsn_stat = require_remote_file(dsn_path, "DSN")
    gs_stat = require_remote_file(gs_exe, "Ghostscript")
    print(
        f"[OK] Host B DSN: {dsn_path} ({dsn_stat.get('size', 0)} bytes)",
        flush=True,
    )
    print(
        f"[OK] Host B Ghostscript: {gs_exe} ({gs_stat.get('size', 0)} bytes)",
        flush=True,
    )

    mkdir = request_json(
        "POST", "/api/v1/fs/mkdir",
        body={"path": remote_path(out_dir), "timeout": 20},
    )
    if not mkdir.get("success"):
        raise RuntimeError(f"Cannot create remote output directory: {mkdir}")

    expected_pdf_path = str(PureWindowsPath(out_dir) / pdf_name)
    existing_pdf = request_json(
        "POST", "/api/v1/fs/stat",
        body={"path": expected_pdf_path, "timeout": 15},
    )
    if existing_pdf.get("success") and existing_pdf.get("exists"):
        if existing_pdf.get("is_dir"):
            raise RuntimeError(f"PDF output path is a directory: {expected_pdf_path}")
        if not args.overwrite:
            raise RuntimeError(
                f"Output PDF already exists: {expected_pdf_path}. "
                "Use --overwrite only when you intend to replace it."
            )

    # 2. Upload a UTF-8 sidecar. The Tcl source stays ASCII-safe while paths
    #    (including Chinese output directories) retain their exact characters.
    job_id = uuid.uuid4().hex
    workspace = args.workspace.strip()
    if not workspace:
        raise SystemExit("--workspace cannot be empty.")
    if not PureWindowsPath(workspace).is_absolute():
        raise SystemExit("--workspace must be an absolute Windows path on host B.")
    try:
        workspace.encode("ascii")
    except UnicodeEncodeError:
        raise SystemExit("--workspace must use an ASCII-only path because it is embedded in the Capture Tcl source.")
    config_file = str(PureWindowsPath(workspace) / f"_pdf_export_config_{job_id}.txt")
    result_file = str(PureWindowsPath(workspace) / f"_pdf_export_result_{job_id}.txt")
    config_values = [
        remote_path(out_dir),
        pdf_name,
        remote_path(gs_exe),
        ps_driver,
    ]
    if any("\n" in value or "\r" in value for value in config_values):
        raise SystemExit("Output paths, PDF name, and printer name must not contain newline characters.")
    config_text = "\n".join(config_values)
    config_write = request_json(
        "POST", "/api/v1/fs/write",
        body={
            "path": config_file,
            "content": config_text,
            "is_base64": False,
            "mode": "w",
            "timeout": 20,
        },
    )
    if not config_write.get("success"):
        raise RuntimeError(f"Cannot upload PDF config sidecar: {config_write}")

    template_text = TEMPLATE.read_text(encoding="utf-8")
    script = render_template(template_text, config_file, result_file)

    # 3. Native target_file opens the DSN before capAutoLoad runs.
    #    keep_open=False makes EDAExecutor request the same graceful WM_CLOSE
    #    used by the AirBridge Capture close button after the Tcl export ends.
    submit = request_json(
        "POST", "/api/v1/eda/execute",
        body={
            "script": script,
            "hint": "airbridge_pdf_export.tcl",
            "version": args.version,
            "target_file": dsn_path,
            "timeout": args.timeout,
            "keep_open": False,
        },
        timeout=60,
    )
    if submit.get("status") != "submitted":
        raise RuntimeError(f"PDF export task was not submitted: {submit}")

    print(
        "[SUBMITTED] Capture on host B will open the DSN natively, run "
        "OrHandlerPDFExport, validate the PDF, then receive a normal close request.",
        flush=True,
    )
    print(f"[INFO] Result marker: {result_file}", flush=True)

    # 4. Poll the RFS result marker; HTTP 'submitted' is not treated as success.
    deadline = time.monotonic() + args.wait_timeout
    result_data = None
    while time.monotonic() < deadline:
        result_data = read_remote_result(result_file)
        required_result_fields = {"STATUS", "PDF_PATH", "PDF_SIZE", "MESSAGE"}
        if result_data and required_result_fields.issubset(result_data):
            break
        time.sleep(max(0.5, args.poll_interval))

    required_result_fields = {"STATUS", "PDF_PATH", "PDF_SIZE", "MESSAGE"}
    if not result_data or not required_result_fields.issubset(result_data):
        log_path = str(PureWindowsPath(workspace) / "_cadence_stdout.log")
        log_data = request_json(
            "POST", "/api/v1/fs/read",
            body={"path": log_path, "max_preview_bytes": 16000, "timeout": 15},
        )
        excerpt = log_data.get("content", "") if log_data.get("success") else ""
        raise RuntimeError(
            "Timed out waiting for the PDF result marker. The Capture task may "
            "still be running or may have failed before the Tcl script started.\n"
            f"Recent Capture log:\n{excerpt[-8000:]}"
        )

    if result_data.get("STATUS") != "OK":
        raise RuntimeError(
            "PDF export failed on host B.\n"
            f"Status: {result_data.get('STATUS')}\n"
            f"Message: {result_data.get('MESSAGE')}\n"
            f"Capture result: {json.dumps(result_data, ensure_ascii=False)}"
        )

    pdf_path = result_data.get("PDF_PATH", "")
    expected_size = int(result_data.get("PDF_SIZE", "0") or 0)
    pdf_stat = require_remote_file(pdf_path, "PDF output")
    actual_size = int(pdf_stat.get("size", 0))
    if actual_size <= 0 or (expected_size and actual_size != expected_size):
        raise RuntimeError(
            f"PDF size verification failed: Tcl={expected_size}, RFS={actual_size}"
        )

    # Remove only this job's temporary config sidecar; keep the result marker
    # as an audit record for the user.
    request_json(
        "POST", "/api/v1/fs/rm",
        body={"path": config_file, "timeout": 15},
    )

    print("[PASS] PDF generated and parsed by Ghostscript.", flush=True)
    print(f"PDF: {pdf_path}", flush=True)
    print(f"Size: {actual_size} bytes", flush=True)
    print(f"Result marker: {result_file}", flush=True)
    print(
        "AirBridge uses its normal WM_CLOSE path after the Tcl task finishes; "
        "check the AirBridge console if a save/confirmation dialog prevents exit.",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as exc:
        raise SystemExit(f"AirBridge HTTP request failed: {exc}")
    except Exception as exc:
        raise SystemExit(str(exc))
