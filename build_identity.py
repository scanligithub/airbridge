"""Build identity embedded in AirBridge binaries.

The Windows CI workflow replaces these source defaults in its build workspace
before PyInstaller runs. Source checkouts and local builds remain identifiable
as unpinned development builds rather than guessing a Git revision.
"""

BUILD_COMMIT = "unknown"
BUILD_VERSION = "airbridge-v2.4"
BUILD_TIMESTAMP = "unknown"
BUILD_PLATFORM = "unknown"


def get_build_identity():
    """Return the small, non-sensitive identity advertised by Runtime Session."""
    return {
        "build_commit": BUILD_COMMIT,
        "build_version": BUILD_VERSION,
        "build_timestamp": BUILD_TIMESTAMP,
        "build_platform": BUILD_PLATFORM,
    }
