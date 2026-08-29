"""Dashboard interactivo del screener de value investing (Streamlit).

Separa a propósito dos costes muy distintos:

- La descarga de fundamentales (red, lenta) solo ocurre al pulsar el botón,
  y queda cacheada por lista de tickers.
- Recalcular métricas y aplicar filtros (CPU local, pandas) es barato y se
  repite en cada interacción con un slider, para que ajustar UMBRALES sea
  instantáneo sin volver a golpear Yahoo Finance.
"""

from __future__ import annotations

from dataclasses import asdict

import altair as alt
import pandas as pd
import streamlit as st

from screener_value import (
    DISCLAIMER,
    UMBRALES,
    aplicar_filtros,
    calcular_metricas,
    deduplicar_listings,
    descargar_fundamentales,
    incorporar_ranking_candidatos,
)
from universos_yfinance import (
    CATEGORIAS_TEMATICAS_ETF,
    SECTORES_NO_FINANCIEROS,
    TIPOS_ACTIVO,
    UNIVERSOS_YAHOO,
    catalogar_tickers,
    obtener_tickers_universo,
)

st.set_page_config(page_title="Screener de value investing", layout="wide")

# Azul secuencial (paso 500) de la paleta de referencia: comparar recuentos
# por región/sector es una lectura de magnitud, no de identidad, así que un
# solo hue ordenado de mayor a menor es la forma correcta, no un color por
# categoría.
AZUL = "#256abf"


@st.cache_data(show_spinner="Consultando el screener de Yahoo Finance...", ttl=86_400)
def _tickers_universo_cacheado(nombre: str, max_por_bucket: int) -> list[str]:
    return obtener_tickers_universo(nombre, max_por_bucket=max_por_bucket)


@st.cache_data(
    show_spinner="Descargando fundamentales de Yahoo Finance (puede tardar varios minutos)...",
    ttl=86_400,
)
def _fundamentales_cacheados(tickers: tuple[str, ...]) -> pd.DataFrame:
    datos = descargar_fundamentales(list(tickers))
    return pd.DataFrame([asdict(d) for d in datos])


@st.cache_data(show_spinner="Consultando ETF/fondos en Yahoo Finance...", ttl=86_400)
def _catalogo_cacheado(
    tipo_activo: str, universo: str | None, categorias: tuple[str, ...], max_por_bucket: int,
) -> pd.DataFrame:
    return catalogar_tickers(
        tipo_activo,
        regiones_o_universo=universo,
        categorias=list(categorias),
        max_por_bucket=max_por_bucket,
    )


def _bar_conteo(df: pd.DataFrame, columna: str, etiqueta: str) -> alt.Chart:
    conteo = (
        df[columna].fillna("(sin dato)").value_counts()
        .rename_axis(columna).reset_index(name="empresas")
    )
    return (
        alt.Chart(conteo)
        .mark_bar(color=AZUL, cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
        .encode(
            x=alt.X("empresas:Q", title="Empresas"),
            y=alt.Y(f"{columna}:N", sort="-x", title=None),
            tooltip=[
                alt.Tooltip(f"{columna}:N", title=etiqueta),
                alt.Tooltip("empresas:Q", title="Empresas"),
            ],
        )
        .properties(height=max(28 * len(conteo), 60))
    )


st.title("Screener de value investing")
st.warning(DISCLAIMER, icon="⚠️")

# --- Sidebar: origen del universo -------------------------------------------
st.sidebar.header("Universo")
modo = st.sidebar.radio("Origen de los tickers", ["Lista manual", "Universo Yahoo"])

with st.sidebar.expander("🔎 Descubrir tickers por categoría"):
    st.caption(
        "Busca tickers por zona económica y categoría, y añádelos a la lista "
        "manual de arriba. Las ACCIONES sí se evalúan con las métricas de "
        "valor al pulsar \"Descargar y calcular\"; los ETF y fondos NO -"
        "PER/ROIC/EV-EBIT no existen a nivel de fondo-, así que para esos dos "
        "este buscador sirve solo para reunir tickers, no para analizarlos."
    )
    tipo_descubrir = st.radio(
        "Tipo de activo", ["accion", "etf", "fondo"], horizontal=True, key="tipo_descubrir",
    )

    if TIPOS_ACTIVO[tipo_descubrir]["soporta_region"]:
        universo_descubrir = st.selectbox("Región", sorted(UNIVERSOS_YAHOO), key="universo_descubrir")
        if tipo_descubrir == "accion":
            opciones_categoria = SECTORES_NO_FINANCIEROS
            etiqueta_categoria = "Sector"
            ayuda_categoria = None
        else:
            opciones_categoria = sorted(CATEGORIAS_TEMATICAS_ETF)
            etiqueta_categoria = "Categoría temática"
            ayuda_categoria = (
                "Taxonomía Morningstar, no GICS: sectores como Industrials o "
                "Consumer Cyclical no tienen categoría ETF equivalente (ver README)."
            )
        categorias_sel = st.multiselect(
            etiqueta_categoria, opciones_categoria,
            key=f"categorias_descubrir_{tipo_descubrir}", help=ayuda_categoria,
        )
    else:
        st.caption(
            "Los fondos no tienen región en Yahoo Finance, solo categoría. Sin "
            "lista curada -escribe un valor Morningstar real, p. ej. "
            "\"Large Growth\" o \"High Yield Bond\"; es best-effort."
        )
        universo_descubrir = None
        categoria_libre = st.text_input("Categoría Morningstar", key="categoria_fondo_descubrir")
        categorias_sel = [categoria_libre.strip()] if categoria_libre.strip() else []

    max_catalogo = st.slider("Máx. resultados por categoría", 5, 100, 15, step=5, key="max_catalogo")

    if st.button("Buscar"):
        if not categorias_sel:
            st.warning("Elige o escribe al menos una categoría.")
        else:
            try:
                st.session_state["catalogo_resultado"] = _catalogo_cacheado(
                    tipo_descubrir, universo_descubrir, tuple(categorias_sel), max_catalogo,
                )
            except Exception as exc:
                st.error(f"Yahoo Finance rechazó la consulta: {exc}")

    catalogo = st.session_state.get("catalogo_resultado")
    if catalogo is not None:
        if catalogo.empty:
            st.caption("Sin resultados para esa combinación.")
        else:
            st.dataframe(catalogo, hide_index=True, width="stretch")
            if st.button(f"➕ Añadir {len(catalogo)} tickers a la lista manual"):
                existentes = [
                    t.strip().upper() for t in
                    st.session_state.get("tickers_manual", "").replace(",", "\n").splitlines()
                    if t.strip()
                ]
                nuevos = [t for t in catalogo["ticker"] if t not in existentes]
                st.session_state["tickers_manual"] = "\n".join(existentes + nuevos)
                st.success(f"{len(nuevos)} tickers nuevos añadidos a la lista manual.")

if modo == "Lista manual":
    st.session_state.setdefault("tickers_manual", "SAN.MC, ITX.MC, TEF.MC, SIE.DE, ASML.AS")
    texto = st.sidebar.text_area(
        "Tickers (separados por coma o salto de línea)",
        height=120,
        key="tickers_manual",
    )
    tickers_pendientes = [
        t.strip().upper() for t in texto.replace(",", "\n").splitlines() if t.strip()
    ]
    resolver_tickers = lambda: tickers_pendientes  # noqa: E731
else:
    universo_nombre = st.sidebar.selectbox("Universo predefinido", sorted(UNIVERSOS_YAHOO))
    max_por_bucket = st.sidebar.slider(
        "Máx. resultados por (región × sector)", 5, 250, 25, step=5,
        help="Cuota de CADA consulta región×sector, no el total del universo.",
    )
    resolver_tickers = lambda: _tickers_universo_cacheado(universo_nombre, max_por_bucket)  # noqa: E731

if st.sidebar.button("🔄 Descargar y calcular", type="primary"):
    tickers = resolver_tickers()
    if not tickers:
        st.sidebar.error("No se ha resuelto ningún ticker con esa configuración.")
    else:
        st.session_state["raw_df"] = _fundamentales_cacheados(tuple(tickers))
        st.session_state["n_tickers"] = len(tickers)

st.sidebar.divider()

if "raw_df" not in st.session_state:
    st.info(
        "Elige un universo en la barra lateral y pulsa **Descargar y calcular** "
        "para empezar. La descarga es la única operación que va a red — mover "
        "los sliders después no vuelve a consultar Yahoo Finance."
    )
    st.stop()

raw_df = st.session_state["raw_df"]
st.caption(f"Fundamentales cargados para {st.session_state['n_tickers']} tickers solicitados.")

# --- Sidebar: umbrales (UMBRALES), editables ---------------------------------
st.sidebar.header("Umbrales de filtro")
if st.sidebar.button("Restaurar valores por defecto"):
    for clave in list(st.session_state):
        if clave.startswith("u_"):
            del st.session_state[clave]
    st.rerun()

u = dict(UMBRALES)
u["per_max"] = st.sidebar.slider(
    "PER máximo", 5.0, 40.0, UMBRALES["per_max"], 0.5, key="u_per_max")
u["per_bajo_mediana_sector"] = st.sidebar.checkbox(
    "Exigir PER < mediana de su sector/región", UMBRALES["per_bajo_mediana_sector"],
    key="u_per_bajo_mediana_sector")
u["min_empresas_sector"] = st.sidebar.slider(
    "Mín. empresas comparables para la mediana regional", 2, 15,
    UMBRALES["min_empresas_sector"], 1, key="u_min_empresas_sector")
u["ev_ebit_max"] = st.sidebar.slider(
    "EV/EBIT máximo", 2.0, 30.0, UMBRALES["ev_ebit_max"], 0.5, key="u_ev_ebit_max")
u["fcf_yield_min"] = st.sidebar.slider(
    "FCF yield mínimo (%)", 0.0, 20.0, UMBRALES["fcf_yield_min"] * 100, 0.5,
    key="u_fcf_yield_min") / 100
u["roic_min"] = st.sidebar.slider(
    "ROIC mínimo (%)", 0.0, 40.0, UMBRALES["roic_min"] * 100, 0.5,
    key="u_roic_min") / 100
u["margen_op_min"] = st.sidebar.slider(
    "Margen operativo mínimo (%)", -10.0, 30.0, UMBRALES["margen_op_min"] * 100, 0.5,
    key="u_margen_op_min") / 100
u["deuda_ebitda_max"] = st.sidebar.slider(
    "Deuda neta/EBITDA máximo", 0.0, 6.0, UMBRALES["deuda_ebitda_max"], 0.1,
    key="u_deuda_ebitda_max")
u["cobertura_intereses_min"] = st.sidebar.slider(
    "Cobertura de intereses mínima", 0.0, 20.0, UMBRALES["cobertura_intereses_min"], 0.5,
    key="u_cobertura_intereses_min")
u["crecimiento_ingresos_min"] = st.sidebar.slider(
    "Crecimiento de ingresos mínimo, CAGR (%)", -20.0, 30.0,
    UMBRALES["crecimiento_ingresos_min"] * 100, 0.5,
    key="u_crecimiento_ingresos_min") / 100
u["market_cap_eur_min"] = st.sidebar.slider(
    "Capitalización mínima (miles de M EUR)", 0.0, 50.0,
    UMBRALES["market_cap_eur_min"] / 1e9, 0.5,
    key="u_market_cap_eur_min") * 1e9

# --- Recalcular métricas y filtros con los umbrales actuales -----------------
metricas = calcular_metricas(raw_df, u=u)
metricas = deduplicar_listings(metricas)
evaluadas = aplicar_filtros(metricas, u)
resultado = incorporar_ranking_candidatos(evaluadas)

total = len(resultado)
candidatas = resultado[resultado["pasa"]]
n_candidatas = len(candidatas)

# --- KPIs ---------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Empresas evaluadas", total)
col2.metric(
    "Candidatas", n_candidatas,
    f"{n_candidatas / total:.1%}" if total else None,
)
col3.metric(
    "ROIC no fiable entre candidatas",
    int((~candidatas["roic_fiable"].astype(bool)).sum()) if n_candidatas else 0,
    help="Capital invertido tan pequeño que el ROIC deja de ser comparable.",
)
col4.metric(
    "Caja neta > 30% cap. entre candidatas",
    int((candidatas["caja_neta_pct_mcap"] > 0.30).sum()) if n_candidatas else 0,
    help="Parte relevante del 'descuento' puede ser solo tesorería ociosa.",
)

# --- Composición del universo -------------------------------------------
st.subheader("Composición del universo evaluado")
col_region, col_sector = st.columns(2)
with col_region:
    st.altair_chart(_bar_conteo(metricas, "region", "Región"), width="stretch")
with col_sector:
    st.altair_chart(_bar_conteo(metricas, "sector", "Sector"), width="stretch")

# --- Tabla filtrable ------------------------------------------------------
st.subheader("Resultados")
f1, f2, f3 = st.columns(3)
solo_candidatas = f1.checkbox("Mostrar solo candidatas", value=True)
regiones_sel = f2.multiselect("Región", sorted(resultado["region"].dropna().unique()))
sectores_sel = f3.multiselect("Sector", sorted(resultado["sector"].dropna().unique()))

vista = resultado.copy()
if solo_candidatas:
    vista = vista[vista["pasa"]]
if regiones_sel:
    vista = vista[vista["region"].isin(regiones_sel)]
if sectores_sel:
    vista = vista[vista["sector"].isin(sectores_sel)]

columnas_tabla = [
    "ticker", "nombre", "region", "sector", "per", "per_mediana_sector",
    "ev_ebit", "fcf_yield", "roic", "roic_fiable", "caja_neta_pct_mcap",
    "deuda_ebitda", "cobertura_int", "cagr_ingresos", "market_cap_eur",
    "puntuacion", "pasa", "motivos_descarte",
]
st.dataframe(
    vista[columnas_tabla].sort_values(["pasa", "puntuacion"], ascending=[False, True]),
    width="stretch",
    hide_index=True,
    column_config={
        "per": st.column_config.NumberColumn(format="%.1f"),
        "per_mediana_sector": st.column_config.NumberColumn("mediana PER", format="%.1f"),
        "ev_ebit": st.column_config.NumberColumn("EV/EBIT", format="%.1f"),
        "fcf_yield": st.column_config.NumberColumn("FCF yield", format="percent"),
        "roic": st.column_config.NumberColumn(format="percent"),
        "caja_neta_pct_mcap": st.column_config.NumberColumn("caja neta % cap.", format="percent"),
        "deuda_ebitda": st.column_config.NumberColumn("deuda/EBITDA", format="%.1f"),
        "cobertura_int": st.column_config.NumberColumn("cobertura int.", format="%.1f"),
        "cagr_ingresos": st.column_config.NumberColumn("CAGR ingresos", format="percent"),
        "market_cap_eur": st.column_config.NumberColumn("cap. (EUR)", format="compact"),
        "motivos_descarte": st.column_config.TextColumn("motivos de descarte", width="large"),
    },
)

st.download_button(
    "Descargar vista actual (CSV)",
    vista.to_csv(index=False).encode("utf-8"),
    file_name="candidatos_filtrados.csv",
    mime="text/csv",
)

st.divider()
st.caption(DISCLAIMER)
