"""Tests sin red para seguimiento.py (Fase 4: rendimiento longitudinal)."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from journal import filtrar_journal_oficial
from seguimiento import (
    calcular_rendimiento,
    extraer_candidatas_unicas,
    extraer_senales_candidatas,
    leer_seguimiento,
    preparar_historial_graficable,
    registrar_seguimiento,
)


def _serie(precios: list[float], inicio="2026-01-05") -> pd.Series:
    fechas = pd.date_range(inicio, periods=len(precios), freq="B")
    return pd.Series(precios, index=fechas)


class TestCalcularRendimiento(unittest.TestCase):
    def test_retorno_y_drawdown_basicos(self):
        # 100 -> 110 -> 90 -> 120: sube, cae, recupera y supera el máximo previo.
        r = calcular_rendimiento(_serie([100, 110, 90, 120]))
        self.assertAlmostEqual(r["retorno_total"], 0.20, places=6)
        self.assertAlmostEqual(r["max_drawdown"], 90 / 110 - 1, places=6)
        self.assertEqual(r["precio_entrada"], 100)
        self.assertEqual(r["precio_actual"], 120)

    def test_retorno_encadenado_coincide_con_ratio_extremos(self):
        precios = [50, 55, 52, 60, 58, 65]
        r = calcular_rendimiento(_serie(precios))
        self.assertAlmostEqual(r["retorno_total"], precios[-1] / precios[0] - 1, places=9)

    def test_solo_baja_drawdown_igual_a_retorno(self):
        r = calcular_rendimiento(_serie([100, 90, 80]))
        self.assertAlmostEqual(r["max_drawdown"], r["retorno_total"], places=9)

    def test_serie_insuficiente_devuelve_nan(self):
        r = calcular_rendimiento(_serie([100]))
        self.assertTrue(np.isnan(r["retorno_total"]))

    def test_serie_vacia_no_rompe(self):
        r = calcular_rendimiento(pd.Series(dtype=float))
        self.assertTrue(np.isnan(r["retorno_total"]))
        self.assertTrue(np.isnan(r["max_drawdown"]))


class TestExtraerCandidatasUnicas(unittest.TestCase):
    def _fila(self, ticker, fecha, pasa, **extra):
        base = dict(
            fecha_ejecucion=fecha, ticker=ticker, nombre=f"{ticker} SA",
            sector="Technology", region="Europa", per=10.0, ev_ebit=8.0,
            roic=0.15, puntuacion=3.0, pasa=pasa,
        )
        base.update(extra)
        return base

    def test_usa_la_primera_aparicion_no_la_ultima(self):
        journal = pd.DataFrame([
            self._fila("AAA", "2026-08-01T07:00:00+00:00", True),
            self._fila("AAA", "2026-08-08T07:00:00+00:00", True),
        ])
        candidatas = extraer_candidatas_unicas(journal)
        self.assertEqual(len(candidatas), 1)
        self.assertEqual(
            candidatas.iloc[0]["fecha_entrada"], pd.Timestamp("2026-08-01T07:00:00+00:00")
        )

    def test_descarta_no_candidatas(self):
        journal = pd.DataFrame([
            self._fila("AAA", "2026-08-01T07:00:00+00:00", True),
            self._fila("BBB", "2026-08-01T07:00:00+00:00", False),
        ])
        candidatas = extraer_candidatas_unicas(journal)
        self.assertEqual(list(candidatas["ticker"]), ["AAA"])

    def test_journal_vacio(self):
        self.assertTrue(extraer_candidatas_unicas(pd.DataFrame()).empty)

    def test_ninguna_candidata_en_el_journal(self):
        journal = pd.DataFrame([self._fila("BBB", "2026-08-01T07:00:00+00:00", False)])
        self.assertTrue(extraer_candidatas_unicas(journal).empty)

    def test_reentrada_despues_de_dejar_de_pasar_abre_otra_senal(self):
        journal = pd.DataFrame([
            self._fila("AAA", "2026-08-01T07:00:00+00:00", True),
            self._fila("AAA", "2026-08-08T07:00:00+00:00", True),
            self._fila("AAA", "2026-08-15T07:00:00+00:00", False),
            self._fila("AAA", "2026-08-22T07:00:00+00:00", True),
        ])
        senales = extraer_senales_candidatas(journal)
        self.assertEqual(len(senales), 2)
        self.assertEqual(
            list(senales["fecha_entrada"]),
            [
                pd.Timestamp("2026-08-01T07:00:00+00:00"),
                pd.Timestamp("2026-08-22T07:00:00+00:00"),
            ],
        )

    def test_error_de_descarga_no_cierra_una_senal(self):
        journal = pd.DataFrame([
            self._fila("AAA", "2026-08-01T07:00:00+00:00", True),
            self._fila(
                "AAA", "2026-08-08T07:00:00+00:00", False,
                error_descarga="Timeout: Yahoo no responde",
            ),
            self._fila("AAA", "2026-08-15T07:00:00+00:00", True),
        ])
        senales = extraer_senales_candidatas(journal)
        self.assertEqual(len(senales), 1)

    def test_prueba_manual_no_cierra_ni_reabre_una_senal_oficial(self):
        journal = pd.DataFrame([
            self._fila(
                "AAA", "2026-08-01T07:00:00+00:00", True,
                snapshot_id="oficial-w31", semana_iso="2026-W31",
            ),
            self._fila(
                "AAA", "2026-08-08T07:00:00+00:00", False,
                snapshot_id="manual-w32", semana_iso="2026-W32",
            ),
            self._fila(
                "AAA", "2026-08-15T07:00:00+00:00", True,
                snapshot_id="oficial-w33", semana_iso="2026-W33",
            ),
        ])
        ejecuciones = pd.DataFrame([
            {
                "snapshot_id": "oficial-w31", "semana_iso": "2026-W31",
                "oficial": True, "revision": 1,
                "fecha_ejecucion": "2026-08-01T07:00:00Z",
            },
            {
                "snapshot_id": "manual-w32", "semana_iso": "2026-W32",
                "oficial": False, "revision": 1,
                "fecha_ejecucion": "2026-08-08T07:00:00Z",
            },
            {
                "snapshot_id": "oficial-w33", "semana_iso": "2026-W33",
                "oficial": True, "revision": 1,
                "fecha_ejecucion": "2026-08-15T07:00:00Z",
            },
        ])

        journal_oficial = filtrar_journal_oficial(journal, ejecuciones)
        senales = extraer_senales_candidatas(journal_oficial)

        self.assertEqual(list(journal_oficial["pasa"]), [True, True])
        self.assertEqual(len(senales), 1)


class TestRegistrarSeguimiento(unittest.TestCase):
    def _rendimiento_ejemplo(self):
        return pd.DataFrame({
            "ticker": ["AAA", "BBB"],
            "fecha_entrada": [pd.Timestamp("2026-08-01"), pd.Timestamp("2026-08-01")],
            "retorno_total": [0.05, -0.02],
            "max_drawdown": [-0.03, -0.08],
        })

    def test_primera_ejecucion_crea_archivo(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "seguimiento.csv"
            registrar_seguimiento(
                self._rendimiento_ejemplo(), ruta, momento=pd.Timestamp("2026-08-24T07:00:00Z"),
            )
            historico = leer_seguimiento(ruta)
            self.assertEqual(len(historico), 2)
            self.assertIn("fecha_calculo", historico.columns)

    def test_ejecuciones_sucesivas_acumulan(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "seguimiento.csv"
            registrar_seguimiento(
                self._rendimiento_ejemplo(), ruta, momento=pd.Timestamp("2026-08-24T07:00:00Z"),
            )
            registrar_seguimiento(
                self._rendimiento_ejemplo(), ruta, momento=pd.Timestamp("2026-08-31T07:00:00Z"),
            )
            historico = leer_seguimiento(ruta)
            self.assertEqual(len(historico), 4)
            self.assertEqual(historico["fecha_calculo"].nunique(), 2)

    def test_leer_seguimiento_inexistente_devuelve_vacio(self):
        self.assertTrue(leer_seguimiento("/ruta/que/no/existe.csv").empty)


class TestHistorialGraficable(unittest.TestCase):
    def test_excluye_senales_con_filas_repetidas_pero_sin_retorno(self):
        seguimiento = pd.DataFrame({
            "ticker": ["VACIA", "VACIA", "VALIDA", "VALIDA"],
            "fecha_entrada": pd.to_datetime([
                "2026-08-01T07:00:00Z", "2026-08-01T07:00:00Z",
                "2026-08-02T07:00:00Z", "2026-08-02T07:00:00Z",
            ]),
            "fecha_calculo": pd.to_datetime([
                "2026-08-03T07:00:00Z", "2026-08-10T07:00:00Z",
                "2026-08-03T07:00:00Z", "2026-08-10T07:00:00Z",
            ]),
            "retorno_total": [np.nan, np.nan, 0.01, 0.03],
            "max_drawdown": [np.nan, np.nan, -0.01, -0.02],
        })

        resultado = preparar_historial_graficable(seguimiento)

        self.assertEqual(list(resultado["ticker"]), ["VALIDA", "VALIDA"])

    def test_una_sola_observacion_valida_no_dibuja_linea(self):
        seguimiento = pd.DataFrame({
            "ticker": ["AAA", "AAA"],
            "fecha_entrada": pd.to_datetime(["2026-08-01", "2026-08-01"], utc=True),
            "fecha_calculo": pd.to_datetime(["2026-08-03", "2026-08-10"], utc=True),
            "retorno_total": [np.nan, 0.02],
        })
        self.assertTrue(preparar_historial_graficable(seguimiento).empty)


if __name__ == "__main__":
    unittest.main(verbosity=2)
