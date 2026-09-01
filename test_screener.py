"""Tests sin red para screener_value.py y universos_yfinance.py."""

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from screener_value import (
    Fundamentales,
    UMBRALES,
    _extremos_historicos,
    aplicar_filtros,
    calcular_metricas,
    cagr_ingresos,
    cobertura_intereses,
    deuda_neta_ebitda,
    enterprise_value,
    ev_ebit,
    fcf_yield,
    capital_invertido,
    deduplicar_listings,
    incorporar_ranking_candidatos,
    resumir_incidencias_calidad,
    margen_operativo,
    region_comparable,
    per,
    roic,
)
from universos_yfinance import (
    catalogar_tickers,
    construir_consulta,
    obtener_tickers_universo,
)


def empresa_ideal(ticker="OK", **cambios):
    base = dict(
        ticker=ticker,
        nombre="Ideal SA",
        sector="Industrials",
        divisa_cotizacion="EUR",
        divisa_financiera="EUR",
        divisa_consistente=True,
        market_cap=10e9,
        market_cap_eur=10e9,
        net_income=1.0e9,
        ebit=1.4e9,
        ebitda=1.8e9,
        ingresos=10e9,
        ingresos_inicio_historico=8e9,
        ingresos_fin_historico=10e9,
        anios_historico=4.0,
        free_cash_flow=0.8e9,
        total_debt=2e9,
        cash=1e9,
        equity=5e9,
        gasto_intereses=100e6,
        tasa_impositiva=0.25,
    )
    base.update(cambios)
    return Fundamentales(**base)


class TestMetricas(unittest.TestCase):
    def test_metricas_basicas(self):
        self.assertAlmostEqual(per(1000, 100), 10)
        self.assertAlmostEqual(enterprise_value(1000, 300, 100), 1200)
        self.assertAlmostEqual(ev_ebit(1000, 300, 100, 150), 8)
        self.assertAlmostEqual(fcf_yield(80, 1000), 0.08)
        self.assertAlmostEqual(margen_operativo(140, 1000), 0.14)

    def test_per_y_ev_ebit_con_perdidas_son_nan(self):
        self.assertTrue(np.isnan(per(1000, -1)))
        self.assertTrue(np.isnan(ev_ebit(1000, 300, 100, -1)))

    def test_capital_invertido_no_resta_caja(self):
        """La caja NO se resta: es capital que debe generar retorno."""
        self.assertAlmostEqual(capital_invertido(300, 500, 100), 800)
        self.assertAlmostEqual(capital_invertido(300, 500, 99999), 800)

    def test_roic_capital_final(self):
        # Capital 300+500=800; NOPAT 200*0.75=150.
        self.assertAlmostEqual(roic(200, 300, 500, 100), 150 / 800)

    def test_roic_capital_medio(self):
        # Capital final 800; inicial 600; media 700; NOPAT 150.
        self.assertAlmostEqual(
            roic(200, 300, 500, 100, 0.25, 200, 400, 100),
            150 / 700,
        )

    def test_caja_neta_ya_no_infla_roic(self):
        """Misma empresa operativa, una con caja enorme: mismo o menor ROIC.

        Con la formula anterior la version con caja daba un ROIC disparado.
        """
        sin_caja = roic(200, 300, 500, 0)
        con_caja = roic(200, 300, 500, 400)
        self.assertAlmostEqual(sin_caja, con_caja)
        self.assertLess(con_caja, 150 / 400)  # lo que daba la formula vieja

    def test_deuda_y_cobertura(self):
        self.assertAlmostEqual(deuda_neta_ebitda(500, 100, 200), 2)
        self.assertEqual(deuda_neta_ebitda(100, 500, 200), 0)
        self.assertEqual(cobertura_intereses(200, 0), np.inf)
        self.assertAlmostEqual(cobertura_intereses(200, -20), 10)

    def test_cagr(self):
        self.assertAlmostEqual(cagr_ingresos(100, 121, 2), 0.10, places=6)


class TestHistorico(unittest.TestCase):
    def test_ordena_columnas_y_calcula_anos_reales(self):
        # Columnas deliberadamente desordenadas.
        df = pd.DataFrame(
            {
                pd.Timestamp("2021-12-31"): [100],
                pd.Timestamp("2024-12-31"): [133.1],
                pd.Timestamp("2022-12-31"): [110],
            },
            index=["Total Revenue"],
        )
        inicio, fin, anios = _extremos_historicos(df, ["Total Revenue"])
        self.assertEqual(inicio, 100)
        self.assertEqual(fin, 133.1)
        self.assertAlmostEqual(anios, 3.0, places=2)


class TestFiltros(unittest.TestCase):
    def _fila(self, empresa):
        return calcular_metricas([empresa, empresa_ideal("REF")]).iloc[0]

    def test_empresa_ideal_pasa(self):
        fila = self._fila(empresa_ideal())
        self.assertTrue(aplicar_filtros(pd.DataFrame([fila])).iloc[0]["pasa"])

    def test_divisas_distintas_descartan_y_anulan_ratios(self):
        empresa = empresa_ideal(
            divisa_cotizacion="USD",
            divisa_financiera="EUR",
            divisa_consistente=False,
        )
        metricas = calcular_metricas([empresa])
        self.assertTrue(np.isnan(metricas.iloc[0]["per"]))
        resultado = aplicar_filtros(metricas)
        self.assertFalse(resultado.iloc[0]["pasa"])
        self.assertIn("divisas", resultado.iloc[0]["motivos_descarte"])

    def test_market_cap_se_filtra_en_eur(self):
        resultado = aplicar_filtros(calcular_metricas([
            empresa_ideal(market_cap=10e9, market_cap_eur=500e6)
        ]))
        self.assertFalse(resultado.iloc[0]["pasa"])
        self.assertIn("capitalización", resultado.iloc[0]["motivos_descarte"].lower())

    def test_dato_ausente_descarta(self):
        resultado = aplicar_filtros(calcular_metricas([
            empresa_ideal(free_cash_flow=np.nan)
        ]))
        self.assertFalse(resultado.iloc[0]["pasa"])
        self.assertIn("sin dato", resultado.iloc[0]["motivos_descarte"])

    def test_calidad_pendiente_de_revision_no_puede_ser_candidata(self):
        resultado = aplicar_filtros(calcular_metricas([
            empresa_ideal(
                calidad_datos="revisar",
                incidencias_datos="resultados obsoletos (700 días)",
            )
        ]))
        self.assertFalse(resultado.iloc[0]["pasa"])
        self.assertIn("resultados obsoletos", resultado.iloc[0]["motivos_descarte"])

    def test_entrada_vacia(self):
        metricas = calcular_metricas([])
        resultado = aplicar_filtros(metricas)
        self.assertTrue(resultado.empty)
        self.assertIn("pasa", resultado.columns)


class TestResumenIncidencias(unittest.TestCase):
    def test_agrupa_por_empresa_y_desglosa_campos_ausentes(self):
        datos = pd.DataFrame([
            {
                "ticker": "A",
                "calidad_datos": "revisar",
                "error_descarga": "",
                "incidencias_datos": (
                    "campos ausentes: EBIT, gasto por intereses; "
                    "fecha de balance ausente"
                ),
            },
            {
                "ticker": "B",
                "calidad_datos": "revisar",
                "error_descarga": "",
                "incidencias_datos": (
                    "campos ausentes: EBIT; resultados obsoleto (700 días); "
                    "flujo de caja obsoleto (700 días)"
                ),
            },
            {
                "ticker": "C",
                "calidad_datos": np.nan,
                "error_descarga": np.nan,
                "incidencias_datos": np.nan,
            },
        ])

        resumen = resumir_incidencias_calidad(datos)

        self.assertEqual(resumen["empresas_con_incidencias"], 2)
        self.assertEqual(resumen["categorias"]["campos_ausentes"], 2)
        # B tiene dos estados obsoletos, pero cuenta una sola vez en la categoría.
        self.assertEqual(resumen["categorias"]["cuentas_obsoletas"], 1)
        self.assertEqual(resumen["categorias"]["fechas_ausentes"], 1)
        self.assertEqual(resumen["campos_ausentes"]["EBIT"], 2)
        self.assertEqual(resumen["campos_ausentes"]["gasto por intereses"], 1)

    def test_separa_errores_de_descarga_de_otras_incidencias(self):
        datos = pd.DataFrame([{
            "ticker": "ERR",
            "calidad_datos": "error",
            "error_descarga": "TimeoutError: sin respuesta",
            "incidencias_datos": "TimeoutError: sin respuesta",
        }])

        resumen = resumir_incidencias_calidad(datos)

        self.assertEqual(resumen["categorias"], {"errores_descarga": 1})
        self.assertEqual(resumen["empresas_con_incidencias"], 1)


class TestMedianaSectorial(unittest.TestCase):
    def test_cuenta_solo_per_validos(self):
        universo = [empresa_ideal("A"), empresa_ideal("B", net_income=-1)]
        metricas = calcular_metricas(universo)
        self.assertEqual(metricas.loc[metricas["ticker"] == "A", "n_sector"].iloc[0], 1)

    def test_per_superior_mediana_descarta(self):
        universo = [empresa_ideal("CARA", net_income=10e9 / 14)] + [
            empresa_ideal(f"B{i}") for i in range(6)
        ]
        resultado = aplicar_filtros(calcular_metricas(universo))
        cara = resultado[resultado["ticker"] == "CARA"].iloc[0]
        self.assertFalse(cara["pasa"])
        self.assertIn("mediana sector", cara["motivos_descarte"])


class TestRanking(unittest.TestCase):
    def test_solo_rankea_candidatas(self):
        universo = [
            empresa_ideal("MEJOR", ebit=2e9, net_income=1.5e9),
            empresa_ideal("DESCARTADA", free_cash_flow=0.01e9),
        ]
        evaluadas = aplicar_filtros(calcular_metricas(universo))
        resultado = incorporar_ranking_candidatos(evaluadas)
        mejor = resultado[resultado["ticker"] == "MEJOR"].iloc[0]
        descartada = resultado[resultado["ticker"] == "DESCARTADA"].iloc[0]
        self.assertFalse(np.isnan(mejor["puntuacion"]))
        self.assertTrue(np.isnan(descartada["puntuacion"]))


class TestUniversos(unittest.TestCase):
    @patch("universos_yfinance.yf.screen")
    def test_devuelve_tickers_deduplicados(self, screen):
        screen.return_value = {
            "quotes": [
                {"symbol": "aaa"},
                {"symbol": "BBB"},
                {"symbol": "AAA"},
                {},
            ]
        }
        tickers = obtener_tickers_universo("espana", max_por_bucket=10, por_sector=False)
        self.assertEqual(tickers, ["AAA", "BBB"])
        screen.assert_called_once()  # 1 region x 1 bucket

    def test_universo_desconocido(self):
        with self.assertRaises(ValueError):
            obtener_tickers_universo("marte")

    def test_maximo_cero_no_llama_red(self):
        self.assertEqual(obtener_tickers_universo("usa", max_por_bucket=0), [])


class TestCatalogoActivos(unittest.TestCase):
    """Descubrimiento de ETF y fondos por categoría, además de acciones."""

    def test_construir_consulta_accion_sin_cambios(self):
        """El refactor a construir_consulta_activo no cambia la consulta de acciones."""
        consulta = construir_consulta(["us"], sectores=["Technology"]).to_dict()
        campos = {op["operands"][0]["operands"][0] if op["operator"] == "OR"
                  else op["operands"][0]
                  for op in consulta["operands"]}
        self.assertEqual(campos, {"region", "sector", "intradayprice", "avgdailyvol3m"})

    @patch("universos_yfinance.yf.screen")
    def test_catalogar_etf_etiqueta_por_categoria_y_region(self, screen):
        screen.return_value = {"quotes": [{"symbol": "spy"}, {"symbol": "qqq"}]}
        catalogo = catalogar_tickers(
            "etf", "espana", categorias=["Technology"], max_por_bucket=10,
        )
        self.assertEqual(set(catalogo["ticker"]), {"SPY", "QQQ"})
        self.assertTrue((catalogo["tipo_activo"] == "etf").all())
        self.assertTrue((catalogo["categoria"] == "Technology").all())
        self.assertTrue((catalogo["region"] == "es").all())
        consulta_enviada = screen.call_args[0][0]
        self.assertIn("categoryname", str(consulta_enviada.to_dict()))

    @patch("universos_yfinance.yf.screen")
    def test_catalogar_fondo_no_exige_ni_filtra_region(self, screen):
        screen.return_value = {"quotes": [{"symbol": "vfiax"}]}
        catalogo = catalogar_tickers("fondo", categorias=["Large Growth"], max_por_bucket=10)
        self.assertEqual(list(catalogo["ticker"]), ["VFIAX"])
        self.assertEqual(catalogo.iloc[0]["region"], "")
        consulta_enviada = screen.call_args[0][0]
        self.assertNotIn("'region'", str(consulta_enviada.to_dict()))

    def test_tipo_activo_desconocido(self):
        with self.assertRaises(ValueError):
            catalogar_tickers("cripto", "usa")

    def test_etf_sin_universo_falla(self):
        with self.assertRaises(ValueError):
            catalogar_tickers("etf", categorias=["Technology"])


class TestSesgoCajaNeta(unittest.TestCase):
    """El bug original: la caja neta puntuaba dos veces en el ranking."""

    def test_ranking_no_premia_dos_veces_la_caja(self):
        # Dos empresas operativamente identicas; B tiene mucha caja neta.
        universo = [
            empresa_ideal("SIN_CAJA", cash=0.2e9, total_debt=2e9),
            empresa_ideal("CON_CAJA", cash=6.0e9, total_debt=2e9),
        ]
        m = calcular_metricas(universo)
        roics = dict(zip(m["ticker"], m["roic"]))
        # Mismo EBIT y mismo capital empleado -> mismo ROIC pese a la caja.
        self.assertAlmostEqual(roics["SIN_CAJA"], roics["CON_CAJA"], places=6)
        # La caja sigue abaratando el EV/EBIT, y eso es correcto: cuenta UNA vez.
        evs = dict(zip(m["ticker"], m["ev_ebit"]))
        self.assertLess(evs["CON_CAJA"], evs["SIN_CAJA"])

    def test_columna_caja_neta_diagnostica(self):
        m = calcular_metricas([empresa_ideal(cash=6e9, total_debt=1e9,
                                             market_cap=10e9)])
        self.assertAlmostEqual(m.iloc[0]["caja_neta_pct_mcap"], 0.5)

    def test_roic_extremo_se_marca_no_fiable(self):
        # Fondos propios minusculos -> ROIC disparado pero sin sentido.
        m = calcular_metricas([empresa_ideal("ASSETLIGHT", equity=0.05e9,
                                             total_debt=0.01e9)])
        self.assertGreater(m.iloc[0]["roic"], 1.0)
        self.assertFalse(bool(m.iloc[0]["roic_fiable"]))

    def test_roic_normal_es_fiable(self):
        m = calcular_metricas([empresa_ideal()])
        self.assertTrue(bool(m.iloc[0]["roic_fiable"]))


class TestMedianaRegional(unittest.TestCase):
    """La mediana sectorial ya no la contamina el sesgo geografico."""

    def test_region_comparable(self):
        self.assertEqual(region_comparable("United States"), "Norteamerica")
        self.assertEqual(region_comparable("Japan"), "Japon")
        self.assertEqual(region_comparable("Spain"), "Europa")
        self.assertEqual(region_comparable("Brazil"), "Otros")

    def test_mediana_se_calcula_por_region(self):
        """Una empresa de EE.UU. con PER 20 rodeada de japonesas con PER 10
        debe compararse con sus pares americanos, no con la mediana japonesa."""
        universo = (
            [empresa_ideal(f"JP{i}", pais="Japan", net_income=1.0e9) for i in range(6)]
            + [empresa_ideal(f"US{i}", pais="United States",
                             net_income=10e9 / 20) for i in range(6)]
        )
        m = calcular_metricas(universo)
        med_jp = m[m["region"] == "Japon"]["per_mediana_sector"].iloc[0]
        med_us = m[m["region"] == "Norteamerica"]["per_mediana_sector"].iloc[0]
        self.assertAlmostEqual(med_jp, 10.0, places=4)
        self.assertAlmostEqual(med_us, 20.0, places=4)
        self.assertTrue((m["base_mediana"] == "sector+region").all())

    def test_fallback_a_sector_global_si_region_pequena(self):
        universo = (
            [empresa_ideal(f"JP{i}", pais="Japan") for i in range(6)]
            + [empresa_ideal("SOLA", pais="United States")]
        )
        m = calcular_metricas(universo)
        fila = m[m["ticker"] == "SOLA"].iloc[0]
        self.assertEqual(fila["base_mediana"], "sector_global")
        self.assertEqual(fila["n_sector"], 7)


class TestDeduplicado(unittest.TestCase):
    def test_colapsa_cotizaciones_duales(self):
        universo = [
            empresa_ideal("GSK.L", nombre="GSK plc", pais="United Kingdom",
                          market_cap_eur=89.9e9),
            empresa_ideal("GSKL.XC", nombre="GSK plc", pais="United Kingdom",
                          market_cap_eur=89.8e9),
            empresa_ideal("OTRA", nombre="Otra SA", pais="United Kingdom"),
        ]
        res = deduplicar_listings(calcular_metricas(universo))
        self.assertEqual(len(res), 2)
        self.assertIn("GSK.L", set(res["ticker"]))  # mayor capitalizacion
        self.assertNotIn("GSKL.XC", set(res["ticker"]))

    def test_sufijos_societarios_no_impiden_el_match(self):
        universo = [
            empresa_ideal("AUTO.L", nombre="Autotrader Group plc",
                          pais="United Kingdom", market_cap_eur=4.8e9),
            empresa_ideal("AUTOL.XC", nombre="Autotrader Group PLC",
                          pais="United Kingdom", market_cap_eur=4.5e9),
        ]
        self.assertEqual(len(deduplicar_listings(calcular_metricas(universo))), 1)

    def test_no_colapsa_empresas_distintas(self):
        universo = [
            empresa_ideal("A", nombre="Alfa SA", pais="Spain"),
            empresa_ideal("B", nombre="Beta SA", pais="Spain"),
        ]
        self.assertEqual(len(deduplicar_listings(calcular_metricas(universo))), 2)

    def test_dataframe_vacio(self):
        self.assertTrue(deduplicar_listings(calcular_metricas([])).empty)


if __name__ == "__main__":
    unittest.main(verbosity=2)
