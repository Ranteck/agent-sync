"""
Unit tests for dry-run mode (zero filesystem writes, read-only plan).
"""

import json
import unittest
from pathlib import Path
from helpers import IsolatedEnvironmentTestCase


class TestDryRun(IsolatedEnvironmentTestCase):

    def test_dry_run_makes_zero_modifications(self):
        """
        In dry-run mode, no directories are created, no files written,
        no symlinks created, and no state files updated.
        """
        from runner import run_sync
        from state import StateManager

        paths = self.get_paths()

        # Set up source settings
        main_settings = {
            "enabledPlugins": {"test-plugin@fixture": True},
            "extraKnownMarketplaces": {"test-mkt": "https://example.com"}
        }
        with open(paths.claude_dir / "settings.json", "w", encoding="utf-8") as f:
            json.dump(main_settings, f)

        # Personal settings
        personal_settings = {"model": "claude-3-opus", "enabledPlugins": {}}
        with open(paths.claude_personal_dir / "settings.json", "w", encoding="utf-8") as f:
            json.dump(personal_settings, f)

        # Non-existent target directory in codex
        missing_codex_skills = paths.codex_dir / "skills"

        # Record tree state before dry run
        def snapshot_tree(root: Path):
            res = {}
            for p in root.rglob("*"):
                if p.is_symlink():
                    res[str(p)] = ("symlink", str(p.readlink()))
                elif p.is_file():
                    res[str(p)] = ("file", p.stat().st_mtime_ns, p.stat().st_size)
                elif p.is_dir():
                    res[str(p)] = ("dir",)
            return res

        snapshot_before = snapshot_tree(self.temp_dir)

        # Run with dry-run
        exit_code, operations = run_sync(paths, mode="dry_run")
        self.assertEqual(exit_code, 0)
        self.assertTrue(len(operations) > 0)

        snapshot_after = snapshot_tree(self.temp_dir)

        # Absolutely NO files or directories should have been created or modified
        self.assertEqual(snapshot_before, snapshot_after)

        # Ensure state file was NOT created
        self.assertFalse((paths.state_dir / "state.json").exists())
        # Ensure missing directory was NOT created
        self.assertFalse(missing_codex_skills.exists())


if __name__ == "__main__":
    unittest.main()
