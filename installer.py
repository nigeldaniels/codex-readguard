"""Install an explicit payload into a Git root without replacing project files."""

import subprocess
from pathlib import Path


class Installation:
    def __init__(self, source: Path, repository: Path):
        self.source = source.resolve()
        self.repository = repository.resolve(strict=True)
        self.destination = self.repository / ".codex"

    def prepare(self) -> dict[Path, bytes]:
        self.require_git_root()
        paths = [Path("guard.py"), Path("README.md"), Path(".gitignore")]
        paths += [path.relative_to(self.source / ".codex") for path in sorted(
            (self.source / ".codex/read_guard").glob("*.py"))]
        if Path("read_guard/__init__.py") not in paths:
            raise ValueError("The source checkout is missing the read_guard package")
        sources = {path: self.source / ".codex" / path for path in paths}
        sources[Path("read_guard/LICENSE")] = self.source / "LICENSE"
        # Hook registration is written last so incomplete code is never exposed
        # as a newly installed hook. No cache, database, or local config is copied.
        sources[Path("hooks.json")] = self.source / ".codex/hooks.json"
        self.require_empty_destinations(list(sources))
        return {path: source.read_bytes() for path, source in sources.items()}

    def apply(self, payload: dict[Path, bytes]) -> None:
        # Recheck immediately before writing and use exclusive file creation.
        # This prevents a dry-run or a concurrent file creation from allowing
        # silent overwrites. Run installation while project files are idle.
        self.require_empty_destinations(list(payload))
        created_files, created_directories = [], []
        try:
            for directory in (self.destination, self.destination / "read_guard"):
                if not directory.exists():
                    directory.mkdir()
                    created_directories.append(directory)
            for relative, content in payload.items():
                path = self.destination / relative
                with path.open("xb") as stream:
                    created_files.append(path)
                    stream.write(content)
        except OSError:
            self.remove_created_paths(created_files, created_directories)
            raise

    def require_git_root(self) -> None:
        result = subprocess.run(["git", "-C", str(self.repository), "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True, timeout=10, check=False)
        if result.returncode or Path(result.stdout.strip()).resolve() != self.repository:
            raise ValueError("Choose an existing Git repository root; initialize it with git init first")

    def require_empty_destinations(self, paths: list[Path]) -> None:
        if self.destination.is_symlink() or (
            self.destination.exists() and not self.destination.is_dir()
        ):
            raise ValueError("The destination .codex must be a normal directory, not a symlink or file")
        conflicts = []
        for name in sorted({path.parts[0] for path in paths}):
            path = self.destination / name
            if path.exists() or path.is_symlink():
                conflicts.append(str(path.relative_to(self.repository)))
        if conflicts:
            raise ValueError("Refusing to overwrite " + ", ".join(conflicts)
                             + "; follow the README's existing-configuration instructions")

    @staticmethod
    def remove_created_paths(files: list[Path], directories: list[Path]) -> None:
        errors = []
        for path in reversed(files):
            try:
                path.unlink()
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        for path in reversed(directories):
            try:
                path.rmdir()
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        if errors:
            raise OSError("Installation rollback needs manual cleanup: " + "; ".join(errors))
