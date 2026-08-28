"""Punto de entrada para la ejecución automatizada semanal (GitHub Actions).

A propósito NO reconstruye el universo desde Yahoo cada semana: usa una
lista de tickers fija y versionada (ver `universo.txt`). Reconstruir el
universo en cada ejecución produciría un objetivo móvil -la Fase 4
(seguimiento longitudinal) necesita comparar la MISMA cesta de candidatas a
lo largo del tiempo, no una recalculada cada vez con criterios de
descubrimiento distintos. Ampliar `universo.txt` sigue siendo una decisión
manual y deliberada, con las herramientas de `universos_yfinance.py`.

Uso: python ejecutar_semanal.py <archivo_tickers.txt> [ruta_journal.csv]
"""

from __future__ import annotations

import sys
from pathlib import Path

from journal import RUTA_JOURNAL_DEFECTO, registrar_ejecucion
from screener_value import ejecutar


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Uso: python {Path(__file__).name} <archivo_tickers.txt> [ruta_journal.csv]")
        sys.exit(1)

    archivo_tickers = sys.argv[1]
    ruta_journal = sys.argv[2] if len(sys.argv) > 2 else RUTA_JOURNAL_DEFECTO

    tickers = [
        linea.strip() for linea in Path(archivo_tickers).read_text(encoding="utf-8").splitlines()
        if linea.strip()
    ]
    resultado = ejecutar(tickers, salida_csv="candidatos.csv")
    filas_nuevas = registrar_ejecucion(resultado, ruta_journal)
    print(f"\n{len(filas_nuevas)} filas añadidas a {ruta_journal}")


if __name__ == "__main__":  # pragma: no cover - CLI
    main()
