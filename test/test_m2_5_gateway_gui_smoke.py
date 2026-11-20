from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Set this before importing Qt so the test is safe on a hosted Windows runner.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_PACKAGE = REPO_ROOT / "mcp_gateway"
if str(GATEWAY_PACKAGE) not in sys.path:
    sys.path.insert(0, str(GATEWAY_PACKAGE))

from PySide6.QtWidgets import QApplication

import airbridge_app


class _CredentialStoreProbe:
    def __init__(self):
        self.present = False

    def exists(self, _agent_id: str) -> bool:
        return self.present

    def load(self, _agent_id: str) -> str:
        raise AssertionError("GUI status inspection must not load credential secrets")


class _NoopRequestBroker:
    """No durable Runtime broker is needed because the worker is not started."""

    def __init__(self, *_args, **_kwargs):
        pass


class _NoopAntigravityIPCServer:
    """Avoid creating API/Runtime SQLite stores in a GUI-only offscreen test."""

    def __init__(self, worker, port: int = 8888):
        self.worker = worker
        self.port = port

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class GatewayGuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication(["AirBridge-Gateway-GUI-Smoke"])

    def test_main_window_loads_policy_and_both_client_adapter_panels(self):
        with tempfile.TemporaryDirectory(prefix="airbridge-gateway-gui-") as temp:
            env = {"LOCALAPPDATA": temp}
            with (
                patch.dict(os.environ, env, clear=False),
                patch(
                    "airbridge_mcp_gateway.credential_store.default_credential_store",
                    return_value=_CredentialStoreProbe(),
                ),
                patch.object(airbridge_app.SerialWorker, "start", lambda _self: None),
                patch.object(airbridge_app, "RequestBroker", _NoopRequestBroker),
                patch.object(
                    airbridge_app,
                    "AntigravityIPCServer",
                    _NoopAntigravityIPCServer,
                ),
            ):
                window = airbridge_app.AirBridgeMainWindow()
                try:
                    self.assertFalse(window.chk_gateway_enabled.isChecked())
                    self.assertFalse(window.chk_gateway_antigravity.isChecked())
                    self.assertFalse(window.chk_gateway_opencode.isChecked())
                    self.assertEqual(
                        window.gateway_antigravity_config_path,
                        window.gateway_antigravity_adapter.cli_global_config(),
                    )
                    self.assertEqual(
                        window.gateway_opencode_config_path,
                        window.gateway_opencode_adapter.global_config(),
                    )
                    self.assertIn(
                        "未安装",
                        window.lbl_gateway_antigravity_adapter_status.text(),
                    )
                    self.assertIn(
                        "未安装",
                        window.lbl_gateway_opencode_adapter_status.text(),
                    )
                    # Source-run tests must never write python.exe as a server command.
                    self.assertFalse(window.gateway_adapter_actions_enabled)
                    self.assertFalse(window.btn_gateway_antigravity_install.isEnabled())
                    self.assertFalse(window.btn_gateway_opencode_install.isEnabled())

                    # Unsaved GUI checkboxes must not enable a client entry.
                    window.chk_gateway_enabled.setChecked(True)
                    window.chk_gateway_antigravity.setChecked(True)
                    self.assertFalse(
                        window._gateway_adapter_enabled_target("antigravity")
                    )

                    # The durable policy plus a present credential is the
                    # sole source of truth for the client disabled flag.
                    window.gateway_policy_store.write(
                        enabled=True,
                        agents={"antigravity": True, "opencode": False},
                    )
                    window.gateway_credential_store.present = True
                    window.chk_gateway_enabled.setChecked(False)
                    window.chk_gateway_antigravity.setChecked(False)
                    self.assertTrue(
                        window._gateway_adapter_enabled_target("antigravity")
                    )
                finally:
                    window.close()


if __name__ == "__main__":
    unittest.main()
