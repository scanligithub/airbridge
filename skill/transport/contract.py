# -*- coding: utf-8 -*-
"""Small, EDA-neutral Transport Contract shared by Skills and Providers."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Protocol


CONTRACT_VERSION = "1.0"


class TransportContractError(ValueError):
    """The caller supplied an invalid Transport Contract value."""


class UnsupportedCapabilityError(RuntimeError):
    """The selected Provider does not implement a requested capability."""


class ProviderClosedError(RuntimeError):
    """A closed Provider instance cannot accept further calls."""


class OperationStatus(str, Enum):
    COMPLETED = "COMPLETED"
    ACCEPTED = "ACCEPTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ProviderInfo:
    provider_name: str
    provider_version: str
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True)
class Capability:
    name: str
    supported: bool


@dataclass(frozen=True)
class OperationResponse:
    status: OperationStatus
    request_id: str
    success: Optional[bool] = None
    result: Optional[Mapping[str, Any]] = None
    error: Optional[str] = None
    provider_request_id: Optional[str] = None
    reason: Optional[str] = None

    @classmethod
    def completed(
        cls,
        request_id: str,
        success: bool,
        result: Optional[Mapping[str, Any]] = None,
        error: Optional[str] = None,
    ) -> "OperationResponse":
        if not request_id:
            raise TransportContractError("request_id must not be empty")
        if not isinstance(success, bool):
            raise TransportContractError("success must be a bool")
        return cls(
            status=OperationStatus.COMPLETED,
            request_id=request_id,
            success=success,
            result=result,
            error=error,
        )

    @classmethod
    def accepted(
        cls, request_id: str, provider_request_id: Optional[str] = None
    ) -> "OperationResponse":
        if not request_id:
            raise TransportContractError("request_id must not be empty")
        return cls(
            status=OperationStatus.ACCEPTED,
            request_id=request_id,
            provider_request_id=provider_request_id,
        )

    @classmethod
    def unknown(cls, request_id: str, reason: str) -> "OperationResponse":
        if not request_id:
            raise TransportContractError("request_id must not be empty")
        if not reason:
            raise TransportContractError("UNKNOWN response requires a reason")
        return cls(
            status=OperationStatus.UNKNOWN,
            request_id=request_id,
            reason=reason,
        )


class TransportProvider(Protocol):
    """Minimal interface consumed by a Skill."""

    def get_info(self) -> ProviderInfo:
        ...

    def get_capabilities(self) -> list[Capability]:
        ...

    def shell_execute(
        self,
        command: str,
        options: Optional[Mapping[str, Any]],
        request_id: str,
    ) -> OperationResponse:
        ...

    def close(self) -> None:
        ...
