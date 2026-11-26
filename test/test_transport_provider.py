# -*- coding: utf-8 -*-
"""Tests for the generic Transport Contract and Fake Provider."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill"))

from transport import (  # noqa: E402
    CONTRACT_VERSION,
    FakeProvider,
    OperationResponse,
    OperationStatus,
    ProviderClosedError,
    UnsupportedCapabilityError,
)


class FakeProviderTests(TestCase):
    def setUp(self):
        self.provider = FakeProvider()

    def test_provider_info_and_contract_version(self):
        info = self.provider.get_info()
        self.assertEqual(info.provider_name, "fake")
        self.assertEqual(info.contract_version, CONTRACT_VERSION)
        self.assertEqual(CONTRACT_VERSION, "1.0")

    def test_capability_is_reported_explicitly(self):
        capabilities = {
            item.name: item.supported
            for item in self.provider.get_capabilities()
        }
        self.assertTrue(capabilities["shell.execute"])

    def test_shell_returns_completed_result_with_same_request_id(self):
        response = self.provider.shell_execute(
            "Get-Location", {"shell": "powershell"}, "request-001"
        )
        self.assertEqual(response.status, OperationStatus.COMPLETED)
        self.assertEqual(response.request_id, "request-001")
        self.assertTrue(response.success)
        self.assertEqual(response.result["exit_code"], 0)
        self.assertEqual(response.result["stdout"], "FAKE: Get-Location")

    def test_unknown_response_keeps_original_request_id(self):
        unknown = OperationResponse.unknown("request-unknown", "simulated timeout")
        provider = FakeProvider(responses={"request-unknown": unknown})
        response = provider.shell_execute("do-work", {}, "request-unknown")
        self.assertEqual(response.status, OperationStatus.UNKNOWN)
        self.assertEqual(response.request_id, "request-unknown")
        self.assertEqual(response.reason, "simulated timeout")

    def test_accepted_response_is_distinct_from_completion(self):
        accepted = OperationResponse.accepted(
            "request-accepted", provider_request_id="provider-42"
        )
        provider = FakeProvider(responses={"request-accepted": accepted})
        response = provider.shell_execute("long-work", {}, "request-accepted")
        self.assertEqual(response.status, OperationStatus.ACCEPTED)
        self.assertEqual(response.provider_request_id, "provider-42")
        self.assertIsNone(response.success)

    def test_unsupported_shell_capability_fails_explicitly(self):
        provider = FakeProvider(supported_capabilities=())
        with self.assertRaises(UnsupportedCapabilityError):
            provider.shell_execute("Get-Location", {}, "request-002")

    def test_invalid_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            self.provider.shell_execute("", {}, "request-003")
        with self.assertRaises(ValueError):
            self.provider.shell_execute("Get-Location", {}, "")
        with self.assertRaises(TypeError):
            self.provider.shell_execute("Get-Location", [], "request-003")

    def test_close_prevents_further_operations(self):
        self.provider.close()
        with self.assertRaises(ProviderClosedError):
            self.provider.get_info()
        with self.assertRaises(ProviderClosedError):
            self.provider.shell_execute("Get-Location", {}, "request-004")

    def test_fake_provider_does_not_require_airbridge_or_eda(self):
        response = self.provider.shell_execute("echo ready", None, "request-005")
        self.assertTrue(response.success)
        self.assertEqual(len(self.provider.executed_commands), 1)


if __name__ == "__main__":
    main()
