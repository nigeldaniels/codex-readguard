"""Bounded text snapshots and repository path validation."""

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path


MAX_FILE_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 3000
MAX_RELATED = 32
MAX_TARGETS = 64


@dataclass(frozen=True)
class Snapshot:
    path: str
    digest: str
    text: str

    def version(self) -> "ReadVersion":
        return ReadVersion(self.path, self.digest, len(self.text))

    @classmethod
    def read(cls, root: Path, name: str) -> "Snapshot":
        path = project_path(root, name)
        # O_NOFOLLOW also closes the final-component symlink race. This is a
        # workflow guard, not an OS sandbox against concurrent malicious renames.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f"Not a regular text file: {name}")
            data = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"File exceeds the {MAX_FILE_BYTES}-byte read limit: {name}")
        if (before.st_mtime_ns, before.st_ctime_ns, before.st_size) != (
            after.st_mtime_ns, after.st_ctime_ns, after.st_size
        ):
            raise ValueError(f"File changed during reading: {name}; retry")
        if b"\x00" in data:
            raise ValueError(f"Binary file cannot establish a text read: {name}")
        return cls(str(path.relative_to(root)), hashlib.sha256(data).hexdigest(),
                   data.decode("utf-8"))

    def chunk(self, offset: int) -> tuple[int, str]:
        if offset < 0 or offset > len(self.text):
            raise ValueError("Offset is outside the file")
        # Offsets count Unicode characters; a byte cap bounds tool output even
        # for non-ASCII source. Only the final incomplete UTF-8 character is cut.
        content = self.text[offset:offset + CHUNK_BYTES]
        content = content.encode("utf-8")[:CHUNK_BYTES].decode("utf-8", errors="ignore")
        return offset + len(content), content


@dataclass(frozen=True)
class ReadVersion:
    """Small evidence boundary; patch validation must not retain whole files."""

    path: str
    digest: str
    total: int


def project_path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or len(name) > 4096:
        raise ValueError("Expected a nonempty repository file path")
    path = Path(name)
    if ".." in path.parts:
        raise ValueError("Parent traversal is unsupported; use a normalized repository path")
    path = path if path.is_absolute() else root / path
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(root) or path == root:
        raise ValueError(f"Path is outside this repository: {name}")
    relative = path.relative_to(root)
    if ".git" in relative.parts or relative.parts[:2] == (".codex", ".read-guard"):
        raise ValueError("Git metadata and guard state cannot be read or edited through the guard")
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Symlink paths are unsupported: {name}")
    return path
