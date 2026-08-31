"""Punto de entrada para la ejecución automatizada semanal (GitHub Actions).

A propósito NO reconstruye el universo desde Yahoo cada semana: usa una
lista de tickers fija y versionada (ver `universo.txt`). Reconstruir el
universo en cada ejecución produciría un objetivo móvil -la Fase 4
(seguimiento longitudinal) necesita comparar la MISMA cesta de candidatas a
lo largo del tiempo, no una recalculada cada vez con criterios de
descubrimiento distintos. Ampliar `universo.txt` sigue siendo una decisión
manual y deliberada, con las herramientas de `universos_yfinance.py`.

Uso: python ejecutar_semanal.py <archivo_tickers.txt> [ruta_journal.csv]
       [ruta_ejecuciones.csv]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

from journal import (
    RUTA_EJECUCIONES_DEFECTO,
    RUTA_JOURNAL_DEFECTO,
    crear_snapshot_id,
    registrar_control_integridad,
    registrar_ejecucion,
    validar_integridad_ejecucion,
)
from screener_value import ejecutar


def _variable_booleana(nombre: str, defecto: bool = False) -> bool:
    valor = os.environ.get(nombre)
    if valor is None:
        return defecto
    return valor.strip().lower() in {"true", "1", "sí", "si", "yes"}


def main() -> None:
    if len(sys.argv) < 2:
        print(
            f"Uso: python {Path(__file__).name} <archivo_tickers.txt> "
            "[ruta_journal.csv] [ruta_ejecuciones.csv]"
        )
        sys.exit(1)

    archivo_tickers = sys.argv[1]
    ruta_journal = sys.argv[2] if len(sys.argv) > 2 else RUTA_JOURNAL_DEFECTO
    ruta_ejecuciones = sys.argv[3] if len(sys.argv) > 3 else RUTA_EJECUCIONES_DEFECTO

    tickers = [
        linea.strip() for linea in Path(archivo_tickers).read_text(encoding="utf-8").splitlines()
        if linea.strip()
    ]
    resultado = ejecutar(tickers, salida_csv="candidatos.csv")
    control = validar_integridad_ejecucion(resultado, len(tickers))
    momento = pd.Timestamp.now(tz="UTC")
    snapshot_id = crear_snapshot_id(momento)
    origen = os.environ.get(
        "SCREENER_ORIGEN", os.environ.get("GITHUB_EVENT_NAME", "local"),
    )
    oficial = _variable_booleana("SCREENER_OFICIAL")

    filas_nuevas = registrar_ejecucion(
        resultado, ruta_journal, momento=momento, snapshot_id=snapshot_id,
    )
    metadatos = registrar_control_integridad(
        control, snapshot_id, momento, ruta_ejecuciones,
        origen=origen, oficial=oficial,
    )
    print(
        f"\nSnapshot válido {snapshot_id}: {len(filas_nuevas)} empresas, "
        f"{control['descargas_correctas']}/{control['tickers_solicitados']} "
        f"descargas correctas ({control['tasa_exito_descarga']:.1%})."
    )
    estado = "oficial" if oficial else "prueba no oficial"
    print(f"Clasificación: {estado}, revisión {metadatos['revision'].iloc[0]}.")
    print(f"Journal: {ruta_journal}\nControl: {ruta_ejecuciones}")


if __name__ == "__main__":  # pragma: no cover - CLI
    main()
