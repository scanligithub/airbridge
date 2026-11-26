"""Generate immutable build identity constants before packaging AirBridge."""

from __future__ import annotations

import argparse
import ast
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IDENTITY_PATH = ROOT / "build_identity.py"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--platform", required=True)
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        raise SystemExit("commit must be a full 40-character lowercase Git SHA")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", args.platform):
        raise SystemExit("platform contains unsupported characters")
    try:
        parsed = datetime.fromisoformat(args.timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit("timestamp must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise SystemExit("timestamp must be UTC")

    tree = ast.parse(IDENTITY_PATH.read_text(encoding="utf-8"))
    version = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "BUILD_VERSION"
            for target in node.targets
        ):
            version = ast.literal_eval(node.value)
            break
    if not isinstance(version, str) or not version.strip():
        raise SystemExit("BUILD_VERSION default is missing")

    content = f'''"""Build identity embedded in AirBridge binaries."""

BUILD_COMMIT = {args.commit!r}
BUILD_VERSION = {version!r}
BUILD_TIMESTAMP = {args.timestamp!r}
BUILD_PLATFORM = {args.platform!r}


def get_build_identity():
    """Return the small, non-sensitive identity advertised by Runtime Session."""
    return {{
        "build_commit": BUILD_COMMIT,
        "build_version": BUILD_VERSION,
        "build_timestamp": BUILD_TIMESTAMP,
        "build_platform": BUILD_PLATFORM,
    }}
'''
    IDENTITY_PATH.write_text(content, encoding="utf-8", newline="\n")
    print(
        "Generated AirBridge build identity: "
        f"commit={args.commit}, version={version}, "
        f"timestamp={args.timestamp}, platform={args.platform}"
    )


if __name__ == "__main__":
    main()
