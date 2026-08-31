"""Tests sin red para la exportación de la Bitácora."""

import unittest
from unittest.mock import patch

import pandas as pd

from exportar_historico import construir_export


class TestConstruirExport(unittest.TestCase):
    def test_candidatas_son_solo_de_la_ultima_ejecucion(self):
        journal = pd.DataFrame([
            {
                "fecha_ejecucion": pd.Timestamp("2026-08-24T07:00:00Z"),
                "semana_iso": "2026-W35", "ticker": "ANTIGUA", "pasa": True,
            },
            {
                "fecha_ejecucion": pd.Timestamp("2026-08-24T10:00:00Z"),
                "semana_iso": "2026-W35", "ticker": "NUEVA", "pasa": True,
            },
            {
                "fecha_ejecucion": pd.Timestamp("2026-08-24T10:00:00Z"),
                "semana_iso": "2026-W35", "ticker": "DESCARTADA", "pasa": False,
            },
        ])

        with (
            patch("exportar_historico.leer_journal", return_value=journal),
            patch("exportar_historico.leer_seguimiento", return_value=pd.DataFrame()),
        ):
            export = construir_export()

        self.assertEqual(export["ultima_ejecucion"], "2026-08-24T10:00:00+00:00")
        self.assertEqual(
            [fila["ticker"] for fila in export["candidatas_ultima_semana"]],
            ["NUEVA"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
