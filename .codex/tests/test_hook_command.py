"""Exercise the checked-in hook command and JSON boundary without an LLM."""

import json
import re
import shutil
import subprocess
import sys

from .support import GuardCase
from .test_boundaries import ENTRYPOINT


class HookCommandTests(GuardCase):
    def setUp(self):
        super().setUp()
        shutil.copytree(ENTRYPOINT.parent, self.root / ".codex",
                        ignore=shutil.ignore_patterns(".read-guard", "__pycache__"))
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       capture_output=True, timeout=5)
        self.config = json.loads((self.root / ".codex/hooks.json").read_text())

    def run_hook(self, event, cwd=None):
        group = self.config["hooks"][event["hook_event_name"]][0]
        if "matcher" in group:
            self.assertRegex(event["tool_name"], group["matcher"])
        command = group["hooks"][0]["command"]
        return subprocess.run(command, shell=True, cwd=cwd or self.root,
                              input=json.dumps(event), text=True, capture_output=True,
                              timeout=5)

    def read_via_hook(self, name):
        command = f"python3 .codex/guard.py read {name}"
        before = self.run_hook(self.event("PreToolUse", command=command))
        self.assertEqual(before.returncode, 0, before.stderr)
        output = subprocess.run([sys.executable, ".codex/guard.py", "read", name],
                                cwd=self.root, text=True, capture_output=True, timeout=5)
        self.assertEqual(output.returncode, 0, output.stderr)
        after = self.run_hook(self.event("PostToolUse", command=command,
                                         tool_response={"output": output.stdout, "exit_code": 0}))
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertIn(output.stdout, json.loads(after.stdout)["hookSpecificOutput"]["additionalContext"])

    def check_via_hook(self, name):
        command = f"python3 .codex/guard.py check {name}"
        before = self.run_hook(self.event("PreToolUse", command=command))
        self.assertEqual(before.returncode, 0, before.stderr)
        output = subprocess.run([sys.executable, ".codex/guard.py", "check", name],
                                cwd=self.root, text=True, capture_output=True, timeout=5)
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertIn("Await READ_GUARD_STATUS", output.stdout)
        after = self.run_hook(self.event("PostToolUse", command=command,
                                         tool_response={"output": output.stdout, "exit_code": 0}))
        self.assertEqual(after.returncode, 0, after.stderr)
        content = json.loads(after.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn((self.root / name).read_text(), content)
        return json.loads(content.splitlines()[0].removeprefix("READ_GUARD_STATUS "))

    def test_native_command_check_reuses_reads_until_content_or_context_changes(self):
        self.assertEqual(self.check_via_hook("source.py")["status"], "unread")
        self.read_via_hook("source.py")
        result = self.run_hook(self.event("UserPromptSubmit"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.check_via_hook("source.py")["status"], "complete")
        self.write("source.py", "different contents\n")
        self.assertEqual(self.check_via_hook("source.py")["status"], "changed")
        self.read_via_hook("source.py")
        self.assertEqual(self.check_via_hook("source.py")["status"], "complete")
        result = self.run_hook(self.event("SessionStart", source="compact"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.check_via_hook("source.py")["status"], "unread")

    def test_end_to_end_deny_read_review_allow_then_deny_reuse(self):
        patch = "*** Begin Patch\n*** Delete File: source.py\n*** End Patch"
        event = self.event("PreToolUse", "apply_patch", patch)
        denied = self.run_hook(event)
        self.assertEqual(denied.returncode, 2)
        self.assertIn("Read ALL", denied.stderr)
        self.assertTrue((self.root / "source.py").exists())
        self.read_via_hook("source.py")
        self.read_via_hook("dependency.py")
        command = ("python3 .codex/guard.py context source.py --related dependency.py "
                   "--reason 'Reviewed the imported dependency and its contract.'")
        before = self.run_hook(self.event("PreToolUse", command=command))
        self.assertEqual(before.returncode, 0, before.stderr)
        output = subprocess.run(command, shell=True, cwd=self.root, text=True,
                                capture_output=True, timeout=5)
        self.assertEqual(output.returncode, 0, output.stderr)
        after = self.run_hook(self.event("PostToolUse", command=command,
                                         tool_response=output.stdout))
        self.assertEqual(after.returncode, 0, after.stderr)
        allowed = self.run_hook(event)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(self.run_hook(event).returncode, 2)

    def test_configured_command_resolves_from_subdirectory(self):
        nested = self.root / "nested"
        nested.mkdir()
        event = self.event("SessionStart", source="startup")
        event["cwd"] = str(nested)
        result = self.run_hook(event, cwd=nested)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Read-before-edit", result.stdout)

    def test_missing_hook_script_returns_blocking_exit_code(self):
        (self.root / ".codex/guard.py").unlink()
        result = self.run_hook(self.event("PreToolUse", command="pwd"))
        self.assertEqual(result.returncode, 2)

    def test_matchers_cover_native_edits_and_shell_calls(self):
        pattern = self.config["hooks"]["PreToolUse"][0]["matcher"]
        for name in ("apply_patch", "Bash", "Edit", "Write"):
            self.assertIsNotNone(re.fullmatch(pattern, name))
        post = self.config["hooks"]["PostToolUse"][0]["hooks"][0]
        self.assertGreaterEqual(post["additionalContextLimit"], 8192)
        self.assertFalse(post.get("async", False))
