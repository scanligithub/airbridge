"""Tests for backward-compatible Runtime Session build identity."""

import unittest

from protocol import FrameType
from build_identity import evaluate_build_identity
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


    def test_matching_full_commits_are_match(self):
        result = evaluate_build_identity(VALID_IDENTITY, dict(VALID_IDENTITY))
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["reason"], "build_commit_match")
        self.assertEqual(result["differences"], [])

    def test_different_full_commits_are_mismatch(self):
        peer = {**VALID_IDENTITY, "build_commit": "b" * 40}
        result = evaluate_build_identity(VALID_IDENTITY, peer)
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["reason"], "build_commit_mismatch")

    def test_unknown_on_both_sides_is_unverified_not_match(self):
        unknown = {
            "build_commit": "unknown",
            "build_version": CLIENT_VERSION,
            "build_timestamp": "unknown",
            "build_platform": "unknown",
        }
        result = evaluate_build_identity(unknown, dict(unknown))
        self.assertEqual(result["status"], "unverified")
        self.assertIn("local_build_commit_unavailable", result["reason"])
        self.assertIn("peer_build_commit_unavailable", result["reason"])

    def test_missing_legacy_peer_identity_is_unverified(self):
        result = evaluate_build_identity(VALID_IDENTITY, None)
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["reason"], "peer_build_commit_unavailable")

    def test_missing_local_identity_is_unverified(self):
        result = evaluate_build_identity(None, VALID_IDENTITY)
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["reason"], "local_build_commit_unavailable")

    def test_invalid_peer_commit_values_are_unverified(self):
        for value in (None, "", "unknown", "a" * 39, "A" * 40, "g" * 40):
            with self.subTest(commit=value):
                peer = {**VALID_IDENTITY, "build_commit": value}
                result = evaluate_build_identity(VALID_IDENTITY, peer)
                self.assertEqual(result["status"], "unverified")
                self.assertEqual(result["reason"], "peer_build_commit_unavailable")

    def test_diagnostic_field_differences_do_not_change_match(self):
        peer = {
            **VALID_IDENTITY,
            "build_version": "airbridge-dev",
            "build_timestamp": "2026-11-27T00:00:00Z",
            "build_platform": "windows-arm64",
        }
        result = evaluate_build_identity(VALID_IDENTITY, peer)
        self.assertEqual(result["status"], "match")
        self.assertEqual(
            result["reason"],
            "build_commit_match;diagnostic_fields_differ",
        )
        self.assertEqual(
            result["differences"],
            ["build_version", "build_timestamp", "build_platform"],
        )

    def test_version_and_platform_differences_do_not_create_mismatch(self):
        peer = {
            **VALID_IDENTITY,
            "build_version": "older-version",
            "build_platform": "linux-x64",
        }
        result = evaluate_build_identity(VALID_IDENTITY, peer)
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["differences"], ["build_version", "build_platform"])


if __name__ == "__main__":
    unittest.main()
