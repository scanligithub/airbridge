"""Build-time integration check for inherited pipes in a windowed EXE."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=Path)
    args = parser.parse_args()

    executable = args.exe.resolve()
    if not executable.is_file():
        raise FileNotFoundError(executable)

    request = {"jsonrpc": "2.0", "id": 17, "method": "probe", "params": {"unicode": "AirBridge 测试"}}
    process = subprocess.Popen(
        [str(executable)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        stdout, stderr = process.communicate(
            (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"),
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise RuntimeError("Windowed EXE did not exit after stdin EOF (possible stdio pipe failure)")

    if process.returncode != 0:
        raise RuntimeError(
            f"Windowed EXE exited {process.returncode}; stderr={stderr.decode('utf-8', errors='replace')!r}"
        )
    if stderr:
        raise RuntimeError(f"Windowed EXE wrote unexpected stderr: {stderr.decode('utf-8', errors='replace')!r}")

    lines = stdout.decode("utf-8", errors="strict").splitlines()
    if len(lines) != 1:
        raise AssertionError(f"Expected exactly one stdout JSON line, received {len(lines)}: {lines!r}")
    response = json.loads(lines[0])
    if response.get("ok") is not True or response.get("echo") != request:
        raise AssertionError(f"Windowed EXE did not preserve the request: {response!r}")
    for key in ("stdin_restored", "stdout_restored", "stderr_restored"):
        if response.get(key) is not True:
            raise AssertionError(f"Missing restored standard stream: {key}")

    print("WINDOWED_EXE_STDIO_INHERITED_PIPES=PASS")
    print(f"exe={executable}")
    print(f"stdout_bytes={len(stdout)}")
    print(f"stderr_bytes={len(stderr)}")
    print("unicode_roundtrip=PASS")
    print("stdout_single_json_line=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
