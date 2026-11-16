"""Isolated repository fixtures; tests never modify application sources."""

import json
import shlex
import tempfile
import unittest
from pathlib import Path

from read_guard.protocol import Request, render_request
from read_guard.service import Guard


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.guard = Guard(self.root)
        self.session = "session-one"
        self.write("source.py", "from dependency import result\nvalue = result\n")
        self.write("dependency.py", "result = 42\n")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def event(self, name, tool="Bash", command="pwd", **extra):
        return {"session_id": self.session, "cwd": str(self.root),
                "hook_event_name": name, "tool_name": tool,
                "tool_input": {"command": command}, **extra}

    def call(self, request, transform=lambda value: value):
        arguments = ["python3", ".codex/guard.py", request.action, request.path]
        if request.action == "read":
            arguments += ["--offset", str(request.offset)]
        elif request.action == "context":
            arguments += ["--related", *request.related, "--reason", request.reason]
        command = shlex.join(arguments)
        self.guard.handle(self.event("PreToolUse", command=command))
        response = {"exit_code": 0, "output": render_request(self.root, request)}
        return self.guard.handle(self.event("PostToolUse", command=command,
                                            tool_response=transform(response)))

    def read_all(self, name):
        offset = 0
        while True:
            request = Request("read", name, offset)
            receipt = render_request(self.root, request)
            self.call(request)
            metadata = json.loads(receipt.splitlines()[0].removeprefix("READ_GUARD "))
            offset = metadata["next_offset"]
            if offset is None:
                break

    def context(self, name="source.py", related=("dependency.py",)):
        return self.call(Request("context", name, related=related,
                                 reason="Reviewed the imported dependency and its contract."))

    def prepare(self):
        self.read_all("source.py")
        self.read_all("dependency.py")
        self.context()

    def patch(self, body="*** Update File: source.py\n@@\n-value = result\n+value = 43"):
        return self.guard.handle(self.event("PreToolUse", "apply_patch",
                                            f"*** Begin Patch\n{body}\n*** End Patch"))
