"""Inventario privado, huella del entorno y exclusión entre procesos."""

import errno
import fcntl
import hashlib
import os
from pathlib import Path
from config import atomic_write_json, safe_read_json


class StateManager:
    def __init__(self, paths):
        self.paths = paths
        self.state_dir = paths.state_dir
        self.state_file = self.state_dir / "state.json"
        self.lock_file = self.state_dir / "sync.lock"
        self._lock_fd = None
        self._state_data = None
        self.load_error = None

    def acquire_lock(self, blocking=False):
        if self._lock_fd is not None:
            return True, False, None
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            return False, False, f"No se pudo abrir el lock: {type(exc).__name__}."
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError as exc:
            os.close(fd)
            contention = exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK)
            return False, contention, None if contention else f"Error de lock: {type(exc).__name__}."
        self._lock_fd = fd
        return True, False, None

    def release_lock(self):
        if self._lock_fd is not None:
            fd, self._lock_fd = self._lock_fd, None
            os.close(fd)

    def load_state(self):
        if self._state_data is None:
            data, error = safe_read_json(self.state_file)
            if self.state_file.exists() and not isinstance(data, dict):
                error = error or "Forma inválida del estado de sincronización."
            self.load_error = error
            self._state_data = data if isinstance(data, dict) else {}
            managed = self._state_data.get("managed", {})
            if not isinstance(managed, dict):
                self.load_error = "Inventario administrado inválido."
        return self._state_data

    def get_managed(self, category):
        values = self.load_state().get("managed", {}).get(category, [])
        if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
            raise ValueError("Categoría de inventario administrado inválida.")
        return list(values)

    def save_stage_inventory(self, category, items, dry_run=False):
        if dry_run:
            return
        state = self.load_state()
        if self.load_error:
            raise ValueError(self.load_error)
        state.setdefault("managed", {})[category] = sorted(set(items))
        atomic_write_json(self.state_file, state)

    def compute_code_version(self):
        digest = hashlib.sha256()
        for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def compute_fingerprint(self):
        digest = hashlib.sha256()
        files = [
            self.paths.claude_dir / "settings.json",
            self.paths.claude_personal_dir / "settings.json",
            self.paths.claude_dir / "plugins/installed_plugins.json",
            self.paths.home / ".claude.json",
            self.paths.claude_dir / "CLAUDE.md",
            self.paths.claude_dir / "RTK.md",
            self.paths.codex_dir / "RTK.md",
            self.paths.claude_dir / "HOUSE-RULES.md",
            self.paths.claude_dir / "AI-NATIVE-SDLC.md",
            self.paths.projects_dir / "house-rules/HOUSE-RULES.md",
            self.paths.projects_dir / "ai-native-sdlc/AI-NATIVE-SDLC.md",
        ]
        files.extend(sorted((self.paths.claude_dir / "plugins/marketplaces").glob("*/.claude-plugin/marketplace.json")))
        for base in [self.paths.claude_dir, self.paths.agents_dir, self.paths.codex_dir]:
            skills = base / "skills"
            if skills.is_dir():
                for child in sorted(skills.iterdir()):
                    if child.name.startswith("."):
                        continue
                    files.append(child)
                    if child.name == "synced":
                        files.extend(sorted(child.glob("*/*/SKILL.md")))
        for path in files:
            digest.update(str(path).encode())
            if path.exists():
                meta = path.stat()
                digest.update(f"{meta.st_mtime_ns}:{meta.st_size}".encode())
            else:
                digest.update(b"missing")
        return digest.hexdigest()

    def is_up_to_date(self):
        state = self.load_state()
        return bool(not self.load_error and state.get("last_status") == "success"
                    and state.get("code_version") == self.compute_code_version()
                    and state.get("fingerprint") == self.compute_fingerprint())

    def save_success(self, dry_run=False):
        if not dry_run:
            state = self.load_state()
            state.update(last_status="success", code_version=self.compute_code_version(),
                         fingerprint=self.compute_fingerprint(), errors=[])
            atomic_write_json(self.state_file, state)

    def record_failure(self, errors, dry_run=False):
        if not dry_run:
            state = self.load_state()
            state.update(last_status="failure", errors=errors)
            atomic_write_json(self.state_file, state)
