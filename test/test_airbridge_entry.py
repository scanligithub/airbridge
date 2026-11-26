from __future__ import annotations

import io
import unittest

from airbridge_entry import main


class AirBridgeEntryPointTests(unittest.TestCase):
    def test_stdio_mode_dispatches_without_starting_gui(self):
        calls: list[str] = []

        def run_stdio() -> int:
            calls.append("stdio")
            return 0

        def run_gui() -> int:
            calls.append("gui")
            return 99

        result = main(
            ["mcp", "stdio"],
            stdio_runner=run_stdio,
            gui_runner=run_gui,
        )

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["stdio"])

    def test_default_mode_preserves_existing_gui_startup(self):
        calls: list[str] = []

        def run_gui() -> int:
            calls.append("gui")
            return 7

        result = main([], gui_runner=run_gui)

        self.assertEqual(result, 7)
        self.assertEqual(calls, ["gui"])

    def test_unsupported_mcp_mode_fails_without_falling_back_to_gui(self):
        calls: list[str] = []
        error_output = io.StringIO()

        result = main(
            ["mcp", "http"],
            gui_runner=lambda: calls.append("gui") or 0,
            stderr=error_output,
        )

        self.assertEqual(result, 2)
        self.assertEqual(calls, [])
        self.assertIn("AirBridge.exe mcp stdio", error_output.getvalue())

    def test_stdio_mode_rejects_ambiguous_extra_arguments(self):
        calls: list[str] = []
        error_output = io.StringIO()

        result = main(
            ["mcp", "stdio", "--unexpected"],
            stdio_runner=lambda: calls.append("stdio") or 0,
            stderr=error_output,
        )

        self.assertEqual(result, 2)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
