"""Recognize guard receipts and common CLI edits without a command allowlist."""

import re
from pathlib import Path

from .protocol import DECLARE_WRITES, Request
from .python_edits import python_targets
from .shell_edits import command_targets, repository_targets, unwrap_environment
from .shell_syntax import ShellCommand, ShellParser


PYTHON = re.compile(r"python(?:3(?:\.\d+)?)?")
SHELLS = {"sh", "bash", "zsh"}


def check_command(root: Path, cwd: Path, command: str, depth: int = 0) -> Request | None:
    if not isinstance(command, str) or not command or len(command) > 131072:
        raise ValueError("Missing or oversized shell command")
    groups = ShellParser(command).parse()
    if len(groups) == 1:
        request = direct_request(root, cwd, groups[0])
        if request and not groups[0].redirects:
            return request
    targets = []
    for group in groups:
        declared = direct_request(root, cwd, group)
        if declared and declared.action == "run":
            targets.extend(declared.writes)
            names, next_cwd = [], cwd
        else:
            targets.extend(script_targets(root, cwd, group, depth))
            names, next_cwd = command_targets(group.words, cwd)
        targets.extend(repository_targets(root, cwd, names + group.redirects))
        cwd = next_cwd
    return Request("edit", writes=tuple(sorted(set(targets)))) if targets else None


def direct_request(root: Path, cwd: Path, group: ShellCommand) -> Request | None:
    arguments = group.words
    if len(arguments) < 3 or not PYTHON.fullmatch(Path(arguments[0]).name):
        return None
    script = Path(arguments[1])
    if (script if script.is_absolute() else cwd / script).resolve() != root / ".codex/guard.py":
        return None
    # Redirected/piped readers cannot earn credit. The declared-write wrapper
    # can receive stdin, but its shell redirections still need separate checks.
    if arguments[2] != "run" and group.has_redirection:
        return None
    for raw in group.raw_words:
        if any(char in raw for char in ("$", "`")) and not (raw.startswith("'") and raw.endswith("'")):
            raise ValueError("Use literal guard arguments; declare the actual file paths")
    if arguments[2] not in {"check", "read", "context", "run"}:
        raise ValueError("Hook mode is runtime-only; use check, read, context, or run")
    return Request.parse(arguments[2:])


def script_targets(root: Path, cwd: Path, group: ShellCommand, depth: int) -> list[str]:
    words = unwrap_environment(group.words)
    if not words:
        return []
    program, args = Path(words[0]).name, words[1:]
    targets = []
    mode, inline = interpreter_input(program, args)
    if mode == "inline":
        targets.extend(nested_targets(root, cwd, inline, depth))
    for document in group.heredocs:
        # Unquoted heredocs expand substitutions even for a data consumer such
        # as cat. Require explicit targets instead of silently discarding code.
        if document.expands and ("$(" in document.body or "`" in document.body):
            raise ValueError(DECLARE_WRITES)
    stdin = [document for document in group.heredocs if document.descriptor == "0"]
    if not stdin or mode != "stdin":
        return targets
    body = stdin[-1].body
    if PYTHON.fullmatch(program):
        targets.extend(repository_targets(root, cwd, python_targets(body)))
    elif program in SHELLS:
        targets.extend(nested_targets(root, cwd, body, depth))
    return targets


def interpreter_input(program: str, args: list[str]) -> tuple[str, str]:
    if not PYTHON.fullmatch(program) and program not in SHELLS:
        return "data", ""
    options = iter(args)
    for arg in options:
        if arg in {"-c", "-m"}:
            mode = "inline" if program in SHELLS and arg == "-c" else "data"
            return mode, next(options, "")
        if arg in {"-W", "-X", "-o", "-O", "--rcfile", "--init-file"}:
            next(options, None)
        elif arg == "--":
            return ("stdin" if next(options, "-") == "-" else "data"), ""
        elif arg == "-" or (program in SHELLS and arg.startswith("-")
                            and not arg.startswith("--") and "s" in arg):
            # Arguments after stdin selection belong to the script, not to the
            # interpreter. They must not turn executable stdin into plain data.
            return "stdin", ""
        elif not arg.startswith("-"):
            return "data", ""
    return "stdin", ""


def nested_targets(root: Path, cwd: Path, source: str, depth: int) -> tuple[str, ...]:
    if depth >= 3:
        raise ValueError(DECLARE_WRITES)
    request = check_command(root, cwd, source, depth + 1) if source.strip() else None
    return request.writes if request else ()
