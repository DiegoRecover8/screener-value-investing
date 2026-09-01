"""Tests sin red para las barreras de la ejecución semanal."""

import sys
import os
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from ejecutar_semanal import _verificar_candidatas_en_sombra, main
from journal import ErrorIntegridadEjecucion
from providers import Fundamentales
from universos_versionados import COLUMNAS_UNIVERSO, calcular_hash_universo


def _resultado(errores: int) -> pd.DataFrame:
    n = 10
    return pd.DataFrame({
        "ticker": [f"T{i}" for i in range(n)],
        "pasa": [False] * n,
        "error_descarga": ["Timeout"] * errores + [""] * (n - errores),
        "motivos_descarte": ["sin dato"] * n,
    })


class TestEjecucionSemanal(unittest.TestCase):
    def _argv(self, tmp: str, universo: str | None = None) -> list[str]:
        return [
            "ejecutar_semanal.py",
            universo or str(Path(tmp) / "universo.txt"),
            str(Path(tmp) / "journal.csv"),
            str(Path(tmp) / "ejecuciones.csv"),
        ]

    def _crear_universo_activo(
        self, tmp: str, con_draft: bool = False,
    ) -> tuple[Path, Path]:
        tickers = [f"T{i}" for i in range(10)]
        raiz = Path(tmp)
        ruta_universo = raiz / "universos/oficiales/uv_2026q3_r01.csv"
        ruta_universo.parent.mkdir(parents=True)
        ruta_universo.write_text(
            ",".join(COLUMNAS_UNIVERSO) + "\n"
            + "".join(f"{ticker},test,,,\n" for ticker in tickers),
            encoding="utf-8",
        )
        ruta_manifest = raiz / "universos/manifest.json"
        versiones = [{
            "universe_id": "uv_2026q3_r01",
            "path": "oficiales/uv_2026q3_r01.csv",
            "status": "active",
            "created_at": "2026-08-31T00:00:00Z",
            "effective_from": "2026-08-31",
            "supersedes": None,
            "asset_type": "equity",
            "ticker_count": 10,
            "sha256": calcular_hash_universo(tickers),
            "selection_method": "test",
            "notes": "",
        }]
        if con_draft:
            tickers_draft = [f"D{i}" for i in range(10)]
            ruta_draft = raiz / "universos/oficiales/uv_2026q3_r02.csv"
            ruta_draft.write_text(
                ",".join(COLUMNAS_UNIVERSO) + "\n"
                + "".join(f"{ticker},test,,,\n" for ticker in tickers_draft),
                encoding="utf-8",
            )
            versiones.append({
                "universe_id": "uv_2026q3_r02",
                "path": "oficiales/uv_2026q3_r02.csv",
                "status": "draft",
                "created_at": "2026-08-31T12:00:00Z",
                "effective_from": None,
                "supersedes": "uv_2026q3_r01",
                "asset_type": "equity",
                "ticker_count": 10,
                "sha256": calcular_hash_universo(tickers_draft),
                "selection_method": "test_draft",
                "notes": "",
            })
        ruta_manifest.write_text(json.dumps({
            "schema_version": 1,
            "active_universe_id": "uv_2026q3_r01",
            "universes": versiones,
        }), encoding="utf-8")
        ruta_espejo = raiz / "universo.txt"
        ruta_espejo.write_text("\n".join(tickers) + "\n", encoding="utf-8")
        return ruta_manifest, ruta_espejo

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
                patch.dict(os.environ, {"SCREENER_OFICIAL": "false"}),
            ):
                main()

            journal = pd.read_csv(Path(tmp, "journal.csv"))
            ejecuciones = pd.read_csv(Path(tmp, "ejecuciones.csv"))

        self.assertEqual(journal["snapshot_id"].nunique(), 1)
        self.assertEqual(journal["snapshot_id"].iloc[0], ejecuciones["snapshot_id"].iloc[0])
        self.assertEqual(ejecuciones["tasa_exito_descarga"].iloc[0], 0.8)
        self.assertFalse(bool(ejecuciones["oficial"].iloc[0]))
        self.assertEqual(ejecuciones["revision"].iloc[0], 1)
        self.assertTrue(ejecuciones["universe_id"].iloc[0].startswith("adhoc_"))
        self.assertEqual(len(ejecuciones["universe_sha256"].iloc[0]), 64)

    def test_variable_de_entorno_marca_un_snapshot_oficial(self):
        with TemporaryDirectory() as tmp:
            manifest, espejo = self._crear_universo_activo(tmp)
            with (
                patch.object(sys, "argv", self._argv(tmp, "--universo-activo")),
                patch("ejecutar_semanal.ejecutar", return_value=_resultado(0)),
                patch.dict(os.environ, {
                    "SCREENER_OFICIAL": "true", "SCREENER_ORIGEN": "schedule",
                    "SCREENER_MANIFEST": str(manifest),
                    "SCREENER_UNIVERSO_ESPEJO": str(espejo),
                    "GITHUB_RUN_ID": "987654", "GITHUB_RUN_ATTEMPT": "2",
                    "GITHUB_SERVER_URL": "https://github.com",
                    "GITHUB_REPOSITORY": "acme/screener", "GITHUB_SHA": "deadbeef",
                }),
            ):
                main()

            ejecuciones = pd.read_csv(Path(tmp, "ejecuciones.csv"))

        self.assertTrue(bool(ejecuciones["oficial"].iloc[0]))
        self.assertEqual(ejecuciones["origen"].iloc[0], "schedule")
        self.assertEqual(ejecuciones["github_run_id"].iloc[0], 987654)
        self.assertEqual(ejecuciones["github_run_attempt"].iloc[0], 2)
        self.assertEqual(
            ejecuciones["github_run_url"].iloc[0],
            "https://github.com/acme/screener/actions/runs/987654",
        )
        self.assertEqual(ejecuciones["github_sha"].iloc[0], "deadbeef")
        self.assertEqual(ejecuciones["universe_id"].iloc[0], "uv_2026q3_r01")
        self.assertEqual(
            ejecuciones["universe_sha256"].iloc[0],
            calcular_hash_universo([f"T{i}" for i in range(10)]),
        )

    def test_no_permite_marcar_como_oficial_una_lista_adhoc(self):
        with TemporaryDirectory() as tmp:
            Path(tmp, "universo.txt").write_text("T0\n", encoding="utf-8")
            with (
                patch.object(sys, "argv", self._argv(tmp)),
                patch("ejecutar_semanal.ejecutar") as ejecutar_mock,
                patch.dict(os.environ, {"SCREENER_OFICIAL": "true"}),
                self.assertRaisesRegex(ValueError, "exige --universo-activo"),
            ):
                main()
            ejecutar_mock.assert_not_called()

    def test_resuelve_un_draft_registrado_con_su_identidad_en_modo_prueba(self):
        with TemporaryDirectory() as tmp:
            manifest, espejo = self._crear_universo_activo(tmp, con_draft=True)
            argv = [
                "ejecutar_semanal.py", "--universo-id", "uv_2026q3_r02",
                str(Path(tmp) / "journal.csv"),
                str(Path(tmp) / "ejecuciones.csv"),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch("ejecutar_semanal.ejecutar", return_value=_resultado(0)),
                patch.dict(os.environ, {
                    "SCREENER_OFICIAL": "false",
                    "SCREENER_MANIFEST": str(manifest),
                    "SCREENER_UNIVERSO_ESPEJO": str(espejo),
                }),
            ):
                main()

            ejecuciones = pd.read_csv(Path(tmp, "ejecuciones.csv"))

        self.assertFalse(bool(ejecuciones["oficial"].iloc[0]))
        self.assertEqual(ejecuciones["universe_id"].iloc[0], "uv_2026q3_r02")
        self.assertEqual(
            ejecuciones["universe_sha256"].iloc[0],
            calcular_hash_universo([f"D{i}" for i in range(10)]),
        )

    def test_no_permite_un_id_explicito_en_modo_oficial(self):
        with TemporaryDirectory() as tmp:
            manifest, espejo = self._crear_universo_activo(tmp, con_draft=True)
            argv = [
                "ejecutar_semanal.py", "--universo-id", "uv_2026q3_r02",
                str(Path(tmp) / "journal.csv"),
                str(Path(tmp) / "ejecuciones.csv"),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch("ejecutar_semanal.ejecutar") as ejecutar_mock,
                patch.dict(os.environ, {
                    "SCREENER_OFICIAL": "true",
                    "SCREENER_MANIFEST": str(manifest),
                    "SCREENER_UNIVERSO_ESPEJO": str(espejo),
                }),
                self.assertRaisesRegex(ValueError, "IDs explícitos"),
            ):
                main()
            ejecutar_mock.assert_not_called()

    def test_workflow_ofrece_prueba_de_draft_y_timeout_ampliado(self):
        workflow = Path(".github/workflows/screener_semanal.yml").read_text(
            encoding="utf-8",
        )
        self.assertIn("universo_prueba:", workflow)
        self.assertIn('timeout-minutes: 30', workflow)
        self.assertIn('--universo-id "$SCREENER_UNIVERSO_PRUEBA"', workflow)
        self.assertIn('[[ "$SCREENER_OFICIAL" == "true" ]]', workflow)
        self.assertIn("verificar_sec:", workflow)
        self.assertIn("secrets.SEC_USER_AGENT", workflow)
        self.assertNotIn("vars.SEC_USER_AGENT", workflow)
        self.assertIn("verificacion_candidatas.csv", workflow)

    def test_verificacion_sec_es_lateral_y_escribe_el_snapshot_compartido(self):
        primarias = pd.DataFrame([{
            "ticker": "AAA", "proveedor_datos": "yfinance_anual",
            "tipo_periodo": "ANUAL", "fecha_resultados": "2025-12-31",
            "fecha_flujo_caja": "2025-12-31", "fecha_balance": "2025-12-31",
            "divisa_financiera": "USD", "ingresos": 100.0,
            "net_income": 10.0, "ebit": 15.0, "free_cash_flow": 8.0,
            "total_debt": 20.0, "cash": 5.0, "equity": 30.0,
            "gasto_intereses": 2.0,
        }])
        resultado = _resultado(0)
        resultado.attrs["fundamentales_candidatas"] = primarias
        secundaria = Fundamentales(
            **{
                **primarias.iloc[0].to_dict(),
                "proveedor_datos": "sec_edgar",
                "url_fuente": "https://sec.test",
            }
        )

        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "verificacion.csv"
            with (
                patch.dict(os.environ, {
                    "SCREENER_VERIFICAR_CANDIDATAS": "true",
                    "SEC_USER_AGENT": "proyecto contacto@example.com",
                    "SCREENER_RUTA_VERIFICACION": str(ruta),
                }),
                patch("ejecutar_semanal.ProveedorSecEdgar") as clase_proveedor,
            ):
                clase_proveedor.return_value.descargar.return_value = [secundaria]
                _verificar_candidatas_en_sombra(
                    resultado, "snap_20260901T120000000000Z",
                    pd.Timestamp("2026-09-01", tz="UTC"),
                )

            verificacion = pd.read_csv(ruta)

        self.assertEqual(verificacion["snapshot_id"].nunique(), 1)
        self.assertEqual(verificacion["snapshot_id"].iloc[0], "snap_20260901T120000000000Z")
        self.assertEqual(set(verificacion["estado"]), {"verificado"})
        # El artefacto lateral no cambia los veredictos del resultado oficial.
        self.assertFalse(resultado["pasa"].any())


if __name__ == "__main__":
    unittest.main(verbosity=2)
