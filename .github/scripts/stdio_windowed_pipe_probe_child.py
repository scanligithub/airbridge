"""Minimal stdio echo probe for a PyInstaller windowed executable."""

from __future__ import annotations

import ctypes
import json
import io
import msvcrt
import os
import sys
from ctypes import wintypes


STD_INPUT_HANDLE = 0xFFFFFFF6
STD_OUTPUT_HANDLE = 0xFFFFFFF5
STD_ERROR_HANDLE = 0xFFFFFFF4
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _open_standard_handle(which: int, *, readable: bool):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    handle = kernel32.GetStdHandle(which)
    raw_handle = handle if isinstance(handle, int) else handle.value
    if raw_handle in (None, 0, INVALID_HANDLE_VALUE):
        raise OSError(f"GetStdHandle({which:#x}) returned an invalid handle")

    flags = os.O_BINARY | (os.O_RDONLY if readable else os.O_WRONLY)
    fd = msvcrt.open_osfhandle(int(raw_handle), flags)
    mode = "rb" if readable else "wb"
    return os.fdopen(fd, mode, buffering=0)


def main() -> int:
    # PyInstaller --noconsole can initialize sys.stdin/stdout/stderr as None.
    # Rebind from inherited STARTF_USESTDHANDLES handles supplied by the MCP host.
    sys.stdin = io.TextIOWrapper(
        _open_standard_handle(STD_INPUT_HANDLE, readable=True),
        encoding="utf-8",
        errors="replace",
    )
    sys.stdout = io.TextIOWrapper(
        _open_standard_handle(STD_OUTPUT_HANDLE, readable=False),
        encoding="utf-8",
        newline="\\n",
        write_through=True,
    )
    sys.stderr = io.TextIOWrapper(
        _open_standard_handle(STD_ERROR_HANDLE, readable=False),
        encoding="utf-8",
        write_through=True,
    )
    sys.__stdin__ = sys.stdin
    sys.__stdout__ = sys.stdout
    sys.__stderr__ = sys.stderr

    line = sys.stdin.readline()
    if not line:
        return 2
    request = json.loads(line)
    response = {
        "ok": True,
        "echo": request,
        "stdin_restored": sys.stdin is not None and hasattr(sys.stdin, "buffer"),
        "stdout_restored": sys.stdout is not None and hasattr(sys.stdout, "buffer"),
        "stderr_restored": sys.stderr is not None and hasattr(sys.stderr, "buffer"),
    }
    sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # In a frozen windowed process, emit only to the inherited stderr pipe.
        try:
            if sys.stderr is not None:
                sys.stderr.write((f"PROBE_ERROR: {type(exc).__name__}: {exc}\n").encode("utf-8"))
        except Exception:
            pass
        raise
