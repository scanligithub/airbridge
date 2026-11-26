"""Restore inherited Windows standard handles in a PyInstaller windowed process.

PyInstaller's windowed bootloader sets sys.stdin/stdout/stderr to None. MCP
hosts still pass real pipe handles with STARTF_USESTDHANDLES; this module wraps
those handles before importing or running the MCP SDK.
"""
from __future__ import annotations

import io
import os
import sys
from typing import TextIO


def _handle_to_text_stream(which: int, *, readable: bool) -> TextIO:
    if os.name != "nt":
        raise OSError("Windows standard-handle restoration is only available on Windows")

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE

    handle = kernel32.GetStdHandle(which)
    raw_handle = handle if isinstance(handle, int) else handle.value
    invalid_handle = ctypes.c_void_p(-1).value
    if raw_handle in (None, 0, invalid_handle):
        error = ctypes.get_last_error()
        raise OSError(error, f"GetStdHandle({which:#x}) returned an invalid handle")

    flags = os.O_BINARY | (os.O_RDONLY if readable else os.O_WRONLY)
    fd = msvcrt.open_osfhandle(int(raw_handle), flags)
    try:
        raw_stream = os.fdopen(fd, "rb" if readable else "wb", buffering=0)
        return io.TextIOWrapper(
            raw_stream,
            encoding="utf-8",
            errors="strict",
            newline=None if readable else "\n",
            line_buffering=not readable,
            write_through=not readable,
        )
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def restore_windows_stdio() -> bool:
    """Restore stdin/stdout/stderr from inherited Win32 handles if needed.

    Returns True when replacement streams were installed, False when the
    process already has all three Python standard streams.
    """
    if all(stream is not None for stream in (sys.stdin, sys.stdout, sys.stderr)):
        return False
    if os.name != "nt":
        raise OSError("STDIO streams are missing outside Windows")

    # STARTF_USESTDHANDLES values from the Win32 API.
    stdin_stream = _handle_to_text_stream(0xFFFFFFF6, readable=True)
    try:
        stdout_stream = _handle_to_text_stream(0xFFFFFFF5, readable=False)
    except Exception:
        stdin_stream.close()
        raise
    try:
        stderr_stream = _handle_to_text_stream(0xFFFFFFF4, readable=False)
    except Exception:
        stdin_stream.close()
        stdout_stream.close()
        raise

    sys.stdin = sys.__stdin__ = stdin_stream
    sys.stdout = sys.__stdout__ = stdout_stream
    sys.stderr = sys.__stderr__ = stderr_stream
    return True
