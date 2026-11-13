"""Local administrator CLI for issuing and revoking AirBridge Agent tokens."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_security import AgentSecurityStore, VALID_SCOPES  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage local AirBridge MCP Agent credentials."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser("issue", help="Issue a new Agent Bearer token.")
    issue.add_argument("--agent-id", required=True)
    issue.add_argument("--scope", action="append", required=True, choices=sorted(VALID_SCOPES))
    issue.add_argument("--label", default="")
    issue.add_argument("--expires-days", type=int, default=90)

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


def main() -> int:
    args = build_parser().parse_args()
    store = AgentSecurityStore.from_env()
    try:
        if args.command == "issue":
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
        if args.command == "revoke":
            if args.token_id:
                changed = store.revoke_token(args.token_id)
            else:
                changed = store.revoke_agent(args.agent_id)
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
