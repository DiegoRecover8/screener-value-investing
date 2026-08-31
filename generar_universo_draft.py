"""Genera un universo draft reproducible desde un snapshot de descubrimiento."""

from __future__ import annotations

import argparse
from pathlib import Path

from selector_universo import generar_draft_desde_snapshot
from universos_versionados import RUTA_ESPEJO_DEFECTO, RUTA_MANIFEST_DEFECTO


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True, help="CSV disc_*.csv publicado")
    parser.add_argument("--control", type=Path, help="JSON del snapshot; por defecto usa el mismo nombre")
    parser.add_argument("--id", dest="universe_id", required=True, help="ID nuevo, por ejemplo uv_2026q3_r02")
    parser.add_argument("--manifest", type=Path, default=RUTA_MANIFEST_DEFECTO)
    parser.add_argument("--espejo", type=Path, default=RUTA_ESPEJO_DEFECTO)
    parser.add_argument("--creado-en", help="timestamp ISO opcional para reproducir metadatos")
    args = parser.parse_args()

    draft = generar_draft_desde_snapshot(
        universe_id=args.universe_id,
        ruta_snapshot=args.snapshot,
        ruta_control_snapshot=args.control,
        ruta_manifest=args.manifest,
        ruta_espejo=args.espejo,
        created_at=args.creado_en,
    )
    print(f"Draft generado: {draft.universe_id}")
    print(f"Tickers: {draft.ticker_count}")
    print(f"SHA-256: {draft.sha256}")
    print(f"CSV: {draft.ruta_csv}")
    print(f"Auditoría: {draft.ruta_auditoria}")
    print(f"Base por cuotas: {draft.resumen['seleccion_base']}")
    print(f"Retención limitada: {draft.resumen['retencion_incumbentes']}")
    print(f"Altas / permanencias / bajas: {draft.resumen['altas']} / "
          f"{draft.resumen['permanencias']} / {draft.resumen['bajas']}")
    print(f"Universo activo sin cambios: {draft.activo_sin_cambios}")
    print("El draft NO ha sido activado.")


if __name__ == "__main__":  # pragma: no cover - CLI
    main()
