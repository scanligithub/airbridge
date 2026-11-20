from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

from mcp import Client, StdioServerParameters

from airbridge_mcp_gateway.credential_store import WindowsCredentialStore


EXPECTED_TOOLS = {
    "airbridge_get_capabilities",
    "airbridge_get_eda_versions",
    "airbridge_eda_execute",
    "airbridge_request_status",
}


async def exercise_frozen_stdio(executable: Path, agent_id: str) -> None:
    parameters = StdioServerParameters(
        command=str(executable.resolve()),
        args=["mcp", "stdio"],
        env={
            "AIRBRIDGE_AGENT_ID": agent_id,
            "AIRBRIDGE_BASE_URL": "http://127.0.0.1:8888",
            "AIRBRIDGE_ENABLE_EDA_EXECUTE": "0",
        },
        cwd=str(executable.resolve().parent),
    )

    # Client uses the real OS subprocess and stdin/stdout pipes. A successful
    # initialize + tools/list also proves stdout contains only MCP frames.
    async with Client(parameters) as client:
        result = await client.list_tools()
        actual_tools = {tool.name for tool in result.tools}
        missing = EXPECTED_TOOLS - actual_tools
        extra = actual_tools - EXPECTED_TOOLS
        if missing or extra:
            raise AssertionError(
                f"Frozen STDIO tool registry mismatch; missing={sorted(missing)}, "
                f"extra={sorted(extra)}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=Path)
    args = parser.parse_args()

    executable = args.exe.resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"Frozen executable not found: {executable}")

    if os.name != "nt":
        raise RuntimeError("This frozen STDIO smoke test must run on Windows")

    agent_id = f"ci-stdio-smoke-{uuid.uuid4().hex[:8]}"
    store = WindowsCredentialStore()
    store.store(agent_id, "non-production-ci-stdio-smoke-token")
    try:
        asyncio.run(exercise_frozen_stdio(executable, agent_id))
    finally:
        store.delete(agent_id)

    print("Frozen AirBridge.exe STDIO initialize + tools/list: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
