"""Comparte herramientas desde Main; mantiene cuentas y preferencias de Personal."""

from config import SyncResult, read_object, atomic_write_json, safe_create_symlink


def _personal_mcp(paths, state, dry_run, result):
    source_path = paths.home / ".claude.json"
    if not source_path.is_file():
        return
    raw = read_object(source_path).get("mcpServers", {})
    if not isinstance(raw, dict):
        raise ValueError("mcpServers de Claude principal inválido.")
    servers = {}
    for name, server in raw.items():
        if not isinstance(server, dict):
            raise ValueError("Servidor MCP de Claude inválido.")
        clean = {key: server[key] for key in ["command", "args", "env", "url", "headers", "type"] if key in server}
        if not isinstance(clean.get("command", clean.get("url")), str):
            raise ValueError("Servidor MCP de Claude sin command o url válida.")
        if "args" in clean and (not isinstance(clean["args"], list) or not all(isinstance(x, str) for x in clean["args"])):
            raise ValueError("Argumentos MCP de Claude inválidos.")
        for key in ["env", "headers"]:
            if key in clean and (not isinstance(clean[key], dict) or not all(isinstance(v, str) for v in clean[key].values())):
                raise ValueError(f"Campo MCP {key} inválido.")
        if "type" in clean and clean["type"] not in ["stdio", "sse", "http"]:
            raise ValueError("Transporte MCP de Claude inválido.")
        servers[name] = clean
    destination = paths.claude_personal_dir / ".claude.json"
    personal = read_object(destination)
    current = personal.get("mcpServers", {})
    if not isinstance(current, dict):
        raise ValueError("mcpServers de Claude personal inválido.")
    desired = {name: server for name, server in current.items() if name not in state.get_managed("personal_mcp") or name in servers}
    desired.update(servers)
    if desired != current:
        personal["mcpServers"] = desired
        atomic_write_json(destination, personal, dry_run)
        result.operations.append("Personal: MCP globales reconciliados; cuenta preservada.")
    if not dry_run:
        state.save_stage_inventory("personal_mcp", list(servers))


def sync_claude_profiles(paths, state, dry_run=False):
    result = SyncResult()
    main_path = paths.claude_dir / "settings.json"
    personal_path = paths.claude_personal_dir / "settings.json"
    try:
        main = read_object(main_path)
        personal = read_object(personal_path)
        for settings in [main, personal]:
            for key in ["enabledPlugins", "extraKnownMarketplaces"]:
                if not isinstance(settings.get(key, {}), dict):
                    raise ValueError(f"Forma inválida de {key}.")
            if not all(isinstance(v, bool) for v in settings.get("enabledPlugins", {}).values()):
                raise ValueError("Forma inválida: los estados de plugins deben ser booleanos.")
        for sub in ["skills", "agents", "plugins"]:
            target = paths.claude_dir / sub
            if target.exists():
                ok, reason = safe_create_symlink(target, paths.claude_personal_dir / sub, dry_run)
                if reason:
                    result.operations.append(reason)
        if not main_path.is_file():
            return result
        changed = False
        for key, category in [("enabledPlugins", "plugins"), ("extraKnownMarketplaces", "marketplaces")]:
            source = main.get(key, {})
            destination = personal.setdefault(key, {})
            for name in state.get_managed(category):
                if name not in source and name in destination:
                    del destination[name]
                    changed = True
                    result.operations.append(f"Personal: entrada administrada retirada de {key}: {name}.")
            for name, value in source.items():
                if name not in destination or destination[name] != value:
                    destination[name] = value
                    changed = True
                    result.operations.append(f"Personal: herramienta actualizada en {key}: {name}.")
        if changed:
            atomic_write_json(personal_path, personal, dry_run)
        _personal_mcp(paths, state, dry_run, result)
        if not dry_run:
            state.save_stage_inventory("plugins", list(main.get("enabledPlugins", {})))
            state.save_stage_inventory("marketplaces", list(main.get("extraKnownMarketplaces", {})))
    except (ValueError, OSError) as exc:
        result.success = False
        result.errors.append(str(exc) if isinstance(exc, ValueError) else f"Claude: {type(exc).__name__}.")
    return result
