"""Hook ligero y reconciliación exclusiva; la simulación nunca adquiere ni crea locks."""

import os
import subprocess
import sys
from pathlib import Path
from config import SyncResult
from state import StateManager
from claude_sync import sync_claude_profiles
from skills_sync import sync_skills
from antigravity_sync import sync_antigravity
from codex_sync import sync_codex


def run_sync(paths, mode="apply", log_file=None):
    if os.environ.get("AGENT_SYNC_ACTIVE") == "1" and mode == "check":
        return 0, ["Recursión evitada: sincronización activa en el proceso padre."]
    state = StateManager(paths)
    state.load_state()
    if state.load_error:
        return 1, [state.load_error]
    if mode == "check":
        if state.is_up_to_date():
            return 0, []
        paths.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        logfile = log_file or paths.state_dir / "sync.log"
        fd = os.open(logfile, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        env = os.environ.copy()
        env["AGENT_SYNC_ACTIVE"] = "1"
        with os.fdopen(fd, "a") as out:
            subprocess.Popen([sys.executable, str(Path(__file__).with_name("main.py")), "--apply"],
                             env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                             start_new_session=True)
        return 0, ["Reconciliación iniciada en segundo plano; consulte ~/.local/state/agent-sync/sync.log."]
    dry_run = mode == "dry_run"
    if mode not in ["dry_run", "apply"]:
        return 1, ["Modo de sincronización inválido."]
    if not dry_run:
        acquired, contention, error = state.acquire_lock()
        if not acquired:
            return (0, ["Otro proceso tiene el bloqueo de sincronización."]) if contention else (1, [error])
    original_active = os.environ.get("AGENT_SYNC_ACTIVE")
    os.environ["AGENT_SYNC_ACTIVE"] = "1"
    result = SyncResult()
    try:
        for stage in [sync_claude_profiles, sync_skills, sync_antigravity, sync_codex]:
            try:
                result.merge(stage(paths, state, dry_run=dry_run))
            except (ValueError, OSError) as exc:
                result.success = False
                result.errors.append(f"{stage.__name__}: {type(exc).__name__}.")
        if not dry_run:
            if result.success:
                state.save_success()
            else:
                state.record_failure(result.errors)
        return (0 if result.success else 1), result.operations + [f"Error: {e}" for e in result.errors]
    finally:
        if original_active is None:
            os.environ.pop("AGENT_SYNC_ACTIVE", None)
        else:
            os.environ["AGENT_SYNC_ACTIVE"] = original_active
        if not dry_run:
            state.release_lock()
