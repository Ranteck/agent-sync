"""
Helper utilities and fixtures for agent-sync unit tests.
Isolated in temporary directories with no side effects on real HOME.
"""

import os
import sys
import stat
import shutil
import tempfile
import unittest
from pathlib import Path

# Add package to sys.path so tests can import from agent_sync
PACKAGE_DIR = Path(__file__).resolve().parent.parent / ".local" / "lib" / "agent-sync"
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


class IsolatedEnvironmentTestCase(unittest.TestCase):
    """
    Base test case that provisions a fully isolated mock HOME and temporary directory.
    Guarantees no reads or writes occur against the user's real ~/.claude, ~/.agents, etc.
    """

    def setUp(self):
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_obj.name).resolve()

        self.mock_home = self.temp_dir / "home"
        self.mock_home.mkdir(parents=True)

        self.claude_dir = self.mock_home / ".claude"
        self.claude_personal_dir = self.mock_home / ".claude-personal"
        self.agents_dir = self.mock_home / ".agents"
        self.gemini_dir = self.mock_home / ".gemini"
        self.codex_dir = self.mock_home / ".codex"
        self.state_dir = self.mock_home / ".local" / "state" / "agent-sync"
        self.projects_dir = self.mock_home / "Proyectos"

        self.bin_dir = self.temp_dir / "bin"
        self.bin_dir.mkdir(parents=True)

        # Ensure directories exist for basic setup
        self.claude_dir.mkdir(parents=True)
        self.claude_personal_dir.mkdir(parents=True)
        self.agents_dir.mkdir(parents=True)
        self.gemini_dir.mkdir(parents=True)
        self.codex_dir.mkdir(parents=True)
        self.projects_dir.mkdir(parents=True)

        # Save and isolate environment variables
        # Use standard system paths only + test bin_dir, avoiding real ~/.local/bin leakage
        self._orig_env = os.environ.copy()
        os.environ["HOME"] = str(self.mock_home)
        os.environ["PATH"] = f"{self.bin_dir}:/usr/local/bin:/usr/bin:/bin"
        os.environ.pop("AGENT_SYNC_ACTIVE", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)
        self.temp_dir_obj.cleanup()

    def get_paths(self):
        from config import SyncPaths
        return SyncPaths(
            home=self.mock_home,
            claude_dir=self.claude_dir,
            claude_personal_dir=self.claude_personal_dir,
            agents_dir=self.agents_dir,
            gemini_dir=self.gemini_dir,
            codex_dir=self.codex_dir,
            state_dir=self.state_dir,
            projects_dir=self.projects_dir,
        )

    def create_fake_bin(self, name: str, script_content: str):
        """
        Creates an executable fake script in self.bin_dir.
        """
        bin_path = self.bin_dir / name
        with open(bin_path, "w", encoding="utf-8") as f:
            f.write(script_content)
        bin_path.chmod(bin_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return bin_path
