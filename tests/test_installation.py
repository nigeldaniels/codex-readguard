"""Installation must preserve existing project files and exclude local state."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer import Installation


SOURCE = Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       capture_output=True, timeout=5)
        self.installation = Installation(SOURCE, self.root)

    def test_installs_only_payload_and_preserves_unrelated_configuration(self):
        (self.root / ".codex").mkdir()
        (self.root / ".codex/config.toml").write_text("# existing configuration\n")
        (self.root / "AGENTS.md").write_text("Existing project instructions\n")
        payload = self.installation.prepare()
        self.installation.apply(payload)
        self.assertEqual((self.root / ".codex/config.toml").read_text(), "# existing configuration\n")
        self.assertEqual((self.root / "AGENTS.md").read_text(), "Existing project instructions\n")
        for path, content in payload.items():
            self.assertEqual((self.root / ".codex" / path).read_bytes(), content)
        self.assertFalse((self.root / ".codex/.read-guard").exists())
        self.assertFalse((self.root / ".codex/tests").exists())
        self.assertEqual((self.root / ".codex/read_guard/LICENSE").read_bytes(),
                         (SOURCE / "LICENSE").read_bytes())
        result = subprocess.run([sys.executable, ".codex/guard.py", "read", "AGENTS.md"],
                                cwd=self.root, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Existing project instructions", result.stdout)

    def test_existing_hook_config_is_rejected_before_any_writes(self):
        (self.root / ".codex").mkdir()
        hooks = self.root / ".codex/hooks.json"
        hooks.write_text('{"hooks": {}}\n')
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            self.installation.prepare()
        self.assertEqual(hooks.read_text(), '{"hooks": {}}\n')
        self.assertFalse((self.root / ".codex/guard.py").exists())

    def test_dry_run_writes_nothing(self):
        result = subprocess.run([sys.executable, str(SOURCE / "install.py"), str(self.root), "--dry-run"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would install: .codex/hooks.json", result.stdout)
        self.assertFalse((self.root / ".codex").exists())

    def test_reinstallation_refuses_to_replace_existing_files(self):
        self.installation.apply(self.installation.prepare())
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            self.installation.prepare()

    def test_symlinked_destinations_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / ".codex").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "normal directory"):
            self.installation.prepare()
        self.assertEqual(list(outside.iterdir()), [])
        (self.root / ".codex").unlink()
        (self.root / ".codex").mkdir()
        (self.root / ".codex/guard.py").symlink_to(outside / "missing.py")
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            self.installation.prepare()

    def test_subdirectories_cannot_be_mistaken_for_repository_roots(self):
        nested = self.root / "nested"
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, "repository root"):
            Installation(SOURCE, nested).prepare()

    def test_failure_rolls_back_created_files_and_preserves_existing_files(self):
        (self.root / ".codex").mkdir()
        config = self.root / ".codex/config.toml"
        config.write_text("# preserve this\n")
        payload = self.installation.prepare()
        original_open = Path.open
        def fail_on_hooks(path, *args, **kwargs):
            if path == self.root / ".codex/hooks.json":
                raise OSError("simulated disk failure")
            return original_open(path, *args, **kwargs)
        with patch.object(Path, "open", fail_on_hooks):
            with self.assertRaisesRegex(OSError, "simulated disk failure"):
                self.installation.apply(payload)
        self.assertEqual(list((self.root / ".codex").iterdir()), [config])
        self.assertEqual(config.read_text(), "# preserve this\n")

    def test_file_created_after_preflight_is_not_overwritten(self):
        payload = self.installation.prepare()
        (self.root / ".codex").mkdir()
        hooks = self.root / ".codex/hooks.json"
        hooks.write_text("newly added\n")
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            self.installation.apply(payload)
        self.assertEqual(hooks.read_text(), "newly added\n")
