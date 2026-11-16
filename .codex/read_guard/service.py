"""Orchestrate hook events, complete-read evidence, and related-file review."""

import hashlib
from pathlib import Path

from .files import MAX_TARGETS, ReadVersion, Snapshot, project_path
from .patches import patch_targets
from .protocol import (check_receipt, context_paths, read_receipt, render_request,
                       response_contains, status_receipt)
from .shell import check_command
from .state import Evidence


GUIDANCE = (
    "Read-before-edit is enabled. Before repeating a read, use python3 .codex/guard.py "
    "check PATH. Only a hook READ_GUARD_STATUS with status complete permits reuse. "
    "Otherwise read every target and all related files completely using python3 "
    ".codex/guard.py read PATH --offset NEXT_OFFSET; follow next_offset until null. "
    "If no hook status arrives, start reading at offset 0. "
    "Use one standalone exec_command per read, max_output_tokens >= 10000, and show "
    "the complete result to the model. Then declare each target with python3 "
    ".codex/guard.py context PATH --related FILE ... --reason 'Explain callers, "
    "dependencies, tests and configuration reviewed'. Normal Git and CLI commands "
    "are allowed. Patches and recognized CLI edits require the same read evidence. "
    "For opaque CLI edits use python3 .codex/guard.py run --writes FILE ... -- COMMAND. "
    "Unchanged read versions survive ordinary prompts and edit approvals. "
    "Related-file declarations must be renewed after each prompt or edit approval. "
    "Session starts, resumes, and compaction reset all evidence. Read .codex/README.md for details."
)


class Guard:
    def __init__(self, root: Path, state_path: Path | None = None):
        self.root = root.resolve()
        self.state_path = state_path or self.root / ".codex/.read-guard/evidence.sqlite3"

    def handle(self, event: dict) -> dict:
        if not isinstance(event, dict):
            raise ValueError("Expected a hook event object")
        session = event.get("session_id")
        name = event.get("hook_event_name")
        if not isinstance(session, str) or not session:
            raise ValueError("Hook event is missing session_id")
        # Transcripts distinguish child contexts when Codex supplies them, even
        # when the runtime reuses the parent's session_id for subagents.
        identity = hashlib.sha256((session + "\0" + str(
            event.get("transcript_path") or "") + "\0" + str(
            event.get("agent_id") or "")).encode()).hexdigest()
        with Evidence(self.state_path, identity) as evidence:
            if name in {"SessionStart", "SessionEnd"}:
                evidence.reset()
                return {} if name == "SessionEnd" else context_message(name, GUIDANCE)
            if name == "UserPromptSubmit":
                evidence.begin_prompt()
                return context_message(name, GUIDANCE)
            if name not in {"PreToolUse", "PostToolUse"}:
                raise ValueError(f"Unsupported hook event: {name}")
            return self.tool_event(event, evidence)

    def tool_event(self, event: dict, evidence: Evidence) -> dict:
        tool = event.get("tool_name")
        inputs = event.get("tool_input")
        if not isinstance(inputs, dict):
            raise ValueError("Expected tool_input object")
        cwd = Path(inputs.get("workdir") or inputs.get("cwd") or event["cwd"]).resolve()
        command = inputs.get("command", inputs.get("cmd"))
        if tool in {"apply_patch", "Edit", "Write"}:
            if event["hook_event_name"] == "PreToolUse":
                self.check_patch(cwd, command, evidence)
            return {}
        if tool != "Bash":
            raise ValueError(f"Unsupported edit tool: {tool}; use apply_patch")
        request = check_command(self.root, cwd, command)
        if request is None:
            return {}
        if request.action in {"run", "edit"}:
            if event["hook_event_name"] == "PreToolUse":
                self.check_targets(list(request.writes), evidence)
            return {}
        if event["hook_event_name"] == "PreToolUse":
            if request.action == "context":
                self.review_context(request, evidence)
            return {}
        return self.record_inspection(request, event.get("tool_response"), evidence)

    def record_inspection(self, request, response: object, evidence: Evidence) -> dict:
        snapshot = None
        if request.action in {"read", "check"}:
            snapshot = Snapshot.read(self.root, request.path)
            receipt = (read_receipt(snapshot, request.offset) if request.action == "read"
                       else check_receipt(snapshot.version()))
        else:
            receipt = render_request(self.root, request)
        if not response_contains(response, receipt):
            return context_message("PostToolUse", "Read/check/context was NOT credited: output "
                                   "was incomplete, failed, changed, or had an unsupported "
                                   "shape. Retry alone with max_output_tokens >= 10000.")
        if request.action == "check":
            version = snapshot.version()
            status, offset = evidence.read_progress(version)
            return context_message("PostToolUse", status_receipt(version, status, offset))
        if request.action == "read":
            end, _ = snapshot.chunk(request.offset)
            evidence.record(snapshot, request.offset, end)
            # Deliver the verified, bounded chunk independently as model context.
            # Code-mode callers may otherwise discard an intact tool response.
            return context_message("PostToolUse", receipt)
        target, related = self.review_context(request, evidence)
        evidence.declare(target, related, request.reason)
        return context_message("PostToolUse", f"Related-file review recorded for {target}.")

    def review_context(self, request, evidence: Evidence) -> tuple[str, dict[str, str]]:
        target, names = context_paths(self.root, request)
        if project_path(self.root, target).exists():
            evidence.require_read(Snapshot.read(self.root, target).version())
        related = {}
        for name in names:
            snapshot = Snapshot.read(self.root, name)
            evidence.require_read(snapshot.version())
            related[name] = snapshot.digest
        return target, related

    def check_patch(self, cwd: Path, patch: str, evidence: Evidence) -> None:
        targets = patch_targets(self.root, cwd, patch)
        self.check_targets(targets, evidence)

    def check_targets(self, targets: list[str], evidence: Evidence) -> None:
        targets = sorted({str(project_path(self.root, path).relative_to(self.root)) for path in targets})
        if not 1 <= len(targets) <= MAX_TARGETS:
            raise ValueError(f"Declare between 1 and {MAX_TARGETS} edit targets")
        snapshots = {}
        for target in targets:
            if project_path(self.root, target).exists():
                self.require_current(target, evidence, snapshots)
            related = evidence.related(target)
            for name, digest in related.items():
                snapshot = self.require_current(name, evidence, snapshots)
                if snapshot.digest != digest:
                    raise ValueError(f"Related file {name} changed; reread it and "
                                     f"declare context again for {target}")
        evidence.consume(targets)

    def require_current(self, name: str, evidence: Evidence,
                        snapshots: dict[str, ReadVersion]) -> ReadVersion:
        # Shared dependencies are hashed only once per multi-file patch. Cache
        # just versions so a large patch never holds all dependency text in RAM.
        if name not in snapshots:
            snapshots[name] = Snapshot.read(self.root, name).version()
        evidence.require_read(snapshots[name])
        return snapshots[name]


def context_message(event: str, message: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": message}}
