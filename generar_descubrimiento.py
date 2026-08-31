"""Genera o reanuda el universo amplio de descubrimiento."""

from __future__ import annotations

import argparse
from pathlib import Path

from descubrimiento_amplio import (
    DIRECTORIO_DESCUBRIMIENTO,
    generar_snapshot_descubrimiento,
)
from universos_versionados import cargar_universo_activo


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--salida", type=Path, default=DIRECTORIO_DESCUBRIMIENTO,
        help="directorio para CSV, JSON y checkpoint",
    )
    parser.add_argument(
        "--reanudar", type=Path,
        help="checkpoint de una ejecución anterior; conserva su configuración",
    )
    parser.add_argument("--id", dest="discovery_id", help="ID opcional para una ejecución nueva")
    parser.add_argument("--silencioso", action="store_true")
    args = parser.parse_args()

    activo = cargar_universo_activo()
    snapshot = generar_snapshot_descubrimiento(
        directorio=args.salida,
        discovery_id=args.discovery_id,
        ruta_checkpoint=args.reanudar,
        tickers_activos=list(activo.tickers),
        verbose=not args.silencioso,
    )
    print(f"\nSnapshot: {snapshot.discovery_id}")
    print(f"Tickers únicos: {snapshot.ticker_count}")
    print(f"Buckets: {snapshot.control['buckets_exitosos']}/"
          f"{snapshot.control['buckets_solicitados']}")
    print(f"SHA-256: {snapshot.sha256}")
    if snapshot.diferencias_activo is not None:
        print(f"Nuevos frente a {activo.universe_id}: "
              f"{len(snapshot.diferencias_activo['anadidos'])}")
        print(f"Del activo no descubiertos: "
              f"{len(snapshot.diferencias_activo['eliminados'])}")
    print(f"CSV: {snapshot.ruta_csv}")
    print(f"Control: {snapshot.ruta_json}")


if __name__ == "__main__":  # pragma: no cover - CLI
    main()
