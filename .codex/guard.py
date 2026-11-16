#!/usr/bin/env python3
"""CLI and Codex hook entrypoint; policy lives in read_guard."""

import json
import subprocess
import sys
from pathlib import Path

from read_guard.protocol import Request, render_request
from read_guard.service import Guard


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    try:
        if sys.argv[1:] == ["hook"]:
            event = json.loads(sys.stdin.buffer.read(4 * 1024 * 1024))
            result = Guard(root).handle(event)
            print(json.dumps(result))
        else:
            request = Request.parse(sys.argv[1:])
            if request.action == "run":
                # The pre-tool hook validates declared writes before this
                # command starts. Preserve normal streaming output and status.
                return subprocess.run(request.command, check=False).returncode
            print(render_request(root, request), end="")
        return 0
    except Exception as exc:
        # Codex normally continues after hook crashes. Exit 2 explicitly denies
        # the pending call instead, including malformed input and storage errors.
        print(f"Read-before-edit guard: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
