"""Genera un draft que excluye listings duales observados y rellena plazas."""

from __future__ import annotations

import argparse
from pathlib import Path

from refinar_universo import generar_draft_refinado


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", dest="universe_id", required=True)
    parser.add_argument("--origen", required=True, help="universe_id activo observado")
    parser.add_argument("--snapshot-oficial", required=True, help="snapshot_id oficial")
    parser.add_argument("--descubrimiento", type=Path, required=True, help="CSV disc_*.csv")
    parser.add_argument("--control-descubrimiento", type=Path)
    parser.add_argument("--creado-en", help="timestamp ISO opcional")
    args = parser.parse_args()

    draft = generar_draft_refinado(
        universe_id=args.universe_id,
        universe_id_origen=args.origen,
        snapshot_id_oficial=args.snapshot_oficial,
        ruta_snapshot_descubrimiento=args.descubrimiento,
        ruta_control_descubrimiento=args.control_descubrimiento,
        created_at=args.creado_en,
    )
    print(f"Draft refinado: {draft.universe_id}")
    print(f"Tickers: {draft.ticker_count}")
    print(f"SHA-256: {draft.sha256}")
    print(f"Listings conocidos excluidos: "
          f"{draft.resumen['listings_excluidos_antes_descarga']}")
    print(f"Rellenos en el mismo bucket: {draft.resumen['rellenos_mismo_bucket']}")
    print(f"Rellenos en la misma región: {draft.resumen['rellenos_misma_region']}")
    print(f"CSV: {draft.ruta_csv}")
    print(f"Auditoría: {draft.ruta_auditoria}")
    print(f"Universo activo sin cambios: {draft.activo_sin_cambios}")
    print("El draft NO ha sido activado.")


if __name__ == "__main__":  # pragma: no cover - CLI
    main()
