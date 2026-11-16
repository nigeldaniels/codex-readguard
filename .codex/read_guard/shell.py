"""Recognize guard receipts and common CLI edits without a command allowlist."""

import re
import shlex
from pathlib import Path

from .protocol import Request
from .python_edits import extract_heredocs
from .shell_edits import command_targets, repository_targets


# Preserve quoting when separating operators: rg '>' must remain a search.
# This covers ordinary shell words, not every Bash construct. Unknown commands
# are allowed and earn no read credit; agents must declare opaque CLI edits.
SHELL_TOKEN = re.compile(
    r'''(?P<operator>[;&|<>()]+|\n)|(?P<word>(?:[^\s;&|<>()'"\\]+|'[^']*'|"(?:\\.|[^"\\])*"|\\.)+)'''
)
SEPARATORS = {";", "&&", "||", "|", "&", "\n"}


def check_command(root: Path, cwd: Path, command: str, depth: int = 0) -> Request | None:
    if not isinstance(command, str) or not command or len(command) > 131072:
        raise ValueError("Missing or oversized shell command")
    request = direct_request(root, cwd, command)
    if request is not None:
        return request
    command, script_targets = extract_heredocs(command)
    targets = repository_targets(root, cwd, script_targets)
    for words, redirects in command_groups(command):
        nested_request = direct_request(root, cwd, shlex.join(words)) if words else None
        if nested_request and nested_request.action == "run":
            targets.extend(nested_request.writes)
        if words and Path(words[0]).name in {"sh", "bash", "zsh"} and "-c" in words and depth < 3:
            index = words.index("-c") + 1
            nested = check_command(root, cwd, words[index], depth + 1) if index < len(words) else None
            if nested:
                targets.extend(nested.writes)
        names, next_cwd = command_targets(words, cwd)
        targets.extend(repository_targets(root, cwd, names + redirects))
        cwd = next_cwd
    return Request("edit", writes=tuple(sorted(set(targets)))) if targets else None


def direct_request(root: Path, cwd: Path, command: str) -> Request | None:
    groups = list(command_groups(command))
    if len(groups) != 1 or groups[0][1]:
        return None
    arguments = groups[0][0]
    if len(arguments) < 3 or not re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(arguments[0]).name):
        return None
    script = Path(arguments[1])
    if (script if script.is_absolute() else cwd / script).resolve() != root / ".codex/guard.py":
        return None
    # Do not credit commands whose arguments the shell would expand differently.
    for match in SHELL_TOKEN.finditer(command):
        raw = match.group()
        if any(char in raw for char in ("$", "`")) and not (raw.startswith("'") and raw.endswith("'")):
            raise ValueError("Use literal guard arguments; declare the actual file paths")
    if arguments[2] not in {"check", "read", "context", "run"}:
        raise ValueError("Hook mode is runtime-only; use check, read, context, or run")
    return Request.parse(arguments[2:])


def command_groups(command: str):
    """Yield literal argv and output paths; fd duplication is not a file write."""
    words, redirects = [], []
    tokens = list(SHELL_TOKEN.finditer(command))
    index = 0
    while index < len(tokens):
        match = tokens[index]
        token = match.group()
        index += 1
        if match.lastgroup == "word":
            words.append(shlex.split(token)[0])
        elif token in SEPARATORS:
            if words or redirects:
                yield words, redirects
            words, redirects = [], []
        elif token in {">", ">>", ">|", "&>", "&>>"} and index < len(tokens):
            redirects.append(shlex.split(tokens[index].group())[0])
            index += 1
        elif token in {"<", ">&", "<&"} and index < len(tokens):
            index += 1
    if words or redirects:
        yield words, redirects
