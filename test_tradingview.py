"""Tests sin red para tradingview.py (mapeo de tickers verificado a mano)."""

import unittest

import pandas as pd

from tradingview import (
    html_widget_tradingview,
    ticker_a_tradingview,
    tickers_candidatos_para_grafico,
)


class TestTickerATradingview(unittest.TestCase):
    def test_mercados_verificados_contra_el_widget_real(self):
        casos = {
            "ITX.MC": "BME:ITX",
            "SAP.DE": "XETR:SAP",
            "ASML.AS": "EURONEXT:ASML",
            "SAN.PA": "EURONEXT:SAN",
            "ENI.MI": "MIL:ENI",
            "OMV.VI": "VIE:OMV",
            "NOKIA.HE": "OMXHEX:NOKIA",
        }
        for ticker, esperado in casos.items():
            with self.subTest(ticker=ticker):
                self.assertEqual(ticker_a_tradingview(ticker), esperado)

    def test_ticker_sin_sufijo_se_pasa_tal_cual(self):
        self.assertEqual(ticker_a_tradingview("AAPL"), "AAPL")

    def test_minusculas_y_espacios_se_normalizan(self):
        self.assertEqual(ticker_a_tradingview(" itx.mc "), "BME:ITX")

    def test_sufijo_no_mapeado_se_pasa_tal_cual(self):
        # p. ej. un sufijo de país fuera de universo.txt: sin mapeo mejor
        # que inventar uno, el propio widget deja buscarlo a mano.
        self.assertEqual(ticker_a_tradingview("BHP.AX"), "BHP.AX")

    def test_ticker_inseguro_se_rechaza(self):
        with self.assertRaises(ValueError):
            ticker_a_tradingview('AAPL<script>')

    def test_selector_grafico_incluye_solo_candidatas_por_ranking(self):
        df = pd.DataFrame([
            {"ticker": "ZZZ", "pasa": False, "puntuacion": 1},
            {"ticker": "BBB", "pasa": True, "puntuacion": 4},
            {"ticker": "AAA", "pasa": True, "puntuacion": 2},
            {"ticker": "AAA", "pasa": True, "puntuacion": 2},
        ])
        self.assertEqual(tickers_candidatos_para_grafico(df), ["AAA", "BBB"])

    def test_widget_usa_embed_actual_y_locale(self):
        contenido = html_widget_tradingview("ITX.MC", locale="es")
        self.assertIn("embed-widget-advanced-chart.js", contenido)
        self.assertIn('"symbol": "BME:ITX"', contenido)
        self.assertIn('"locale": "es"', contenido)
        self.assertIn('"hide_side_toolbar": true', contenido)
        self.assertIn("height:680px", contenido)
        self.assertNotIn("tv.js", contenido)


if __name__ == "__main__":
    unittest.main(verbosity=2)
