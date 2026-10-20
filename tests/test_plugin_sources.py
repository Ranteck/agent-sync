import json

from helpers import IsolatedEnvironmentTestCase


class TestPluginSources(IsolatedEnvironmentTestCase):
    def catalog(self, entries):
        folder = self.claude_dir / "plugins/marketplaces/fixture/.claude-plugin"
        folder.mkdir(parents=True)
        (folder / "marketplace.json").write_text(json.dumps({"plugins": entries}))

    def test_monorepo_path_selects_the_actual_plugin(self):
        from plugin_sources import prepare_source
        root = self.temp_dir / "repo"
        plugin = root / "claude-plugin/demo"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin/plugin.json").write_text('{"name":"demo"}')
        self.catalog([{"name":"demo","source":{"path":"claude-plugin/demo"}}])
        result = prepare_source(self.get_paths(), "demo@fixture", {"installPath": str(root)})
        self.assertEqual(result["installPath"], str(plugin))

    def test_skill_collection_includes_only_declared_skills(self):
        from plugin_sources import prepare_source
        root = self.temp_dir / "collection"
        for name in ["approved", "extra"]:
            (root / name).mkdir(parents=True)
            (root / name / "SKILL.md").write_text("# Fixture")
        self.catalog([{"name":"demo","skills":["./approved"]}])
        record = {"installPath":str(root),"version":"1.0"}
        preview = prepare_source(self.get_paths(), "demo@fixture", record, dry_run=True)
        self.assertFalse(self.state_dir.exists())
        result = prepare_source(self.get_paths(), "demo@fixture", record)
        self.assertEqual(result["installPath"], preview["installPath"])
        from pathlib import Path
        self.assertEqual([p.name for p in (Path(result["installPath"])/"skills").iterdir()], ["approved"])
