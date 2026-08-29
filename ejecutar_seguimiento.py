"""Punto de entrada del seguimiento longitudinal (Fase 4, GitHub Actions).

Lee journal_candidatos.csv, extrae la primera aparición de cada candidata
única, descarga su precio ajustado desde esa fecha y registra el
rendimiento (retorno TWR, drawdown máximo) en seguimiento_candidatas.csv
-otro histórico que se AÑADE cada semana, nunca se sobrescribe.

Uso: python ejecutar_seguimiento.py [journal_candidatos.csv] [seguimiento_candidatas.csv]
"""

from __future__ import annotations

import sys

from journal import RUTA_JOURNAL_DEFECTO, leer_journal
from seguimiento import (
    RUTA_SEGUIMIENTO_DEFECTO,
    evaluar_seguimiento,
    extraer_candidatas_unicas,
    registrar_seguimiento,
)


def main() -> None:
    ruta_journal = sys.argv[1] if len(sys.argv) > 1 else RUTA_JOURNAL_DEFECTO
    ruta_seguimiento = sys.argv[2] if len(sys.argv) > 2 else RUTA_SEGUIMIENTO_DEFECTO

    journal = leer_journal(ruta_journal)
    candidatas = extraer_candidatas_unicas(journal)
    if candidatas.empty:
        print("Sin candidatas históricas en el journal todavía; nada que trackear.")
        return

    print(f"Calculando rendimiento de {len(candidatas)} candidatas únicas...")
    rendimientos = evaluar_seguimiento(candidatas)
    filas = registrar_seguimiento(rendimientos, ruta_seguimiento)
    print(f"\n{len(filas)} filas añadidas a {ruta_seguimiento}\n")

    columnas = [
        "ticker", "nombre", "fecha_entrada", "precio_entrada", "precio_actual",
        "retorno_total", "max_drawdown", "dias_en_seguimiento",
    ]
    print(rendimientos[columnas].to_string(index=False))


if __name__ == "__main__":  # pragma: no cover - CLI
    main()
