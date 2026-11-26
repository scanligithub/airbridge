# -*- coding: utf-8 -*-
"""Deterministic in-memory Fake Provider for Transport MVP tests."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from .contract import (
    CONTRACT_VERSION,
    Capability,
    OperationResponse,
    ProviderClosedError,
    ProviderInfo,
    UnsupportedCapabilityError,
)


class FakeProvider:
    """A local fake; it never starts a process or contacts AirBridge.

    Responses can be supplied by request ID to simulate COMPLETED, ACCEPTED,
    and UNKNOWN outcomes. Unspecified requests complete successfully with a
    small synthetic Shell result.
    """

    def __init__(
        self,
        responses: Optional[Mapping[str, OperationResponse]] = None,
        supported_capabilities: Optional[Iterable[str]] = None,
    ) -> None:
        self._responses = dict(responses or {})
        self._supported = frozenset(
            supported_capabilities
            if supported_capabilities is not None
            else ("shell.execute",)
        )
        self._closed = False
        self.executed_commands: list[dict[str, Any]] = []

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProviderClosedError("Transport Provider is closed")

    def get_info(self) -> ProviderInfo:
        self._ensure_open()
        return ProviderInfo(
            provider_name="fake",
            provider_version="0.1.0",
            contract_version=CONTRACT_VERSION,
        )

    def get_capabilities(self) -> list[Capability]:
        self._ensure_open()
        names = sorted(set(self._supported) | {"shell.execute"})
        return [
            Capability(name=name, supported=name in self._supported)
            for name in names
        ]

    def shell_execute(
        self,
        command: str,
        options: Optional[Mapping[str, Any]],
        request_id: str,
    ) -> OperationResponse:
        self._ensure_open()
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id must be a non-empty string")
        if options is not None and not isinstance(options, Mapping):
            raise TypeError("options must be a mapping or None")
        if "shell.execute" not in self._supported:
            raise UnsupportedCapabilityError(
                "Provider does not support shell.execute"
            )

        self.executed_commands.append({
            "command": command,
            "options": dict(options or {}),
            "request_id": request_id,
        })
        configured = self._responses.get(request_id)
        if configured is not None:
            if configured.request_id != request_id:
                raise ValueError("configured response request_id does not match")
            return configured

        return OperationResponse.completed(
            request_id=request_id,
            success=True,
            result={
                "exit_code": 0,
                "stdout": "FAKE: " + command,
                "stderr": "",
            },
        )

    def close(self) -> None:
        self._closed = True
