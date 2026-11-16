"""Explicit CLI receipts, independently checked against hook tool output."""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from .files import MAX_RELATED, ReadVersion, Snapshot, project_path


DECLARE_WRITES = (
    "This edit command needs explicit file targets. Read them and their related "
    "files, declare context, then use python3 .codex/guard.py run "
    "--writes FILE ... -- COMMAND ARG ..."
)


@dataclass(frozen=True)
class Request:
    action: str
    path: str = ""
    offset: int = 0
    related: tuple[str, ...] = ()
    reason: str = ""
    writes: tuple[str, ...] = ()
    command: tuple[str, ...] = ()

    @classmethod
    def parse(cls, arguments: list[str]) -> "Request":
        if arguments[:1] == ["run"]:
            return cls.parse_run(arguments[1:])
        parser = argparse.ArgumentParser(description="Read complete files before editing")
        commands = parser.add_subparsers(dest="action", required=True)
        check = commands.add_parser("check", help="Ask the hook whether this version was fully read")
        check.add_argument("path")
        read = commands.add_parser("read")
        read.add_argument("path")
        read.add_argument("--offset", type=int, default=0)
        context = commands.add_parser("context")
        context.add_argument("path")
        context.add_argument("--related", nargs="+", required=True)
        context.add_argument("--reason", required=True)
        # argparse's normal SystemExit(2) is a supported hook denial too.
        args = parser.parse_args(arguments)
        return cls(args.action, args.path, getattr(args, "offset", 0),
                   tuple(getattr(args, "related", ())), getattr(args, "reason", ""))

    @classmethod
    def parse_run(cls, arguments: list[str]) -> "Request":
        if "--" not in arguments:
            raise ValueError(DECLARE_WRITES)
        boundary = arguments.index("--")
        parser = argparse.ArgumentParser(description="Run a CLI edit after reviewing its targets")
        parser.add_argument("--writes", nargs="+", required=True)
        args = parser.parse_args(arguments[:boundary])
        command = tuple(arguments[boundary + 1:])
        if not command:
            raise ValueError("Provide a command after --")
        return cls("run", writes=tuple(args.writes), command=command)


def render_request(root: Path, request: Request) -> str:
    if request.action == "check":
        return check_receipt(Snapshot.read(root, request.path).version())
    if request.action == "read":
        return read_receipt(Snapshot.read(root, request.path), request.offset)
    target, related = context_paths(root, request)
    return bounded_receipt("READ_GUARD_CONTEXT " + json.dumps({
        "target": target, "related": related, "reason": request.reason
    }, ensure_ascii=False) + "\n")


def check_receipt(snapshot: ReadVersion) -> str:
    # The CLI has no trusted session identity. Only the native hook can look up
    # that session's evidence; this receipt must never claim a cache hit itself.
    return bounded_receipt("READ_GUARD_CHECK " + json.dumps({
        "path": snapshot.path, "sha256": snapshot.digest, "total": snapshot.total
    }, ensure_ascii=False) + "\nAwait READ_GUARD_STATUS from the hook. "
        "If absent, read the file normally; this receipt grants no read credit.\n")


def status_receipt(snapshot: ReadVersion, status: str, offset: int | None) -> str:
    instruction = ("Already read completely at this version; no reread needed."
                   if status == "complete" else
                   "Read this path starting at next_offset, then follow each read receipt.")
    return bounded_receipt("READ_GUARD_STATUS " + json.dumps({
        "path": snapshot.path, "sha256": snapshot.digest, "status": status,
        "next_offset": offset
    }, ensure_ascii=False) + "\n" + instruction + "\n")


def read_receipt(snapshot: Snapshot, offset: int) -> str:
    end, content = snapshot.chunk(offset)
    metadata = {"path": snapshot.path, "sha256": snapshot.digest,
                "offset": offset, "end": end, "total": len(snapshot.text),
                "next_offset": end if end < len(snapshot.text) else None}
    return bounded_receipt("READ_GUARD " + json.dumps(metadata, ensure_ascii=False)
                           + "\n" + content + "\nEND_READ_GUARD\n")


def bounded_receipt(text: str) -> str:
    if len(text.encode("utf-8")) > 8192:
        raise ValueError("Read/context receipt is too long; use shorter paths or explanation")
    return text


def context_paths(root: Path, request: Request) -> tuple[str, list[str]]:
    target = str(project_path(root, request.path).relative_to(root))
    related = sorted({str(project_path(root, name).relative_to(root))
                      for name in request.related})
    if not 1 <= len(related) <= MAX_RELATED or target in related:
        raise ValueError(f"Declare 1–{MAX_RELATED} distinct related files other than the target")
    if not 20 <= len(request.reason.strip()) <= 2000:
        raise ValueError("Explain the callers, dependencies, tests/config reviewed (20–2000 characters)")
    return target, related


def response_contains(response: object, receipt: str) -> bool:
    """Only accept a complete receipt in an actual text output field.

    Codex shell output can be a string or an output/content JSON object. Unknown
    shapes get no credit. Checking the entire receipt rejects middle truncation,
    head/tail previews, failures, and a mere filename or checksum in the output.
    """
    if isinstance(response, str):
        return receipt in response
    if not isinstance(response, dict):
        return False
    if response.get("exit_code", 0) not in (None, 0) or response.get("isError"):
        return False
    if isinstance(response.get("output"), str):
        return receipt in response["output"]
    content = response.get("content", [])
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "text"
        and isinstance(block.get("text"), str) and receipt in block["text"]
        for block in content
    )
