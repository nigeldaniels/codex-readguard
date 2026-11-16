"""The guard checks missing code context, not general CLI permissions."""

import shlex
import subprocess
import sys

from read_guard.protocol import Request
from read_guard.shell import check_command
from .support import GuardCase
from .test_boundaries import ENTRYPOINT


class ShellEditTests(GuardCase):
    def pre(self, command, **extra):
        return self.guard.handle(self.event("PreToolUse", command=command, **extra))

    def test_normal_git_and_cli_commands_pass_without_read_evidence(self):
        commands = [
            "git -C /tmp/example status", "git --no-pager diff", "git -c color.ui=false log -1",
            "git add source.py", "git commit -m 'Fix behavior'", "git fetch origin",
            "git push origin topic", "git switch topic", "git restore --staged source.py",
            "gh pr view 82", "gh api repos/example/project", "docker compose ps",
            "npm test", "uv run pytest", "python3 -c 'print(1 + 2)'", "curl -I https://example.com",
            "printf '%s' hello | wc -c", "git status\ngit diff --stat", "ls $(pwd)",
            "FOO='a b' env BAR=yes python3 -c 'print(1)'", "rg '>' source.py",
            "rg 'write_text|open' .", "pytest 2>&1 | tail -n 20", "pytest >/tmp/guard-test.log",
            "pytest >.test-artifacts/results.log", "sh -c 'git status'",
            "python3 -c 'from pathlib import Path; print(Path(\"source.py\").read_text())'",
            "python3 -c 'print(\"a\".replace(\"a\", \"b\"))'", "rg -o result source.py",
            "ssh -o BatchMode=yes host hostname", "git commit -m '--output=example'",
            "python3 - <<'PY'\nprint('hello')\nPY",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.pre(command), {})

    def test_cli_edits_require_the_same_read_and_context_as_patches(self):
        commands = [
            "sed -i s/result/answer/g source.py", "sed -i -e s/result/answer/g source.py",
            "perl -pi -e s/result/answer/g source.py", "printf changed > source.py",
            "cat dependency.py | tee source.py", "rm source.py", "touch source.py",
            "cp dependency.py source.py", "git checkout -- source.py", "git restore source.py",
            "git diff --output=source.py", "sort --output=source.py dependency.py",
            "python3 -c 'open(\"source.py\", \"w\").write(\"changed\")'",
            "python3 -c 'from pathlib import Path; Path(\"source.py\").write_text(\"changed\")'",
            "python3 - <<'PY'\nfrom pathlib import Path\nPath('source.py').write_text('changed')\nPY",
            "sh -c 'sed -i s/result/answer/g source.py'", "uniq dependency.py source.py",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.guard.handle(self.event("SessionStart", source="startup"))
                with self.assertRaisesRegex(ValueError, "Read ALL"):
                    self.pre(command)
                self.prepare()
                self.assertEqual(self.pre(command), {})
                with self.assertRaisesRegex(ValueError, "Declare related"):
                    self.pre(command)

    def test_read_target_without_related_review_still_blocks_cli_edit(self):
        self.read_all("source.py")
        with self.assertRaisesRegex(ValueError, "Declare related"):
            self.pre("sed -i s/result/answer/g source.py")
        with self.assertRaisesRegex(ValueError, "Read ALL.*dependency"):
            self.context()

    def test_multi_target_cli_edits_check_every_file_before_consuming(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "Declare related"):
            self.pre("sed -i s/result/answer/g source.py dependency.py")
        self.assertEqual(self.pre("sed -i s/result/answer/g source.py"), {})

    def test_opaque_writers_request_declared_targets_instead_of_a_banned_command(self):
        for command in ("git apply change.patch", "black source.py", "ruff check --fix .",
                        "python3 -c 'open(filename, \"w\").write(\"changed\")'"):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "run --writes"):
                self.pre(command)

    def test_declared_script_edits_require_reads_then_run_unchanged(self):
        script = "from pathlib import Path; Path('source.py').write_text('changed\\n')"
        arguments = [sys.executable, str(ENTRYPOINT), "run", "--writes", "source.py",
                     "--", sys.executable, "-c", script]
        # The fixture's script path is used only for hook recognition. The actual
        # wrapper has no model/session state; the native pre-tool hook is the gate.
        command = shlex.join(["python3", ".codex/guard.py", *arguments[2:]])
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.pre(command)
        self.prepare()
        self.pre(command)
        result = subprocess.run(arguments, cwd=self.root, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "source.py").read_text(), "changed\n")

    def test_wrapper_preserves_exit_status_and_requires_an_explicit_command(self):
        args = [sys.executable, str(ENTRYPOINT), "run", "--writes", "source.py",
                "--", sys.executable, "-c", "raise SystemExit(7)"]
        result = subprocess.run(args, cwd=self.root, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 7)
        with self.assertRaises(ValueError):
            Request.parse(["run", "--writes", "source.py", "--"])

    def test_search_or_piped_reader_does_not_acquire_read_credit(self):
        command = "python3 .codex/guard.py read source.py | head -n 1"
        self.assertIsNone(check_command(self.root, self.root, command))
        self.pre(command)
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.pre("sed -i s/result/answer/g source.py")

    def test_cd_prefix_resolves_edit_paths(self):
        self.write("nested/source.py", "original\n")
        with self.assertRaisesRegex(ValueError, "Read ALL.*nested/source.py"):
            self.pre("cd nested && sed -i s/original/changed/ source.py")

    def test_stale_read_is_rejected_for_cli_edits(self):
        self.prepare()
        self.write("source.py", "different content\n")
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.pre("sed -i s/different/new/ source.py")

    def test_git_directory_option_applies_to_file_restore(self):
        self.write("nested/source.py", "original\n")
        with self.assertRaisesRegex(ValueError, "Read ALL.*nested/source.py"):
            self.pre("git -C nested restore source.py")

    def test_chained_declared_edits_cannot_skip_the_read_check(self):
        command = "git status && python3 .codex/guard.py run --writes source.py -- sed -i s/a/b/ source.py"
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.pre(command)
        self.prepare()
        self.assertEqual(self.pre(command), {})
