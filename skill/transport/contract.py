# -*- coding: utf-8 -*-
"""Small, EDA-neutral Transport Contract shared by Skills and Providers."""
from __future__ import annotations

import uuid
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


def normalize_request_id(request_id: str) -> str:
    """Validate and canonicalize the UUID used for cross-provider correlation."""
    if not isinstance(request_id, str):
        raise TransportContractError("request_id must be a UUID string")
    try:
        return str(uuid.UUID(request_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TransportContractError("request_id must be a valid UUID") from exc


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
        normalized = normalize_request_id(request_id)
        if not isinstance(success, bool):
            raise TransportContractError("success must be a bool")
        return cls(
            status=OperationStatus.COMPLETED,
            request_id=normalized,
            success=success,
            result=result,
            error=error,
        )

    @classmethod
    def accepted(
        cls, request_id: str, provider_request_id: Optional[str] = None
    ) -> "OperationResponse":
        return cls(
            status=OperationStatus.ACCEPTED,
            request_id=normalize_request_id(request_id),
            provider_request_id=provider_request_id,
        )

    @classmethod
    def unknown(cls, request_id: str, reason: str) -> "OperationResponse":
        if not reason:
            raise TransportContractError("UNKNOWN response requires a reason")
        return cls(
            status=OperationStatus.UNKNOWN,
            request_id=normalize_request_id(request_id),
            reason=reason,
        )


class TransportProvider(Protocol):
    """Minimal interface consumed by an EDA-independent Skill."""

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

    def get_operation(self, request_id: str) -> OperationResponse:
        """Read the existing operation by its original request ID; never resubmit."""
        ...

    def close(self) -> None:
        ...
