"""Transactional, session-scoped evidence; never store source contents."""

import json
import sqlite3
import time
from pathlib import Path

from .files import ReadVersion, Snapshot


MAX_READS = 4096
RETENTION_SECONDS = 7 * 24 * 60 * 60


class Evidence:
    def __init__(self, path: Path, session: str):
        self.path = path
        self.session = session
        self.connection = None

    def __enter__(self) -> "Evidence":
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=2)
        try:
            self.path.chmod(0o600)
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("CREATE TABLE IF NOT EXISTS reads ("
                                    "session TEXT, path TEXT, digest TEXT, total INTEGER, "
                                    "ranges TEXT, updated REAL, PRIMARY KEY(session, path))")
            self.connection.execute("CREATE TABLE IF NOT EXISTS contexts ("
                                    "session TEXT, path TEXT, related TEXT, reason TEXT, "
                                    "updated REAL, PRIMARY KEY(session, path))")
        except Exception:
            self.connection.close()
            raise
        return self

    def __exit__(self, kind, value, traceback):
        try:
            self.connection.rollback() if kind else self.connection.commit()
        finally:
            self.connection.close()

    def reset(self) -> None:
        self.connection.execute("DELETE FROM reads WHERE session = ?", (self.session,))
        self.begin_prompt()

    def begin_prompt(self) -> None:
        # A new request changes what is relevant, not which source versions were
        # delivered. Keep reads but require a fresh related-file declaration.
        self.connection.execute("DELETE FROM contexts WHERE session = ?", (self.session,))
        cutoff = time.time() - RETENTION_SECONDS
        self.connection.execute("DELETE FROM reads WHERE updated < ?", (cutoff,))
        self.connection.execute("DELETE FROM contexts WHERE updated < ?", (cutoff,))

    def record(self, snapshot: Snapshot, start: int, end: int) -> None:
        row = self.connection.execute(
            "SELECT digest, ranges FROM reads WHERE session = ? AND path = ?",
            (self.session, snapshot.path)).fetchone()
        ranges = json.loads(row[1]) if row and row[0] == snapshot.digest else []
        ranges = merge_ranges(ranges + [[start, end]])
        count = self.connection.execute("SELECT COUNT(*) FROM reads WHERE session = ?",
                                        (self.session,)).fetchone()[0]
        if not row and count >= MAX_READS:
            raise ValueError("Session read limit reached; start a new task/session")
        self.connection.execute("INSERT OR REPLACE INTO reads VALUES (?, ?, ?, ?, ?, ?)",
                                (self.session, snapshot.path, snapshot.digest,
                                 len(snapshot.text), json.dumps(ranges), time.time()))

    def require_read(self, snapshot: ReadVersion) -> None:
        status, _ = self.read_progress(snapshot)
        if status != "complete":
            raise ValueError(f"Read ALL of {snapshot.path} at its current version first. "
                             "Use python3 .codex/guard.py check PATH to find the first "
                             "missing offset, then read PATH following next_offset.")

    def read_progress(self, snapshot: ReadVersion) -> tuple[str, int | None]:
        row = self.connection.execute(
            "SELECT digest, total, ranges FROM reads WHERE session = ? AND path = ?",
            (self.session, snapshot.path)).fetchone()
        if not row:
            return "unread", 0
        if row[0] != snapshot.digest or row[1] != snapshot.total:
            return "changed", 0
        ranges = json.loads(row[2])
        if ranges == [[0, snapshot.total]]:
            return "complete", None
        # Coverage is sorted and merged on recording. Resume at the first gap,
        # never at the furthest offset: out-of-order reads may leave a hole.
        offset = 0
        for start, end in ranges:
            if start > offset:
                break
            offset = end
        return "partial", offset

    def declare(self, target: str, related: dict[str, str], reason: str) -> None:
        count = self.connection.execute("SELECT COUNT(*) FROM contexts WHERE session = ?",
                                        (self.session,)).fetchone()[0]
        if count >= MAX_READS:
            raise ValueError("Session context limit reached; start a new task/session")
        self.connection.execute("INSERT OR REPLACE INTO contexts VALUES (?, ?, ?, ?, ?)",
                                (self.session, target, json.dumps(related), reason, time.time()))

    def related(self, target: str) -> dict[str, str]:
        row = self.connection.execute(
            "SELECT related FROM contexts WHERE session = ? AND path = ?",
            (self.session, target)).fetchone()
        if not row:
            raise ValueError(f"Declare related-file context for {target} first: "
                             "python3 .codex/guard.py context PATH --related FILE ... "
                             "--reason 'Explain the dependencies/callers/tests/config reviewed'")
        return json.loads(row[0])

    def consume(self, targets: list[str]) -> None:
        # Consume before execution so two concurrent patches cannot spend the
        # same declaration. Keep read versions: unchanged files can be reused
        # after a failed/no-op edit once their related-file review is renewed.
        for path in targets:
            self.connection.execute("DELETE FROM contexts WHERE session = ? AND path = ?",
                                    (self.session, path))


def merge_ranges(ranges: list[list[int]]) -> list[list[int]]:
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged
