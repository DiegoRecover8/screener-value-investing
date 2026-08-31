"""Exporta el histórico oficial y sus controles a un JSON para la página
estática de histórico (Claude Artifact).

Un Artifact publicado no puede leer los CSV del repo en tiempo real -no es
un servidor-, así que la página muestra un JSON embebido que se refresca
pegando la salida de este script cada vez que se quiere actualizar. Es
pura transformación de datos ya calculados por `journal.py`/`seguimiento.py`,
sin red. Las pruebas manuales y las revisiones oficiales sustituidas quedan
auditadas en los CSV, pero no entran en el journal que consume la Bitácora.

Uso: python exportar_historico.py > historico.json
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from journal import (
    extraer_ultima_ejecucion,
    filtrar_journal_oficial,
    leer_ejecuciones,
    leer_journal,
)
from seguimiento import leer_seguimiento


def _serializar(valor):
    if pd.isna(valor):
        return None
    if isinstance(valor, pd.Timestamp):
        return valor.isoformat()
    if isinstance(valor, np.integer):
        return int(valor)
    if isinstance(valor, np.floating):
        return float(valor)
    if isinstance(valor, np.bool_):
        return bool(valor)
    return valor


def _filas(df: pd.DataFrame) -> list[dict]:
    return [
        {col: _serializar(val) for col, val in fila.items()}
        for fila in df.to_dict(orient="records")
    ]


def construir_export() -> dict:
    """Estructura mínima que necesita la página: sin columnas sobrantes."""
    journal_completo = leer_journal()
    ejecuciones = leer_ejecuciones()
    journal = filtrar_journal_oficial(journal_completo, ejecuciones)
    seguimiento = leer_seguimiento()
    generado_en = pd.Timestamp.now(tz="UTC").isoformat()

    if journal.empty:
        return {
            "generado_en": generado_en,
            "ultima_ejecucion": None,
            "ultima_semana": None,
            "candidatas_ultima_semana": [],
            "journal": [],
            "ejecuciones": _filas(ejecuciones),
            "seguimiento": _filas(seguimiento),
        }

    filas_ultima_ejecucion = extraer_ultima_ejecucion(journal)
    ultima_ejecucion = filas_ultima_ejecucion["fecha_ejecucion"].iloc[0]
    ultima_semana = filas_ultima_ejecucion["semana_iso"].iloc[0]
    candidatas_ultima_ejecucion = filas_ultima_ejecucion[
        filas_ultima_ejecucion["pasa"].astype(bool)
    ]

    return {
        "generado_en": generado_en,
        "ultima_ejecucion": ultima_ejecucion.isoformat(),
        "ultima_semana": ultima_semana,
        # Se conserva el nombre de la clave para no romper la Bitácora ya
        # publicada; su contenido corresponde estrictamente a la ejecución
        # más reciente, aunque haya otras en la misma semana ISO.
        "candidatas_ultima_semana": _filas(candidatas_ultima_ejecucion),
        "journal": _filas(journal),
        "ejecuciones": _filas(ejecuciones),
        "seguimiento": _filas(seguimiento),
    }


def main() -> None:  # pragma: no cover - CLI
    json.dump(construir_export(), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
