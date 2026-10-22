# Repository Guidelines

## Project Structure & Module Organization

Agent Sync distributes tools managed in the primary Claude profile to Claude Personal, Codex, and Antigravity.

- `sync-agents` is the executable launcher; it resolves symlinks to find the checkout.
- `.local/lib/agent-sync/` contains `main.py` (CLI), `runner.py` (orchestration), destination adapters, skill/plugin discovery, and configuration/state helpers.
- `tests/` contains isolated unit tests and shared fixtures in `helpers.py`.
- `.github/workflows/tests.yml` defines CI. `.stow-local-ignore` supports GNU Stow deployment. There is no separate assets directory.

## Build, Test, and Development Commands

Use Linux and Python 3.11+. There is no build step or third-party Python dependency installation.

- `python3 sync-agents --help`: inspect CLI options; CI checks this entry point.
- `python3 -m unittest discover -s tests`: run the full suite.
- `python3 -m unittest discover -s tests -p 'test_antigravity.py'`: run one adapter's tests.
- `./sync-agents --dry-run`: preview synchronization without writes or installer calls.
- `./sync-agents --apply`: synchronize profiles immediately; review the dry run first.

Running without flags checks for changes and may schedule background synchronization. Native plugin operations require the `codex` and `agy` CLIs.

## Coding Style & Naming Conventions

Follow existing Python conventions: four-space indentation, `snake_case` modules/functions, `PascalCase` classes, and uppercase constants. Use `pathlib.Path`, existing `SyncPaths`/`SyncResult` structures, and shared safe I/O helpers. Keep destination logic in its adapter. Preserve existing Spanish CLI messages. No formatter or linter is configured.

## Testing Guidelines

Use standard-library `unittest`, `test_*.py` files, and descriptive `test_*` methods. Reuse `IsolatedEnvironmentTestCase` for temporary homes and fake executables; never depend on real profile settings or authentication. Cover preservation of local customizations, malformed input, idempotence, and write-free dry runs when relevant. CI requires the suite to pass; no numeric coverage threshold is configured.

## Commit & Pull Request Guidelines

History uses short descriptive subjects, including “Sync shared rules into Antigravity's GEMINI.md”; no enforced prefix convention is evident. Prefer imperative summaries. Describe the behavior change, affected destinations, and validation in PRs; link relevant issues and update `README.md` when usage changes.

## Security & Configuration

Preserve credentials, unmanaged entries, permissions, and Stow symlinks. Reuse atomic writes and backups. Keep secrets and real profile data out of fixtures and logs. Runtime state belongs in `~/.local/state/agent-sync/`, outside the repository.
