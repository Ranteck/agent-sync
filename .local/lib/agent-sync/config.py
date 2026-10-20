"""
Configuración, rutas y utilidades de E/S atómica y segura para agent-sync.
"""

import os
import sys
import json
import subprocess
import hashlib
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Tuple, Dict, List


@dataclass
class SyncPaths:
    home: Path
    claude_dir: Path
    claude_personal_dir: Path
    agents_dir: Path
    gemini_dir: Path
    codex_dir: Path
    state_dir: Path
    projects_dir: Path

    @classmethod
    def from_env(cls, home: Optional[Path] = None) -> "SyncPaths":
        if home is None:
            home = Path(os.environ.get("HOME", Path.home())).resolve()
        else:
            home = Path(home).resolve()

        return cls(
            home=home,
            claude_dir=home / ".claude",
            claude_personal_dir=home / ".claude-personal",
            agents_dir=home / ".agents",
            gemini_dir=home / ".gemini",
            codex_dir=home / ".codex",
            state_dir=home / ".local" / "state" / "agent-sync",
            projects_dir=home / "Proyectos",
        )


@dataclass
class SyncResult:
    success: bool = True
    operations: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def merge(self, other: "SyncResult") -> None:
        if not other.success:
            self.success = False
        self.operations.extend(other.operations)
        self.errors.extend(other.errors)


def safe_read_json(path: Path):
    if not path.exists():
        return None, None
    try:
        return json.loads(path.read_text()), None
    except json.JSONDecodeError as exc:
        return None, f"JSON inválido en {path.name}: línea {exc.lineno}, columna {exc.colno}."
    except OSError as exc:
        return None, f"No se pudo leer {path.name}: {type(exc).__name__}."


def read_object(path: Path):
    data, error = safe_read_json(path)
    if error:
        raise ValueError(error)
    if data is None and not path.exists():
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Forma inválida en {path.name}: se esperaba un objeto JSON.")
    return data


def atomic_write_text(path: Path, text: str, dry_run: bool = False):
    if dry_run:
        return True
    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
    if target.exists():
        backup_dir = Path.home() / ".local/state/agent-sync/backups"
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = backup_dir / (hashlib.sha256(str(target).encode()).hexdigest() + ".bak")
        if not backup.exists():
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(target.read_bytes())
    fd, name = tempfile.mkstemp(prefix=".agent-sync-", dir=target.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(text)
            out.flush()
            os.fchmod(out.fileno(), mode)
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    return True


def atomic_write_json(path: Path, data, dry_run: bool = False):
    return atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", dry_run)


def safe_create_symlink(target: Path, link_path: Path, dry_run: bool = False):
    if link_path.is_symlink():
        if link_path.resolve() == target.resolve():
            return True, None
        return False, f"Enlace ajeno preservado: {link_path.name}."
    if link_path.exists():
        return False, f"Adaptación local preservada: {link_path.name}."
    if not dry_run:
        link_path.parent.mkdir(parents=True, exist_ok=True)
        link_path.symlink_to(target)
    return True, None


def run_command_safe(cmd, timeout=120):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=False)
        return proc.returncode, f"{Path(cmd[0]).name}: código {proc.returncode}."
    except OSError as exc:
        return -1, f"{Path(cmd[0]).name}: {type(exc).__name__}."
    except subprocess.TimeoutExpired:
        return -2, f"{Path(cmd[0]).name}: tiempo límite superado."
