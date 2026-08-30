"""Tests sin red para tradingview.py (mapeo de tickers verificado a mano)."""

import unittest

from tradingview import ticker_a_tradingview


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
