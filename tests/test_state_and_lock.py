"""
Unit tests for state tracking, fingerprinting, locking, and recursion guard.
"""

import os
import json
import fcntl
import unittest
from pathlib import Path
from helpers import IsolatedEnvironmentTestCase


class TestStateAndLock(IsolatedEnvironmentTestCase):

    def test_idempotence_and_no_native_cli_call_when_up_to_date(self):
        """
        After an initial apply, running again when nothing has changed should
        detect matching fingerprint and NOT invoke native CLIs.
        """
        from runner import run_sync
        from state import StateManager

        paths = self.get_paths()

        # Create basic main settings
        (paths.claude_dir / "settings.json").write_text(json.dumps({"enabledPlugins": {}}))
        (paths.claude_personal_dir / "settings.json").write_text(json.dumps({"enabledPlugins": {}}))

        # Fake agy and codex that record invocations in a log file
        log_file = self.temp_dir / "invocations.log"
        self.create_fake_bin("agy", f"""#!/usr/bin/env bash
echo "agy called" >> "{log_file}"
if [[ "$1" == "plugin" && "$2" == "list" ]]; then
    echo '[]'
fi
exit 0
""")
        self.create_fake_bin("codex", f"""#!/usr/bin/env bash
echo "codex called" >> "{log_file}"
echo '{{"installed": []}}'
exit 0
""")

        # First run: apply
        exit_code, _ = run_sync(paths, mode="apply")
        self.assertEqual(exit_code, 0)

        # Check state was written as success
        state_mgr = StateManager(paths)
        state_data = state_mgr.load_state()
        self.assertEqual(state_data.get("last_status"), "success")

        # Invocations should exist in log
        initial_invocations = log_file.read_text().strip().splitlines()
        self.assertGreater(len(initial_invocations), 0)

        # Second run: fast hook check (mode="check")
        # Nothing changed, so it should be up to date and NOT invoke native CLIs
        exit_code2, _ = run_sync(paths, mode="check")
        self.assertEqual(exit_code2, 0)

        # Log count should NOT have increased!
        after_invocations = log_file.read_text().strip().splitlines()
        self.assertEqual(len(initial_invocations), len(after_invocations))

    def test_lock_prevents_concurrent_sync(self):
        """
        Verify that fcntl.flock on sync.lock detects contention and exits cleanly.
        """
        from runner import run_sync

        paths = self.get_paths()
        paths.state_dir.mkdir(parents=True, exist_ok=True)
        lock_file = paths.state_dir / "sync.lock"

        # Hold lock externally
        lock_fd = open(lock_file, "w")
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            exit_code, ops = run_sync(paths, mode="apply")
            # Should detect lock held and return cleanly without crashing
            self.assertEqual(exit_code, 0)
            self.assertTrue(any("bloqueo" in op.lower() or "lock" in op.lower() or "otro proceso" in op.lower() for op in ops))
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()

    def test_recursion_guard_agent_sync_active(self):
        """
        Verify AGENT_SYNC_ACTIVE environment variable prevents nested execution.
        """
        from runner import run_sync

        paths = self.get_paths()
        os.environ["AGENT_SYNC_ACTIVE"] = "1"

        exit_code, ops = run_sync(paths, mode="check")
        self.assertEqual(exit_code, 0)
        self.assertTrue(any("recursión" in op.lower() or "active" in op.lower() for op in ops))

    def test_failure_does_not_declare_success_state(self):
        """
        If a failure occurs during reconciliation, state.json must NOT declare status=success.
        """
        from runner import run_sync
        from state import StateManager

        paths = self.get_paths()

        # Malformed settings.json
        (paths.claude_dir / "settings.json").write_text("{bad json")
        (paths.claude_personal_dir / "settings.json").write_text("{}")

        exit_code, _ = run_sync(paths, mode="apply")
        self.assertNotEqual(exit_code, 0)

        state_mgr = StateManager(paths)
        state_data = state_mgr.load_state()
        self.assertNotEqual(state_data.get("last_status"), "success")

    def test_state_reports_corrupt_json_error(self):
        """
        If state.json is corrupted, load_state must expose the error, not silently swallow it.
        """
        from state import StateManager

        paths = self.get_paths()
        paths.state_dir.mkdir(parents=True, exist_ok=True)
        (paths.state_dir / "state.json").write_text("{bad state json")

        state_mgr = StateManager(paths)
        data = state_mgr.load_state()
        self.assertIsNotNone(state_mgr.load_error)
        self.assertTrue("JSON inválido" in state_mgr.load_error)

    def test_stage_inventory_persistence_on_subsequent_failure(self):
        """
        If stage 1 (Claude profiles) succeeds and stage 2 fails,
        managed items from stage 1 must be preserved in state, not lost.
        """
        from runner import run_sync
        from state import StateManager

        paths = self.get_paths()

        # Valid claude settings
        main_settings = {"enabledPlugins": {"plugin-managed": True}}
        (paths.claude_dir / "settings.json").write_text(json.dumps(main_settings))
        (paths.claude_personal_dir / "settings.json").write_text(json.dumps({"enabledPlugins": {}}))

        # Make antigravity stage fail with broken fake binary
        self.create_fake_bin("agy", """#!/usr/bin/env bash
exit 5
""")
        exit_code, _ = run_sync(paths, mode="apply")
        self.assertNotEqual(exit_code, 0)

        # State should record failure, BUT managed plugins from stage 1 must be preserved
        state_mgr = StateManager(paths)
        state_data = state_mgr.load_state()
        self.assertEqual(state_data.get("last_status"), "failure")
        self.assertIn("plugin-managed", state_mgr.get_managed("plugins"))


if __name__ == "__main__":
    unittest.main()
