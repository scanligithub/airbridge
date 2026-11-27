"""Build identity embedded in AirBridge binaries.

The Windows CI workflow replaces these source defaults in its build workspace
before PyInstaller runs. Source checkouts and local builds remain identifiable
as unpinned development builds rather than guessing a Git revision.
"""

import re
from typing import Any, Dict


BUILD_COMMIT = "unknown"
BUILD_VERSION = "airbridge-v2.4"
BUILD_TIMESTAMP = "unknown"
BUILD_PLATFORM = "unknown"

_BUILD_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_DIAGNOSTIC_FIELDS = (
    "build_version",
    "build_timestamp",
    "build_platform",
)


def get_build_identity() -> Dict[str, str]:
    """Return the small, non-sensitive identity advertised by Runtime Session."""
    return {
        "build_commit": BUILD_COMMIT,
        "build_version": BUILD_VERSION,
        "build_timestamp": BUILD_TIMESTAMP,
        "build_platform": BUILD_PLATFORM,
    }


def _is_verified_commit(value: Any) -> bool:
    return isinstance(value, str) and _BUILD_COMMIT_PATTERN.fullmatch(value) is not None


def evaluate_build_identity(
    local_identity: Any,
    peer_identity: Any,
) -> Dict[str, Any]:
    """Compare self-reported build commits without conflating unknown with mismatch.

    A matching full Git SHA is the sole condition for ``match``. Other identity
    fields are diagnostic only and never change the status.
    """
    local_commit = (
        local_identity.get("build_commit")
        if isinstance(local_identity, dict)
        else None
    )
    peer_commit = (
        peer_identity.get("build_commit")
        if isinstance(peer_identity, dict)
        else None
    )

    unavailable = []
    if not _is_verified_commit(local_commit):
        unavailable.append("local_build_commit_unavailable")
    if not _is_verified_commit(peer_commit):
        unavailable.append("peer_build_commit_unavailable")

    differences = []
    if isinstance(local_identity, dict) and isinstance(peer_identity, dict):
        differences = [
            field
            for field in _DIAGNOSTIC_FIELDS
            if local_identity.get(field) != peer_identity.get(field)
        ]

    if unavailable:
        return {
            "status": "unverified",
            "reason": ";".join(unavailable),
            "differences": differences,
        }

    if local_commit != peer_commit:
        return {
            "status": "mismatch",
            "reason": "build_commit_mismatch",
            "differences": differences,
        }

    return {
        "status": "match",
        "reason": (
            "build_commit_match"
            if not differences
            else "build_commit_match;diagnostic_fields_differ"
        ),
        "differences": differences,
    }
