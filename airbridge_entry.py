"""Single executable entry point for the AirBridge GUI and local MCP STDIO mode.

The MCP path is deliberately selected before importing the GUI module so a
STDIO child process does not initialize Qt or load the desktop UI.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from typing import TextIO


def _run_stdio() -> int:
    # A --windowed PyInstaller EXE starts with Python standard streams set to
    # None. Restore the MCP Host's inherited pipe handles before importing MCP.
    from stdio_io import restore_windows_stdio

    try:
        restore_windows_stdio()
    except Exception as exc:
        try:
            if sys.stderr is not None:
                sys.stderr.write(f"AirBridge MCP STDIO pipe setup failed: {exc}\n")
                sys.stderr.flush()
        except Exception:
            pass
        return 2

    # Import lazily: stdout is reserved exclusively for MCP protocol frames.
    from airbridge_mcp_gateway.server import main as run_mcp_stdio

    result = run_mcp_stdio()
    return int(result or 0)


def _run_gui() -> int:
    # Keep Qt and the large AirBridge desktop module out of the MCP process.
    from airbridge_app import run_gui

    return int(run_gui() or 0)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdio_runner: Callable[[], int | None] | None = None,
    gui_runner: Callable[[], int | None] | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Dispatch the explicit MCP command or preserve the existing GUI default."""
    args = list(sys.argv[1:] if argv is None else argv)
    error_stream = stderr if stderr is not None else sys.stderr

    if args[:2] == ["mcp", "stdio"]:
        if len(args) != 2:
            if error_stream is not None:
                error_stream.write(
                    "AirBridge MCP STDIO usage: AirBridge.exe mcp stdio\n"
                )
            return 2
        runner = stdio_runner or _run_stdio
        result = runner()
        return int(result or 0)

    if args and args[0] == "mcp":
        if error_stream is not None:
            error_stream.write(
                "Unsupported MCP mode. The only supported mode is: "
                "AirBridge.exe mcp stdio\n"
            )
        return 2

    runner = gui_runner or _run_gui
    result = runner()
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
