"""Plugins mediante el instalador de Codex y traducción de MCP globales."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from config import SyncResult, atomic_write_json, atomic_write_text, safe_read_json
from plugin_sources import prepare_source


def _object(path):
    data, error = safe_read_json(path)
    if error:
        raise ValueError(error)
    if data is None and not path.exists():
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Se esperaba un objeto JSON en {path.name}.")
    return data


def _command(binary, args, paths):
    env = os.environ.copy()
    env.update(CODEX_HOME=str(paths.codex_dir), AGENT_SYNC_ACTIVE="1")
    try:
        proc = subprocess.run([binary, *args], env=env, capture_output=True,
                              text=True, timeout=120, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Codex {args[1]}: {type(exc).__name__}.") from exc
    if proc.returncode:
        raise ValueError(f"Codex {args[1]} falló (código {proc.returncode}).")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Codex {args[1]} devolvió JSON inválido.") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Codex {args[1]} devolvió un contrato inválido.")
    return data


def _config(path):
    text = path.read_text() if path.exists() else ""
    return text, tomllib.loads(text)


def _header(line):
    stripped = line.strip()
    if not stripped.startswith("[") or stripped.startswith("[["):
        return None
    try:
        node = tomllib.loads(stripped)
    except tomllib.TOMLDecodeError:
        return None
    parts = []
    while isinstance(node, dict) and len(node) == 1:
        key, value = next(iter(node.items()))
        if not isinstance(value, dict):
            return None
        parts.append(key)
        node = value
    return tuple(parts) if parts and node == {} else None


def _toml(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml(x) for x in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml(k)} = {_toml(v)}" for k, v in value.items()) + " }"
    raise ValueError("Tipo no admitido para un campo administrado de Codex.")


def _table(text, path, values):
    """Reemplaza únicamente una tabla administrada y sus subtablas."""
    lines = text.splitlines(keepends=True)
    output = []
    dropping = False
    for line in lines:
        header = _header(line)
        if header is not None:
            dropping = header[:len(path)] == path
        if not dropping:
            output.append(line)
    result = "".join(output).rstrip() + "\n"
    if values is not None:
        result += "\n[" + ".".join(_toml(p) for p in path) + "]\n"
        result += "".join(f"{_toml(k)} = {_toml(v)}\n" for k, v in values.items())
    tomllib.loads(result)
    return result


def _enabled(text, plugin_id, enabled):
    """Conserva el resto del cuerpo de la tabla del plugin, incluidos comentarios."""
    lines = text.splitlines(keepends=True)
    active = False
    found = False
    changed = False
    output = []
    for line in lines:
        header = _header(line)
        if header is not None:
            if active and not changed:
                output.append(f"enabled = {_toml(enabled)}\n")
                changed = True
            active = header == ("plugins", plugin_id)
            found |= active
        if active and re.match(r'^\s*(enabled|"enabled"|\x27enabled\x27)\s*=', line):
            line = f"enabled = {_toml(enabled)}\n"
            changed = True
        output.append(line)
    if active and not changed:
        output.append(f"enabled = {_toml(enabled)}\n")
    if not found:
        return _table(text, ("plugins", plugin_id), {"enabled": enabled})
    result = "".join(output)
    if tomllib.loads(result)["plugins"][plugin_id]["enabled"] is not enabled:
        raise ValueError("No se pudo actualizar el estado del plugin en Codex.")
    return result


def _mcp(servers):
    if not isinstance(servers, dict):
        raise ValueError("mcpServers debe ser un objeto.")
    output = {}
    omitted = []
    for name, server in servers.items():
        if not isinstance(server, dict) or not isinstance(name, str):
            raise ValueError("Servidor MCP inválido.")
        if server.get("type") == "sse":
            omitted.append(f"MCP {name}: transporte SSE requiere adaptación; se omite.")
            continue
        if "command" in server:
            if not isinstance(server["command"], str) or not server["command"]:
                raise ValueError(f"MCP {name}: command inválido.")
            clean = {"command": server["command"]}
            if "args" in server:
                if not isinstance(server["args"], list) or not all(isinstance(x, str) for x in server["args"]):
                    raise ValueError(f"MCP {name}: args inválido.")
                clean["args"] = server["args"]
            if "env" in server:
                if not isinstance(server["env"], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in server["env"].items()):
                    raise ValueError(f"MCP {name}: env inválido.")
                clean["env"] = {k: v for k, v in server["env"].items()}
        elif isinstance(server.get("url"), str) and server["url"].startswith(("http://", "https://")):
            clean = {"url": server["url"]}
            if "headers" in server:
                if not isinstance(server["headers"], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in server["headers"].items()):
                    raise ValueError(f"MCP {name}: headers inválido.")
                clean["http_headers"] = {k: v for k, v in server["headers"].items()}
        else:
            raise ValueError(f"MCP {name}: falta command o URL HTTP válida.")
        output[name] = clean
    return output, omitted


def _plugin_name(plugin_id, record):
    root = Path(record["installPath"])
    for manifest_path in [root / ".codex-plugin/plugin.json", root / "plugin.json", root / ".claude-plugin/plugin.json"]:
        if manifest_path.is_file():
            name = _object(manifest_path).get("name")
            if not isinstance(name, str) or not re.fullmatch(r"[\w.-]+", name, re.ASCII):
                raise ValueError("Nombre de manifiesto de plugin inválido.")
            return name
    return plugin_id.split("@")[0]


def _local_marketplace(paths, market, records, target_market=None):
    target_market = target_market or market
    root = paths.state_dir / "marketplaces" / target_market
    plugins = []
    names = set()
    for plugin_id, record in records.items():
        if plugin_id.split("@")[1] != market:
            continue
        source = Path(record["installPath"])
        if not source.is_dir():
            continue
        name = _plugin_name(plugin_id, record)
        if name in names:
            raise ValueError("Nombres de plugins en conflicto en el catálogo administrado.")
        names.add(name)
        link = root / name
        root.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            if link.resolve() != source.resolve():
                link.unlink()
                link.symlink_to(source)
        elif not link.exists():
            link.symlink_to(source)
        else:
            raise ValueError("Conflicto en el catálogo local administrado de Codex.")
        plugins.append({"name": name, "source": f"./{name}"})
    atomic_write_json(root / ".claude-plugin" / "marketplace.json", {
        "name": target_market, "owner": {"name": "agent-sync"}, "plugins": plugins,
    })
    return root


def _plugin_mcp_names(paths, native, desired, added_paths):
    names = set()
    for plugin_id, row in native.items():
        if not desired.get(plugin_id, row.get("enabled", True)):
            continue
        if not re.fullmatch(r"[\w.-]+@[\w.-]+", plugin_id, re.ASCII):
            raise ValueError("Identificador nativo de plugin inválido.")
        name, market = plugin_id.split("@")
        roots = [paths.codex_dir / "plugins/cache" / market / name / str(row.get("version", ""))]
        if plugin_id in added_paths:
            roots.append(added_paths[plugin_id])
        for root in roots:
            manifest = _object(root / ".codex-plugin/plugin.json")
            definitions = manifest.get("mcpServers", {})
            if isinstance(definitions, dict):
                names.update(definitions)
            elif isinstance(definitions, str):
                loaded = _object(root / definitions)
                names.update(loaded.get("mcpServers", loaded))
            loaded = _object(root / ".mcp.json")
            names.update(loaded.get("mcpServers", loaded))
    return names


def sync_native_codex(paths, state, dry_run=False):
    result = SyncResult()
    config_path = paths.codex_dir / "config.toml"
    try:
        settings_path = paths.claude_dir / "settings.json"
        if not settings_path.is_file():
            result.operations.append("Claude principal sin settings.json; se preserva Codex.")
            return result
        settings = _object(settings_path)
        desired = settings.get("enabledPlugins", {})
        if not isinstance(desired, dict) or not all(isinstance(k, str) and isinstance(v, bool) for k, v in desired.items()):
            raise ValueError("enabledPlugins debe mapear identificadores a booleanos.")
        for plugin_id in desired:
            if not re.fullmatch(r"[\w.-]+@[\w.-]+", plugin_id, re.ASCII):
                raise ValueError("Identificador de plugin inválido.")
        inventory = _object(paths.claude_dir / "plugins" / "installed_plugins.json").get("plugins", {})
        if not isinstance(inventory, dict):
            raise ValueError("Inventario de plugins inválido.")
        records = {}
        for plugin_id in desired:
            rows = inventory.get(plugin_id, [])
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                raise ValueError("Registro de instalación de plugin inválido.")
            row = next((r for r in rows if r.get("scope", "user") == "user"), None)
            if row is not None:
                if not isinstance(row.get("installPath"), str) or not Path(row["installPath"]).is_absolute():
                    raise ValueError("Ruta de instalación de plugin inválida.")
                if not all(isinstance(row.get(k, ""), str) for k in ["version", "gitCommitSha", "lastUpdated"]):
                    raise ValueError("Versión de plugin inválida.")
                records[plugin_id] = {k: row.get(k, "") for k in ["installPath", "version", "gitCommitSha", "lastUpdated"]}
        servers, omitted = _mcp(_object(paths.home / ".claude.json").get("mcpServers", {}))
        result.operations.extend(omitted)
        _, config = _config(config_path)
        if not isinstance(config.get("plugins", {}), dict) or not isinstance(config.get("marketplaces", {}), dict):
            raise ValueError("Tablas de plugins/marketplaces de Codex inválidas.")
        managed = state.load_state().get("managed", {})
        previous = set(state.get_managed("codex_plugins"))
        versions = managed.get("codex_plugin_versions", {})
        if not isinstance(versions, dict):
            raise ValueError("Estado de versiones de plugins de Codex inválido.")
        if dry_run:
            result.operations.extend(f"Codex: reconciliar plugin {k} ({'activo' if v else 'inactivo'})." for k, v in desired.items())
            result.operations.extend(f"Codex: reconciliar MCP {k}." for k in servers)
            return result
        binary = shutil.which("codex")
        if not binary:
            result.operations.append("CLI codex ausente; se omite la sincronización nativa.")
            return result
        data = _command(binary, ["plugin", "list", "--json"], paths)
        native_rows = data.get("installed")
        if not isinstance(native_rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("pluginId"), str) for r in native_rows):
            raise ValueError("Inventario nativo de Codex inválido.")
        native = {r["pluginId"]: r for r in native_rows}
        new_versions = {}
        targets = dict(managed.get("codex_plugin_targets", {}))
        adopted = {}
        added_markets = set()
        refreshed_markets = set()
        added_paths = {}
        for plugin_id, enabled in desired.items():
            if plugin_id.endswith("@builtin"):
                result.operations.append(f"Codex: {plugin_id} es interno de Claude; se omite.")
                continue
            record = records.get(plugin_id)
            version = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest() if record else ""
            update = plugin_id in versions and versions[plugin_id] != version
            target_id = targets.get(plugin_id, plugin_id)
            market = target_id.split("@")[1]
            source_market = plugin_id.split("@")[1]
            if enabled and (target_id not in native or update):
                if record is None:
                    result.operations.append(f"Codex: {plugin_id} sin instalación de usuario en Claude; se omite.")
                    continue
                record = prepare_source(paths, plugin_id, record)
                records[plugin_id] = record
                source = config.get("marketplaces", {}).get(market)
                if target_id not in native:
                    if source is not None and source.get("source") != str(paths.state_dir / "marketplaces" / market):
                        market = "agent-sync-" + source_market
                    target_id = _plugin_name(plugin_id, record) + "@" + market
                    source = config.get("marketplaces", {}).get(market)
                local_catalog = source is None or source.get("source") == str(paths.state_dir / "marketplaces" / market)
                if local_catalog:
                    root = _local_marketplace(paths, source_market, records, market)
                    if source is None and market not in added_markets:
                        response = _command(binary, ["plugin", "marketplace", "add", str(root), "--json"], paths)
                        if response.get("marketplaceName") != market:
                            raise ValueError("Codex registró un marketplace inesperado.")
                        added_markets.add(market)
                elif update and source.get("source_type") == "git" and market not in refreshed_markets:
                    _command(binary, ["plugin", "marketplace", "upgrade", market, "--json"], paths)
                    refreshed_markets.add(market)
                try:
                    response = _command(binary, ["plugin", "add", target_id, "--json"], paths)
                except ValueError as exc:
                    raise ValueError(f"Plugin {plugin_id}: {exc}") from exc
                if response.get("pluginId") != target_id:
                    raise ValueError("Codex no confirmó la instalación solicitada.")
                if isinstance(response.get("installedPath"), str):
                    added_paths[target_id] = Path(response["installedPath"])
                native.setdefault(target_id, {"enabled": True, "version": response.get("version", "")})
                result.operations.append(f"Codex: plugin {plugin_id} instalado/actualizado por su CLI nativo.")
            if target_id in native:
                adopted[plugin_id] = target_id
                targets[plugin_id] = target_id
                new_versions[plugin_id] = version
        # El instalador nativo puede modificar config.toml: se vuelve a leer antes de editarlo.
        text, current = _config(config_path)
        original = text
        effective = {**desired, **{target: desired[origin] for origin, target in adopted.items()}}
        plugin_servers = _plugin_mcp_names(paths, native, effective, added_paths)
        for name in set(servers) & plugin_servers:
            del servers[name]
            result.operations.append(f"Codex: MCP {name} aportado por un plugin; no se duplica.")
        for plugin_id, target_id in adopted.items():
            text = _enabled(text, target_id, desired[plugin_id])
            if target_id != plugin_id and plugin_id in current.get("plugins", {}):
                text = _enabled(text, plugin_id, False)
        for target_id in previous - set(adopted.values()):
            if target_id in current.get("plugins", {}):
                text = _enabled(text, target_id, False)
                result.operations.append(f"Codex: plugin administrado {target_id} desactivado; caché conservada.")
        old_servers = set(state.get_managed("codex_mcp"))
        for name in old_servers - set(servers):
            text = _table(text, ("mcp_servers", name), None)
        for name, server in servers.items():
            if current.get("mcp_servers", {}).get(name) != server:
                text = _table(text, ("mcp_servers", name), server)
                result.operations.append(f"Codex: MCP global {name} actualizado.")
        if text != original:
            atomic_write_text(config_path, text)
        owned = state.load_state().setdefault("managed", {})
        owned.update(codex_plugins=sorted(previous | set(adopted.values())), codex_plugin_targets=targets, codex_plugin_versions=new_versions, codex_mcp=sorted(servers))
    except (ValueError, OSError, TypeError) as exc:
        result.success = False
        if isinstance(exc, tomllib.TOMLDecodeError):
            result.errors.append("config.toml inválido; no se modificaron sus tablas.")
        else:
            result.errors.append(str(exc) if isinstance(exc, ValueError) else f"Error de E/S de Codex: {type(exc).__name__}.")
    return result
