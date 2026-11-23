"""Tests sin red para la exportación de la Bitácora."""

import unittest
from unittest.mock import patch

import pandas as pd

from exportar_historico import construir_export


class TestConstruirExport(unittest.TestCase):
    def test_excluye_una_ejecucion_manual_mas_reciente(self):
        journal = pd.DataFrame([
            {
                "fecha_ejecucion": pd.Timestamp("2026-08-24T07:00:00Z"),
                "semana_iso": "2026-W35", "snapshot_id": "oficial-1",
                "ticker": "OFICIAL", "pasa": True,
            },
            {
                "fecha_ejecucion": pd.Timestamp("2026-08-24T10:00:00Z"),
                "semana_iso": "2026-W35", "snapshot_id": "manual-2",
                "ticker": "MANUAL", "pasa": True,
            },
        ])
        ejecuciones = pd.DataFrame([
            {
                "snapshot_id": "oficial-1", "fecha_ejecucion": "2026-08-24T07:00:00Z",
                "semana_iso": "2026-W35", "oficial": True, "revision": 1,
            },
            {
                "snapshot_id": "manual-2", "fecha_ejecucion": "2026-08-24T10:00:00Z",
                "semana_iso": "2026-W35", "oficial": False, "revision": 2,
            },
        ])

        with (
            patch("exportar_historico.leer_journal", return_value=journal),
            patch("exportar_historico.leer_ejecuciones", return_value=ejecuciones),
            patch("exportar_historico.leer_seguimiento", return_value=pd.DataFrame()),
        ):
            export = construir_export()

        self.assertEqual(export["ultima_ejecucion"], "2026-08-24T07:00:00+00:00")
        self.assertEqual(
            [fila["ticker"] for fila in export["candidatas_ultima_semana"]],
            ["OFICIAL"],
        )
        self.assertEqual([fila["ticker"] for fila in export["journal"]], ["OFICIAL"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
