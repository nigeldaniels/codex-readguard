"""Patch, shell, and external hook-protocol failure boundaries."""

import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path

from read_guard.files import MAX_FILE_BYTES, Snapshot
from read_guard.protocol import Request, response_contains
from read_guard.shell import check_command
from .support import GuardCase


ENTRYPOINT = Path(__file__).resolve().parents[1] / "guard.py"


class BoundaryTests(GuardCase):
    def test_multi_file_patch_is_all_or_nothing(self):
        self.prepare()
        body = "*** Delete File: source.py\n*** Delete File: dependency.py"
        with self.assertRaisesRegex(ValueError, "Declare related"):
            self.patch(body)
        # A denied batch must not consume the first file's valid evidence.
        self.patch()

    def test_add_requires_context_and_overwrite_requires_read(self):
        body = "*** Add File: new.py\n+new = True"
        with self.assertRaisesRegex(ValueError, "Declare related"):
            self.patch(body)
        self.read_all("dependency.py")
        self.context("new.py")
        self.patch(body)
        self.context("new.py")
        self.write("new.py", "already exists\n")
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.patch(body)

    def test_move_requires_both_source_and_destination_context(self):
        self.prepare()
        body = "*** Update File: source.py\n*** Move to: renamed.py\n@@\n-value = result\n+value = 43"
        with self.assertRaisesRegex(ValueError, "Declare related.*renamed"):
            self.patch(body)
        self.context("renamed.py")
        self.patch(body)

    def test_related_files_cannot_be_the_target_itself(self):
        self.read_all("source.py")
        with self.assertRaisesRegex(ValueError, "other than the target"):
            self.context(related=("source.py",))

    def test_no_related_exception_or_empty_explanation(self):
        self.prepare()
        for related, reason in (((), "Reviewed all the necessary dependencies"),
                                (("dependency.py",), "")):
            with self.subTest(related=related, reason=reason):
                with self.assertRaises((ValueError, SystemExit)), contextlib.redirect_stderr(io.StringIO()):
                    self.call(Request("context", "source.py", related=related, reason=reason))

    def test_subdirectory_invocation_and_absolute_patch_paths(self):
        self.prepare()
        (self.root / "nested").mkdir()
        event = self.event("PreToolUse", "apply_patch",
                           f"*** Begin Patch\n*** Delete File: {self.root / 'source.py'}\n*** End Patch")
        event["cwd"] = str(self.root / "nested")
        self.guard.handle(event)

    def test_outside_symlink_metadata_and_parent_paths_are_denied(self):
        (self.root / "linked.py").symlink_to(self.root / "source.py")
        for name in ("/etc/passwd", "linked.py", "nested/../source.py",
                     ".git/config", ".codex/.read-guard/evidence.sqlite3"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.patch(f"*** Delete File: {name}")

    def test_unknown_and_malformed_patch_shapes_are_denied(self):
        self.prepare()
        for body in ("", "*** Unknown File: source.py", "*** Move to: source.py",
                     "*** Delete File: source.py\n*** Delete File: source.py"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.patch(body)

    def test_binary_oversized_and_invalid_utf8_reads_are_denied(self):
        for data in (b"\x00binary", b"\xff", b"x" * (MAX_FILE_BYTES + 1)):
            with self.subTest(length=len(data)):
                (self.root / "source.py").write_bytes(data)
                with self.assertRaises((ValueError, UnicodeError)):
                    Snapshot.read(self.root, "source.py")

    def test_inspection_and_named_test_runners_are_allowed(self):
        for command in ("rg --files", "rg -n value source.py", "git status --short",
                        "git diff --stat", "cat source.py | head -n 3", "sed -n '1,20p' source.py",
                        "python3 -m unittest discover -s .codex/tests -t .codex -v",
                        ".venv/bin/python -m pytest tests/unit", "cargo check"):
            with self.subTest(command=command):
                self.assertIsNone(check_command(self.root, self.root, command))

    def test_tty_does_not_block_an_ordinary_cli_command(self):
        event = self.event("PreToolUse", command="cat source.py")
        event["tool_input"]["tty"] = True
        self.assertEqual(self.guard.handle(event), {})

    def test_response_formats_and_corrupt_state_fail_closed(self):
        for response in ("exact receipt", {"output": "exact receipt"},
                         {"content": [{"type": "text", "text": "exact receipt"}]}):
            self.assertTrue(response_contains(response, "exact receipt"))
        self.guard.state_path.parent.mkdir(parents=True)
        self.guard.state_path.write_bytes(b"corrupt sqlite database")
        with self.assertRaises(Exception):
            self.patch()

    def test_hook_entrypoint_denies_invalid_events_instead_of_crashing_open(self):
        for value in ("invalid json", json.dumps({"hook_event_name": "PreToolUse"})):
            result = subprocess.run([sys.executable, str(ENTRYPOINT), "hook"],
                                    input=value, text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertIn("Read-before-edit guard", result.stderr)

    def test_cli_reader_returns_complete_bounded_receipt(self):
        result = subprocess.run([sys.executable, str(ENTRYPOINT), "read", ".codex/guard.py"],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(ENTRYPOINT.read_text(), result.stdout)
        self.assertTrue(result.stdout.endswith("END_READ_GUARD\n"))
        self.assertLessEqual(len(result.stdout.encode()), 8192)
