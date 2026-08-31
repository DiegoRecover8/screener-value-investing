"""Tests sin red para journal.py (histórico acumulado de la Fase 3)."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from journal import extraer_ultima_ejecucion, leer_journal, registrar_ejecucion
from journal import (
    ErrorIntegridadEjecucion,
    crear_snapshot_id,
    filtrar_journal_oficial,
    migrar_snapshot_ids_journal,
    migrar_trazabilidad_github_control,
    registrar_control_integridad,
    snapshot_ids_oficiales_efectivos,
    validar_integridad_ejecucion,
)


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
            self.assertIn("snapshot_id", journal.columns)
            self.assertEqual(journal["semana_iso"].iloc[0], "2026-W35")
            self.assertEqual(journal["snapshot_id"].nunique(), 1)
            self.assertEqual(
                journal["snapshot_id"].iloc[0],
                "snap_20260824T070000000000Z",
            )

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

    def test_no_permite_repetir_el_mismo_snapshot(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "journal.csv"
            momento = pd.Timestamp("2026-08-24T07:00:00Z")
            registrar_ejecucion(_resultado_ejemplo(), ruta, momento=momento)
            with self.assertRaisesRegex(ErrorIntegridadEjecucion, "ya existe"):
                registrar_ejecucion(_resultado_ejemplo(), ruta, momento=momento)

    def test_leer_journal_inexistente_devuelve_vacio(self):
        self.assertTrue(leer_journal("/ruta/que/no/existe/journal.csv").empty)

    def test_ultima_ejecucion_no_mezcla_snapshots_de_la_misma_semana(self):
        primera = _resultado_ejemplo(("ANTIGUA", "BBB"))
        primera["pasa"] = [True, False]
        segunda = _resultado_ejemplo(("NUEVA", "DDD"))
        segunda["pasa"] = [True, False]

        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "journal.csv"
            registrar_ejecucion(
                primera, ruta, momento=pd.Timestamp("2026-08-24T07:00:00Z"),
            )
            registrar_ejecucion(
                segunda, ruta, momento=pd.Timestamp("2026-08-24T10:00:00Z"),
            )
            ultima = extraer_ultima_ejecucion(leer_journal(ruta))

        self.assertEqual(set(ultima["ticker"]), {"NUEVA", "DDD"})
        self.assertEqual(ultima["fecha_ejecucion"].nunique(), 1)

    def test_migra_un_journal_antiguo_antes_de_anadir(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "journal.csv"
            antiguas = _resultado_ejemplo()
            antiguas.insert(0, "fecha_ejecucion", "2026-08-24T07:00:00+00:00")
            antiguas.insert(1, "semana_iso", "2026-W35")
            antiguas.to_csv(ruta, index=False)

            self.assertTrue(migrar_snapshot_ids_journal(ruta))
            self.assertFalse(migrar_snapshot_ids_journal(ruta))
            registrar_ejecucion(
                _resultado_ejemplo(("CCC", "DDD")), ruta,
                momento=pd.Timestamp("2026-08-31T07:00:00Z"),
            )
            journal = leer_journal(ruta)

        self.assertEqual(len(journal), 4)
        self.assertEqual(journal["snapshot_id"].nunique(), 2)


class TestIntegridadEjecucion(unittest.TestCase):
    def _resultado(self, n=10, errores=0):
        return pd.DataFrame({
            "ticker": [f"T{i}" for i in range(n)],
            "pasa": [False] * n,
            "error_descarga": ["Timeout"] * errores + [""] * (n - errores),
            "motivos_descarte": ["sin dato"] * n,
        })

    def test_cero_candidatas_es_valido_si_las_descargas_son_suficientes(self):
        resultado = self._resultado(10, errores=2)
        control = validar_integridad_ejecucion(resultado, 10)
        self.assertEqual(control["candidatas"], 0)
        self.assertEqual(control["tasa_exito_descarga"], 0.8)

    def test_rechaza_una_tasa_de_descarga_inferior_al_80_por_ciento(self):
        with self.assertRaisesRegex(ErrorIntegridadEjecucion, "70.0%"):
            validar_integridad_ejecucion(self._resultado(10, errores=3), 10)

    def test_rechaza_un_recuento_bruto_incoherente(self):
        resultado = self._resultado(9)
        resultado.attrs["control_integridad"] = {
            "resultados_brutos": 9, "errores_descarga": 0, "deduplicados": 0,
        }
        with self.assertRaisesRegex(ErrorIntegridadEjecucion, "9 resultados para 10"):
            validar_integridad_ejecucion(resultado, 10)

    def test_registra_metadatos_con_el_mismo_snapshot_id(self):
        control = validar_integridad_ejecucion(self._resultado(10), 10)
        momento = pd.Timestamp("2026-08-24T07:00:00Z")
        snapshot_id = crear_snapshot_id(momento)
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "ejecuciones.csv"
            registrar_control_integridad(
                control, snapshot_id, momento, ruta, origen="schedule",
                oficial=True, github_run_id="123", github_run_attempt="2",
                github_run_url="https://github.com/acme/screener/actions/runs/123",
                github_sha="abc123",
            )
            registrar_control_integridad(
                control, "snap_revision_2", momento + pd.Timedelta(hours=1), ruta,
                origen="workflow_dispatch", oficial=False,
            )
            metadatos = pd.read_csv(ruta)

        self.assertEqual(metadatos.loc[0, "snapshot_id"], snapshot_id)
        self.assertEqual(metadatos.loc[0, "origen"], "schedule")
        self.assertEqual(metadatos.loc[0, "empresas_evaluadas"], 10)
        self.assertEqual(list(metadatos["revision"]), [1, 2])
        self.assertEqual(list(metadatos["oficial"]), [True, False])
        self.assertEqual(str(metadatos.loc[0, "github_run_id"]), "123.0")
        self.assertEqual(str(metadatos.loc[0, "github_run_attempt"]), "2.0")
        self.assertEqual(
            metadatos.loc[0, "github_run_url"],
            "https://github.com/acme/screener/actions/runs/123",
        )
        self.assertEqual(metadatos.loc[0, "github_sha"], "abc123")

    def test_migra_control_antiguo_sin_alterar_la_fila_publicada(self):
        cabecera = (
            "snapshot_id,fecha_ejecucion,semana_iso,origen,oficial,revision,"
            "tickers_solicitados,resultados_brutos,descargas_correctas,"
            "errores_descarga,deduplicados,empresas_evaluadas,candidatas,"
            "tasa_exito_descarga,umbral_exito_minimo\n"
        )
        fila = (
            "snap_antiguo,2026-08-31T08:15:23+00:00,2026-W36,"
            "workflow_dispatch,False,1,205,205,205,0,19,186,1,1.0,0.8\n"
        )
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "ejecuciones.csv"
            ruta.write_text(cabecera + fila, encoding="utf-8")

            self.assertTrue(migrar_trazabilidad_github_control(ruta))
            self.assertFalse(migrar_trazabilidad_github_control(ruta))
            texto_migrado = ruta.read_text(encoding="utf-8")
            metadatos = pd.read_csv(ruta)

        self.assertIn(fila.rstrip("\n") + ",,,,\n", texto_migrado)
        self.assertTrue(metadatos["github_run_id"].isna().all())
        self.assertTrue(metadatos["github_run_url"].isna().all())

    def test_mayor_revision_oficial_sustituye_a_la_anterior_sin_borrarla(self):
        journal = pd.DataFrame([
            {"snapshot_id": "legacy", "ticker": "LEGACY"},
            {"snapshot_id": "oficial-1", "ticker": "ANTERIOR"},
            {"snapshot_id": "manual-2", "ticker": "PRUEBA"},
            {"snapshot_id": "oficial-3", "ticker": "VIGENTE"},
        ])
        ejecuciones = pd.DataFrame([
            {
                "snapshot_id": "oficial-1", "semana_iso": "2026-W35",
                "oficial": True, "revision": 1,
                "fecha_ejecucion": "2026-08-24T07:00:00Z",
            },
            {
                "snapshot_id": "manual-2", "semana_iso": "2026-W35",
                "oficial": False, "revision": 2,
                "fecha_ejecucion": "2026-08-24T08:00:00Z",
            },
            {
                "snapshot_id": "oficial-3", "semana_iso": "2026-W35",
                "oficial": True, "revision": 3,
                "fecha_ejecucion": "2026-08-24T09:00:00Z",
            },
        ])

        ids = snapshot_ids_oficiales_efectivos(journal, ejecuciones)
        filtrado = filtrar_journal_oficial(journal, ejecuciones)

        self.assertEqual(ids, {"legacy", "oficial-3"})
        self.assertEqual(set(filtrado["ticker"]), {"LEGACY", "VIGENTE"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
