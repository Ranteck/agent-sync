"""Enlaces de skills con precedencia local y bajas limitadas a enlaces administrados."""

from config import SyncResult, safe_create_symlink


def discover_skills_in_agents(root):
    found = {}
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.name.startswith(".") and child.name != "synced" and child.exists():
                found[child.name] = child
        for skill in sorted((root / "synced").glob("*/*/SKILL.md")):
            found.setdefault(skill.parent.name, skill.parent)
    return found


def link_skills(source, destination, state, category, dry_run, result):
    previous = set(state.get_managed(category))
    owned = set()
    for name in previous - set(source):
        target = destination / name
        if target.is_symlink() and not target.exists():
            if not dry_run:
                target.unlink()
            result.operations.append(f"Skill administrada retirada: {name}.")
    for name, origin in source.items():
        target = destination / name
        # Un enlace que ya remite al destino no es una fuente independiente.
        if origin.is_symlink() and origin.resolve() == target.resolve():
            if name in previous:
                owned.add(name)
            continue
        existed = target.exists() or target.is_symlink()
        ok, reason = safe_create_symlink(origin, target, dry_run)
        if ok:
            if not existed or name in previous:
                owned.add(name)
            if not existed:
                result.operations.append(f"Skill vinculada: {name}.")
        elif reason:
            result.operations.append(reason)
    if not dry_run:
        state.save_stage_inventory(category, sorted(owned))


def sync_skills(paths, state, dry_run=False):
    result = SyncResult()
    try:
        claude = paths.claude_dir / "skills"
        agents = paths.agents_dir / "skills"
        from_agents = discover_skills_in_agents(agents)
        from_claude = discover_skills_in_agents(claude)
        link_skills(from_agents, claude, state, "skills_claude", dry_run, result)
        # La simulación debe proyectar los enlaces que todavía no creó.
        if not dry_run:
            from_claude = discover_skills_in_agents(claude)
        link_skills(from_claude, agents, state, "skills_agents", dry_run, result)
    except (OSError, ValueError) as exc:
        result.success = False
        result.errors.append(f"Skills: {type(exc).__name__}.")
    return result
