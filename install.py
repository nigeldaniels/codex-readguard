#!/usr/bin/env python3
"""Install the reviewed payload; filesystem policy lives in installer.py."""

import argparse
import subprocess
import sys
from pathlib import Path

from installer import Installation


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Readguard without overwriting files")
    parser.add_argument("repository", type=Path, help="Root of the destination Git repository")
    parser.add_argument("--dry-run", action="store_true", help="Validate and list files without writing")
    args = parser.parse_args()
    try:
        installation = Installation(Path(__file__).resolve().parent, args.repository)
        payload = installation.prepare()
        if not args.dry_run:
            installation.apply(payload)
        for path in payload:
            print(f'{"Would install" if args.dry_run else "Installed"}: .codex/{path}')
        print("Next: merge examples/AGENTS.md into your project instructions, then review /hooks.")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Readguard installation stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
