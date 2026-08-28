"""Tests sin red para journal.py (histórico acumulado de la Fase 3)."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from journal import leer_journal, registrar_ejecucion


def _resultado_ejemplo(tickers=("AAA", "BBB")):
    return pd.DataFrame({
        "ticker": list(tickers),
        "pasa": [True, False],
        "puntuacion": [3.0, float("nan")],
        "motivos_descarte": ["", "PER: sin dato"],
    })


class TestJournal(unittest.TestCase):
    def test_primera_ejecucion_crea_archivo_con_cabecera(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "journal.csv"
            momento = pd.Timestamp("2026-08-24T07:00:00Z")  # lunes
            registrar_ejecucion(_resultado_ejemplo(), ruta, momento=momento)

            journal = leer_journal(ruta)
            self.assertEqual(len(journal), 2)
            self.assertIn("fecha_ejecucion", journal.columns)
            self.assertEqual(journal["semana_iso"].iloc[0], "2026-W35")

    def test_segunda_ejecucion_añade_sin_repetir_cabecera(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "journal.csv"
            registrar_ejecucion(
                _resultado_ejemplo(("AAA", "BBB")), ruta,
                momento=pd.Timestamp("2026-08-24T07:00:00Z"),
            )
            registrar_ejecucion(
                _resultado_ejemplo(("AAA", "BBB")), ruta,
                momento=pd.Timestamp("2026-08-31T07:00:00Z"),
            )

            journal = leer_journal(ruta)
            self.assertEqual(len(journal), 4)  # 2 tickers x 2 ejecuciones
            self.assertEqual(journal["semana_iso"].nunique(), 2)

    def test_no_muta_el_resultado_original(self):
        original = _resultado_ejemplo()
        columnas_antes = list(original.columns)
        with TemporaryDirectory() as tmp:
            registrar_ejecucion(original, Path(tmp) / "journal.csv")
        self.assertEqual(list(original.columns), columnas_antes)

    def test_leer_journal_inexistente_devuelve_vacio(self):
        self.assertTrue(leer_journal("/ruta/que/no/existe/journal.csv").empty)


if __name__ == "__main__":
    unittest.main(verbosity=2)
