"""Tests sin red para prompt_llm.py (Fase 5 ligera: prompt copy-paste)."""

import unittest

import numpy as np
import pandas as pd

from prompt_llm import generar_prompt_interpretacion


def _candidata(ticker="OK", **cambios):
    base = dict(
        ticker=ticker, nombre=f"{ticker} SA", sector="Industrials", region="Europa",
        per=9.5, ev_ebit=11.8, fcf_yield=0.171, roic=0.121, roic_fiable=True,
        deuda_ebitda=2.19, cobertura_int=8.0, cagr_ingresos=0.05,
        caja_neta_pct_mcap=-0.10, market_cap_eur=3.2e9, puntuacion=2.0,
    )
    base.update(cambios)
    return base


class TestGenerarPrompt(unittest.TestCase):
    def test_dataframe_vacio_devuelve_none(self):
        self.assertIsNone(generar_prompt_interpretacion(pd.DataFrame()))
        self.assertIsNone(generar_prompt_interpretacion(None))

    def test_incluye_ticker_y_metricas(self):
        prompt = generar_prompt_interpretacion(pd.DataFrame([_candidata("NOS.LS")]))
        self.assertIn("NOS.LS", prompt)
        self.assertIn("PER: 9.50", prompt)
        self.assertIn("17.1%", prompt)  # fcf_yield formateado como %

    def test_incluye_instrucciones_de_no_recomendar(self):
        prompt = generar_prompt_interpretacion(pd.DataFrame([_candidata()]))
        self.assertIn("NO recomiendes comprar", prompt)
        self.assertIn("NO inventes una tesis", prompt)

    def test_incluye_el_disclaimer(self):
        from screener_value import DISCLAIMER
        prompt = generar_prompt_interpretacion(pd.DataFrame([_candidata()]))
        self.assertIn(DISCLAIMER, prompt)

    def test_senala_roic_no_fiable(self):
        prompt = generar_prompt_interpretacion(
            pd.DataFrame([_candidata("DUDOSA", roic_fiable=False)])
        )
        self.assertIn("fiable: no", prompt)

    def test_varias_candidatas_se_numeran(self):
        df = pd.DataFrame([_candidata("AAA"), _candidata("BBB")])
        prompt = generar_prompt_interpretacion(df)
        self.assertIn("1. AAA", prompt)
        self.assertIn("2. BBB", prompt)
        self.assertIn("CANDIDATAS (2)", prompt)

    def test_valor_ausente_se_muestra_como_nd_no_rompe(self):
        prompt = generar_prompt_interpretacion(
            pd.DataFrame([_candidata("SINDATO", cagr_ingresos=np.nan)])
        )
        self.assertIn("N/D", prompt)

    def test_prompt_ingles_para_dashboard_bilingue(self):
        prompt = generar_prompt_interpretacion(
            pd.DataFrame([_candidata("ENGLISH", roic_fiable=False)]),
            idioma="en",
        )
        self.assertIn("CANDIDATES (1)", prompt)
        self.assertIn("Net debt/EBITDA", prompt)
        self.assertIn("reliable: no", prompt)
        self.assertIn("must not", prompt)

    def test_idioma_no_soportado_se_rechaza(self):
        with self.assertRaises(ValueError):
            generar_prompt_interpretacion(pd.DataFrame([_candidata()]), idioma="fr")


if __name__ == "__main__":
    unittest.main(verbosity=2)
