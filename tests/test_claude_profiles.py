"""
Unit tests for Claude profiles synchronization (Main -> Personal).
"""

import os
import json
import stat
import unittest
from pathlib import Path
from helpers import IsolatedEnvironmentTestCase


class TestClaudeProfilesSync(IsolatedEnvironmentTestCase):

    def test_global_mcp_reaches_personal_without_copying_accounts(self):
        from claude_sync import sync_claude_profiles
        from state import StateManager
        (self.claude_dir / "settings.json").write_text('{}')
        (self.mock_home / ".claude.json").write_text(json.dumps({"account": "main-only", "mcpServers": {"shared": {"type": "stdio", "command": "fixture", "ignored": "no"}}}))
        target = self.claude_personal_dir / ".claude.json"
        target.write_text(json.dumps({"account": "personal-only", "mcpServers": {"local": {"command": "keep-local"}}}))
        result = sync_claude_profiles(self.get_paths(), StateManager(self.get_paths()))
        self.assertTrue(result.success, result.errors)
        data = json.loads(target.read_text())
        self.assertEqual(data["account"], "personal-only")
        self.assertEqual(data["mcpServers"]["local"]["command"], "keep-local")
        self.assertIn("shared", data["mcpServers"])
        self.assertEqual(data["mcpServers"]["shared"], {"type": "stdio", "command": "fixture"})

    def test_main_circleback_false_reflects_in_personal(self):
        """
        Verify that Main with circleback=False sets circleback=False in Personal,
        replacing Personal's circleback=True.
        """
        from claude_sync import sync_claude_profiles
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        # Main has circleback: False
        main_settings = {
            "enabledPlugins": {
                "circleback": False,
                "superpowers": True,
            },
            "extraKnownMarketplaces": {
                "official": "https://example.com/mkt",
            }
        }
        with open(paths.claude_dir / "settings.json", "w", encoding="utf-8") as f:
            json.dump(main_settings, f)

        # Personal initially has circleback: True, plus personal preferences and auth
        personal_settings = {
            "enabledPlugins": {
                "circleback": True,
            },
            "model": "claude-3-opus",
            "account": {"email": "personal@example.com", "apiKey": "secret-personal-token"},
            "extraKnownMarketplaces": {}
        }
        with open(paths.claude_personal_dir / "settings.json", "w", encoding="utf-8") as f:
            json.dump(personal_settings, f)

        # Run sync
        result = sync_claude_profiles(paths, state, dry_run=False)
        self.assertTrue(result.success)

        # Verify Personal settings
        with open(paths.claude_personal_dir / "settings.json", "r", encoding="utf-8") as f:
            updated_personal = json.load(f)

        # circleback must now be False (reflecting Main)
        self.assertFalse(updated_personal["enabledPlugins"]["circleback"])
        # superpowers must be True
        self.assertTrue(updated_personal["enabledPlugins"]["superpowers"])
        # Marketplace synced
        self.assertEqual(updated_personal["extraKnownMarketplaces"]["official"], "https://example.com/mkt")
        # Personal preferences and credentials intact!
        self.assertEqual(updated_personal["model"], "claude-3-opus")
        self.assertEqual(updated_personal["account"]["apiKey"], "secret-personal-token")

    def test_personal_local_extras_preserved(self):
        """
        Verify that local extra plugins in Personal (not in Main and not managed)
        are preserved intact.
        """
        from claude_sync import sync_claude_profiles
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        main_settings = {
            "enabledPlugins": {"main-plugin": True}
        }
        with open(paths.claude_dir / "settings.json", "w", encoding="utf-8") as f:
            json.dump(main_settings, f)

        personal_settings = {
            "enabledPlugins": {
                "personal-custom-plugin": True,
                "another-local-plugin": False,
            }
        }
        with open(paths.claude_personal_dir / "settings.json", "w", encoding="utf-8") as f:
            json.dump(personal_settings, f)

        result = sync_claude_profiles(paths, state, dry_run=False)
        self.assertTrue(result.success)

        with open(paths.claude_personal_dir / "settings.json", "r", encoding="utf-8") as f:
            updated_personal = json.load(f)

        # Main plugin added
        self.assertTrue(updated_personal["enabledPlugins"]["main-plugin"])
        # Extras preserved
        self.assertTrue(updated_personal["enabledPlugins"]["personal-custom-plugin"])
        self.assertFalse(updated_personal["enabledPlugins"]["another-local-plugin"])

    def test_managed_plugin_removal(self):
        """
        Verify that if a plugin was previously managed from Main, and then removed
        from Main, it gets removed from Personal. But unmanaged extras stay.
        """
        from claude_sync import sync_claude_profiles
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        # Step 1: Main has plugin-a and plugin-b
        main_settings = {
            "enabledPlugins": {"plugin-a": True, "plugin-b": True}
        }
        with open(paths.claude_dir / "settings.json", "w", encoding="utf-8") as f:
            json.dump(main_settings, f)

        personal_settings = {
            "enabledPlugins": {"unmanaged-extra": True}
        }
        with open(paths.claude_personal_dir / "settings.json", "w", encoding="utf-8") as f:
            json.dump(personal_settings, f)

        sync_claude_profiles(paths, state, dry_run=False)

        # Step 2: Now plugin-b is removed from Main
        main_settings["enabledPlugins"].pop("plugin-b")
        with open(paths.claude_dir / "settings.json", "w", encoding="utf-8") as f:
            json.dump(main_settings, f)

        sync_claude_profiles(paths, state, dry_run=False)

        with open(paths.claude_personal_dir / "settings.json", "r", encoding="utf-8") as f:
            updated_personal = json.load(f)

        self.assertIn("plugin-a", updated_personal["enabledPlugins"])
        self.assertNotIn("plugin-b", updated_personal["enabledPlugins"])
        self.assertIn("unmanaged-extra", updated_personal["enabledPlugins"])

    def test_invalid_json_syntax_does_not_destroy_config(self):
        """
        Corrupted JSON syntax in settings.json raises a visible error, does NOT overwrite
        or destroy the file, and marks failure.
        """
        from claude_sync import sync_claude_profiles
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        bad_content = "{\ninvalid json syntax ...\n"
        with open(paths.claude_dir / "settings.json", "w", encoding="utf-8") as f:
            f.write(bad_content)

        personal_settings = {"model": "claude-3-5-sonnet", "enabledPlugins": {}}
        with open(paths.claude_personal_dir / "settings.json", "w", encoding="utf-8") as f:
            json.dump(personal_settings, f)

        result = sync_claude_profiles(paths, state, dry_run=False)
        self.assertFalse(result.success)
        self.assertTrue(len(result.errors) > 0)

        # Verify neither file was corrupted or wiped
        self.assertEqual((paths.claude_dir / "settings.json").read_text(encoding="utf-8"), bad_content)
        with open(paths.claude_personal_dir / "settings.json", "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), personal_settings)

    def test_invalid_json_shape_fails_and_does_not_delete_managed(self):
        """
        Valid JSON with incorrect shape (e.g. null, [], enabledPlugins not dict,
        or enabled not bool) MUST fail before writes/deletions, not normalize to {}
        and wipe managed entries.
        """
        from claude_sync import sync_claude_profiles
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        # Suppose plugin-x was already managed
        state.save_stage_inventory("plugins", ["plugin-x"])

        personal_orig = {
            "enabledPlugins": {"plugin-x": True}
        }
        with open(paths.claude_personal_dir / "settings.json", "w", encoding="utf-8") as f:
            json.dump(personal_orig, f)

        # Main has enabledPlugins as null (valid JSON, invalid shape)
        with open(paths.claude_dir / "settings.json", "w", encoding="utf-8") as f:
            f.write('{"enabledPlugins": null}')

        result = sync_claude_profiles(paths, state, dry_run=False)
        self.assertFalse(result.success)
        self.assertTrue(any("forma" in e.lower() or "shape" in e.lower() or "inválid" in e.lower() for e in result.errors))

        # Personal file must NOT have been modified or wiped
        with open(paths.claude_personal_dir / "settings.json", "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), personal_orig)

        # Test case: enabledPlugins value is not a bool
        with open(paths.claude_dir / "settings.json", "w", encoding="utf-8") as f:
            f.write('{"enabledPlugins": {"bad-plugin": "not_a_bool"}}')

        result2 = sync_claude_profiles(paths, state, dry_run=False)
        self.assertFalse(result2.success)

        # Test case: top level is a list, not a dict
        with open(paths.claude_dir / "settings.json", "w", encoding="utf-8") as f:
            f.write('[1, 2, 3]')

        result3 = sync_claude_profiles(paths, state, dry_run=False)
        self.assertFalse(result3.success)

    def test_file_permissions_and_stow_link_preservation(self):
        """
        atomic_write_json must preserve existing file permissions and update Stow
        symlink targets without replacing the symlink with a plain file.
        """
        from config import atomic_write_json

        # 1. Test mode preservation
        test_file = self.temp_dir / "test_perm.json"
        with open(test_file, "w", encoding="utf-8") as f:
            f.write('{"initial": 1}\n')
        test_file.chmod(0o600)

        atomic_write_json(test_file, {"updated": 2})
        mode = test_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

        # 2. Test Stow symlink preservation
        stow_target = self.temp_dir / "package_file.json"
        with open(stow_target, "w", encoding="utf-8") as f:
            f.write('{"stow": true}\n')

        stow_link = self.temp_dir / "linked_file.json"
        stow_link.symlink_to(stow_target)

        atomic_write_json(stow_link, {"stow": "updated"})

        # stow_link should STILL be a symlink!
        self.assertTrue(stow_link.is_symlink())
        self.assertEqual(stow_link.resolve(), stow_target.resolve())
        # Target should have the updated content
        with open(stow_target, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"stow": "updated"})

    def test_symlinks_created_for_base_folders(self):
        """
        Ensures ~/.claude-personal/skills, agents, plugins symlink to ~/.claude/<subfolder>.
        """
        from claude_sync import sync_claude_profiles
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        (paths.claude_dir / "skills").mkdir(exist_ok=True)
        (paths.claude_dir / "agents").mkdir(exist_ok=True)
        (paths.claude_dir / "plugins").mkdir(exist_ok=True)

        result = sync_claude_profiles(paths, state, dry_run=False)
        self.assertTrue(result.success)

        for sub in ["skills", "agents", "plugins"]:
            link = paths.claude_personal_dir / sub
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), (paths.claude_dir / sub).resolve())


if __name__ == "__main__":
    unittest.main()
