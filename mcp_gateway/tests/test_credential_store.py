from __future__ import annotations

import os
import unittest
import uuid

from airbridge_mcp_gateway.credential_store import (
    CredentialStoreError,
    WindowsCredentialStore,
)


class CredentialStoreTests(unittest.TestCase):
    def test_invalid_agent_id_is_rejected_before_platform_access(self):
        with self.assertRaisesRegex(CredentialStoreError, "invalid Agent ID"):
            WindowsCredentialStore._target("../not-an-agent")

    def test_target_is_stable_and_namespaced(self):
        self.assertEqual(
            WindowsCredentialStore._target("OpenCode"),
            "AirBridge/Agent/opencode",
        )

    @unittest.skipUnless(os.name == "nt", "Windows Credential Manager integration test")
    def test_windows_credential_manager_round_trip(self):
        store = WindowsCredentialStore()
        agent_id = "test-" + uuid.uuid4().hex
        token = "abg1." + uuid.uuid4().hex + ".credential-probe"
        try:
            store.store(agent_id, token)
            self.assertEqual(store.load(agent_id), token)
        finally:
            store.delete(agent_id)
        with self.assertRaises(CredentialStoreError):
            store.load(agent_id)

    @unittest.skipIf(os.name == "nt", "non-Windows fail-closed test")
    def test_non_windows_backend_fails_closed(self):
        with self.assertRaisesRegex(CredentialStoreError, "only on Windows"):
            WindowsCredentialStore()


if __name__ == "__main__":
    unittest.main()
