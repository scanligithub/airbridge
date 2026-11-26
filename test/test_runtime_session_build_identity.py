"""Tests for backward-compatible Runtime Session build identity."""

import unittest

from protocol import FrameType
from runtime_session_protocol import (
    CLIENT_VERSION,
    SessionProtocolError,
    make_session_capabilities,
    validate_session_envelope,
)


VALID_IDENTITY = {
    "build_commit": "a" * 40,
    "build_version": CLIENT_VERSION,
    "build_timestamp": "2026-11-26T02:30:00Z",
    "build_platform": "windows-x64",
}


class RuntimeSessionBuildIdentityTests(unittest.TestCase):
    def test_capabilities_include_source_build_defaults(self):
        message = make_session_capabilities(
            "00000000-0000-4000-8000-000000000001"
        )
        identity = message["payload"]["build_identity"]
        self.assertEqual(identity["build_commit"], "unknown")
        self.assertEqual(identity["build_version"], CLIENT_VERSION)
        self.assertEqual(identity["build_timestamp"], "unknown")
        self.assertEqual(identity["build_platform"], "unknown")
        validate_session_envelope(FrameType.SESSION_CAPABILITIES, message)

    def test_capabilities_advertise_build_identity(self):
        message = make_session_capabilities(
            "00000000-0000-4000-8000-000000000001",
            build_identity=VALID_IDENTITY,
        )
        self.assertEqual(message["payload"]["build_identity"], VALID_IDENTITY)
        validate_session_envelope(FrameType.SESSION_CAPABILITIES, message)

    def test_legacy_peer_without_build_identity_remains_valid(self):
        message = make_session_capabilities(
            "00000000-0000-4000-8000-000000000001",
            build_identity=VALID_IDENTITY,
        )
        del message["payload"]["build_identity"]
        validate_session_envelope(FrameType.SESSION_CAPABILITIES, message)

    def test_empty_build_identity_is_rejected(self):
        message = make_session_capabilities(
            "00000000-0000-4000-8000-000000000001",
            build_identity={},
        )
        with self.assertRaises(SessionProtocolError):
            validate_session_envelope(FrameType.SESSION_CAPABILITIES, message)

    def test_invalid_build_commit_is_rejected(self):
        message = make_session_capabilities(
            "00000000-0000-4000-8000-000000000001",
            build_identity={**VALID_IDENTITY, "build_commit": "not-a-commit"},
        )
        with self.assertRaises(SessionProtocolError):
            validate_session_envelope(FrameType.SESSION_CAPABILITIES, message)

    def test_invalid_build_timestamp_is_rejected(self):
        message = make_session_capabilities(
            "00000000-0000-4000-8000-000000000001",
            build_identity={**VALID_IDENTITY, "build_timestamp": "yesterday"},
        )
        with self.assertRaises(SessionProtocolError):
            validate_session_envelope(FrameType.SESSION_CAPABILITIES, message)

    def test_missing_identity_field_is_rejected(self):
        identity = dict(VALID_IDENTITY)
        identity.pop("build_platform")
        message = make_session_capabilities(
            "00000000-0000-4000-8000-000000000001",
            build_identity=identity,
        )
        with self.assertRaises(SessionProtocolError):
            validate_session_envelope(FrameType.SESSION_CAPABILITIES, message)


if __name__ == "__main__":
    unittest.main()
