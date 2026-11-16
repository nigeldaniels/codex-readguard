"""Extract every mutation path from the apply_patch wire format."""

from pathlib import Path

from .files import MAX_TARGETS, project_path


def patch_targets(root: Path, cwd: Path, patch: str) -> list[str]:
    if not isinstance(patch, str) or len(patch) > 2 * 1024 * 1024:
        raise ValueError("Missing or oversized patch")
    lines = patch.strip().splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("Unrecognized patch envelope; use the apply_patch tool")
    paths = []
    operation = None
    for line in lines[1:-1]:
        header = parse_header(line)
        if header:
            kind, name = header
            if kind == "Move to" and operation != "Update File":
                raise ValueError("Move must follow an update")
            if kind != "Move to":
                operation = kind
            path = Path(name)
            path = project_path(root, str(path if path.is_absolute() else cwd / path))
            if path.exists() and not path.is_file():
                raise ValueError("Patch target is not a regular file")
            paths.append(str(path.relative_to(root)))
        elif line.startswith("*** ") and line != "*** End of File":
            raise ValueError(f"Unrecognized patch directive: {line[:80]}")
        elif operation is None:
            raise ValueError("Patch content appeared before a file header")
    if not paths or len(paths) > MAX_TARGETS or len(paths) != len(set(paths)):
        raise ValueError(f"Patch must touch 1–{MAX_TARGETS} distinct paths")
    return paths


def parse_header(line: str) -> tuple[str, str] | None:
    for kind in ("Add File", "Update File", "Delete File", "Move to"):
        prefix = f"*** {kind}: "
        if line.startswith(prefix):
            name = line[len(prefix):]
            if not name.strip():
                raise ValueError("Empty patch path")
            return kind, name
    return None
