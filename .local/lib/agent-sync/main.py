#!/usr/bin/env python3
"""
agent-sync CLI Entrypoint.
Sincronización del ecosistema de asistentes IA: Claude Main, Claude Personal, Codex y Antigravity.
Invocable directamente o mediante runpy desde el launcher ~/.local/bin/sync-agents.
"""

import sys
import argparse
from pathlib import Path

# Asegurar que el directorio de la biblioteca esté en sys.path
LIB_DIR = Path(__file__).resolve().parent
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from config import SyncPaths
from runner import run_sync


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync-agents",
        description="Sincronizador del ecosistema de agentes IA (Claude Main, Personal, Codex, Antigravity).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--apply",
        action="store_true",
        help="Ejecuta la reconciliación síncrona manual con salida detallada.",
    )
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Modo simulación: muestra el plan de acciones de solo lectura sin realizar escrituras.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    paths = SyncPaths.from_env()

    if args.dry_run:
        mode = "dry_run"
    elif args.apply:
        mode = "apply"
    else:
        mode = "check"

    try:
        exit_code, operations = run_sync(paths, mode=mode)
    except (OSError, ValueError, TypeError) as exc:
        print(f"No se pudo completar agent-sync: {type(exc).__name__}.", file=sys.stderr)
        return 1

    # Imprimir resultados en consola si no es hook rápido silencioso sin cambios
    if mode != "check" or exit_code != 0:
        for op in operations:
            print(op, file=sys.stdout if mode == "dry_run" and exit_code == 0 else sys.stderr)
    elif operations:
        # En check sólo mostramos mensaje si se lanzó proceso en background o hubo aviso
        if any("segundo plano" in op or "recursión" in op for op in operations):
            for op in operations:
                print(op, file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
