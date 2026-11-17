"""Standalone stdio MCP server exposing a small, guarded AirBridge toolset."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import __version__
from .config import GatewayConfig
from .service import AirBridgeGateway


logger = logging.getLogger("airbridge_mcp_gateway")


def create_server(gateway: AirBridgeGateway | None = None) -> MCPServer:
    service = gateway or AirBridgeGateway(GatewayConfig.from_env())
    server = MCPServer(
        "AirBridge Gateway",
        version=__version__,
        instructions=(
            "Provides domain-neutral access to the local AirBridge HTTP API. "
            "It does not interpret EDA scripts or provide Capture/Allegro business APIs. "
            "Never resubmit an execution whose result is unconfirmed; query its request_id."
        ),
    )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def airbridge_get_capabilities() -> dict[str, Any]:
        """Read the latest Host-A/Host-B Runtime Session capability negotiation state."""
        return service.get_capabilities()

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def airbridge_get_eda_versions() -> dict[str, Any]:
        """Read EDA software versions discovered on Host B through AirBridge."""
        return service.get_eda_versions()

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=False,
        )
    )
    def airbridge_eda_execute(
        script: str,
        hint: str = "agent_request.tcl",
        version: str = "auto",
        target_file: str = "",
        timeout: int = 60,
        keep_open: bool = True,
        wait_timeout_ms: int = 60_000,
    ) -> dict[str, Any]:
        """Submit opaque script text to the currently managed remote EDA runtime.

        Execution is disabled by default and requires AIRBRIDGE_ENABLE_EDA_EXECUTE=1.
        The gateway creates and returns a request_id for status polling.
        """
        return service.execute_eda(
            script=script,
            hint=hint,
            version=version,
            target_file=target_file,
            timeout=timeout,
            keep_open=keep_open,
            wait_timeout_ms=wait_timeout_ms,
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def airbridge_request_status(request_id: str) -> dict[str, Any]:
        """Read the state/result for a previously returned AirBridge request_id."""
        return service.get_request_status(request_id)

    return server


def main() -> None:
    try:
        gateway = AirBridgeGateway(GatewayConfig.from_env())
    except Exception:
        logger.exception("Invalid AirBridge MCP Gateway configuration")
        raise
    logger.info("Starting AirBridge MCP Gateway against %s", gateway.config.base_url)
    create_server(gateway).run(transport="stdio")


if __name__ == "__main__":
    main()
