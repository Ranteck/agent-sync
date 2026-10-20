"""Reglas y skills compartidas, con instalación nativa de plugins de Codex."""

import hashlib
from config import SyncResult, read_object, atomic_write_json, atomic_write_text
from skills_sync import discover_skills_in_agents, link_skills
from native_codex import sync_native_codex


def sync_codex(paths, state, dry_run=False):
    result = SyncResult()
    if not paths.codex_dir.is_dir():
        result.operations.append("Codex no instalado; se omite su sincronización.")
        return result
    try:
        parts = []
        rtk = paths.claude_dir / "RTK.md"
        if not rtk.is_file():
            rtk = paths.codex_dir / "RTK.md"
        if rtk.is_file():
            parts.append(rtk.read_text().strip())
        for directory, filename, marker in [("house-rules", "HOUSE-RULES.md", "house-rules"),
                                             ("ai-native-sdlc", "AI-NATIVE-SDLC.md", "ai-native-sdlc")]:
            source = paths.projects_dir / directory / filename
            if not source.is_file():
                source = paths.claude_dir / filename
            if source.is_file():
                parts.append(f"<!-- {marker}:start -->\n{source.read_text().strip()}\n<!-- {marker}:end -->")
        source = paths.claude_dir / "CLAUDE.md"
        if source.is_file():
            rules = "\n".join(line for line in source.read_text().splitlines() if not line.startswith("@")).strip()
            if rules:
                parts.append(f"<!-- claude-guidelines:start -->\n{rules}\n<!-- claude-guidelines:end -->")
        desired = "\n\n".join(parts) + "\n" if parts else ""
        target = paths.codex_dir / "AGENTS.md"
        current = target.read_text() if target.is_file() else ""
        owned = state.load_state().setdefault("managed", {})
        digest = hashlib.sha256(current.encode()).hexdigest()
        if desired and (not current or current == desired or owned.get("codex_rules_hash") == digest):
            if desired != current:
                atomic_write_text(target, desired, dry_run)
                result.operations.append("Codex: reglas compartidas actualizadas.")
            if not dry_run:
                owned["codex_rules_hash"] = hashlib.sha256(desired.encode()).hexdigest()
        elif desired != current and desired:
            result.operations.append("Codex: AGENTS.md propio preservado; no coincide con una versión administrada.")
        origin = discover_skills_in_agents(paths.claude_dir / "skills")
        link_skills(origin, paths.codex_dir / "skills", state, "skills_codex", dry_run, result)
        hooks_path = paths.codex_dir / "hooks.json"
        if hooks_path.is_file():
            hooks = read_object(hooks_path)
            table = hooks.get("hooks", {})
            if not isinstance(table, dict) or not isinstance(table.get("SessionStart", []), list):
                raise ValueError("Forma inválida de hooks de Codex.")
            groups = table.get("SessionStart", [])
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
                    raise ValueError("Grupo de hooks de Codex inválido.")
            present = any("sync-agents" in hook.get("command", "") or "sync-agent-env.py" in hook.get("command", "")
                          for group in groups for hook in group.get("hooks", []) if isinstance(hook, dict))
            if not present:
                table.setdefault("SessionStart", []).append({"hooks": [{"type": "command", "command": "sync-agents", "timeout": 5}]})
                hooks["hooks"] = table
                atomic_write_json(hooks_path, hooks, dry_run)
                result.operations.append("Codex: hook de inicio agregado; conserva la autorización nativa de hooks.")
        result.merge(sync_native_codex(paths, state, dry_run))
    except (OSError, ValueError) as exc:
        result.success = False
        result.errors.append(str(exc) if isinstance(exc, ValueError) else f"Codex: {type(exc).__name__}.")
    return result
