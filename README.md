# Agent Sync

Distribuye las herramientas administradas en Claude principal a Claude personal,
Codex y Antigravity. Las cuentas, credenciales, modelos y preferencias de cada
perfil permanecen separados.

## Instalación

Requiere Python 3.11 o posterior y Linux (`fcntl`). No agrega dependencias Python.
Los CLIs `codex` y `agy` deben estar instalados para administrar sus plugins.

```sh
git clone https://github.com/Ranteck/agent-sync.git
cd agent-sync
./sync-agents --dry-run
```

Para disponer de `sync-agents` en el PATH, enlazá el ejecutable del checkout a
tu directorio de comandos. Por ejemplo, desde la raíz del proyecto:

```sh
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/sync-agents" "$HOME/.local/bin/sync-agents"
```

Si ya existe un lanzador, conservá su integración o ejecutá `./sync-agents`
directamente. Antes de aplicar cambios, revisá el resultado de `--dry-run`.

## Organización

- `sync-agents`: ejecutable independiente, también utilizable mediante symlink.
- `.local/lib/agent-sync/`: entrada Python, adaptadores por destino y estado.
- `tests/`: pruebas aisladas; no usan la configuración real del usuario.
- `.stow-local-ignore`: excluye documentación, pruebas y metadatos de Git.

El repositorio también funciona como paquete GNU Stow. Ubicalo como carpeta
`agent-sync` dentro de tus dotfiles. Desde la raíz de los dotfiles:

```sh
stow -R -t ~ agent-sync
```

Esto instala la biblioteca en `~/.local/lib/agent-sync/`. Los lanzadores que ya
apunten a esa ubicación siguen funcionando; el ejecutable de la raíz se enlaza
por separado con el comando de instalación anterior.

## Uso

```sh
sync-agents --dry-run   # Plan sin escrituras ni llamadas a los instaladores
sync-agents --apply     # Aplicación síncrona y reporte de errores
sync-agents             # Hook: detecta cambios y agenda una aplicación puntual
```

Se puede llamar a `sync-agents` desde los hooks de inicio de Claude y Codex, o
de invocación de Antigravity. La instalación no configura esos hooks.
Los cambios se detectan en la próxima invocación del hook;
las sesiones abiertas pueden necesitar reiniciarse para cargar las herramientas.
El modo de inicio no vuelve a instalar paquetes cuando el inventario sigue igual.

## Qué administra

- Claude principal (`~/.claude/settings.json`) define las habilitaciones y
  marketplaces compartidos. También distribuye sus MCP globales al archivo
  `~/.claude-personal/.claude.json`. Personal conserva sus entradas adicionales.
- Las skills se enlazan, incluyendo `synced/<grupo>/<skill>`. Se conservan
  directorios y enlaces ajenos; sólo se retiran enlaces administrados rotos.
- Codex conserva sus ports nativos existentes. Los faltantes pasan por
  `codex plugin add`; si falta el marketplace, se genera uno local que referencia
  las instalaciones de Claude. No hace falta clonar repositorios manualmente.
- Antigravity importa cada plugin activo por su `installPath`, confirma su
  presencia con el CLI nativo y evita volver a importar la misma versión.
- Las bajas de plugins administrados desactivan su uso; no purgan sus cachés.
  Las herramientas propias de los destinos se conservan.
- Se traducen MCP globales compatibles y los plugins aportan sus propios MCP.
  Las referencias a variables se conservan literalmente: cada runtime debe
  soportarlas o recibir las credenciales en su entorno.
- Se actualizan las reglas compartidas de Codex sólo cuando el archivo coincide
  con una versión administrada. Las instrucciones propias se preservan y avisan.

Los plugins internos de Claude y los MCP SSE que necesiten un puente se reportan
como omitidos. Si falta un CLI, se informa la omisión. Un resultado exitoso no
significa que todos los servicios estén autenticados ni prueba sus operaciones.
Las habilitaciones de hooks siguen sujetas a la confianza del runtime de destino.

## Estado y recuperación

`~/.local/state/agent-sync/` contiene el lock entre procesos, el inventario, los
catálogos locales y `sync.log` para las ejecuciones en segundo plano. Los archivos
modificados por el script conservan sus permisos y enlaces Stow; la primera copia
de respaldo se guarda en `backups/`, fuera del repositorio. Los instaladores
nativos administran sus propios cambios de archivos.

Ante un error se conserva el inventario de las etapas completadas y no se marca
la sincronización como exitosa. Un estado corrupto se informa sin sobrescribirlo.
Para inspeccionar un fallo, ejecutar `sync-agents --apply` o consultar `sync.log`.

## Verificación

```sh
python3 -m unittest discover -s tests
```

Las pruebas unitarias usan homes temporales e instaladores simulados. Se verificó
además instalar y desactivar un plugin local con los CLIs reales de Codex y
Antigravity en un home temporal, sin autenticar ni ejecutar servicios externos.
