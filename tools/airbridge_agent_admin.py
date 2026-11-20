"""Local administrator CLI for issuing and revoking AirBridge Agent credentials."""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "mcp_gateway"))

from agent_security import AgentSecurityStore, VALID_SCOPES  # noqa: E402
from airbridge_mcp_gateway.credential_store import (  # noqa: E402
    CredentialStoreError,
    WindowsCredentialStore,
)

DEFAULT_AGENT_SCOPES = ["runtime.read", "eda.versions.read", "request.read"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage local AirBridge MCP Agent credentials."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser("issue", help="Legacy: issue a one-time Bearer token (explicit reveal required).")
    issue.add_argument("--agent-id", required=True)
    issue.add_argument(
        "--reveal-token", action="store_true",
        help="Explicitly allow displaying the secret once; prefer the tokenless provision command.",
    )
    issue.add_argument("--scope", action="append", required=True, choices=sorted(VALID_SCOPES))
    issue.add_argument("--label", default="")
    issue.add_argument("--expires-days", type=int, default=90)

    provision = sub.add_parser(
        "provision",
        help="Create an Agent credential directly in Windows Credential Manager; never display the Token.",
    )
    provision.add_argument("--agent-id", required=True)
    provision.add_argument("--scope", action="append", choices=sorted(VALID_SCOPES))
    provision.add_argument("--label", default="")
    provision.add_argument("--expires-days", type=int, default=90)

    revoke = sub.add_parser("revoke", help="Revoke one token or every token for an Agent.")
    group = revoke.add_mutually_exclusive_group(required=True)
    group.add_argument("--token-id")
    group.add_argument("--agent-id")

    listing = sub.add_parser("list", help="List credential metadata without secrets.")
    listing.add_argument("--agent-id")

    audit = sub.add_parser("audit", help="Show recent authorization audit records.")
    audit.add_argument("--agent-id")
    audit.add_argument("--limit", type=int, default=100)
    return parser


def _provision_agent(store: AgentSecurityStore, args: argparse.Namespace) -> dict:
    if os.name != "nt":
        raise CredentialStoreError(
            "Zero-Touch provisioning currently requires Windows Credential Manager"
        )
    credentials = WindowsCredentialStore()
    existing = store.list_tokens(args.agent_id)
    if any(item["enabled"] for item in existing):
        raise CredentialStoreError(
            "Agent already has an active credential; revoke it before provisioning a replacement"
        )
    scopes = args.scope or DEFAULT_AGENT_SCOPES
    issued = store.issue_token(
        args.agent_id,
        scopes,
        label=args.label,
        expires_in_days=args.expires_days,
        reject_if_active=True,
    )
    try:
        credentials.store(issued["agent_id"], issued["token"])
        stored = credentials.load(issued["agent_id"])
        if not hmac.compare_digest(stored, issued["token"]):
            raise CredentialStoreError(
                "Credential verification failed after writing to Windows Credential Manager"
            )
    except Exception as provisioning_error:
        # Roll back both sides independently. A failed server-side revoke must
        # never prevent attempting to remove a credential already written
        # to the user's Windows vault.
        rollback_failures = []
        try:
            store.revoke_token(issued["token_id"])
        except Exception:
            rollback_failures.append("server credential revocation")
        try:
            credentials.delete(issued["agent_id"])
        except Exception:
            rollback_failures.append("local credential deletion")
        if rollback_failures:
            raise CredentialStoreError(
                "Provisioning failed; rollback incomplete: "
                + ", ".join(rollback_failures)
            ) from provisioning_error
        raise
    return {
        "provisioned": True,
        "agent_id": issued["agent_id"],
        "token_id": issued["token_id"],
        "scopes": issued["scopes"],
        "expires_at": issued["expires_at"],
        "gateway_environment": {"AIRBRIDGE_AGENT_ID": issued["agent_id"]},
    }


def _revoke_agent(store: AgentSecurityStore, agent_id: str) -> tuple[dict, int]:
    """Revoke server credentials first, then report local vault cleanup precisely."""
    changed = store.revoke_agent(agent_id)
    if os.name != "nt":
        return {"revoked": changed, "credential_cleanup": "not_applicable"}, 0
    try:
        deleted = WindowsCredentialStore().delete(agent_id)
    except CredentialStoreError:
        return {
            "revoked": changed,
            "credential_deleted": False,
            "credential_cleanup": "failed",
        }, 1
    return {
        "revoked": changed,
        "credential_deleted": deleted,
        "credential_cleanup": "deleted" if deleted else "not_found",
    }, 0


def main() -> int:
    args = build_parser().parse_args()
    store = AgentSecurityStore.from_env()
    try:
        if args.command == "issue":
            if not args.reveal_token:
                raise CredentialStoreError(
                    "Token display is disabled by default; use 'provision' or explicitly pass --reveal-token"
                )
            result = store.issue_token(
                args.agent_id,
                args.scope,
                label=args.label,
                expires_in_days=args.expires_days,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print(
                "\nIMPORTANT: copy the token into this Agent's MCP Host environment now. "
                "The server stores only its SHA-256 digest; the secret cannot be shown again.",
                file=sys.stderr,
            )
            return 0
        if args.command == "provision":
            print(json.dumps(_provision_agent(store, args), ensure_ascii=False, indent=2))
            return 0
        if args.command == "revoke":
            if args.token_id:
                changed = store.revoke_token(args.token_id)
                credential_deleted = None
            else:
                result, exit_code = _revoke_agent(store, args.agent_id)
                print(json.dumps(result, ensure_ascii=False))
                if exit_code:
                    print(
                        "Server credential is revoked, but local Credential Manager cleanup failed; retry cleanup.",
                        file=sys.stderr,
                    )
                return exit_code
            print(json.dumps({"revoked": changed}, ensure_ascii=False))
            return 0
        if args.command == "list":
            print(json.dumps(store.list_tokens(args.agent_id), ensure_ascii=False, indent=2))
            return 0
        if args.command == "audit":
            print(
                json.dumps(
                    store.list_audit(agent_id=args.agent_id, limit=args.limit),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
