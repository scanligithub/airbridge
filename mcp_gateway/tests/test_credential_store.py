from __future__ import annotations

import os
import threading
import unittest
import uuid

from airbridge_mcp_gateway.credential_store import (
    CredentialStoreError,
    WindowsAgentCredentialLock,
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

    def test_agent_mutex_name_is_hashed_and_stable(self):
        name = WindowsAgentCredentialLock._target_name("OpenCode")
        self.assertTrue(name.startswith("Local\\AirBridge.AgentCredential."))
        self.assertNotIn("opencode", name)
        self.assertEqual(name, WindowsAgentCredentialLock._target_name("opencode"))

    def test_agent_mutex_rejects_invalid_ids_before_platform_access(self):
        with self.assertRaisesRegex(CredentialStoreError, "invalid Agent ID"):
            WindowsAgentCredentialLock._target_name("../invalid")

    @unittest.skipUnless(os.name == "nt", "Windows named mutex integration test")
    def test_windows_agent_mutex_serializes_threads(self):
        acquired_first = threading.Event()
        release_first = threading.Event()
        acquired_second = threading.Event()

        def first_worker():
            with WindowsAgentCredentialLock("mutex-probe"):
                acquired_first.set()
                release_first.wait(timeout=5.0)

        def second_worker():
            with WindowsAgentCredentialLock("mutex-probe"):
                acquired_second.set()

        first = threading.Thread(target=first_worker, daemon=True)
        second = threading.Thread(target=second_worker, daemon=True)
        first.start()
        try:
            self.assertTrue(acquired_first.wait(timeout=2.0))
            second.start()
            self.assertFalse(acquired_second.wait(timeout=0.2))
            release_first.set()
            self.assertTrue(acquired_second.wait(timeout=2.0))
        finally:
            release_first.set()
            first.join(timeout=2.0)
            if second.ident is not None:
                second.join(timeout=2.0)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())

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
