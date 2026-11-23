"""Tests sin red para la abstracción y verificación de proveedores."""

from datetime import datetime, timezone
import unittest

import numpy as np
import pandas as pd

from providers import ProveedorYFinance
from screener_value import calcular_metricas, descargar_fundamentales


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

    def test_ev_no_netea_caja_restringida_ni_pasivos_operativos(self):
        """El float de clientes no se trata como caja o deuda financiera."""
        ticker = _TickerCompleto()
        ticker.info = {**ticker.info, "marketCap": 10_000.0}
        ticker.balance_sheet = ticker.balance_sheet.copy()
        ticker.balance_sheet.loc["Restricted Cash"] = [900.0, 800.0]
        ticker.balance_sheet.loc["Current Deferred Revenue"] = [700.0, 600.0]
        ticker.balance_sheet.loc["Value In Circulation"] = [3_000.0, 2_500.0]
        ticker.balance_sheet.loc["Customer Advances"] = [400.0, 350.0]

        datos = _proveedor(_Cliente(ticker=ticker)).descargar(["FLOAT"])[0]
        metricas = calcular_metricas([datos])

        self.assertEqual(datos.total_debt, 200.0)
        self.assertEqual(datos.cash, 100.0)
        self.assertAlmostEqual(
            metricas.iloc[0]["ev_ebit"],
            (10_000.0 + 200.0 - 100.0) / 140.0,
        )

    def test_caja_restringida_sola_no_se_presenta_como_liquidez_ev(self):
        ticker = _TickerCompleto()
        ticker.balance_sheet = ticker.balance_sheet.drop(
            index=["Cash And Cash Equivalents"],
        )
        ticker.balance_sheet.loc[
            "Cash Cash Equivalents Restricted Cash And Restricted Cash Equivalents"
        ] = [1_000.0, 900.0]

        datos = _proveedor(_Cliente(ticker=ticker)).descargar(["RESTRICTED"])[0]

        self.assertTrue(np.isnan(datos.cash))
        self.assertEqual(datos.calidad_datos, "revisar")
        self.assertIn("caja", datos.incidencias_datos)

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


class YFRateLimitError(Exception):
    """Doble con el mismo nombre que la excepción de yfinance."""


class _TickerVacio:
    """Lo que devuelve yfinance cuando oculta un 429: info sí, cuentas vacías."""

    info = _TickerCompleto.info
    income_stmt = cashflow = ttm_income_stmt = ttm_cashflow = balance_sheet = (
        pd.DataFrame()
    )


class _ClienteSecuencia:
    """Devuelve (o lanza) un resultado distinto en cada llamada a Ticker()."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = 0

    def Ticker(self, _simbolo):
        respuesta = self.respuestas[min(self.llamadas, len(self.respuestas) - 1)]
        self.llamadas += 1
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta


def _proveedor_con_reintentos(cliente, esperas, reintentos=3):
    return ProveedorYFinance(
        cliente=cliente,
        reloj=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
        resolver_fx=lambda _divisa: 1.0,
        reintentos=reintentos,
        pausa_reintento=10.0,
        pausa_entre_tickers=0.5,
        dormir=esperas.append,
    )


class TestReintentosYFinance(unittest.TestCase):
    def test_sin_reintentos_conserva_un_intento_por_ticker(self):
        cliente = _ClienteSecuencia([YFRateLimitError("Too Many Requests"), _TickerCompleto()])
        datos = _proveedor(cliente).descargar(["A"])[0]
        self.assertEqual(cliente.llamadas, 1)
        self.assertEqual(datos.calidad_datos, "error")

    def test_reintenta_un_limite_de_peticiones_con_espera_creciente(self):
        esperas = []
        cliente = _ClienteSecuencia([
            YFRateLimitError("Too Many Requests"),
            YFRateLimitError("Too Many Requests"),
            _TickerCompleto(),
        ])
        datos = _proveedor_con_reintentos(cliente, esperas).descargar(["A"])[0]
        self.assertEqual(cliente.llamadas, 3)
        self.assertEqual(esperas, [10.0, 20.0])
        self.assertEqual(datos.error_descarga, "")

    def test_no_reintenta_errores_que_no_son_transitorios(self):
        esperas = []
        cliente = _ClienteSecuencia([KeyError("symbol"), _TickerCompleto()])
        datos = _proveedor_con_reintentos(cliente, esperas).descargar(["A"])[0]
        self.assertEqual(cliente.llamadas, 1)
        self.assertEqual(esperas, [])
        self.assertIn("KeyError", datos.error_descarga)

    def test_estados_vacios_se_reintentan_una_vez(self):
        esperas = []
        cliente = _ClienteSecuencia([_TickerVacio(), _TickerCompleto()])
        datos = _proveedor_con_reintentos(cliente, esperas).descargar(["A"])[0]
        self.assertEqual(cliente.llamadas, 2)
        self.assertFalse(np.isnan(datos.ebit))

    def test_estados_vacios_persistentes_quedan_anotados(self):
        esperas = []
        cliente = _ClienteSecuencia([_TickerVacio()])
        datos = _proveedor_con_reintentos(cliente, esperas).descargar(["A"])[0]
        self.assertEqual(cliente.llamadas, 2)
        self.assertIn("posible límite de peticiones", datos.incidencias_datos)
        self.assertEqual(datos.calidad_datos, "inutilizable")

    def test_corta_las_esperas_si_yahoo_bloquea_varios_tickers_seguidos(self):
        esperas = []
        cliente = _ClienteSecuencia([YFRateLimitError("Too Many Requests")])
        datos = _proveedor_con_reintentos(cliente, esperas, reintentos=2).descargar(
            ["A", "B", "C", "D", "E"],
        )
        self.assertTrue(all(d.calidad_datos == "error" for d in datos))
        reintentos = [e for e in esperas if e != 0.5]
        # 3 tickers agotan sus 2 reintentos; los siguientes fallan sin esperar.
        self.assertEqual(reintentos, [10.0, 20.0] * 3)
        self.assertEqual(cliente.llamadas, 3 * 3 + 2)

    def test_pausa_entre_tickers(self):
        esperas = []
        _proveedor_con_reintentos(
            _ClienteSecuencia([_TickerCompleto()]), esperas,
        ).descargar(["A", "B", "C"])
        self.assertEqual(esperas, [0.5, 0.5])
