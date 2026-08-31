"""Tests sin red para las barreras de la ejecución semanal."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from ejecutar_semanal import main
from journal import ErrorIntegridadEjecucion


def _resultado(errores: int) -> pd.DataFrame:
    n = 10
    return pd.DataFrame({
        "ticker": [f"T{i}" for i in range(n)],
        "pasa": [False] * n,
        "error_descarga": ["Timeout"] * errores + [""] * (n - errores),
        "motivos_descarte": ["sin dato"] * n,
    })


class TestEjecucionSemanal(unittest.TestCase):
    def _argv(self, tmp: str) -> list[str]:
        return [
            "ejecutar_semanal.py",
            str(Path(tmp) / "universo.txt"),
            str(Path(tmp) / "journal.csv"),
            str(Path(tmp) / "ejecuciones.csv"),
        ]

    def test_control_fallido_no_escribe_ningun_historico(self):
        with TemporaryDirectory() as tmp:
            Path(tmp, "universo.txt").write_text(
                "\n".join(f"T{i}" for i in range(10)), encoding="utf-8",
            )
            with (
                patch.object(sys, "argv", self._argv(tmp)),
                patch("ejecutar_semanal.ejecutar", return_value=_resultado(3)),
                self.assertRaises(ErrorIntegridadEjecucion),
            ):
                main()

            self.assertFalse(Path(tmp, "journal.csv").exists())
            self.assertFalse(Path(tmp, "ejecuciones.csv").exists())

    def test_control_valido_comparte_snapshot_entre_journal_y_metadatos(self):
        with TemporaryDirectory() as tmp:
            Path(tmp, "universo.txt").write_text(
                "\n".join(f"T{i}" for i in range(10)), encoding="utf-8",
            )
            with (
                patch.object(sys, "argv", self._argv(tmp)),
                patch("ejecutar_semanal.ejecutar", return_value=_resultado(2)),
            ):
                main()

            journal = pd.read_csv(Path(tmp, "journal.csv"))
            ejecuciones = pd.read_csv(Path(tmp, "ejecuciones.csv"))

        self.assertEqual(journal["snapshot_id"].nunique(), 1)
        self.assertEqual(journal["snapshot_id"].iloc[0], ejecuciones["snapshot_id"].iloc[0])
        self.assertEqual(ejecuciones["tasa_exito_descarga"].iloc[0], 0.8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
