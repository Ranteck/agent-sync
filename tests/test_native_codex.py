import json
import os
import sys
import tomllib
from pathlib import Path

from helpers import IsolatedEnvironmentTestCase, PACKAGE_DIR


class TestNativeCodex(IsolatedEnvironmentTestCase):
    def setUp(self):
        super().setUp()
        self.config = self.codex_dir / "config.toml"
        self.config.write_text('model = "keep-model"\n# keep comment\n[mcp_servers.local]\ncommand = "keep-local"\n')
        self.calls = self.temp_dir / "calls.jsonl"
        self.response = self.temp_dir / "native-response.json"
        self.response.write_text('{"installed": []}')
        self.create_fake_bin("codex", f'''#!{sys.executable}
import json,sys
from pathlib import Path
with Path({str(self.calls)!r}).open("a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")
if "list" in sys.argv:
 print(Path({str(self.response)!r}).read_text())
elif sys.argv[1:3] == ["plugin", "add"]:
 print(json.dumps({{"pluginId": sys.argv[3]}}))
elif sys.argv[1:4] == ["plugin", "marketplace", "add"]:
 manifest = json.loads((Path(sys.argv[4])/".claude-plugin/marketplace.json").read_text())
 print(json.dumps({{"marketplaceName": manifest["name"]}}))
else:
 print("{{}}")
''')

    def source(self, plugins=None, records=None, mcp=None):
        (self.claude_dir / "settings.json").write_text(json.dumps({"enabledPlugins": plugins or {}}))
        folder = self.claude_dir / "plugins"
        folder.mkdir(exist_ok=True)
        (folder / "installed_plugins.json").write_text(json.dumps({"plugins": records or {}}))
        (self.mock_home / ".claude.json").write_text(json.dumps({"mcpServers": mcp or {}}))

    def sync(self, state=None, dry_run=False):
        self.assertTrue((PACKAGE_DIR / "native_codex.py").is_file(), "Falta implementar la sincronización nativa de plugins/MCP")
        from native_codex import sync_native_codex
        from state import StateManager
        return sync_native_codex(self.get_paths(), state or StateManager(self.get_paths()), dry_run=dry_run)

    def test_installs_missing_plugin_from_claude_without_manual_clone(self):
        src = self.temp_dir / "plugin"
        (src / ".claude-plugin").mkdir(parents=True)
        (src / ".claude-plugin/plugin.json").write_text('{"name":"demo","version":"1.0"}')
        self.source({"demo@fixture": True}, {"demo@fixture": [{"scope": "user", "installPath": str(src), "version": "1.0"}]})
        result = self.sync()
        self.assertTrue(result.success, result.errors)
        calls = [json.loads(x) for x in self.calls.read_text().splitlines()]
        self.assertTrue(any(x[:3] == ["plugin", "marketplace", "add"] for x in calls))
        self.assertIn(["plugin", "add", "demo@fixture", "--json"], calls)
        self.assertTrue(tomllib.loads(self.config.read_text())["plugins"]["demo@fixture"]["enabled"])

    def test_existing_native_port_is_preserved_and_disable_reaches_codex(self):
        self.response.write_text(json.dumps({"installed": [{"pluginId": "demo@fixture", "enabled": True, "version": "9.0"}]}))
        self.source({"demo@fixture": False})
        result = self.sync()
        self.assertTrue(result.success, result.errors)
        config = tomllib.loads(self.config.read_text())
        self.assertFalse(config["plugins"]["demo@fixture"]["enabled"])
        self.assertEqual(config["model"], "keep-model")
        self.assertIn("# keep comment", self.config.read_text())
        calls = [json.loads(x) for x in self.calls.read_text().splitlines()]
        self.assertEqual(calls, [["plugin", "list", "--json"]])

    def test_global_mcp_is_translated_and_local_servers_survive(self):
        self.source(mcp={"shared": {"type": "stdio", "command": "uvx", "args": ["fixture"], "env": {"TOKEN": "${TOKEN}"}, "ignored": "no"}})
        result = self.sync()
        self.assertTrue(result.success, result.errors)
        servers = tomllib.loads(self.config.read_text())["mcp_servers"]
        self.assertEqual(servers["shared"], {"command": "uvx", "args": ["fixture"], "env": {"TOKEN": "${TOKEN}"}})
        self.assertEqual(servers["local"]["command"], "keep-local")

    def test_bajas_disable_only_previously_managed_plugins(self):
        from state import StateManager
        self.config.write_text('[plugins."owned@fixture"]\nenabled = true\n[plugins."local@fixture"]\nenabled = true\n')
        self.source()
        state = StateManager(self.get_paths())
        state.load_state()["managed"] = {"codex_plugins": ["owned@fixture"]}
        result = self.sync(state)
        self.assertTrue(result.success, result.errors)
        plugins = tomllib.loads(self.config.read_text())["plugins"]
        self.assertFalse(plugins["owned@fixture"]["enabled"])
        self.assertTrue(plugins["local@fixture"]["enabled"])
        self.assertFalse(any("remove" in x for x in self.calls.read_text().splitlines()))

    def test_invalid_source_fails_without_writing_destination(self):
        self.source()
        (self.claude_dir / "settings.json").write_text('{"enabledPlugins": []}')
        before = self.config.read_bytes()
        result = self.sync()
        self.assertFalse(result.success)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.calls.exists())

    def test_failure_is_sanitized_and_not_marked_as_managed(self):
        from state import StateManager
        self.source({"demo@fixture": True})
        self.create_fake_bin("codex", f'#!{sys.executable}\nimport sys\nprint("private-token",file=sys.stderr)\nsys.exit(7)\n')
        state = StateManager(self.get_paths())
        result = self.sync(state)
        self.assertFalse(result.success)
        self.assertNotIn("private-token", str(result.errors))
        self.assertEqual(state.get_managed("codex_plugins"), [])

    def test_dry_run_does_not_call_native_tools_or_create_state(self):
        self.source({"demo@fixture": True})
        before = self.config.read_bytes()
        result = self.sync(dry_run=True)
        self.assertTrue(result.success, result.errors)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.state_dir.exists())

    def test_wrong_mcp_shape_fails_before_any_write(self):
        self.source(mcp={"broken": {"command": ["invalid"]}})
        before = self.config.read_bytes()
        result = self.sync()
        self.assertFalse(result.success)
        self.assertEqual(before, self.config.read_bytes())
        self.assertFalse(self.calls.exists())

    def test_update_refreshes_the_owned_local_catalog_path(self):
        from state import StateManager
        state = StateManager(self.get_paths())
        src = self.temp_dir / "plugin-v1"
        src.mkdir()
        plugin = "demo@fixture"
        self.source({plugin: True}, {plugin: [{"installPath": str(src), "version": "1.0"}]})
        self.assertTrue(self.sync(state).success)
        catalog = self.state_dir / "marketplaces/fixture"
        self.config.write_text(self.config.read_text() + f'\n[marketplaces.fixture]\nsource_type = "local"\nsource = {json.dumps(str(catalog))}\n')
        self.response.write_text(json.dumps({"installed": [{"pluginId": plugin, "enabled": True, "version": "1.0"}]}))
        updated = self.temp_dir / "plugin-v2"
        updated.mkdir()
        self.source({plugin: True}, {plugin: [{"installPath": str(updated), "version": "2.0"}]})
        result = self.sync(state)
        self.assertTrue(result.success, result.errors)
        self.assertEqual((catalog / "demo").resolve(), updated)

    def test_mcp_already_provided_by_native_plugin_is_not_duplicated(self):
        plugin = "demo@fixture"
        self.response.write_text(json.dumps({"installed": [{"pluginId": plugin, "enabled": True, "version": "1.0"}]}))
        root = self.codex_dir / "plugins/cache/fixture/demo/1.0"
        root.mkdir(parents=True)
        (root / ".mcp.json").write_text('{"mcpServers": {"shared": {"command": "fixture"}}}')
        self.source({plugin: True}, mcp={"shared": {"command": "fixture"}})
        result = self.sync()
        self.assertTrue(result.success, result.errors)
        self.assertNotIn("shared", tomllib.loads(self.config.read_text())["mcp_servers"])

    def test_local_alias_handles_manifest_name_without_replacing_native_marketplace(self):
        from state import StateManager
        self.config.write_text('[marketplaces.fixture]\nsource_type = "git"\nsource = "https://example.invalid/native.git"\n')
        root = self.temp_dir / "plugin"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin/plugin.json").write_text('{"name":"actual-name"}')
        self.source({"demo@fixture": True}, {"demo@fixture": [{"installPath": str(root), "version": "1.0"}]})
        state = StateManager(self.get_paths())
        result = self.sync(state)
        self.assertTrue(result.success, result.errors)
        config = tomllib.loads(self.config.read_text())
        alias = "actual-name@agent-sync-fixture"
        self.assertTrue(config["plugins"][alias]["enabled"])
        self.assertEqual(config["marketplaces"]["fixture"]["source"], "https://example.invalid/native.git")
        self.response.write_text(json.dumps({"installed": [{"pluginId": alias, "enabled": True, "version": "1.0"}]}))
        self.source({"demo@fixture": False})
        result = self.sync(state)
        self.assertTrue(result.success, result.errors)
        self.assertFalse(tomllib.loads(self.config.read_text())["plugins"][alias]["enabled"])
