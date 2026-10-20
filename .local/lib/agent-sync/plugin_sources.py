"""Resuelve subdirectorios y colecciones de skills declaradas por el marketplace."""

import hashlib
import json
import re
from pathlib import Path
from config import read_object, atomic_write_json


def prepare_source(paths, plugin_id, record, dry_run=False):
    name, _, market = plugin_id.partition("@")
    if not re.fullmatch(r"[\w.-]+", name, re.ASCII) or not re.fullmatch(r"[\w.-]*", market, re.ASCII):
        raise ValueError("Identificador de fuente de plugin inválido.")
    root = Path(record["installPath"])
    catalog = paths.claude_dir / "plugins/marketplaces" / market / ".claude-plugin/marketplace.json"
    definitions = read_object(catalog).get("plugins", [])
    if not isinstance(definitions, list) or any(not isinstance(e, dict) for e in definitions):
        raise ValueError("Catálogo de plugins inválido.")
    entry = next((e for e in definitions if e.get("name") == name), {})
    source = entry.get("source", {})
    subdir = source.get("path") if isinstance(source, dict) else None
    if subdir is not None:
        if not isinstance(subdir, str) or Path(subdir).is_absolute() or ".." in Path(subdir).parts:
            raise ValueError("Subdirectorio de plugin inválido.")
        candidate = root / subdir
        if candidate.is_dir() and (candidate / ".claude-plugin/plugin.json").is_file():
            root = candidate
    declared = entry.get("skills", [])
    if not isinstance(declared, list) or not all(isinstance(p, str) for p in declared):
        raise ValueError("Declaración de skills de plugin inválida.")
    result = {**record, "installPath": str(root)}
    if declared and not any((root / file).is_file() for file in ["plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json"]):
        selected = []
        for relative in declared:
            skill = root / relative
            if Path(relative).is_absolute() or not skill.resolve().is_relative_to(root.resolve()) or not (skill / "SKILL.md").is_file():
                raise ValueError("Skill declarada fuera de la fuente o inexistente.")
            selected.append(skill)
        prepared = paths.state_dir / "sources" / (market or "local") / name
        revision = hashlib.sha256(json.dumps({"source": str(root), "version": record.get("version", ""), "skills": declared}, sort_keys=True).encode()).hexdigest()[:16]
        if not dry_run:
            skills_dir = prepared / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            names = {skill.name for skill in selected}
            for old in skills_dir.iterdir():
                if old.name not in names and old.is_symlink():
                    old.unlink()
            for skill in selected:
                target = skills_dir / skill.name
                if target.is_symlink() and target.resolve() != skill.resolve():
                    target.unlink()
                if not target.exists() and not target.is_symlink():
                    target.symlink_to(skill)
            manifest = prepared / ".claude-plugin/plugin.json"
            value = {"name": name, "version": "0.0.0+sync." + revision, "skills": "./skills/"}
            if read_object(manifest) != value:
                atomic_write_json(manifest, value)
        result.update(installPath=str(prepared), _prepared_skills=[str(p) for p in selected], _source_revision=revision)
    return result
