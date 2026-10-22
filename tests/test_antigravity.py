"""
Unit tests for Antigravity (~/.gemini) synchronization.
"""

import json
import hashlib
import unittest
from pathlib import Path
from helpers import IsolatedEnvironmentTestCase


class TestAntigravitySync(IsolatedEnvironmentTestCase):

    def test_skills_json_generation_and_preservation_of_custom_entries(self):
        """
        Verify skills.json combines Claude skills, Agent skills, plugin skills,
        and preserves pre-existing custom entries.
        """
        from antigravity_sync import sync_antigravity
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        gemini_config = paths.gemini_dir / "config"
        gemini_config.mkdir(parents=True, exist_ok=True)

        # Pre-existing custom entry in skills.json
        pre_existing = {
            "entries": [
                {"path": "/opt/custom/team-skills"}
            ]
        }
        with open(gemini_config / "skills.json", "w", encoding="utf-8") as f:
            json.dump(pre_existing, f)

        # Mock installed_plugins.json in Claude
        plugins_dir = paths.claude_dir / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)

        plugin_skill_dir = self.temp_dir / "installed-plugin" / "skills" / "my-skill"
        plugin_skill_dir.mkdir(parents=True, exist_ok=True)
        (plugin_skill_dir / "SKILL.md").write_text("# Plugin Skill")

        installed_plugins = {
            "version": 2,
            "plugins": {
                "sample-plugin": [
                    {"installPath": str(self.temp_dir / "installed-plugin")}
                ]
            }
        }
        with open(plugins_dir / "installed_plugins.json", "w", encoding="utf-8") as f:
            json.dump(installed_plugins, f)

        (paths.claude_dir / "settings.json").write_text(json.dumps({"enabledPlugins": {"sample-plugin": True}}))
        result = sync_antigravity(paths, state, dry_run=False)
        self.assertTrue(result.success)

        with open(gemini_config / "skills.json", "r", encoding="utf-8") as f:
            data = json.load(f)

        paths_in_json = [entry["path"] for entry in data.get("entries", [])]
        # Custom entry preserved
        self.assertIn("/opt/custom/team-skills", paths_in_json)
        # Claude skills included
        self.assertIn(str(paths.claude_dir / "skills"), paths_in_json)
        # Agents skills included
        self.assertIn(str(paths.agents_dir / "skills"), paths_in_json)
        # Discovered plugin skills path included
        self.assertIn(str(self.temp_dir / "installed-plugin" / "skills"), paths_in_json)

    def test_mcp_config_preserves_custom_and_extracts_clean_fields(self):
        """
        Verify mcp_config.json extracts command, args, env, url from ~/.claude.json,
        preserves explicit env references without resolving secrets, and preserves
        pre-existing custom servers.
        """
        from antigravity_sync import sync_antigravity
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        gemini_config = paths.gemini_dir / "config"
        gemini_config.mkdir(parents=True, exist_ok=True)

        # Pre-existing custom server in mcp_config.json
        pre_existing_mcp = {
            "mcpServers": {
                "custom-local-server": {
                    "command": "custom-binary",
                    "args": ["--port", "9000"]
                }
            }
        }
        with open(gemini_config / "mcp_config.json", "w", encoding="utf-8") as f:
            json.dump(pre_existing_mcp, f)

        # ~/.claude.json with MCP servers
        claude_json = {
            "mcpServers": {
                "fetch-mcp": {
                    "command": "uvx",
                    "args": ["mcp-server-fetch"],
                    "env": {"API_TOKEN": "$FETCH_TOKEN"},
                    "ignored_field": "do-not-copy-this"
                }
            }
        }
        with open(paths.home / ".claude.json", "w", encoding="utf-8") as f:
            json.dump(claude_json, f)

        result = sync_antigravity(paths, state, dry_run=False)
        self.assertTrue(result.success)

        with open(gemini_config / "mcp_config.json", "r", encoding="utf-8") as f:
            mcp_data = json.load(f)

        servers = mcp_data.get("mcpServers", {})
        # Pre-existing server preserved
        self.assertIn("custom-local-server", servers)
        self.assertEqual(servers["custom-local-server"]["command"], "custom-binary")

        # Synced server present with cleaned fields and literal env reference
        self.assertIn("fetch-mcp", servers)
        self.assertEqual(servers["fetch-mcp"]["command"], "uvx")
        self.assertEqual(servers["fetch-mcp"]["args"], ["mcp-server-fetch"])
        self.assertEqual(servers["fetch-mcp"]["env"], {"API_TOKEN": "$FETCH_TOKEN"})
        self.assertNotIn("ignored_field", servers["fetch-mcp"])

    def test_native_agy_plugin_import_uses_explicit_paths_and_verifies_json(self):
        """
        Verify native agy plugin import uses explicit paths of active plugins (not generic claude source)
        and verifies list --json output.
        """
        from antigravity_sync import sync_antigravity
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        plugin_install_dir = self.temp_dir / "my-active-plugin"
        plugin_install_dir.mkdir(parents=True)
        (plugin_install_dir / "skills").mkdir()
        (plugin_install_dir / ".claude-plugin").mkdir()
        (plugin_install_dir / ".claude-plugin/plugin.json").write_text('{"name":"my-plugin","skills":"./skills"}')

        plugins_dir = paths.claude_dir / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        installed_plugins = {
            "version": 2,
            "plugins": {
                "my-plugin": [
                    {"installPath": str(plugin_install_dir)}
                ]
            }
        }
        with open(plugins_dir / "installed_plugins.json", "w", encoding="utf-8") as f:
            json.dump(installed_plugins, f)

        # Mock settings.json marking my-plugin as enabled
        main_settings = {
            "enabledPlugins": {"my-plugin": True}
        }
        with open(paths.claude_dir / "settings.json", "w", encoding="utf-8") as f:
            json.dump(main_settings, f)

        # Fake agy script that records commands and simulates agy behavior
        log_file = self.temp_dir / "agy_commands.log"
        fake_agy = self.create_fake_bin("agy", f"""#!/usr/bin/env bash
echo "$@" >> "{log_file}"
if [[ "$1" == "plugin" && "$2" == "import" ]]; then
    # Verify it was invoked with the explicit install path, NOT generic 'claude'
    if [[ "$3" == "{plugin_install_dir}" ]]; then
        echo imported > "{self.temp_dir / 'imported'}"
        exit 0
    else
        echo "Error: expected explicit path, got $3" >&2
        exit 1
    fi
elif [[ "$1" == "plugin" && "$2" == "list" && "$3" == "--json" ]]; then
    if [[ -f "{self.temp_dir / 'imported'}" ]]; then
        echo '[{{"name": "my-plugin", "enabled": true}}]'
    else
        echo '[]'
    fi
    exit 0
fi
exit 0
""")

        result = sync_antigravity(paths, state, dry_run=False)
        self.assertTrue(result.success)

        # Check commands logged
        commands = log_file.read_text().splitlines()
        self.assertTrue(any(f"plugin import {plugin_install_dir}" in cmd for cmd in commands))
        self.assertTrue(any("plugin list --json" in cmd for cmd in commands))

    def test_native_agy_cli_failure_is_visible_and_sanitized(self):
        """
        Verify native agy failure produces sanitized error without dumping raw secrets.
        """
        from antigravity_sync import sync_antigravity
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        # Active plugin
        plugin_dir = self.temp_dir / "fail-plugin"
        plugin_dir.mkdir(parents=True)
        plugins_dir = paths.claude_dir / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        installed_plugins = {
            "version": 2,
            "plugins": {
                "fail-plugin": [{"installPath": str(plugin_dir)}]
            }
        }
        with open(plugins_dir / "installed_plugins.json", "w", encoding="utf-8") as f:
            json.dump(installed_plugins, f)
        with open(paths.claude_dir / "settings.json", "w", encoding="utf-8") as f:
            json.dump({"enabledPlugins": {"fail-plugin": True}}, f)

        fake_agy = self.create_fake_bin("agy", """#!/usr/bin/env bash
echo "Sensitive token=XYZ999 secret=ABC123" >&2
exit 2
""")
        result = sync_antigravity(paths, state, dry_run=False)
        self.assertFalse(result.success)
        for err in result.errors:
            self.assertNotIn("XYZ999", err)
            self.assertNotIn("ABC123", err)

    def test_native_agy_missing_reports_omission_cleanly(self):
        """
        When agy is not in PATH, sync reports omission without failing or crashing.
        """
        from antigravity_sync import sync_antigravity
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)

        result = sync_antigravity(paths, state, dry_run=False)
        self.assertTrue(result.success)
        self.assertTrue(any("omit" in op.lower() for op in result.operations))

    def test_lsp_only_plugin_is_reported_as_unsupported(self):
        from antigravity_sync import sync_antigravity
        from state import StateManager
        plugin = self.temp_dir / "lsp-only"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin/plugin.json").write_text('{"name":"lsp-only","lspServers":{"fixture":{}}}')
        (self.claude_dir / "settings.json").write_text('{"enabledPlugins":{"lsp-only@fixture":true}}')
        (self.claude_dir / "plugins").mkdir()
        (self.claude_dir / "plugins/installed_plugins.json").write_text(json.dumps({"plugins":{"lsp-only@fixture":[{"installPath":str(plugin)}]}}))
        self.create_fake_bin("agy", "#!/usr/bin/env bash\nif [[ $2 == list ]]; then echo '[]'; else exit 7; fi\n")
        result = sync_antigravity(self.get_paths(), StateManager(self.get_paths()))
        self.assertTrue(result.success, result.errors)
        self.assertTrue(any("omit" in op.lower() for op in result.operations))


class TestAntigravityRules(IsolatedEnvironmentTestCase):

    def setUp(self):
        super().setUp()
        (self.claude_dir / "settings.json").write_text('{"enabledPlugins": {}}')
        (self.claude_dir / "plugins").mkdir()
        (self.claude_dir / "plugins/installed_plugins.json").write_text('{"plugins": {}}')
        config = self.gemini_dir / "config"
        config.mkdir()
        (config / "skills.json").write_text('{"entries": []}')
        (config / "mcp_config.json").write_text('{"mcpServers": {}}')
        (self.claude_dir / "RTK.md").write_text("# RTK\nBe concise.\n")
        for directory, filename, content in [
            ("house-rules", "HOUSE-RULES.md", "# House Rules\nFollow conventions."),
            ("ai-native-sdlc", "AI-NATIVE-SDLC.md", "# AI Native SDLC\nIntent -> Spec -> Plan."),
        ]:
            source = self.projects_dir / directory
            source.mkdir()
            (source / filename).write_text(content)
        (self.claude_dir / "CLAUDE.md").write_text("@RTK.md\n# Workflow Rules\nAlways verify.\n")
        self.target = self.gemini_dir / "GEMINI.md"
        self.expected = (
            "# RTK\nBe concise.\n\n"
            "<!-- house-rules:start -->\n# House Rules\nFollow conventions.\n<!-- house-rules:end -->\n\n"
            "<!-- ai-native-sdlc:start -->\n# AI Native SDLC\nIntent -> Spec -> Plan.\n<!-- ai-native-sdlc:end -->\n\n"
            "<!-- claude-guidelines:start -->\n# Workflow Rules\nAlways verify.\n<!-- claude-guidelines:end -->\n"
        )

    def test_missing_rules_are_created_with_the_same_content_as_codex(self):
        from antigravity_sync import sync_antigravity
        from codex_sync import sync_codex
        from state import StateManager

        paths = self.get_paths()
        state = StateManager(paths)
        result = sync_antigravity(paths, state)
        self.assertTrue(result.success, result.errors)
        self.assertEqual(self.target.read_bytes(), self.expected.encode())
        self.assertIn("Antigravity: reglas compartidas actualizadas.", result.operations)
        self.assertEqual(state.load_state()["managed"]["gemini_rules_hash"],
                         hashlib.sha256(self.expected.encode()).hexdigest())
        codex = sync_codex(paths, state)
        self.assertTrue(codex.success, codex.errors)
        self.assertEqual((self.codex_dir / "AGENTS.md").read_bytes(), self.target.read_bytes())

    def test_a_single_shared_part_is_adopted_without_a_hash(self):
        from antigravity_sync import sync_antigravity
        from state import StateManager

        for part in self.expected.strip().split("\n\n"):
            with self.subTest(part=part.splitlines()[0]):
                state = StateManager(self.get_paths())
                state.load_state().setdefault("managed", {}).pop("gemini_rules_hash", None)
                self.target.write_text("\n" + part + "\n\n")
                result = sync_antigravity(self.get_paths(), state)
                self.assertTrue(result.success, result.errors)
                self.assertEqual(self.target.read_text(), self.expected)
                self.assertIn("Antigravity: reglas compartidas actualizadas.", result.operations)
                self.assertIn("gemini_rules_hash", state.load_state()["managed"])

    def test_custom_rules_without_a_hash_are_preserved_and_reported(self):
        from antigravity_sync import sync_antigravity
        from state import StateManager

        custom = "# RTK\nBe concise.\n\nMy own instructions.\n"
        self.target.write_text(custom)
        state = StateManager(self.get_paths())
        result = sync_antigravity(self.get_paths(), state)
        self.assertTrue(result.success, result.errors)
        self.assertEqual(self.target.read_text(), custom)
        self.assertIn("Antigravity: GEMINI.md propio preservado; no coincide con una versión administrada.",
                      result.operations)
        self.assertNotIn("gemini_rules_hash", state.load_state()["managed"])

    def test_managed_rules_are_updated_when_a_source_changes(self):
        from antigravity_sync import sync_antigravity
        from state import StateManager

        paths = self.get_paths()
        first = sync_antigravity(paths, StateManager(paths))
        self.assertTrue(first.success, first.errors)
        (self.claude_dir / "RTK.md").write_text("# RTK\nUpdated instructions.\n")
        state = StateManager(paths)
        result = sync_antigravity(paths, state)
        self.assertTrue(result.success, result.errors)
        updated = self.expected.replace("Be concise.", "Updated instructions.")
        self.assertEqual(self.target.read_text(), updated)
        self.assertIn("Antigravity: reglas compartidas actualizadas.", result.operations)
        self.assertEqual(state.load_state()["managed"]["gemini_rules_hash"],
                         hashlib.sha256(updated.encode()).hexdigest())

    def test_local_edits_to_managed_rules_are_preserved(self):
        from antigravity_sync import sync_antigravity
        from state import StateManager

        paths = self.get_paths()
        first = sync_antigravity(paths, StateManager(paths))
        self.assertTrue(first.success, first.errors)
        custom = self.target.read_text() + "Local addition.\n"
        self.target.write_text(custom)
        result = sync_antigravity(paths, StateManager(paths))
        self.assertTrue(result.success, result.errors)
        self.assertEqual(self.target.read_text(), custom)
        self.assertIn("Antigravity: GEMINI.md propio preservado; no coincide con una versión administrada.",
                      result.operations)

    def test_matching_rules_are_adopted_without_rewriting(self):
        from antigravity_sync import sync_antigravity
        from state import StateManager

        self.target.write_text(self.expected)
        before = self.target.stat().st_mtime_ns
        state = StateManager(self.get_paths())
        result = sync_antigravity(self.get_paths(), state)
        self.assertTrue(result.success, result.errors)
        self.assertEqual(self.target.stat().st_mtime_ns, before)
        self.assertEqual(state.load_state()["managed"]["gemini_rules_hash"],
                         hashlib.sha256(self.expected.encode()).hexdigest())
        self.assertFalse(any("reglas compartidas" in op or "GEMINI.md" in op for op in result.operations))

    def test_dry_run_reports_updates_without_writing_rules_or_hash(self):
        from antigravity_sync import sync_antigravity
        from state import StateManager

        for current in [None, "# RTK\nBe concise.\n"]:
            with self.subTest(current=current):
                if current is not None:
                    self.target.write_text(current)
                state = StateManager(self.get_paths())
                result = sync_antigravity(self.get_paths(), state, dry_run=True)
                self.assertTrue(result.success, result.errors)
                if current is None:
                    self.assertFalse(self.target.exists())
                else:
                    self.assertEqual(self.target.read_text(), current)
                self.assertIn("Antigravity: reglas compartidas actualizadas.", result.operations)
                self.assertNotIn("gemini_rules_hash", state.load_state().get("managed", {}))
                self.assertFalse(state.state_file.exists())


if __name__ == "__main__":
    unittest.main()
