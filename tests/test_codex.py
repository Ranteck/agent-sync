"""
Unit tests for OpenAI Codex (~/.codex) synchronization.
"""

import json
import unittest
from pathlib import Path
from helpers import IsolatedEnvironmentTestCase


class TestCodexSync(IsolatedEnvironmentTestCase):

    def test_existing_custom_rules_survive_without_a_managed_baseline(self):
        from codex_sync import sync_codex
        from state import StateManager
        target = self.codex_dir / "AGENTS.md"
        target.write_text("Custom Codex instructions\n")
        (self.claude_dir / "RTK.md").write_text("Shared RTK instructions\n")
        result = sync_codex(self.get_paths(), StateManager(self.get_paths()))
        self.assertTrue(result.success, result.errors)
        self.assertEqual(target.read_text(), "Custom Codex instructions\n")

    def test_missing_rule_sources_do_not_empty_existing_instructions(self):
        from codex_sync import sync_codex
        from state import StateManager
        target = self.codex_dir / "AGENTS.md"
        target.write_text("Keep these instructions\n")
        result = sync_codex(self.get_paths(), StateManager(self.get_paths()))
        self.assertTrue(result.success, result.errors)
        self.assertEqual(target.read_text(), "Keep these instructions\n")

    def test_codex_agents_md_composition(self):
        """
        Verify AGENTS.md composition from RTK, House Rules, AI-Native SDLC, and CLAUDE.md.
        """
        from codex_sync import sync_codex
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        # Setup source rule files
        (paths.claude_dir / "RTK.md").write_text("# RTK Guidelines\nBe concise.")

        hr_dir = paths.projects_dir / "house-rules"
        hr_dir.mkdir(parents=True)
        (hr_dir / "HOUSE-RULES.md").write_text("# House Rules\nFollow project conventions.")

        sdlc_dir = paths.projects_dir / "ai-native-sdlc"
        sdlc_dir.mkdir(parents=True)
        (sdlc_dir / "AI-NATIVE-SDLC.md").write_text("# AI Native SDLC\nIntent -> Spec -> Plan.")

        (paths.claude_dir / "CLAUDE.md").write_text("@mention-ignored\n# Workflow Rules\nAlways verify.")

        result = sync_codex(paths, state, dry_run=False)
        self.assertTrue(result.success)

        agents_md = paths.codex_dir / "AGENTS.md"
        self.assertTrue(agents_md.is_file())
        content = agents_md.read_text(encoding="utf-8")

        self.assertIn("# RTK Guidelines", content)
        self.assertIn("<!-- house-rules:start -->", content)
        self.assertIn("# House Rules", content)
        self.assertIn("<!-- ai-native-sdlc:start -->", content)
        self.assertIn("# AI Native SDLC", content)
        self.assertIn("<!-- claude-guidelines:start -->", content)
        self.assertIn("# Workflow Rules", content)
        self.assertNotIn("@mention-ignored", content)

    def test_codex_skills_and_preservation_of_local_adaptations(self):
        """
        Verify Claude skills are linked to ~/.codex/skills/ and local adaptations in Codex
        are preserved intact.
        """
        from codex_sync import sync_codex
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        # Claude skill
        c_skill = paths.claude_dir / "skills" / "claude-tool"
        c_skill.mkdir(parents=True)
        (c_skill / "SKILL.md").write_text("# Claude Tool")

        # Local adaptation in codex
        codex_skills = paths.codex_dir / "skills"
        codex_skills.mkdir(parents=True)
        local_adaptation = codex_skills / "codex-local-skill"
        local_adaptation.mkdir(parents=True)
        (local_adaptation / "SKILL.md").write_text("# Codex Adaptation")

        result = sync_codex(paths, state, dry_run=False)
        self.assertTrue(result.success)

        # Synced skill linked
        self.assertTrue((codex_skills / "claude-tool").is_symlink())
        # Local adaptation intact
        self.assertTrue(local_adaptation.is_dir())
        self.assertFalse(local_adaptation.is_symlink())
        self.assertIn("# Codex Adaptation", (local_adaptation / "SKILL.md").read_text())

    def test_codex_hooks_preserves_existing_and_adds_session_start(self):
        """
        Verify existing hooks in hooks.json are preserved and SessionStart hook for sync-agents
        is registered.
        """
        from codex_sync import sync_codex
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        existing_hooks = {
            "hooks": {
                "PreToolCall": [
                    {"hooks": [{"type": "command", "command": "echo pre-tool"}]}
                ],
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "echo starting"}]}
                ]
            }
        }
        with open(paths.codex_dir / "hooks.json", "w", encoding="utf-8") as f:
            json.dump(existing_hooks, f)

        result = sync_codex(paths, state, dry_run=False)
        self.assertTrue(result.success)

        with open(paths.codex_dir / "hooks.json", "r", encoding="utf-8") as f:
            updated = json.load(f)

        # PreToolCall preserved
        self.assertEqual(len(updated["hooks"]["PreToolCall"]), 1)
        # SessionStart has original and added sync hook
        session_start = updated["hooks"]["SessionStart"]
        self.assertTrue(any(
            any("sync-agents" in h.get("command", "") for h in g.get("hooks", []))
            for g in session_start
        ))


if __name__ == "__main__":
    unittest.main()
