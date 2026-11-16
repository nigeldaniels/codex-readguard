"""Invariants for complete, fresh, model-visible read evidence."""

import concurrent.futures
import json

from read_guard.protocol import Request
from .support import GuardCase


class EvidenceTests(GuardCase):
    def test_edit_without_read_is_denied(self):
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.patch()

    def test_read_without_related_review_is_denied(self):
        self.read_all("source.py")
        with self.assertRaisesRegex(ValueError, "Declare related"):
            self.patch()
        with self.assertRaisesRegex(ValueError, "Read ALL.*dependency"):
            self.context()

    def test_complete_target_and_related_read_allow_exactly_one_patch(self):
        self.prepare()
        self.assertEqual(self.patch(), {})
        with self.assertRaisesRegex(ValueError, "Declare related"):
            self.patch()

    def test_partial_read_cannot_authorize_context(self):
        self.write("source.py", "abcdefgh\n" * 1000)
        self.call(Request("read", "source.py"))
        self.read_all("dependency.py")
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.context()
        self.read_all("source.py")
        self.context()
        self.patch()

    def test_truncation_and_failed_output_receive_no_credit(self):
        for transform in (
            lambda value: {**value, "output": value["output"][:-10]},
            lambda value: {**value, "exit_code": 1},
            lambda value: {**value, "output": value["output"].replace("value =", "…")},
            lambda value: {"unrecognized": value["output"]},
        ):
            with self.subTest(transform=transform):
                self.guard.handle(self.event("SessionStart", source="startup"))
                result = self.call(Request("read", "source.py"), transform)
                self.assertIn("NOT credited", json.dumps(result))
                with self.assertRaisesRegex(ValueError, "Read ALL"):
                    self.patch()

    def test_verified_read_is_also_delivered_as_model_context(self):
        result = self.call(Request("read", "source.py"))
        content = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn((self.root / "source.py").read_text(), content)

    def test_stale_target_and_related_files_require_rereading(self):
        for name in ("source.py", "dependency.py"):
            with self.subTest(name=name):
                self.prepare()
                self.write(name, (self.root / name).read_text() + "# changed\n")
                with self.assertRaisesRegex(ValueError, "Read ALL"):
                    self.patch()
                self.read_all(name)
                if name == "dependency.py":
                    with self.assertRaisesRegex(ValueError, "Related file.*changed"):
                        self.patch()
                self.context()
                self.patch()

    def test_reads_from_different_file_versions_are_not_combined(self):
        self.write("source.py", "a" * 6000)
        self.call(Request("read", "source.py", 0))
        self.write("source.py", "b" + "a" * 5999)
        self.call(Request("read", "source.py", 3000))
        self.read_all("dependency.py")
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.context()

    def test_same_size_change_is_detected(self):
        self.prepare()
        self.write("dependency.py", "result = 99\n")
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.patch()

    def test_empty_unicode_and_no_final_newline_files(self):
        for text in ("", "🦊λ\r\n" * 1100, "no final newline"):
            with self.subTest(text=text[:20]):
                self.write("source.py", text)
                self.prepare()
                self.patch()

    def test_session_and_transcript_isolation(self):
        self.prepare()
        self.session = "other-session"
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.patch()
        self.session = "session-one"
        event = self.event("PreToolUse", "apply_patch",
                           "*** Begin Patch\n*** Delete File: source.py\n*** End Patch",
                           transcript_path="child-transcript")
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.guard.handle(event)
        self.patch()

    def test_session_lifecycle_clears_evidence(self):
        for name, source in (("SessionStart", "startup"), ("SessionStart", "resume"),
                             ("SessionStart", "compact"), ("SessionStart", "clear"),
                             ("SessionStart", "fork"), ("SessionEnd", "")):
            with self.subTest(event=name, source=source):
                self.prepare()
                self.guard.handle(self.event(name, source=source))
                with self.assertRaisesRegex(ValueError, "Read ALL"):
                    self.patch()

    def test_parallel_patches_cannot_reuse_read_evidence(self):
        self.prepare()
        def attempt():
            try:
                self.patch()
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(outcomes), [False, True])

    def test_plain_cat_is_inspection_but_does_not_establish_read_evidence(self):
        self.guard.handle(self.event("PostToolUse", command="cat source.py",
                                    tool_response={"output": (self.root / "source.py").read_text()}))
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.patch()
