"""CLI para inspeccionar, comparar y activar universos versionados."""

from __future__ import annotations

import argparse
from pathlib import Path

from universos_versionados import (
    RUTA_ESPEJO_DEFECTO,
    RUTA_MANIFEST_DEFECTO,
    activar_universo,
    cargar_manifest,
    cargar_tickers,
    cargar_universo_activo,
    comparar_tickers,
)


def _tickers_referencia(referencia: str, ruta_manifest: Path) -> list[str]:
    manifest = cargar_manifest(ruta_manifest)
    por_id = {v["universe_id"]: v for v in manifest["universes"]}
    if referencia in por_id:
        return cargar_tickers(ruta_manifest.parent / por_id[referencia]["path"])
    return cargar_tickers(referencia)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=RUTA_MANIFEST_DEFECTO,
        help="manifest a utilizar",
    )
    parser.add_argument(
        "--espejo", type=Path, default=RUTA_ESPEJO_DEFECTO,
        help="lista TXT compatible con la Action",
    )
    subparsers = parser.add_subparsers(dest="comando", required=True)
    subparsers.add_parser("validar")
    subparsers.add_parser("mostrar-activo")
    comparar = subparsers.add_parser("comparar")
    comparar.add_argument("origen", help="universe_id o ruta CSV/TXT")
    comparar.add_argument("destino", help="universe_id o ruta CSV/TXT")
    activar = subparsers.add_parser("activar")
    activar.add_argument("universe_id")
    args = parser.parse_args()

    if args.comando == "activar":
        activo = activar_universo(args.universe_id, args.manifest, args.espejo)
        print(f"Universo activado: {activo.universe_id} ({len(activo.tickers)} tickers)")
        return

    activo = cargar_universo_activo(args.manifest, args.espejo)
    if args.comando == "validar":
        print(
            f"Manifest válido: {activo.universe_id}, {len(activo.tickers)} tickers, "
            f"sha256={activo.sha256}"
        )
    elif args.comando == "mostrar-activo":
        print(f"universe_id: {activo.universe_id}")
        print(f"ruta: {activo.ruta}")
        print(f"tickers: {len(activo.tickers)}")
        print(f"sha256: {activo.sha256}")
    else:
        diferencias = comparar_tickers(
            _tickers_referencia(args.origen, args.manifest),
            _tickers_referencia(args.destino, args.manifest),
        )
        print(f"Añadidos ({len(diferencias['anadidos'])}):")
        print("\n".join(diferencias["anadidos"]) or "(ninguno)")
        print(f"\nEliminados ({len(diferencias['eliminados'])}):")
        print("\n".join(diferencias["eliminados"]) or "(ninguno)")
        print(f"\nComunes: {len(diferencias['comunes'])}")


if __name__ == "__main__":  # pragma: no cover - CLI
    main()
