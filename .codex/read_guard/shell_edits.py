"""Target extraction for common editing commands, with explicit uncertainty."""

import glob
import os
import re
from pathlib import Path

from .files import MAX_TARGETS, project_path
from .protocol import DECLARE_WRITES
from .python_edits import python_targets


# Never exclude `build`: this project keeps maintained source in that directory.
GENERATED_PARTS = {".git", ".venv", "venv", "node_modules", "target", "__pycache__",
                   ".pytest_cache", ".test-artifacts"}


def command_targets(words: list[str], cwd: Path) -> tuple[list[str], Path]:
    words = unwrap_environment(words)
    if not words:
        return [], cwd
    program, args = Path(words[0]).name, words[1:]
    if program == "cd" and len(args) == 1:
        return [], (cwd / args[0]).resolve()
    if program == "git":
        return git_targets(args, cwd), cwd
    if program in {"sed", "perl"} and any(in_place(arg) for arg in args):
        return replacement_targets(args), cwd
    if program in {"tee", "touch", "rm", "unlink"}:
        return operands(args), cwd
    if program == "truncate":
        return operands(args, {"-s", "--size", "-r", "--reference"}), cwd
    if program == "uniq":
        paths = operands(args)
        return paths[-1:] if len(paths) == 2 else [], cwd
    if program in {"cp", "mv"}:
        paths = operands(args)
        if len(paths) != 2 or (cwd / paths[-1]).is_dir():
            raise ValueError(DECLARE_WRITES)
        return paths if program == "mv" else paths[-1:], cwd
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?", program) and "-c" in args:
        index = args.index("-c") + 1
        return python_targets(args[index]) if index < len(args) else [], cwd
    if program in {"black", "gofmt", "clang-format", "prettier", "ruff"} and formatter_writes(program, args):
        raise ValueError(DECLARE_WRITES)
    return output_option(args) if program in {"sort", "curl", "wget"} else [], cwd


def repository_targets(root: Path, cwd: Path, names: list[str]) -> list[str]:
    targets = []
    for name in names:
        if not name or name == "-" or name.startswith("/dev/"):
            continue
        if "$" in name or "`" in name:
            raise ValueError(DECLARE_WRITES)
        path = Path(os.path.abspath(cwd / name))
        if not path.is_relative_to(root):
            continue
        if GENERATED_PARTS.intersection(path.relative_to(root).parts):
            continue
        # Bound glob expansion; do not recursively scan directories on every call.
        matches = glob.iglob(str(path)) if glob.has_magic(str(path)) else iter([str(path)])
        for match in matches:
            candidate = project_path(root, match)
            if candidate.is_dir():
                raise ValueError(DECLARE_WRITES)
            targets.append(str(candidate.relative_to(root)))
            if len(targets) > MAX_TARGETS:
                raise ValueError(DECLARE_WRITES)
    return targets


def git_targets(args: list[str], cwd: Path) -> list[str]:
    # Global Git options must not turn harmless commands into false denials.
    index = 0
    while index < len(args) and args[index].startswith("-"):
        if args[index] in {"-C", "--work-tree"} and index + 1 < len(args):
            cwd = (cwd / args[index + 1]).resolve()
        elif args[index].startswith("-C") and len(args[index]) > 2:
            cwd = (cwd / args[index][2:]).resolve()
        index += 2 if args[index] in {"-C", "-c", "--git-dir", "--work-tree"} else 1
    if index >= len(args):
        return []
    command, rest = args[index], args[index + 1:]
    if command in {"apply", "am"} and not set(rest).intersection({"--check", "--stat", "--numstat", "--summary"}):
        raise ValueError(DECLARE_WRITES)
    if command == "checkout" and "--" in rest:
        return [str(cwd / name) for name in rest[rest.index("--") + 1:]]
    if command == "restore" and ("--staged" not in rest or "--worktree" in rest):
        return [str(cwd / name) for name in operands(rest, {"-s", "--source"})]
    return output_option(rest) if command in {"diff", "log", "show"} else []


def replacement_targets(args: list[str]) -> list[str]:
    has_script_option = any(arg in {"-e", "--expression", "-f", "--file"}
                            or arg.startswith(("-e", "--expression=", "--file=")) for arg in args)
    paths = operands(args, {"-e", "--expression", "-f", "--file"})
    if not has_script_option:
        paths = paths[1:]
    if not paths:
        raise ValueError(DECLARE_WRITES)
    return paths


def operands(args: list[str], value_options: set[str] | None = None) -> list[str]:
    result, skip, literal = [], False, False
    for arg in args:
        if skip:
            skip = False
        elif arg == "--" and not literal:
            literal = True
        elif not literal and value_options and arg in value_options:
            skip = True
        elif literal or not arg.startswith("-"):
            result.append(arg)
    return result


def unwrap_environment(words: list[str]) -> list[str]:
    words = list(words)
    if words and Path(words[0]).name == "env":
        words = words[1:]
    while words and (re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", words[0]) or words[0] == "--"):
        words = words[1:]
    return words


def output_option(args: list[str]) -> list[str]:
    for index, arg in enumerate(args):
        if arg.startswith("--output="):
            return [arg.split("=", 1)[1]]
        if arg in {"--output", "-o"} and index + 1 < len(args):
            return [args[index + 1]]
    return []


def in_place(arg: str) -> bool:
    return arg.startswith("--in-place") or bool(re.match(r"^-[A-Za-z]*i", arg))


def formatter_writes(program: str, args: list[str]) -> bool:
    if program == "black":
        return not set(args).intersection({"--check", "--diff", "--help", "--version"})
    return any(arg in {"-w", "-i", "--write", "--fix", "--fix-only"} for arg in args)
