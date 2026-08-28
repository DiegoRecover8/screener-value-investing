"""Histórico acumulado de ejecuciones del screener (Fase 3).

Cada ejecución AÑADE filas al journal, nunca lo sobrescribe -a diferencia de
`candidatos.csv`, que es una foto de la última ejecución. Es la base para
medir en el futuro (Fase 4) cómo rindieron realmente las candidatas
pasadas: sin este histórico no hay con qué comparar el precio de entrada.

Cada fila lleva el timestamp de cuándo se calculó, no solo qué se calculó
-la misma disciplina de auditabilidad que `motivos_descarte` en
`screener_value.py`, aplicada a lo largo del tiempo en vez de a un filtro.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

RUTA_JOURNAL_DEFECTO = "journal_candidatos.csv"


def registrar_ejecucion(
    resultado: pd.DataFrame,
    ruta_journal: str | Path = RUTA_JOURNAL_DEFECTO,
    momento: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Añade `resultado` (salida de `incorporar_ranking_candidatos`) al journal.

    Antepone dos columnas: `fecha_ejecucion` (UTC, ISO 8601 -el instante en
    que se calcularon estas métricas, no la fecha de los estados
    financieros subyacentes) y `semana_iso` (para agrupar por semana
    natural, p. ej. "2026-W35"). Escribe la cabecera solo si el archivo no
    existe todavía; en caso contrario añade filas sin repetirla.

    `momento` es inyectable para tests deterministas; en producción se usa
    el instante actual en UTC. Devuelve las filas añadidas (con las dos
    columnas nuevas), no el journal completo -para eso, léelo aparte.
    """
    momento = pd.Timestamp.now(tz="UTC") if momento is None else momento
    filas = resultado.copy()
    iso = momento.isocalendar()
    filas.insert(0, "fecha_ejecucion", momento.isoformat())
    filas.insert(1, "semana_iso", f"{iso.year}-W{iso.week:02d}")

    ruta = Path(ruta_journal)
    escribir_cabecera = not ruta.exists()
    filas.to_csv(ruta, mode="a", index=False, header=escribir_cabecera)
    return filas


def leer_journal(ruta_journal: str | Path = RUTA_JOURNAL_DEFECTO) -> pd.DataFrame:
    """Lee el histórico completo, o un DataFrame vacío si no existe todavía."""
    ruta = Path(ruta_journal)
    if not ruta.exists():
        return pd.DataFrame()
    return pd.read_csv(ruta, parse_dates=["fecha_ejecucion"])
