"""Tests sin red para la abstracción y verificación de proveedores."""

from datetime import datetime, timezone
import unittest

import numpy as np
import pandas as pd

from providers import ProveedorYFinance
from screener_value import descargar_fundamentales


def _estado_resultados(fechas, ingresos):
    valores = {
        "Total Revenue": ingresos,
        "EBIT": [140.0] * len(fechas),
        "Net Income": [100.0] * len(fechas),
        "Interest Expense": [-10.0] * len(fechas),
        "EBITDA": [180.0] * len(fechas),
        "Tax Provision": [25.0] * len(fechas),
        "Pretax Income": [100.0] * len(fechas),
    }
    return pd.DataFrame(valores, index=fechas).T


def _flujo_caja(fechas):
    return pd.DataFrame(
        {fecha: [80.0] for fecha in fechas}, index=["Free Cash Flow"],
    )


def _balance(fechas):
    return pd.DataFrame(
        {
            fecha: [200.0, 100.0, 500.0]
            for fecha in fechas
        },
        index=["Total Debt", "Cash And Cash Equivalents", "Stockholders Equity"],
    )


class _TickerCompleto:
    info = {
        "longName": "Empresa de prueba",
        "sector": "Industrials",
        "country": "Spain",
        "currency": "EUR",
        "financialCurrency": "EUR",
        "marketCap": 10_000.0,
    }
    income_stmt = _estado_resultados(
        [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31")],
        [1_000.0, 900.0],
    )
    cashflow = _flujo_caja(
        [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31")],
    )
    ttm_income_stmt = _estado_resultados(
        [pd.Timestamp("2026-06-30")], [1_100.0],
    )
    ttm_cashflow = _flujo_caja([pd.Timestamp("2026-06-30")])
    balance_sheet = _balance(
        [pd.Timestamp("2026-06-30"), pd.Timestamp("2025-12-31")],
    )


class _Cliente:
    def __init__(self, ticker=None, error=None):
        self.ticker = ticker or _TickerCompleto()
        self.error = error

    def Ticker(self, _simbolo):
        if self.error:
            raise self.error
        return self.ticker


def _proveedor(cliente):
    return ProveedorYFinance(
        cliente=cliente,
        reloj=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
        resolver_fx=lambda _divisa: 1.0,
    )


class TestProveedorYFinance(unittest.TestCase):
    def test_registra_procedencia_periodo_fechas_y_calidad(self):
        datos = _proveedor(_Cliente()).descargar([" test "])[0]

        self.assertEqual(datos.ticker, "TEST")
        self.assertEqual(datos.proveedor_datos, "yfinance")
        self.assertEqual(datos.tipo_periodo, "TTM")
        self.assertEqual(datos.fecha_resultados, "2026-06-30")
        self.assertEqual(datos.fecha_flujo_caja, "2026-06-30")
        self.assertEqual(datos.fecha_balance, "2026-06-30")
        self.assertEqual(datos.calidad_datos, "ok")
        self.assertEqual(datos.incidencias_datos, "")
        self.assertTrue(datos.fecha_consulta_utc.startswith("2026-09-01T"))
        self.assertEqual(datos.comparables_anuales["ingresos"], 1_000.0)
        self.assertEqual(datos.comparables_anuales["free_cash_flow"], 80.0)
        self.assertEqual(datos.fecha_resultados_anual, "2025-12-31")

    def test_no_mezcla_resultados_ttm_con_flujo_anual(self):
        ticker = _TickerCompleto()
        ticker.ttm_cashflow = pd.DataFrame()
        datos = _proveedor(_Cliente(ticker=ticker)).descargar(["TEST"])[0]

        self.assertEqual(datos.tipo_periodo, "ANUAL")
        self.assertEqual(datos.ingresos, 1_000.0)
        self.assertEqual(datos.fecha_resultados, "2025-12-31")
        self.assertEqual(datos.fecha_flujo_caja, "2025-12-31")

    def test_marca_cuentas_obsoletas_para_revision(self):
        ticker = _TickerCompleto()
        ticker.ttm_income_stmt = pd.DataFrame()
        ticker.ttm_cashflow = pd.DataFrame()
        ticker.income_stmt = _estado_resultados(
            [pd.Timestamp("2024-01-01"), pd.Timestamp("2023-01-01")],
            [1_000.0, 900.0],
        )
        ticker.cashflow = _flujo_caja([pd.Timestamp("2024-01-01")])
        ticker.balance_sheet = _balance([pd.Timestamp("2024-01-01")])

        datos = _proveedor(_Cliente(ticker=ticker)).descargar(["TEST"])[0]

        self.assertEqual(datos.calidad_datos, "revisar")
        self.assertIn("obsoleto", datos.incidencias_datos)

    def test_un_error_conserva_una_fila_auditable(self):
        datos = _proveedor(_Cliente(error=TimeoutError("sin respuesta"))).descargar(
            ["TEST"],
        )[0]

        self.assertEqual(datos.calidad_datos, "error")
        self.assertIn("TimeoutError", datos.error_descarga)
        self.assertEqual(datos.proveedor_datos, "yfinance")

    def test_descargador_publico_acepta_un_proveedor_inyectado(self):
        class _Proveedor:
            nombre = "prueba"

            def descargar(self, tickers):
                return [f"{self.nombre}:{ticker}" for ticker in tickers]

        self.assertEqual(
            descargar_fundamentales(["AAA"], proveedor=_Proveedor()),
            ["prueba:AAA"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
