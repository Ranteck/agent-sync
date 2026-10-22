# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Comandos

```sh
python3 -m unittest discover -s tests                 # suite completa (CI: Python 3.11)
python3 -m unittest discover -s tests -p test_skills.py   # un archivo
python3 -m unittest discover -s tests -k <nombre_test>    # un test por nombre
```

Los tests importan `helpers` como módulo de primer nivel, así que siempre van con
`discover -s tests`. No hay linter ni dependencias: sólo stdlib, Python ≥ 3.11
(`tomllib`) y Linux (`fcntl`).

`./sync-agents --dry-run` lee la configuración **real** del usuario (`~/.claude`,
`~/.codex`, `~/.gemini`…) sin escribir; `--apply` la modifica. Para probar cambios,
usá los tests.

## Arquitectura

`sync-agents` ejecuta `.local/lib/agent-sync/main.py` con `runpy`. `main.py` agrega su
directorio a `sys.path` y los módulos se importan entre sí como módulos planos
(`from config import ...`), no como paquete.

**Modos** (`runner.run_sync`):
- sin flags (hook de inicio): si `StateManager.is_up_to_date()` da verdadero, sale en
  silencio. Si no, lanza `main.py --apply` desacoplado, con salida en
  `~/.local/state/agent-sync/sync.log`.
- `--apply`: toma un `flock` no bloqueante y corre las etapas.
- `--dry-run`: no toma el lock ni escribe, y no invoca los CLIs nativos.

`AGENT_SYNC_ACTIVE=1` evita la recursión: los CLIs `codex` y `agy` se invocan con esa
variable, porque sus propios hooks vuelven a llamar a `sync-agents`.

**Detección de cambios**: `state.json` guarda `code_version` (hash de todos los `.py`
de la biblioteca) y `fingerprint` (mtime y tamaño de las fuentes listadas en
`StateManager.compute_fingerprint`). Si una etapa empieza a leer un archivo fuente
nuevo, agregalo ahí; si no, el hook no detecta sus cambios.

**Etapas**, en orden: `sync_claude_profiles` → `sync_skills` → `sync_antigravity` →
`sync_codex` (que llama a `native_codex.sync_native_codex`). Cada etapa devuelve un
`SyncResult` y convierte `ValueError` y `OSError` en `result.errors` con mensajes en
español sin detalles internos (se usa `type(exc).__name__`, nunca rutas completas ni
contenido). Un fallo no corta las etapas siguientes, pero impide marcar la
sincronización como exitosa.

**Modelo de propiedad**: `state.json["managed"]` guarda, por categoría, lo que
agent-sync creó (`save_stage_inventory`). Sólo se retira o desactiva lo que figura
en ese inventario; los enlaces, entradas MCP, plugins y archivos ajenos se preservan
y se informan. Las reglas compartidas (`~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`)
sólo se sobrescriben si el contenido actual coincide con un hash administrado.

**Escrituras**: siempre con `config.atomic_write_text` / `atomic_write_json` /
`safe_create_symlink`, pasando `dry_run`. Esas funciones conservan los permisos,
siguen los symlinks de Stow y guardan la primera copia en `state/backups/`.

Piezas compartidas entre destinos:
- `codex_sync.build_shared_rules`: concatena `RTK.md`, `HOUSE-RULES.md` y
  `AI-NATIVE-SDLC.md` (primero de `~/Proyectos/<repo>/`, si no de `~/.claude/`) y
  `~/.claude/CLAUDE.md` sin las líneas `@import`, cada bloque entre marcadores
  `<!-- x:start/end -->`.
- `native_codex._mcp`: valida y traduce los MCP globales de `~/.claude.json` y omite
  los SSE. Antigravity la reutiliza y renombra `http_headers` → `headers`.
- `plugin_sources.prepare_source`: resuelve el `source.path` del marketplace. Si el
  plugin sólo declara `skills` sin manifiesto, arma un plugin sintético en
  `state/sources/`.

## Tests

Heredan de `helpers.IsolatedEnvironmentTestCase`: HOME temporal, `PATH` limitado a
un `bin/` propio y `get_paths()` para las rutas. Los CLIs externos se simulan con
`create_fake_bin`. Ningún test debe tocar el HOME real.

## Empaquetado

El repo es un paquete GNU Stow (`stow -t ~ agent-sync` instala la biblioteca en
`~/.local/lib/agent-sync/`). Todo archivo nuevo de la raíz que no deba terminar en
`~` va en `.stow-local-ignore`.
