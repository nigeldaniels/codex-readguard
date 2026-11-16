"""Heredoc text is data; surrounding writes and executable stdin still count."""

import subprocess

from read_guard.protocol import Request, render_request
from read_guard.shell import check_command
from .support import GuardCase


class HeredocTests(GuardCase):
    def inspect(self, command):
        # Syntax-check only: never execute the command being used as a fixture.
        syntax = subprocess.run(["bash", "-n"], input=command, text=True,
                                capture_output=True, timeout=5)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        return check_command(self.root, self.root, command)

    def test_issue_markdown_in_literal_heredoc_is_not_shell_syntax(self):
        body = "It's documentation.\n>\n<!-- issue marker -->\nsed -i s/a/b/ source.py\n"
        for delimiter in ("'EOF'", '"EOF"', "E'OF'", "\\EOF"):
            with self.subTest(delimiter=delimiter):
                command = f"cat > /tmp/readguard-example.md <<{delimiter}\n{body}EOF\n"
                self.assertIsNone(self.inspect(command))

    def test_output_before_after_and_in_pipeline_remains_a_write(self):
        for header in ("cat > source.py <<'EOF'", "cat <<'EOF' > source.py",
                       "cat <<'EOF' | tee source.py", "cat <<'EOF' >> source.py",
                       "cat <<'EOF' 1>source.py", "cat <<'EOF' &>source.py"):
            with self.subTest(header=header):
                command = header + "\n<!-- issue marker -->\nEOF\n"
                self.assertEqual(self.inspect(command).writes, ("source.py",))
                with self.assertRaisesRegex(ValueError, "Read ALL"):
                    self.guard.handle(self.event("PreToolUse", command=command))

    def test_project_heredoc_is_allowed_after_full_read_and_related_review(self):
        command = "cat <<'EOF' > source.py\n<!-- issue marker -->\nEOF\n"
        self.prepare()
        self.assertEqual(self.guard.handle(self.event("PreToolUse", command=command)), {})

    def test_multiple_heredocs_preserve_all_commands_and_output_paths(self):
        command = ("cat <<'ONE' > source.py; cat <<'TWO' > dependency.py\n"
                   "<!-- first -->\nONE\n<!-- second -->\nTWO\n")
        self.assertEqual(self.inspect(command).writes, ("dependency.py", "source.py"))

    def test_tab_stripping_and_punctuation_in_delimiters(self):
        command = "cat <<-'END.MD' > source.py\n\t<!-- marker -->\n\tEND.MD\n"
        self.assertEqual(self.inspect(command).writes, ("source.py",))

    def test_terminator_requires_exact_match(self):
        command = "cat <<'EOF' > /tmp/example.md\nEOF \nrm source.py\nEOF\n"
        self.assertIsNone(self.inspect(command))

    def test_commands_following_the_body_remain_visible(self):
        command = "cat <<'EOF' > /tmp/example.md\n<!-- marker -->\nEOF\nrm source.py\n"
        self.assertEqual(self.inspect(command).writes, ("source.py",))

    def test_python_stdin_is_parsed_as_python_not_shell(self):
        command = ("python3 - <<'PY'\nfrom pathlib import Path\n"
                   "text = '''it's a test\n<!-- marker -->\n'''\n"
                   "Path('/tmp/readguard-example.md').write_text(text)\nPY\n")
        self.assertIsNone(self.inspect(command))
        command = command.replace("/tmp/readguard-example.md", "source.py")
        self.assertEqual(self.inspect(command).writes, ("source.py",))

    def test_python_stdin_uses_command_working_directory(self):
        self.write("nested/source.py", "before\n")
        command = ("cd nested; python3 - <<'PY'\nfrom pathlib import Path\n"
                   "Path('source.py').write_text('changed')\nPY\n")
        self.assertEqual(self.inspect(command).writes, ("nested/source.py",))

    def test_shell_stdin_keeps_its_edit_targets(self):
        command = "bash <<'SH'\nsed -i s/result/answer/g source.py\nSH\n"
        self.assertEqual(self.inspect(command).writes, ("source.py",))

    def test_interpreter_arguments_do_not_hide_executable_stdin(self):
        python = "from pathlib import Path\nPath('source.py').write_text('changed')\n"
        for header in ("python3 - argument", "python3 -X utf8 - argument", "python3 - argument -c"):
            with self.subTest(header=header):
                self.assertEqual(self.inspect(header + " <<'EOF'\n" + python + "EOF\n").writes,
                                 ("source.py",))
        for header in ("bash -s -- argument", "bash -es argument"):
            with self.subTest(header=header):
                command = header + " <<'EOF'\nprintf changed > source.py\nEOF\n"
                self.assertEqual(self.inspect(command).writes, ("source.py",))

    def test_explicit_script_receives_heredoc_as_data(self):
        command = "python3 consumer.py <<'EOF'\nPath('source.py').write_text('example')\nEOF\n"
        self.assertIsNone(self.inspect(command))

    def test_heredoc_like_quoted_arguments_and_comments_are_not_headers(self):
        for command in ("printf '%s' '<<EOF\nrm source.py\nEOF'",
                        "printf '%s' '>' # <<EOF >\n", "rg '<<' source.py"):
            with self.subTest(command=command):
                self.assertIsNone(self.inspect(command))

    def test_reader_with_redirected_input_cannot_acquire_read_credit(self):
        command = "python3 .codex/guard.py read source.py <<'EOF'\nunused\nEOF\n"
        self.assertIsNone(self.inspect(command))
        self.guard.handle(self.event("PostToolUse", command=command,
                                    tool_response=render_request(self.root, Request("read", "source.py"))))
        with self.assertRaisesRegex(ValueError, "Read ALL"):
            self.patch()

    def test_unquoted_heredoc_substitutions_require_declared_writes(self):
        command = "cat <<EOF > /tmp/example.md\n$(printf changed > source.py)\nEOF\n"
        with self.assertRaisesRegex(ValueError, "run --writes"):
            self.inspect(command)
        literal = command.replace("<<EOF", "<<'EOF'")
        self.assertIsNone(self.inspect(literal))

    def test_declared_wrapper_keeps_stdin_and_all_output_targets(self):
        command = ("python3 .codex/guard.py run --writes source.py -- python3 - <<'PY'\n"
                   "from pathlib import Path\nname = 'source.py'\nPath(name).write_text('changed')\nPY\n")
        self.assertEqual(self.inspect(command).writes, ("source.py",))
        redirected = command.replace(" <<'PY'", " > dependency.py <<'PY'")
        self.assertEqual(self.inspect(redirected).writes, ("dependency.py", "source.py"))

    def test_bad_redirections_report_a_diagnostic_instead_of_crashing(self):
        for command in ("printf hello >\n", "printf hello >", "printf hello > | cat",
                        "cat <<\n", "cat <<'EOF'\nno terminator\n"):
            with self.subTest(command=command):
                with self.assertRaisesRegex(ValueError, "redirection|heredoc"):
                    check_command(self.root, self.root, command)
