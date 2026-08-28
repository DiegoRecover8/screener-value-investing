"""Screener fundamental de acciones orientado a valor y calidad.

El módulo separa tres responsabilidades:

1. Descarga de datos actuales con yfinance.
2. Cálculo puro de métricas, testeable sin red.
3. Filtros y ranking de las empresas que realmente superan la rúbrica.

Los datos de Yahoo Finance sirven para descubrir candidatos, no sustituyen la
revisión de los estados financieros publicados por la empresa.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Iterable

import numpy as np
import pandas as pd


UMBRALES = {
    "per_max": 15.0,
    "per_bajo_mediana_sector": True,
    "min_empresas_sector": 5,
    "ev_ebit_max": 12.0,
    "fcf_yield_min": 0.06,
    "roic_min": 0.10,
    "margen_op_min": 0.0,
    "deuda_ebitda_max": 2.5,
    "cobertura_intereses_min": 5.0,
    "crecimiento_ingresos_min": 0.0,
    "market_cap_eur_min": 2_000_000_000,
}

DISCLAIMER = (
    "AVISO: esta herramienta es un ejercicio educativo de análisis fundamental. "
    "No constituye asesoramiento financiero ni una recomendación de compra o "
    "venta. Los datos proceden de Yahoo Finance, pueden contener errores o "
    "estar incompletos (ver limitaciones en el README), y superar estos "
    "filtros no implica que una empresa sea una buena inversión. Verifica "
    "siempre las cuentas publicadas por la propia empresa antes de decidir."
)

TASA_IMPOSITIVA_DEFECTO = 0.25

# Por encima de este ROIC la base de capital es tan pequeña que la métrica
# deja de ser comparable. No filtra: marca la fila para revisión manual.
ROIC_MAXIMO_FIABLE = 1.00  # 100%

# Agrupación para las medianas sectoriales. Comparar el PER de una empresa
# estadounidense contra una mediana dominada por cotizadas japonesas no mide
# "barata frente a su sector", mide "barata frente a otro mercado".
REGIONES_COMPARABLES = {
    "Norteamerica": {"United States", "Canada"},
    "Europa": {
        "United Kingdom", "Germany", "France", "Netherlands", "Switzerland",
        "Spain", "Italy", "Sweden", "Denmark", "Norway", "Finland", "Belgium",
        "Austria", "Ireland", "Portugal", "Luxembourg", "Iceland",
    },
    "Japon": {"Japan"},
    "AsiaPacifico": {
        "Australia", "New Zealand", "Singapore", "Hong Kong", "China",
        "South Korea", "Taiwan",
    },
}


def region_comparable(pais: str) -> str:
    """Agrupa países en bloques de valoración comparable."""
    pais = (pais or "").strip()
    for region, paises in REGIONES_COMPARABLES.items():
        if pais in paises:
            return region
    return "Otros"


def _es_na(valor) -> bool:
    try:
        return bool(pd.isna(valor))
    except (TypeError, ValueError):
        return True


def _div(a, b):
    """División segura."""
    if a is None or b is None:
        return np.nan
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return np.nan
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return np.nan
    return a / b


def per(market_cap, net_income):
    if _es_na(net_income) or float(net_income) <= 0:
        return np.nan
    return _div(market_cap, net_income)


def enterprise_value(market_cap, total_debt, cash):
    if any(_es_na(v) for v in (market_cap, total_debt, cash)):
        return np.nan
    return float(market_cap) + float(total_debt) - float(cash)


def ev_ebit(market_cap, total_debt, cash, ebit):
    if _es_na(ebit) or float(ebit) <= 0:
        return np.nan
    return _div(enterprise_value(market_cap, total_debt, cash), ebit)


def fcf_yield(free_cash_flow, market_cap):
    return _div(free_cash_flow, market_cap)


def capital_invertido(total_debt, equity, cash=None):
    """Capital empleado = deuda financiera + fondos propios.

    NO se resta la caja, a diferencia de la versión inicial. Restarla inflaba
    artificialmente el ROIC de las empresas con caja neta (denominador
    minúsculo) y, combinado con el EV/EBIT del ranking —que TAMBIÉN mejora con
    la caja neta—, hacía que el mismo hecho de balance puntuase dos veces.

    Sin restarla, la caja ociosa cuenta como capital que no genera retorno
    operativo, que es justo lo que queremos detectar: una empresa que acumula
    liquidez improductiva muestra un ROIC más bajo, no más alto.

    El parámetro `cash` se mantiene por compatibilidad de firma y se ignora.
    """
    if any(_es_na(v) for v in (total_debt, equity)):
        return np.nan
    capital = float(total_debt) + float(equity)
    return capital if capital > 0 else np.nan


def roic(
    ebit,
    total_debt,
    equity,
    cash,
    tasa=TASA_IMPOSITIVA_DEFECTO,
    total_debt_inicio=np.nan,
    equity_inicio=np.nan,
    cash_inicio=np.nan,
):
    """ROIC aproximado usando capital invertido medio cuando está disponible."""
    if _es_na(ebit):
        return np.nan
    capital_fin = capital_invertido(total_debt, equity, cash)
    if _es_na(capital_fin):
        return np.nan

    capital_inicio = capital_invertido(total_debt_inicio, equity_inicio, cash_inicio)
    capital_medio = (
        (capital_inicio + capital_fin) / 2
        if not _es_na(capital_inicio)
        else capital_fin
    )
    tasa = TASA_IMPOSITIVA_DEFECTO if _es_na(tasa) else min(max(float(tasa), 0.0), 0.50)
    nopat = float(ebit) * (1 - tasa)
    return _div(nopat, capital_medio)


def margen_operativo(ebit, ingresos):
    return _div(ebit, ingresos)


def deuda_neta_ebitda(total_debt, cash, ebitda):
    if _es_na(ebitda) or float(ebitda) <= 0:
        return np.nan
    deuda_neta = enterprise_value(0, total_debt, cash)
    if _es_na(deuda_neta):
        return np.nan
    return 0.0 if deuda_neta <= 0 else _div(deuda_neta, ebitda)


def cobertura_intereses(ebit, gasto_intereses):
    if _es_na(ebit) or _es_na(gasto_intereses):
        return np.nan
    gasto = abs(float(gasto_intereses))
    return np.inf if gasto == 0 else _div(ebit, gasto)


def cagr_ingresos(ingresos_inicio, ingresos_fin, anios):
    if _es_na(ingresos_inicio) or _es_na(ingresos_fin) or _es_na(anios):
        return np.nan
    if float(ingresos_inicio) <= 0 or float(anios) <= 0:
        return np.nan
    if float(ingresos_fin) <= 0:
        return -1.0
    return (float(ingresos_fin) / float(ingresos_inicio)) ** (1 / float(anios)) - 1


@dataclass
class Fundamentales:
    ticker: str
    nombre: str = ""
    sector: str = "SIN_SECTOR"
    pais: str = ""
    divisa_cotizacion: str = ""
    divisa_financiera: str = ""
    divisa_consistente: bool = True
    market_cap: float = np.nan
    market_cap_eur: float = np.nan
    net_income: float = np.nan
    ebit: float = np.nan
    ebitda: float = np.nan
    ingresos: float = np.nan
    ingresos_inicio_historico: float = np.nan
    ingresos_fin_historico: float = np.nan
    anios_historico: float = np.nan
    free_cash_flow: float = np.nan
    total_debt: float = np.nan
    cash: float = np.nan
    equity: float = np.nan
    total_debt_inicio: float = np.nan
    cash_inicio: float = np.nan
    equity_inicio: float = np.nan
    gasto_intereses: float = np.nan
    tasa_impositiva: float = TASA_IMPOSITIVA_DEFECTO
    error_descarga: str = ""


COLUMNAS_METRICAS = [
    "ticker", "nombre", "sector", "pais", "divisa_cotizacion",
    "divisa_financiera", "divisa_consistente", "market_cap",
    "market_cap_eur", "per", "ev_ebit", "fcf_yield", "roic",
    "margen_op", "deuda_ebitda", "cobertura_int", "cagr_ingresos",
    "earnings_yield", "caja_neta_pct_mcap", "roic_fiable", "region",
    "per_mediana_sector", "n_sector", "base_mediana", "error_descarga",
]


def calcular_metricas(
    datos: list[Fundamentales] | pd.DataFrame, u: dict = UMBRALES
) -> pd.DataFrame:
    """Convierte fundamentales crudos en métricas comparables.

    `u["min_empresas_sector"]` decide el fallback de la mediana regional (ver
    más abajo). Se acepta como parámetro -y no se lee del `UMBRALES` global-
    para que el dashboard pueda recalcular con un umbral distinto sin que la
    mediana y el filtro que la usa queden desincronizados.
    """
    if isinstance(datos, pd.DataFrame):
        df = datos.copy()
    else:
        df = pd.DataFrame([asdict(d) for d in datos])
    if df.empty:
        return pd.DataFrame(columns=COLUMNAS_METRICAS)

    out = pd.DataFrame(index=df.index)
    for col in (
        "ticker", "nombre", "sector", "pais", "divisa_cotizacion",
        "divisa_financiera", "divisa_consistente", "market_cap",
        "market_cap_eur", "error_descarga",
    ):
        defecto = True if col == "divisa_consistente" else ""
        out[col] = df[col] if col in df else defecto

    # Las ratios que mezclan cotización y cuentas solo se calculan si ambas
    # están expresadas en la misma divisa.
    mc_compatible = df["market_cap"].where(out["divisa_consistente"], np.nan)
    out["per"] = [per(mc, ni) for mc, ni in zip(mc_compatible, df["net_income"])]
    out["ev_ebit"] = [
        ev_ebit(mc, d, c, e)
        for mc, d, c, e in zip(mc_compatible, df["total_debt"], df["cash"], df["ebit"])
    ]
    out["fcf_yield"] = [
        fcf_yield(fcf, mc) for fcf, mc in zip(df["free_cash_flow"], mc_compatible)
    ]
    out["roic"] = [
        roic(e, d, eq, c, tasa, di, eqi, ci)
        for e, d, eq, c, tasa, di, eqi, ci in zip(
            df["ebit"], df["total_debt"], df["equity"], df["cash"],
            df["tasa_impositiva"], df["total_debt_inicio"],
            df["equity_inicio"], df["cash_inicio"],
        )
    ]
    out["margen_op"] = [margen_operativo(e, r) for e, r in zip(df["ebit"], df["ingresos"])]
    out["deuda_ebitda"] = [
        deuda_neta_ebitda(d, c, eb)
        for d, c, eb in zip(df["total_debt"], df["cash"], df["ebitda"])
    ]
    out["cobertura_int"] = [
        cobertura_intereses(e, g) for e, g in zip(df["ebit"], df["gasto_intereses"])
    ]
    out["cagr_ingresos"] = [
        cagr_ingresos(i0, i1, n)
        for i0, i1, n in zip(
            df["ingresos_inicio_historico"],
            df["ingresos_fin_historico"],
            df["anios_historico"],
        )
    ]
    out["earnings_yield"] = 1 / out["ev_ebit"].replace(0, np.nan)

    # --- Diagnóstico de balance -------------------------------------------
    # Caja neta sobre capitalización: mide cuánto de lo "barato" es solo
    # tesorería. Un valor alto es la firma del value trap japonés clásico
    # (liquidez ociosa que deprime el ROE y nunca vuelve al accionista).
    caja_neta = [
        np.nan if any(_es_na(v) for v in (c, d)) else float(c) - float(d)
        for c, d in zip(df["cash"], df["total_debt"])
    ]
    out["caja_neta_pct_mcap"] = [
        _div(cn, mc) for cn, mc in zip(caja_neta, mc_compatible)
    ]
    # ROIC deja de ser informativo cuando la base de capital es diminuta
    # (negocios asset-light con fondos propios casi nulos): un 400% no
    # significa "40 veces mejor", significa "métrica sin sentido aquí".
    out["roic_fiable"] = [
        (not _es_na(r)) and float(r) <= ROIC_MAXIMO_FIABLE for r in out["roic"]
    ]

    # --- Mediana sectorial por región comparable ---------------------------
    # Calcularla sobre todo el universo la contaminaba: con un universo 70%
    # japonés, la "mediana del sector" era en realidad la mediana japonesa
    # (PER ~17,5 frente a ~32 en EE.UU.), y ninguna empresa estadounidense
    # podía pasar el filtro relativo. Se compara con los pares de su región.
    out["region"] = [region_comparable(p) for p in out["pais"]]
    grupo_regional = out.groupby(["sector", "region"])["per"]
    med_regional = grupo_regional.transform("median")
    n_regional = grupo_regional.transform("count")
    # Fallback al sector global si la región tiene pocos comparables.
    grupo_global = out.groupby("sector")["per"]
    med_global = grupo_global.transform("median")
    n_global = grupo_global.transform("count")
    suficiente = n_regional >= u["min_empresas_sector"]
    out["per_mediana_sector"] = med_regional.where(suficiente, med_global)
    # Cuenta PER válidos, no simplemente tickers del sector.
    out["n_sector"] = n_regional.where(suficiente, n_global)
    out["base_mediana"] = np.where(suficiente, "sector+region", "sector_global")
    return out


def evaluar_fila(fila: pd.Series, u: dict = UMBRALES) -> tuple[bool, list[str]]:
    motivos: list[str] = []

    if fila.get("error_descarga", ""):
        motivos.append(f"descarga: {fila['error_descarga']}")
    if not bool(fila.get("divisa_consistente", False)):
        motivos.append("divisas de cotización y estados financieros incompatibles")

    def chk(nombre, valor, condicion, texto):
        if _es_na(valor):
            motivos.append(f"{nombre}: sin dato")
        elif not condicion:
            motivos.append(texto)

    chk("PER", fila["per"], fila["per"] < u["per_max"], f"PER {fila['per']:.1f} >= {u['per_max']}")
    n_sector = fila.get("n_sector", 0)
    if u["per_bajo_mediana_sector"] and n_sector >= u["min_empresas_sector"]:
        med = fila.get("per_mediana_sector", np.nan)
        condicion = not _es_na(fila["per"]) and not _es_na(med) and fila["per"] < med
        texto = (
            f"PER {fila['per']:.1f} >= mediana sector {med:.1f}"
            if not _es_na(med) and not _es_na(fila["per"])
            else "PER vs sector: sin dato"
        )
        chk("PER vs sector", med, condicion, texto)
    chk("EV/EBIT", fila["ev_ebit"], fila["ev_ebit"] < u["ev_ebit_max"], f"EV/EBIT {fila['ev_ebit']:.1f} >= {u['ev_ebit_max']}")
    chk("FCF yield", fila["fcf_yield"], fila["fcf_yield"] > u["fcf_yield_min"], f"FCF yield {fila['fcf_yield']:.1%} <= {u['fcf_yield_min']:.0%}")
    chk("ROIC", fila["roic"], fila["roic"] > u["roic_min"], f"ROIC {fila['roic']:.1%} <= {u['roic_min']:.0%}")
    chk("Margen op.", fila["margen_op"], fila["margen_op"] > u["margen_op_min"], f"margen operativo {fila['margen_op']:.1%} <= {u['margen_op_min']:.0%}")
    chk("Deuda/EBITDA", fila["deuda_ebitda"], fila["deuda_ebitda"] < u["deuda_ebitda_max"], f"deuda neta/EBITDA {fila['deuda_ebitda']:.1f} >= {u['deuda_ebitda_max']}")
    chk("Cobertura int.", fila["cobertura_int"], fila["cobertura_int"] > u["cobertura_intereses_min"], f"cobertura intereses {fila['cobertura_int']:.1f} <= {u['cobertura_intereses_min']}")
    chk("Crec. ingresos", fila["cagr_ingresos"], fila["cagr_ingresos"] >= u["crecimiento_ingresos_min"], f"CAGR ingresos {fila['cagr_ingresos']:.1%} < {u['crecimiento_ingresos_min']:.0%}")
    chk("Capitalización EUR", fila["market_cap_eur"], fila["market_cap_eur"] > u["market_cap_eur_min"], f"capitalización {fila['market_cap_eur']/1e9:.2f}B EUR < {u['market_cap_eur_min']/1e9:.1f}B EUR")
    return not motivos, motivos


def aplicar_filtros(metricas: pd.DataFrame, u: dict = UMBRALES) -> pd.DataFrame:
    res = metricas.copy()
    if res.empty:
        res["pasa"] = pd.Series(dtype=bool)
        res["motivos_descarte"] = pd.Series(dtype=str)
        return res
    evaluaciones = [evaluar_fila(fila, u) for _, fila in res.iterrows()]
    res["pasa"] = [x[0] for x in evaluaciones]
    res["motivos_descarte"] = ["; ".join(x[1]) for x in evaluaciones]
    return res


def ranking_compuesto(df: pd.DataFrame) -> pd.DataFrame:
    """Ranking Magic Formula sobre el DataFrame recibido."""
    res = df.copy()
    res["rank_roic"] = res["roic"].rank(ascending=False, na_option="bottom")
    res["rank_ey"] = res["earnings_yield"].rank(ascending=False, na_option="bottom")
    res["puntuacion"] = res["rank_roic"] + res["rank_ey"]
    return res.sort_values("puntuacion")


def _clave_empresa(nombre: str) -> str:
    """Normaliza el nombre para detectar la misma empresa en varias bolsas."""
    texto = str(nombre or "").upper()
    for ruido in (" PLC", " AG", " NV", " N.V.", " SA", " S.A.", " INC", " INC.",
                  " CORPORATION", " CORP", " CORP.", " LTD", " LTD.", " LIMITED",
                  " CO.", " GROUP", " HOLDINGS", " COMPANY", ",", "."):
        texto = texto.replace(ruido, " ")
    return " ".join(texto.split())


def deduplicar_listings(df: pd.DataFrame) -> pd.DataFrame:
    """Colapsa cotizaciones duales de la misma empresa.

    GSK.L y GSKL.XC, o AUTO.L y AUTOL.XC, son la misma compañía en distinta
    bolsa: inflan el universo y aparecen dos veces entre las candidatas. Se
    conserva la fila con mayor capitalización en EUR (proxy del listado
    principal) y, a igualdad, el ticker más corto.
    """
    if df.empty or "nombre" not in df:
        return df
    res = df.copy()
    res["_clave"] = [
        f"{_clave_empresa(n)}|{p}" for n, p in zip(res["nombre"], res.get("pais", ""))
    ]
    res["_orden"] = list(zip(
        -res.get("market_cap_eur", pd.Series(0, index=res.index)).fillna(0),
        res["ticker"].astype(str).str.len(),
    ))
    # Las filas sin nombre (errores de descarga) no se agrupan entre sí.
    sin_nombre = res["_clave"].str.startswith("|")
    unicas = (
        res[~sin_nombre]
        .sort_values("_orden")
        .drop_duplicates("_clave", keep="first")
    )
    salida = pd.concat([unicas, res[sin_nombre]]).drop(columns=["_clave", "_orden"])
    return salida.loc[[i for i in df.index if i in salida.index]]


def incorporar_ranking_candidatos(evaluadas: pd.DataFrame) -> pd.DataFrame:
    """Añade ranking solo a las empresas que superan todos los filtros."""
    res = evaluadas.copy()
    for col in ("rank_roic", "rank_ey", "puntuacion"):
        res[col] = np.nan
    if not res.empty:
        candidatas = ranking_compuesto(res[res["pasa"]])
        res.loc[candidatas.index, ["rank_roic", "rank_ey", "puntuacion"]] = candidatas[
            ["rank_roic", "rank_ey", "puntuacion"]
        ]
    return res.sort_values(["pasa", "puntuacion"], ascending=[False, True], na_position="last")


def _serie_ordenada(df: pd.DataFrame, claves: Iterable[str]) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)
    for clave in claves:
        if clave not in df.index:
            continue
        serie = pd.to_numeric(df.loc[clave], errors="coerce").dropna()
        if serie.empty:
            continue
        fechas = pd.to_datetime(serie.index, errors="coerce")
        if fechas.notna().all():
            serie = serie.iloc[np.argsort(fechas.values)[::-1]]
        return serie
    return pd.Series(dtype=float)


def _valor_reciente(df: pd.DataFrame, claves: Iterable[str]):
    serie = _serie_ordenada(df, claves)
    return np.nan if serie.empty else float(serie.iloc[0])


def _valor_reciente_y_anterior(df: pd.DataFrame, claves: Iterable[str]):
    serie = _serie_ordenada(df, claves)
    if serie.empty:
        return np.nan, np.nan
    return float(serie.iloc[0]), float(serie.iloc[1]) if len(serie) > 1 else np.nan


def _extremos_historicos(df: pd.DataFrame, claves: Iterable[str]):
    """Devuelve (más antiguo, más reciente, años reales entre ambos)."""
    serie = _serie_ordenada(df, claves)
    if len(serie) < 2:
        return np.nan, np.nan, np.nan
    fechas = pd.to_datetime(serie.index, errors="coerce")
    if fechas.isna().any():
        return np.nan, np.nan, np.nan
    anios = (fechas[0] - fechas[-1]).days / 365.25
    if anios <= 0:
        return np.nan, np.nan, np.nan
    return float(serie.iloc[-1]), float(serie.iloc[0]), float(anios)


@lru_cache(maxsize=32)
def _tipo_cambio_a_eur(divisa: str) -> float:  # pragma: no cover - red
    """Número de EUR por unidad de la divisa indicada."""
    divisa = (divisa or "").upper()
    if divisa == "EUR":
        return 1.0
    if not divisa:
        return np.nan
    import yfinance as yf

    historico = yf.download(
        f"{divisa}EUR=X", period="5d", interval="1d", progress=False,
        auto_adjust=False, threads=False,
    )
    if historico is None or historico.empty or "Close" not in historico:
        return np.nan
    valores = pd.to_numeric(historico["Close"].squeeze(), errors="coerce").dropna()
    return np.nan if valores.empty else float(valores.iloc[-1])


def _tasa_impositiva(fin_ttm: pd.DataFrame) -> float:
    provision = _valor_reciente(fin_ttm, ["Tax Provision"])
    pre_tax = _valor_reciente(fin_ttm, ["Pretax Income"])
    tasa = _div(provision, pre_tax)
    if _es_na(tasa) or tasa < 0 or tasa > 0.50:
        return TASA_IMPOSITIVA_DEFECTO
    return float(tasa)


def descargar_fundamentales(tickers: list[str]) -> list[Fundamentales]:  # pragma: no cover - red
    """Descarga datos TTM, balance reciente e histórico anual.

    Conserva también los fallos para que el CSV muestre la cobertura real.
    """
    import yfinance as yf

    salida: list[Fundamentales] = []
    for tkr in dict.fromkeys(t.strip().upper() for t in tickers if t.strip()):
        try:
            ticker = yf.Ticker(tkr)
            info = ticker.info or {}
            fin_anual = ticker.income_stmt
            cf_anual = ticker.cashflow
            fin_ttm = ticker.ttm_income_stmt
            cf_ttm = ticker.ttm_cashflow
            bs = ticker.balance_sheet

            # Algunos mercados no publican el agregado TTM en Yahoo. En ese
            # caso se usa de forma coherente el último ejercicio anual para
            # todas las magnitudes de resultados, en vez de mezclar fuentes.
            fin_periodo = fin_ttm if fin_ttm is not None and not fin_ttm.empty else fin_anual
            cf_periodo = cf_ttm if cf_ttm is not None and not cf_ttm.empty else cf_anual

            ingresos = _valor_reciente(fin_periodo, ["Total Revenue", "Operating Revenue"])
            ebit = _valor_reciente(fin_periodo, ["EBIT", "Operating Income"])
            net_income = _valor_reciente(fin_periodo, ["Net Income", "Net Income Common Stockholders"])
            intereses = _valor_reciente(fin_periodo, ["Interest Expense", "Interest Expense Non Operating"])
            ebitda = _valor_reciente(fin_periodo, ["EBITDA", "Normalized EBITDA"])

            fcf = _valor_reciente(cf_periodo, ["Free Cash Flow"])

            deuda, deuda_inicio = _valor_reciente_y_anterior(bs, ["Total Debt"])
            caja, caja_inicio = _valor_reciente_y_anterior(
                bs,
                [
                    "Cash Cash Equivalents And Short Term Investments",
                    "Cash And Cash Equivalents",
                    "Cash",
                ],
            )
            equity, equity_inicio = _valor_reciente_y_anterior(
                bs, ["Stockholders Equity", "Total Equity Gross Minority Interest"]
            )
            ingresos_inicio, ingresos_fin, anios = _extremos_historicos(
                fin_anual, ["Total Revenue", "Operating Revenue"]
            )

            divisa_cotizacion = str(info.get("currency") or "").upper()
            divisa_financiera = str(info.get("financialCurrency") or "").upper()
            divisa_consistente = bool(
                divisa_cotizacion
                and divisa_financiera
                and divisa_cotizacion == divisa_financiera
            )
            market_cap = info.get("marketCap", np.nan)
            fx_eur = _tipo_cambio_a_eur(divisa_cotizacion)
            market_cap_eur = (
                float(market_cap) * fx_eur
                if not _es_na(market_cap) and not _es_na(fx_eur)
                else np.nan
            )

            salida.append(Fundamentales(
                ticker=tkr,
                nombre=info.get("longName", ""),
                sector=info.get("sector", "SIN_SECTOR"),
                pais=info.get("country", ""),
                divisa_cotizacion=divisa_cotizacion,
                divisa_financiera=divisa_financiera,
                divisa_consistente=divisa_consistente,
                market_cap=market_cap,
                market_cap_eur=market_cap_eur,
                net_income=net_income,
                ebit=ebit,
                ebitda=ebitda,
                ingresos=ingresos,
                ingresos_inicio_historico=ingresos_inicio,
                ingresos_fin_historico=ingresos_fin,
                anios_historico=anios,
                free_cash_flow=fcf,
                total_debt=deuda,
                cash=caja,
                equity=equity,
                total_debt_inicio=deuda_inicio,
                cash_inicio=caja_inicio,
                equity_inicio=equity_inicio,
                gasto_intereses=intereses,
                tasa_impositiva=_tasa_impositiva(fin_periodo),
            ))
            print(f"  ok  {tkr}")
        except Exception as exc:
            salida.append(Fundamentales(
                ticker=tkr,
                divisa_consistente=False,
                error_descarga=f"{type(exc).__name__}: {exc}",
            ))
            print(f"  ERR {tkr}: {exc}")
    return salida


def ejecutar(tickers: list[str], salida_csv: str = "candidatos.csv") -> pd.DataFrame:  # pragma: no cover - red
    print(DISCLAIMER)
    print(f"\nDescargando {len(tickers)} valores...")
    datos = descargar_fundamentales(tickers)
    metricas = calcular_metricas(datos)
    antes = len(metricas)
    metricas = deduplicar_listings(metricas)
    if len(metricas) < antes:
        print(f"\nDeduplicado: {antes - len(metricas)} cotizaciones duales colapsadas "
              f"({antes} -> {len(metricas)} empresas)")
    if "pais" in metricas:
        print("\nComposición del universo por región:")
        for region, n in metricas["region"].value_counts().items():
            print(f"  {region:>14s}: {n:5d}  ({n / len(metricas):5.1%})")
    evaluadas = aplicar_filtros(metricas)
    resultado = incorporar_ranking_candidatos(evaluadas)
    resultado.to_csv(salida_csv, index=False)

    candidatas = resultado[resultado["pasa"]]
    print(f"\n{'=' * 70}\nCANDIDATOS: {len(candidatas)} de {len(resultado)}\n{'=' * 70}")
    if not candidatas.empty:
        columnas = [
            "ticker", "nombre", "region", "sector", "per", "ev_ebit",
            "fcf_yield", "roic", "caja_neta_pct_mcap", "deuda_ebitda",
            "puntuacion",
        ]
        print(candidatas[columnas].to_string(index=False))
        dudosas = candidatas[~candidatas["roic_fiable"].astype(bool)]
        if not dudosas.empty:
            print("\nAVISO - ROIC sobre una base de capital muy pequeña "
                  "(métrica poco comparable, revisar a mano):")
            print("  " + ", ".join(dudosas["ticker"].astype(str)))
        mucha_caja = candidatas[candidatas["caja_neta_pct_mcap"] > 0.30]
        if not mucha_caja.empty:
            print("\nAVISO - más del 30% de la capitalización es caja neta. "
                  "Parte del 'descuento' es solo balance; comprobar si esa "
                  "liquidez se remunera o lleva años ociosa:")
            print("  " + ", ".join(mucha_caja["ticker"].astype(str)))
    else:
        print("Ninguna empresa supera todos los filtros.")
    print(f"\nDetalle y motivos de descarte: {salida_csv}")
    print(f"\n{DISCLAIMER}")
    return resultado


if __name__ == "__main__":  # pragma: no cover - CLI
    import sys

    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as archivo:
            universo = [linea.strip() for linea in archivo if linea.strip()]
    else:
        universo = ["SAN.MC", "ITX.MC", "TEF.MC", "SIE.DE", "ASML.AS"]
    ejecutar(universo)
