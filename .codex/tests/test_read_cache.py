"""Reuse only complete, current reads within the same surviving context."""

import json
import os

from read_guard.protocol import Request, render_request
from .support import GuardCase


class ReadCacheTests(GuardCase):
    def check(self, name="source.py"):
        result = self.call(Request("check", name))
        content = result["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn((self.root / name).read_text() or "END_READ_GUARD", content)
        return json.loads(content.splitlines()[0].removeprefix("READ_GUARD_STATUS "))

    def test_check_does_not_establish_reads_or_related_review(self):
        self.assertEqual(self.check()["status"], "unread")
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.patch()
        self.read_all("source.py")
        self.assertEqual(self.check()["status"], "complete")
        self.read_all("dependency.py")
        with self.assertRaisesRegex(ValueError, "Declare related"):
            self.patch()

    def test_prompt_keeps_reads_but_requires_fresh_related_review(self):
        self.prepare()
        self.guard.handle(self.event("UserPromptSubmit"))
        for name in ("source.py", "dependency.py"):
            self.assertEqual(self.check(name)["status"], "complete")
        with self.assertRaisesRegex(ValueError, "Declare related"):
            self.patch()
        self.context()
        self.patch()

    def test_failed_or_no_op_edit_can_retry_without_rereading(self):
        self.prepare()
        # An approved tool may fail or do nothing. Only its declaration is
        # spent; the unchanged content still has a complete verified read.
        self.patch()
        self.assertEqual(self.check()["status"], "complete")
        with self.assertRaisesRegex(ValueError, "Declare related"):
            self.patch()
        self.context()
        self.patch()

    def test_changed_target_requires_read_even_with_same_size_and_mtime(self):
        self.prepare()
        self.patch()
        path = self.root / "source.py"
        before = path.stat()
        self.write("source.py", path.read_text().replace("value", "other"))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        status = self.check()
        self.assertEqual((status["status"], status["next_offset"]), ("changed", 0))
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.context()
        self.read_all("source.py")
        self.context()
        self.patch()

    def test_changed_related_file_is_not_reused_across_prompts(self):
        self.prepare()
        self.guard.handle(self.event("UserPromptSubmit"))
        self.write("dependency.py", "result = 99\n")
        self.assertEqual(self.check()["status"], "complete")
        self.assertEqual(self.check("dependency.py")["status"], "changed")
        with self.assertRaisesRegex(ValueError, "Read ALL.*dependency"):
            self.context()
        self.read_all("dependency.py")
        self.context()
        self.patch()

    def test_partial_read_resumes_at_first_gap_across_prompts(self):
        self.write("source.py", "x" * 9000)
        self.call(Request("read", "source.py", 6000))
        self.assertEqual(self.check()["next_offset"], 0)
        self.call(Request("read", "source.py", 0))
        self.guard.handle(self.event("UserPromptSubmit"))
        status = self.check()
        self.assertEqual((status["status"], status["next_offset"]), ("partial", 3000))
        self.call(Request("read", "source.py", status["next_offset"]))
        self.assertEqual(self.check()["status"], "complete")
        self.assertIsNone(self.check()["next_offset"])

    def test_empty_file_requires_a_read_then_can_be_reused(self):
        self.write("source.py", "")
        self.assertEqual(self.check()["status"], "unread")
        self.read_all("source.py")
        self.assertEqual(self.check()["status"], "complete")

    def test_status_is_reset_on_compaction_and_isolated_by_session_and_agent(self):
        self.prepare()
        self.session = "other-session"
        self.assertEqual(self.check()["status"], "unread")
        self.session = "session-one"
        request = Request("check", "source.py")
        result = self.guard.handle(self.event(
            "PostToolUse", command="python3 .codex/guard.py check source.py",
            agent_id="another-agent", tool_response=render_request(self.root, request)))
        self.assertIn('"status": "unread"', result["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.check()["status"], "complete")
        self.guard.handle(self.event("SessionStart", source="compact"))
        self.assertEqual(self.check()["status"], "unread")

    def test_failed_truncated_or_stale_check_response_cannot_claim_cache_hit(self):
        self.read_all("source.py")
        request = Request("check", "source.py")
        for transform in (lambda value: {**value, "exit_code": 1},
                          lambda value: {**value, "output": value["output"][:-1]}):
            result = self.call(request, transform)
            self.assertIn("NOT credited", json.dumps(result))
            self.assertNotIn("READ_GUARD_STATUS", json.dumps(result))
        receipt = render_request(self.root, request)
        self.write("source.py", "changed between command and hook\n")
        result = self.guard.handle(self.event(
            "PostToolUse", command="python3 .codex/guard.py check source.py",
            tool_response=receipt))
        self.assertIn("NOT credited", json.dumps(result))
        self.assertNotIn("READ_GUARD_STATUS", json.dumps(result))

    def test_standalone_check_receipt_cannot_claim_a_cache_hit(self):
        self.read_all("source.py")
        receipt = render_request(self.root, Request("check", "source.py"))
        self.assertIn("Await READ_GUARD_STATUS from the hook", receipt)
        self.assertIn("this receipt grants no read credit", receipt)
        self.assertNotIn('"status": "complete"', receipt)
