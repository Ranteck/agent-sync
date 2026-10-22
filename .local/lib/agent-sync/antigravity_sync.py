"""Importa plugins instalados de Claude y conserva extensiones propias de Antigravity."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from config import SyncResult, read_object, atomic_write_json, safe_create_symlink
from codex_sync import build_shared_rules, sync_shared_rules
from native_codex import _mcp
from plugin_sources import prepare_source


def _agy(binary, args, expect_json=False):
    env = os.environ.copy()
    env["AGENT_SYNC_ACTIVE"] = "1"
    try:
        proc = subprocess.run([binary, "plugin", *args], env=env, capture_output=True,
                              text=True, timeout=120, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Antigravity {args[0]}: {type(exc).__name__}.") from exc
    if proc.returncode:
        raise ValueError(f"Antigravity {args[0]} falló (código {proc.returncode}).")
    if not expect_json:
        return None
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Inventario de Antigravity: JSON inválido.") from exc
    if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("name"), str) for r in rows):
        raise ValueError("Inventario de Antigravity inválido.")
    return {r["name"]: r for r in rows}


def sync_antigravity(paths, state, dry_run=False):
    result = SyncResult()
    try:
        sync_shared_rules(build_shared_rules(paths), paths.gemini_dir / "GEMINI.md", state,
                          "gemini_rules_hash", "Antigravity", dry_run, result)
        config = paths.gemini_dir / "config"
        settings = read_object(paths.claude_dir / "settings.json")
        enabled = settings.get("enabledPlugins", {})
        if not isinstance(enabled, dict) or not all(isinstance(v, bool) for v in enabled.values()):
            raise ValueError("Estados de plugins de Claude inválidos.")
        raw = read_object(paths.claude_dir / "plugins/installed_plugins.json").get("plugins", {})
        if not isinstance(raw, dict):
            raise ValueError("Inventario de plugins de Claude inválido.")
        records = {}
        for plugin_id, rows in raw.items():
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("Registro de instalación de Claude inválido.")
            record = next((row for row in rows if row.get("scope", "user") == "user"), None)
            if record:
                root = record.get("installPath")
                if not isinstance(root, str) or not Path(root).is_absolute():
                    raise ValueError("Ruta de plugin de Claude inválida.")
                records[plugin_id] = prepare_source(paths, plugin_id, record, dry_run) if enabled.get(plugin_id) is True else record
        skills_file = config / "skills.json"
        skills = read_object(skills_file)
        entries = skills.get("entries", [])
        if not isinstance(entries, list) or any(not isinstance(e, dict) or not isinstance(e.get("path"), str) for e in entries):
            raise ValueError("entries de skills.json inválido.")
        for e in entries:
            for key in ["include_only", "exclude"]:
                if key in e and (not isinstance(e[key], list) or not all(isinstance(v, str) for v in e[key])):
                    raise ValueError("Filtro de skills inválido.")
        mcp_file = config / "mcp_config.json"
        mcp = read_object(mcp_file)
        current_servers = mcp.get("mcpServers", {})
        if not isinstance(current_servers, dict):
            raise ValueError("mcpServers de Antigravity inválido.")
        global_config = read_object(paths.home / ".claude.json")
        servers, omitted = _mcp(global_config.get("mcpServers", {}))
        servers = {name: {(key if key != "http_headers" else "headers"): value for key, value in server.items()} for name, server in servers.items()}
        result.operations.extend(omitted)
        previous = set(state.get_managed("agy_imported_plugins"))
        managed = state.load_state().get("managed", {})
        old_versions = managed.get("agy_plugin_versions", {})
        if not isinstance(old_versions, dict):
            raise ValueError("Estado de versiones de Antigravity inválido.")
        binary = shutil.which("agy")
        imported = set()
        versions = dict(old_versions)
        owned = set(previous)
        native = _agy(binary, ["list", "--json"], True) if binary and not dry_run and (records or previous) else {}
        for plugin_id, record in records.items():
            if enabled.get(plugin_id) is not True:
                continue
            name = plugin_id.split("@")[0]
            root = Path(record["installPath"])
            claude_manifest = root / ".claude-plugin/plugin.json"
            manifest = read_object(claude_manifest if claude_manifest.is_file() else root / "plugin.json")
            name = manifest.get("name", name)
            if not isinstance(name, str) or not name or "/" in name:
                raise ValueError("Nombre de plugin de Antigravity inválido.")
            components = bool(record.get("_prepared_skills")) or any(manifest.get(k) for k in ["skills", "agents", "commands", "hooks", "mcpServers"])
            components |= any((root / k).exists() for k in ["skills", "agents", "commands", "hooks/hooks.json", ".mcp.json"])
            if not components:
                result.operations.append(f"Antigravity: {plugin_id} sin componentes importables; se omite.")
                continue
            if not claude_manifest.is_file() and not (root / "gemini-extension.json").is_file() and not record.get("_prepared_skills"):
                result.operations.append(f"Antigravity: formato nativo de {plugin_id} no importable; se comparten sus skills compatibles.")
                continue
            version = hashlib.sha256(json.dumps({"path": str(root), "version": record.get("version", ""), "revision": record.get("_source_revision", "")}, sort_keys=True).encode()).hexdigest()
            if binary:
                if dry_run:
                    result.operations.append(f"Antigravity: importar {plugin_id} desde su ruta instalada.")
                elif name in native and plugin_id not in previous:
                    result.operations.append(f"Antigravity: extensión propia {name} preservada.")
                else:
                    if name not in native or old_versions.get(plugin_id) != version:
                        _agy(binary, ["import", str(root)])
                        native = _agy(binary, ["list", "--json"], True)
                        if name not in native:
                            raise ValueError(f"Antigravity no confirmó la importación de {name}.")
                        result.operations.append(f"Antigravity: plugin import {root} confirmado.")
                    if native[name].get("enabled") is False:
                        _agy(binary, ["enable", name])
                    owned.add(plugin_id)
                    versions[plugin_id] = version
                    state.load_state().setdefault("managed", {})["agy_plugin_versions"] = versions
                    state.save_stage_inventory("agy_imported_plugins", sorted(owned))
                imported.add(plugin_id)
        if binary and not dry_run:
            for plugin_id in previous:
                if enabled.get(plugin_id) is not True:
                    name = plugin_id.split("@")[0]
                    if name in native and native[name].get("enabled", True):
                        _agy(binary, ["disable", name])
                        result.operations.append(f"Antigravity: {name} desactivado; caché conservada.")
        if not binary:
            result.operations.append("CLI agy ausente; se omite la importación nativa y se comparten skills compatibles.")
        claude = paths.claude_dir / "skills"
        agents = paths.agents_dir / "skills"
        wanted = [str(claude), str(agents)]
        for base in [claude, agents]:
            nested = base / "synced"
            if nested.is_dir():
                wanted.extend(str(group) for group in sorted(nested.iterdir()) if group.is_dir())
        legacy = set()
        for plugin_id, record in records.items():
            root = Path(record["installPath"])
            for candidate in [root / "skills", root / "cli/assets/skills", root / ".claude/skills"]:
                if candidate.is_dir():
                    legacy.add(str(candidate))
                    if enabled.get(plugin_id) is True and plugin_id not in imported:
                        wanted.append(str(candidate))
        old_paths = set(state.get_managed("gemini_plugin_skill_paths"))
        # El script anterior generaba listas de rutas sin filtros ni otras propiedades.
        legacy_format = all(set(e) == {"path"} for e in entries) and {str(claude), str(agents)}.issubset({e["path"] for e in entries})
        retired = old_paths | (legacy if legacy_format else set())
        custom = [{key: e[key] for key in ["path", "include_only", "exclude"] if key in e}
                  for e in entries if e["path"] not in retired and e["path"] not in wanted]
        target_entries = [{"path": path} for path in dict.fromkeys(wanted)] + custom
        if entries != target_entries:
            atomic_write_json(skills_file, {**skills, "entries": target_entries}, dry_run)
            result.operations.append("Antigravity: rutas de skills reconciliadas.")
        target_servers = dict(current_servers)
        if (paths.home / ".claude.json").is_file():
            for name in state.get_managed("gemini_mcp"):
                if name not in servers:
                    target_servers.pop(name, None)
        target_servers.update(servers)
        if current_servers != target_servers:
            atomic_write_json(mcp_file, {**mcp, "mcpServers": target_servers}, dry_run)
            result.operations.append("Antigravity: MCP globales reconciliados.")
        if claude.exists():
            safe_create_symlink(claude, config / "skills", dry_run)
        if not dry_run:
            state.save_stage_inventory("agy_imported_plugins", sorted(owned))
            state.save_stage_inventory("gemini_plugin_skill_paths", list(dict.fromkeys(wanted)))
            state.save_stage_inventory("gemini_mcp", list(servers))
            state.load_state().setdefault("managed", {})["agy_plugin_versions"] = versions
    except (ValueError, OSError, TypeError) as exc:
        result.success = False
        result.errors.append(str(exc) if isinstance(exc, ValueError) else f"Antigravity: {type(exc).__name__}.")
    return result
