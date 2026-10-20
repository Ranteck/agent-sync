"""
Unit tests for universal and Claude skills synchronization.
"""

import os
import unittest
from pathlib import Path
from helpers import IsolatedEnvironmentTestCase


class TestSkillsSync(IsolatedEnvironmentTestCase):

    def test_bidirectional_skills_symlinking_and_loop_prevention(self):
        """
        Verify skills from ~/.agents/skills are linked into ~/.claude/skills
        and vice-versa, avoiding symlink loops.
        """
        from skills_sync import sync_skills
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        agents_skill = paths.agents_dir / "skills" / "skill-from-agents"
        agents_skill.mkdir(parents=True)
        (agents_skill / "SKILL.md").write_text("# Agent Skill")

        claude_skill = paths.claude_dir / "skills" / "skill-from-claude"
        claude_skill.mkdir(parents=True)
        (claude_skill / "SKILL.md").write_text("# Claude Skill")

        result = sync_skills(paths, state, dry_run=False)
        self.assertTrue(result.success)

        # Check in claude_skills
        dest1 = paths.claude_dir / "skills" / "skill-from-agents"
        self.assertTrue(dest1.is_symlink())
        self.assertEqual(dest1.resolve(), agents_skill.resolve())

        # Check in agents_skills
        dest2 = paths.agents_dir / "skills" / "skill-from-claude"
        self.assertTrue(dest2.is_symlink())
        self.assertEqual(dest2.resolve(), claude_skill.resolve())

        # Running again should be idempotent and not create loops or errors
        result2 = sync_skills(paths, state, dry_run=False)
        self.assertTrue(result2.success)

    def test_synced_directory_two_level_discovery_both_sources(self):
        """
        Verify skills located inside synced/<uuid1_uuid2>/skill/SKILL.md (two levels from synced)
        are discovered from both ~/.agents/skills and ~/.claude/skills.
        """
        from skills_sync import sync_skills
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        # 1. Nested skill in ~/.agents/skills/synced/group-uuid/nested-agent-skill
        agents_nested = paths.agents_dir / "skills" / "synced" / "group1_uuid" / "nested-agent-skill"
        agents_nested.mkdir(parents=True)
        (agents_nested / "SKILL.md").write_text("# Nested Agent Skill")

        # 2. Nested skill in ~/.claude/skills/synced/group2_uuid/nested-claude-skill
        claude_nested = paths.claude_dir / "skills" / "synced" / "group2_uuid" / "nested-claude-skill"
        claude_nested.mkdir(parents=True)
        (claude_nested / "SKILL.md").write_text("# Nested Claude Skill")

        result = sync_skills(paths, state, dry_run=False)
        self.assertTrue(result.success)

        # Discovered and linked into claude_skills
        target1 = paths.claude_dir / "skills" / "nested-agent-skill"
        self.assertTrue(target1.is_symlink())
        self.assertEqual(target1.resolve(), agents_nested.resolve())

        # Discovered and linked into agents_skills
        target2 = paths.agents_dir / "skills" / "nested-claude-skill"
        self.assertTrue(target2.is_symlink())
        self.assertEqual(target2.resolve(), claude_nested.resolve())

    def test_existing_adaptations_not_overwritten(self):
        """
        If a skill with the same name already exists as a directory (adaptation),
        it must NOT be replaced or clobbered by a symlink.
        """
        from skills_sync import sync_skills
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        # Source skill in agents
        agents_skill = paths.agents_dir / "skills" / "shared-skill"
        agents_skill.mkdir(parents=True)
        (agents_skill / "SKILL.md").write_text("# Original Skill")

        # Claude already has an adapted local version as a real directory
        claude_adaptation = paths.claude_dir / "skills" / "shared-skill"
        claude_adaptation.mkdir(parents=True)
        (claude_adaptation / "SKILL.md").write_text("# Adapted Local Skill")

        result = sync_skills(paths, state, dry_run=False)
        self.assertTrue(result.success)

        # Claude skill must remain a real directory with original adaptation content
        self.assertFalse(claude_adaptation.is_symlink())
        self.assertIn("# Adapted Local Skill", (claude_adaptation / "SKILL.md").read_text())

    def test_unmanaged_symlink_not_replaced(self):
        """
        If a symlink already exists and points to an external custom location,
        and is NOT managed by agent-sync, it must NOT be overwritten or pointed elsewhere.
        """
        from skills_sync import sync_skills
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        # Source skill in agents
        agents_skill = paths.agents_dir / "skills" / "custom-linked-skill"
        agents_skill.mkdir(parents=True)
        (agents_skill / "SKILL.md").write_text("# Agent Source")

        # External location
        external_target = self.temp_dir / "external-custom-location"
        external_target.mkdir(parents=True)
        (external_target / "SKILL.md").write_text("# External")

        # Unmanaged symlink already in claude pointing to external
        claude_link = paths.claude_dir / "skills" / "custom-linked-skill"
        claude_link.parent.mkdir(parents=True, exist_ok=True)
        claude_link.symlink_to(external_target)

        result = sync_skills(paths, state, dry_run=False)
        self.assertTrue(result.success)

        # The symlink in claude MUST still point to external_target, NOT agents_skill!
        self.assertTrue(claude_link.is_symlink())
        self.assertEqual(claude_link.resolve(), external_target.resolve())

    def test_managed_skill_removal(self):
        """
        When a skill is removed from the source, only managed symlinks created by
        agent-sync are removed; unmanaged items are preserved.
        """
        from skills_sync import sync_skills
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        skill_a = paths.claude_dir / "skills" / "temp-skill"
        skill_a.mkdir(parents=True)
        (skill_a / "SKILL.md").write_text("# Temp")

        # First sync links temp-skill to agents
        sync_skills(paths, state, dry_run=False)
        linked = paths.agents_dir / "skills" / "temp-skill"
        self.assertTrue(linked.is_symlink())

        # Also create an unmanaged manual directory
        unmanaged = paths.agents_dir / "skills" / "unmanaged-custom"
        unmanaged.mkdir(parents=True)

        # Now remove temp-skill from claude
        (skill_a / "SKILL.md").unlink()
        skill_a.rmdir()

        # Second sync
        sync_skills(paths, state, dry_run=False)

        # Managed symlink should be removed
        self.assertFalse(linked.exists())
        # Unmanaged item remains intact
        self.assertTrue(unmanaged.exists())


if __name__ == "__main__":
    unittest.main()
