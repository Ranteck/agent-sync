import json
import os
import subprocess
import sys
import time

from helpers import IsolatedEnvironmentTestCase, PACKAGE_DIR


class TestRuntime(IsolatedEnvironmentTestCase):
    def test_background_hook_applies_then_skips_without_restarting_tools(self):
        (self.claude_dir / "settings.json").write_text('{"enabledPlugins": {}}')
        (self.claude_personal_dir / "settings.json").write_text('{}')
        calls = self.temp_dir / "codex-calls"
        self.create_fake_bin("codex", f'''#!{sys.executable}
from pathlib import Path
with Path({str(calls)!r}).open("a") as f: f.write("called\\n")
print('{{"installed": []}}')
''')
        cmd = [sys.executable, str(PACKAGE_DIR / "main.py")]
        first = subprocess.run(cmd, env=os.environ.copy(), capture_output=True, text=True, timeout=5)
        self.assertEqual(first.returncode, 0, first.stderr)
        state = self.state_dir / "state.json"
        for _ in range(100):
            if state.exists() and json.loads(state.read_text()).get("last_status") == "success":
                break
            time.sleep(0.02)
        self.assertTrue(state.is_file())
        self.assertEqual(json.loads(state.read_text())["last_status"], "success")
        before = (state.stat().st_mtime_ns, calls.read_text())
        again = subprocess.run(cmd, env=os.environ.copy(), capture_output=True, text=True, timeout=5)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual((state.stat().st_mtime_ns, calls.read_text()), before)

    def test_corrupt_state_is_not_overwritten(self):
        from runner import run_sync
        self.state_dir.mkdir(parents=True)
        state = self.state_dir / "state.json"
        state.write_text("not json")
        code, _ = run_sync(self.get_paths(), mode="apply")
        self.assertEqual(code, 1)
        self.assertEqual(state.read_text(), "not json")
