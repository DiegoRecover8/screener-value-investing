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
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as components_html

from journal import (
    RUTA_JOURNAL_DEFECTO,
    extraer_ultima_ejecucion,
    leer_ejecuciones,
    leer_journal,
    snapshot_ids_oficiales_efectivos,
)
from prompt_llm import generar_prompt_interpretacion
from screener_value import (
    UMBRALES,
    aplicar_filtros,
    calcular_metricas,
    deduplicar_listings,
    descargar_fundamentales,
    incorporar_ranking_candidatos,
)
from seguimiento import RUTA_SEGUIMIENTO_DEFECTO, leer_seguimiento
from tradingview import (
    html_widget_tradingview,
    ticker_a_tradingview,
    tickers_candidatos_para_grafico,
)
from universos_yfinance import (
    CATEGORIAS_TEMATICAS_ETF,
    SECTORES_NO_FINANCIEROS,
    TIPOS_ACTIVO,
    UNIVERSOS_YAHOO,
    catalogar_tickers,
    obtener_tickers_universo,
)

RUTA_UNIVERSO_TXT = Path("universo.txt")

# Verificadas contra yfinance antes de incluirlas -no es una lista curada
# por Yahoo (FundQuery no valida categoryname), son ejemplos que en la
# práctica devuelven resultados reales. "World Allocation" se probó y no
# devolvió nada, por eso no está.
CATEGORIAS_FONDO_EJEMPLO = [
    "Large Blend", "Large Growth", "Large Value", "Foreign Large Blend",
    "High Yield Bond", "Intermediate-Term Bond", "Small Blend",
]

st.set_page_config(
    page_title="Value Investing Research Lab",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.session_state.setdefault("idioma", "en")


def _t(english: str, spanish: str) -> str:
    """Return UI copy in the language selected for this browser session."""
    return english if st.session_state["idioma"] == "en" else spanish


DISCLAIMER_UI = {
    "en": (
        "Research and educational use only. This screen is not financial advice "
        "and every candidate requires primary-source review."
    ),
    "es": (
        "Uso exclusivamente investigador y educativo. Esta pantalla no es "
        "asesoramiento financiero y cada candidata requiere revisar fuentes primarias."
    ),
}

st.markdown(
    """
    <style>
      .stApp { background: linear-gradient(180deg, #f7f9fc 0, #ffffff 22rem); }
      [data-testid="stSidebar"] { border-right: 1px solid #e6ebf2; }
      .research-hero {
        padding: 1.5rem 1.65rem; border: 1px solid #dce5f2; border-radius: 18px;
        background: linear-gradient(125deg, #0c274b 0%, #1559a6 68%, #2a78cc 100%);
        color: white; box-shadow: 0 12px 34px rgba(12, 39, 75, .12);
        margin-bottom: 1rem;
      }
      .research-hero h1 { margin: 0; color: white; font-size: 2.15rem; }
      .research-hero p { margin: .45rem 0 0; color: #dcecff; max-width: 780px; }
      .eyebrow { text-transform: uppercase; letter-spacing: .12em; font-size: .73rem;
                 font-weight: 700; color: #9dcbff; }
      [data-testid="stMetric"] {
        background: rgba(255,255,255,.92); border: 1px solid #e4eaf2;
        padding: .9rem 1rem; border-radius: 14px;
      }
      div[data-testid="stDataFrame"] { border: 1px solid #e4eaf2; border-radius: 12px; }
      .block-container { padding-top: 1.5rem; padding-bottom: 3rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Azul secuencial (paso 500) de la paleta de referencia: comparar recuentos
# por región/sector es una lectura de magnitud, no de identidad, así que un
# solo hue ordenado de mayor a menor es la forma correcta, no un color por
# categoría.
AZUL = "#256abf"


@st.cache_data(show_spinner=False, ttl=86_400)
def _tickers_universo_cacheado(nombre: str, max_por_bucket: int) -> list[str]:
    return obtener_tickers_universo(nombre, max_por_bucket=max_por_bucket)


@st.cache_data(show_spinner=False, ttl=86_400)
def _fundamentales_cacheados(tickers: tuple[str, ...]) -> pd.DataFrame:
    datos = descargar_fundamentales(list(tickers))
    return pd.DataFrame([asdict(d) for d in datos])


@st.cache_data(show_spinner=False, ttl=86_400)
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
        df[columna].fillna(_t("(missing)", "(sin dato)")).value_counts()
        .rename_axis(columna).reset_index(name="empresas")
    )
    return (
        alt.Chart(conteo)
        .mark_bar(color=AZUL, cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
        .encode(
            x=alt.X("empresas:Q", title=_t("Companies", "Empresas")),
            y=alt.Y(f"{columna}:N", sort="-x", title=None),
            tooltip=[
                alt.Tooltip(f"{columna}:N", title=etiqueta),
                alt.Tooltip("empresas:Q", title=_t("Companies", "Empresas")),
            ],
        )
        .properties(height=max(28 * len(conteo), 60))
    )


def _grafico_tradingview(ticker: str, altura: int = 520) -> None:
    """Embebe el widget gratuito de TradingView para `ticker`.

    Vive en Streamlit y no en el Artifact de histórico a propósito: la CSP
    de los Artifacts de Claude bloquea cualquier script externo salvo
    Google Fonts, así que TradingView no puede cargar ahí. Aquí, al ser una
    página normal servida localmente, no hay esa restricción.

    El símbolo es un mapeo best-effort (ver `tradingview.py`) -si no
    resuelve a la empresa correcta, el propio widget deja buscarla a mano
    haciendo clic en el nombre del símbolo.
    """
    simbolo = ticker_a_tradingview(ticker)
    st.caption(_t(
        f"Candidate `{ticker}` · TradingView symbol `{simbolo}` (best-effort mapping). "
        "The widget allows a manual symbol change if the listing is unavailable.",
        f"Candidata `{ticker}` · símbolo de TradingView `{simbolo}` (mapeo aproximado). "
        "El widget permite cambiarlo manualmente si la cotización no está disponible.",
    ))
    components_html(
        html_widget_tradingview(ticker, locale=st.session_state["idioma"], altura=altura),
        height=altura + 8,
        scrolling=False,
    )


def _vista_historico() -> None:
    """Muestra lo que la Action semanal ya calculó, sin red ni recálculo.

    Lee directamente journal_candidatos.csv y seguimiento_candidatas.csv
    -los mismos archivos que .github/workflows/screener_semanal.yml
    commitea cada semana- para que el dashboard deje de ser solo una
    herramienta de análisis en vivo desconectada de esa automatización.
    """
    st.subheader(_t("📅 Audited run history", "📅 Histórico auditado de ejecuciones"))
    st.caption(_t(
        "Read-only view of the snapshots already calculated and committed by "
        "GitHub Actions. It does not contact Yahoo Finance.",
        "Vista de solo lectura de los snapshots ya calculados y guardados por "
        "GitHub Actions. No consulta Yahoo Finance.",
    ))

    journal = leer_journal()
    if journal.empty:
        st.info(_t(
            f"`{RUTA_JOURNAL_DEFECTO}` does not exist in this directory yet. "
            "Run `python ejecutar_semanal.py --universo-activo "
            "journal_candidatos.csv` or start the workflow from GitHub Actions.",
            f"`{RUTA_JOURNAL_DEFECTO}` todavía no existe en esta carpeta. "
            "Ejecuta `python ejecutar_semanal.py --universo-activo "
            "journal_candidatos.csv` o inicia el workflow desde GitHub Actions.",
        ))
        return

    ultima_ejecucion = extraer_ultima_ejecucion(journal)
    ultima_fecha = ultima_ejecucion["fecha_ejecucion"].iloc[0]
    ultima_semana = ultima_ejecucion["semana_iso"].iloc[0]
    candidatas_ultima_ejecucion = ultima_ejecucion[
        ultima_ejecucion["pasa"].astype(bool)
    ]

    col1, col2, col3 = st.columns(3)
    col1.metric(_t("Latest run", "Última ejecución"), ultima_fecha.strftime("%Y-%m-%d %H:%M UTC"))
    col2.metric(_t("ISO week", "Semana ISO"), ultima_semana)
    col3.metric(_t("Candidates", "Candidatas"), len(candidatas_ultima_ejecucion))
    snapshot_actual = str(ultima_ejecucion["snapshot_id"].iloc[0])
    ejecuciones = leer_ejecuciones()
    control_actual = ejecuciones[ejecuciones.get("snapshot_id") == snapshot_actual]
    if control_actual.empty:
        st.caption(_t(
            f"Snapshot `{snapshot_actual}` · predates the control table",
            f"Snapshot `{snapshot_actual}` · anterior a la tabla de control",
        ))
    else:
        control_actual = control_actual.iloc[0]
        ids_efectivos = snapshot_ids_oficiales_efectivos(journal, ejecuciones)
        if snapshot_actual in ids_efectivos:
            estado = _t("effective official", "oficial efectivo")
        elif bool(control_actual["oficial"]):
            estado = _t(
                "official, superseded by a later revision",
                "oficial sustituido por una revisión posterior",
            )
        else:
            estado = _t("non-official test", "prueba no oficial")
        st.caption(
            f"Snapshot: `{snapshot_actual}` · {estado} · "
            f"{_t('source', 'origen')}: {control_actual['origen']} · "
            f"{_t('revision', 'revisión')} {int(control_actual['revision'])}"
        )

    columnas_journal = [
        "ticker", "nombre", "sector", "region", "per", "ev_ebit", "roic",
        "deuda_ebitda", "puntuacion", "pasa", "motivos_descarte",
    ]

    st.markdown(_t("#### Latest-run candidates", "#### Candidatas de la última ejecución"))
    if candidatas_ultima_ejecucion.empty:
        st.caption(_t("No candidates in the latest run.", "Ninguna candidata en la última ejecución."))
    else:
        st.dataframe(
            candidatas_ultima_ejecucion[columnas_journal].sort_values("puntuacion"),
            hide_index=True, width="stretch",
        )

    st.markdown(_t("#### Complete research journal", "#### Journal de investigación completo"))
    semanas = sorted(journal["semana_iso"].unique(), reverse=True)
    semanas_sel = st.multiselect(_t("Filter by week", "Filtrar por semana"), semanas)
    vista_journal = journal[journal["semana_iso"].isin(semanas_sel)] if semanas_sel else journal
    st.dataframe(
        vista_journal[["snapshot_id", "fecha_ejecucion", "semana_iso"] + columnas_journal]
        .sort_values(["fecha_ejecucion", "puntuacion"], ascending=[False, True]),
        hide_index=True, width="stretch",
    )
    st.download_button(
        _t("Download complete journal (CSV)", "Descargar journal completo (CSV)"),
        journal.to_csv(index=False).encode("utf-8"),
        file_name=RUTA_JOURNAL_DEFECTO, mime="text/csv",
    )

    st.markdown(_t("#### 📈 Candidate chart", "#### 📈 Gráfico de una candidata"))
    tickers_grafico = tickers_candidatos_para_grafico(ultima_ejecucion)
    if not tickers_grafico:
        st.caption(_t(
            "The latest run has no candidates to chart.",
            "La última ejecución no tiene candidatas que graficar.",
        ))
    else:
        ticker_grafico = st.selectbox(
            _t("Select a latest-run candidate", "Selecciona una candidata de la última ejecución"),
            tickers_grafico,
            key="tv_ticker_historico",
        )
        _grafico_tradingview(ticker_grafico)

    st.markdown(_t("#### Tracked signal performance", "#### Rendimiento de señales seguidas"))
    seguimiento = leer_seguimiento()
    if seguimiento.empty:
        st.caption(_t(
            f"`{RUTA_SEGUIMIENTO_DEFECTO}` does not exist yet; the official workflow "
            "generates it together with the journal.",
            f"`{RUTA_SEGUIMIENTO_DEFECTO}` todavía no existe; el workflow oficial "
            "lo genera junto al journal.",
        ))
        return

    claves_senal = ["ticker", "fecha_entrada"]
    ultimo_snapshot = (
        seguimiento.sort_values("fecha_calculo")
        .groupby(claves_senal, as_index=False, dropna=False).tail(1)
    )
    st.dataframe(
        ultimo_snapshot[[
            "ticker", "nombre", "fecha_entrada", "precio_entrada", "precio_actual",
            "retorno_total", "max_drawdown", "dias_en_seguimiento",
        ]],
        hide_index=True, width="stretch",
        column_config={
            "retorno_total": st.column_config.NumberColumn(_t("total return", "retorno total"), format="percent"),
            "max_drawdown": st.column_config.NumberColumn(_t("max drawdown", "drawdown máx."), format="percent"),
        },
    )
    st.download_button(
        _t("Download complete tracking data (CSV)", "Descargar seguimiento completo (CSV)"),
        seguimiento.to_csv(index=False).encode("utf-8"),
        file_name=RUTA_SEGUIMIENTO_DEFECTO, mime="text/csv",
    )

    seguimiento = seguimiento.copy()
    seguimiento["senal"] = (
        seguimiento["ticker"].astype(str) + _t(" · entry ", " · entrada ")
        + seguimiento["fecha_entrada"].dt.strftime("%Y-%m-%d")
    )
    senales_con_historia = seguimiento.groupby("senal").size()
    senales_con_historia = senales_con_historia[senales_con_historia > 1]
    if not senales_con_historia.empty:
        senal_evolucion = st.selectbox(
            _t("View a signal's weekly history", "Ver evolución semanal de una señal"),
            sorted(senales_con_historia.index),
        )
        serie = seguimiento[seguimiento["senal"] == senal_evolucion]
        grafico = (
            alt.Chart(serie)
            .mark_line(point=True, color=AZUL)
            .encode(
                x=alt.X("fecha_calculo:T", title=_t("Calculation date", "Fecha de cálculo")),
                y=alt.Y("retorno_total:Q", title=_t("Total return", "Retorno total"), axis=alt.Axis(format="%")),
                tooltip=[
                    alt.Tooltip("fecha_calculo:T", title=_t("Date", "Fecha")),
                    alt.Tooltip("retorno_total:Q", title=_t("Return", "Retorno"), format=".1%"),
                    alt.Tooltip("max_drawdown:Q", title=_t("Max drawdown", "Drawdown máx."), format=".1%"),
                ],
            )
        )
        st.altair_chart(grafico, width="stretch")


st.sidebar.caption("Language / Idioma")
idioma_en, idioma_es = st.sidebar.columns(2)
if idioma_en.button("🇬🇧 EN", use_container_width=True, type="primary" if st.session_state["idioma"] == "en" else "secondary"):
    if st.session_state["idioma"] != "en":
        st.session_state["idioma"] = "en"
        st.rerun()
if idioma_es.button("🇪🇸 ES", use_container_width=True, type="primary" if st.session_state["idioma"] == "es" else "secondary"):
    if st.session_state["idioma"] != "es":
        st.session_state["idioma"] = "es"
        st.rerun()

st.markdown(
    f"""
    <section class="research-hero">
      <div class="eyebrow">{_t('Reproducible equity research', 'Investigación bursátil reproducible')}</div>
      <h1>{_t('Value Investing Research Lab', 'Laboratorio de Value Investing')}</h1>
      <p>{_t(
          'Explore versioned universes, inspect every rejection and turn quantitative candidates into a primary-source research queue.',
          'Explora universos versionados, inspecciona cada descarte y convierte las candidatas cuantitativas en una cola de investigación con fuentes primarias.'
      )}</p>
    </section>
    """,
    unsafe_allow_html=True,
)
st.warning(DISCLAIMER_UI[st.session_state["idioma"]], icon="⚠️")

VISTAS = ["live", "history"]
ETIQUETAS_VISTA = {
    "live": _t("🔍 Live analysis", "🔍 Análisis en vivo"),
    "history": _t("📅 Audited history", "📅 Histórico auditado"),
}
vista = st.sidebar.radio(
    _t("Workspace", "Espacio de trabajo"),
    VISTAS,
    format_func=ETIQUETAS_VISTA.get,
)
st.sidebar.divider()

if vista == "history":
    _vista_historico()
    st.stop()

# --- Sidebar: origen del universo -------------------------------------------
st.sidebar.header(_t("Universe", "Universo"))
MODOS = ["manual", "yahoo", "official"]
ETIQUETAS_MODO = {
    "manual": _t("Manual list", "Lista manual"),
    "yahoo": _t("Yahoo discovery universe", "Universo de descubrimiento Yahoo"),
    "official": _t("Official active universe", "Universo oficial activo"),
}
modo = st.sidebar.radio(
    _t("Ticker source", "Origen de los tickers"),
    MODOS,
    format_func=ETIQUETAS_MODO.get,
)

with st.sidebar.expander(_t("🔎 Discover by category", "🔎 Descubrir por categoría")):
    st.caption(_t(
        "Search by region and category, then add symbols to the manual list. "
        "Equities can be evaluated; ETFs and funds are discovery-only because "
        "company-level P/E, ROIC and EV/EBIT are not defined for a fund.",
        "Busca por región y categoría y añade símbolos a la lista manual. Las "
        "acciones se pueden evaluar; ETF y fondos son solo para descubrimiento "
        "porque PER, ROIC y EV/EBIT no se definen a nivel de fondo.",
    ))
    TIPOS_UI = ["accion", "etf", "fondo"]
    ETIQUETAS_TIPO = {
        "accion": _t("Equity", "Acción"),
        "etf": "ETF",
        "fondo": _t("Fund", "Fondo"),
    }
    tipo_descubrir = st.radio(
        _t("Asset type", "Tipo de activo"),
        TIPOS_UI,
        format_func=ETIQUETAS_TIPO.get,
        horizontal=True,
        key="tipo_descubrir",
    )

    if TIPOS_ACTIVO[tipo_descubrir]["soporta_region"]:
        universo_descubrir = st.selectbox(_t("Region", "Región"), sorted(UNIVERSOS_YAHOO), key="universo_descubrir")
        if tipo_descubrir == "accion":
            opciones_categoria = SECTORES_NO_FINANCIEROS
            etiqueta_categoria = _t("Sector", "Sector")
            ayuda_categoria = None
        else:
            opciones_categoria = sorted(CATEGORIAS_TEMATICAS_ETF)
            etiqueta_categoria = _t("Theme", "Categoría temática")
            ayuda_categoria = _t(
                "Morningstar taxonomy, not GICS; some equity sectors have no direct ETF category.",
                "Taxonomía Morningstar, no GICS; algunos sectores no tienen categoría ETF equivalente.",
            )
        categorias_sel = st.multiselect(
            etiqueta_categoria, opciones_categoria,
            key=f"categorias_descubrir_{tipo_descubrir}", help=ayuda_categoria,
        )
    else:
        st.caption(_t(
            "Yahoo funds expose a Morningstar category but no region. The examples "
            "below were tested; custom category names remain best effort.",
            "Los fondos de Yahoo exponen categoría Morningstar pero no región. Los "
            "ejemplos se han probado; las categorías personalizadas son aproximadas.",
        ))
        universo_descubrir = None
        opcion_fondo = st.selectbox(
            _t("Morningstar category", "Categoría Morningstar"),
            CATEGORIAS_FONDO_EJEMPLO + ["__other__"],
            format_func={
                **{item: item for item in CATEGORIAS_FONDO_EJEMPLO},
                "__other__": _t("Other (type it)", "Otra (escribir)"),
            }.get,
            key="categoria_fondo_descubrir",
        )
        if opcion_fondo == "__other__":
            categoria_libre = st.text_input(
                _t("Exact Morningstar category", "Categoría Morningstar exacta"),
                key="categoria_fondo_libre",
                help=_t('For example, "Foreign Large Growth".', 'Por ejemplo, "Foreign Large Growth".'),
            )
            categorias_sel = [categoria_libre.strip()] if categoria_libre.strip() else []
        else:
            categorias_sel = [opcion_fondo]

    max_catalogo = st.slider(_t("Max results per category", "Máx. resultados por categoría"), 5, 100, 15, step=5, key="max_catalogo")

    if st.button(_t("Search", "Buscar")):
        if not categorias_sel:
            st.warning(_t("Select or enter at least one category.", "Elige o escribe al menos una categoría."))
        else:
            try:
                with st.spinner(_t("Querying Yahoo Finance…", "Consultando Yahoo Finance…")):
                    st.session_state["catalogo_resultado"] = _catalogo_cacheado(
                        tipo_descubrir, universo_descubrir, tuple(categorias_sel), max_catalogo,
                    )
            except Exception as exc:
                st.error(_t(f"Yahoo Finance rejected the query: {exc}", f"Yahoo Finance rechazó la consulta: {exc}"))

    catalogo = st.session_state.get("catalogo_resultado")
    if catalogo is not None:
        if catalogo.empty:
            st.caption(_t("No results for this combination.", "Sin resultados para esa combinación."))
        else:
            st.dataframe(catalogo, hide_index=True, width="stretch")
            if st.button(_t(f"➕ Add {len(catalogo)} tickers to manual list", f"➕ Añadir {len(catalogo)} tickers a la lista manual")):
                existentes = [
                    t.strip().upper() for t in
                    st.session_state.get("tickers_manual", "").replace(",", "\n").splitlines()
                    if t.strip()
                ]
                nuevos = [t for t in catalogo["ticker"] if t not in existentes]
                st.session_state["tickers_manual"] = "\n".join(existentes + nuevos)
                st.success(_t(f"Added {len(nuevos)} new tickers.", f"Se han añadido {len(nuevos)} tickers nuevos."))

if modo == "manual":
    st.session_state.setdefault("tickers_manual", "SAN.MC, ITX.MC, TEF.MC, SIE.DE, ASML.AS")
    texto = st.sidebar.text_area(
        _t("Tickers (comma or newline separated)", "Tickers (separados por coma o salto de línea)"),
        height=120,
        key="tickers_manual",
    )
    tickers_pendientes = [
        t.strip().upper() for t in texto.replace(",", "\n").splitlines() if t.strip()
    ]
    resolver_tickers = lambda: tickers_pendientes  # noqa: E731
elif modo == "yahoo":
    universo_nombre = st.sidebar.selectbox(_t("Discovery profile", "Perfil de descubrimiento"), sorted(UNIVERSOS_YAHOO))
    max_por_bucket = st.sidebar.slider(
        _t("Max results per region × sector", "Máx. resultados por región × sector"),
        5, 250, 25, step=5,
        help=_t("Quota for each query, not the universe total.", "Cuota de cada consulta, no el total del universo."),
    )
    resolver_tickers = lambda: _tickers_universo_cacheado(universo_nombre, max_por_bucket)  # noqa: E731
else:
    if RUTA_UNIVERSO_TXT.exists():
        tickers_universo_txt = [
            t.strip().upper() for t in RUTA_UNIVERSO_TXT.read_text(encoding="utf-8").splitlines()
            if t.strip()
        ]
        st.sidebar.caption(
            _t(
                f"{len(tickers_universo_txt)} tickers · transitional mirror of the active "
                "versioned universe used by GitHub Actions.",
                f"{len(tickers_universo_txt)} tickers · espejo transitorio del universo "
                "versionado activo usado por GitHub Actions.",
            )
        )
        resolver_tickers = lambda: tickers_universo_txt  # noqa: E731
    else:
        st.sidebar.error(_t(f"{RUTA_UNIVERSO_TXT} was not found.", f"No se encontró {RUTA_UNIVERSO_TXT}."))
        resolver_tickers = lambda: []  # noqa: E731

if st.sidebar.button(_t("🔄 Download & calculate", "🔄 Descargar y calcular"), type="primary"):
    with st.spinner(_t("Resolving universe…", "Resolviendo universo…")):
        tickers = resolver_tickers()
    if not tickers:
        st.sidebar.error(_t("No ticker was resolved from this configuration.", "No se ha resuelto ningún ticker con esta configuración."))
    else:
        with st.spinner(_t(
            "Downloading fundamentals (this may take several minutes)…",
            "Descargando fundamentales (puede tardar varios minutos)…",
        )):
            st.session_state["raw_df"] = _fundamentales_cacheados(tuple(tickers))
        st.session_state["n_tickers"] = len(tickers)

st.sidebar.divider()

if "raw_df" not in st.session_state:
    st.info(_t(
        "Choose a universe in the sidebar and select **Download & calculate**. "
        "Only that action contacts the data provider; changing thresholds reuses the cache.",
        "Elige un universo en la barra lateral y pulsa **Descargar y calcular**. "
        "Solo esa acción consulta al proveedor; cambiar umbrales reutiliza la caché.",
    ))
    st.stop()

raw_df = st.session_state["raw_df"]
st.caption(_t(
    f"Fundamentals loaded for {st.session_state['n_tickers']} requested tickers.",
    f"Fundamentales cargados para {st.session_state['n_tickers']} tickers solicitados.",
))

# --- Sidebar: umbrales (UMBRALES), editables ---------------------------------
st.sidebar.header(_t("Research thresholds", "Umbrales de investigación"))
if st.sidebar.button(_t("Restore defaults", "Restaurar valores por defecto")):
    for clave in list(st.session_state):
        if clave.startswith("u_"):
            del st.session_state[clave]
    st.rerun()

u = dict(UMBRALES)
u["per_max"] = st.sidebar.slider(
    _t("Maximum P/E", "PER máximo"), 5.0, 40.0, UMBRALES["per_max"], 0.5, key="u_per_max")
u["per_bajo_mediana_sector"] = st.sidebar.checkbox(
    _t("Require P/E below sector/region median", "Exigir PER inferior a la mediana sector/región"), UMBRALES["per_bajo_mediana_sector"],
    key="u_per_bajo_mediana_sector")
u["min_empresas_sector"] = st.sidebar.slider(
    _t("Minimum peers for regional median", "Mín. comparables para la mediana regional"), 2, 15,
    UMBRALES["min_empresas_sector"], 1, key="u_min_empresas_sector")
u["ev_ebit_max"] = st.sidebar.slider(
    _t("Maximum EV/EBIT", "EV/EBIT máximo"), 2.0, 30.0, UMBRALES["ev_ebit_max"], 0.5, key="u_ev_ebit_max")
u["fcf_yield_min"] = st.sidebar.slider(
    _t("Minimum FCF yield (%)", "FCF yield mínimo (%)"), 0.0, 20.0, UMBRALES["fcf_yield_min"] * 100, 0.5,
    key="u_fcf_yield_min") / 100
u["roic_min"] = st.sidebar.slider(
    _t("Minimum ROIC (%)", "ROIC mínimo (%)"), 0.0, 40.0, UMBRALES["roic_min"] * 100, 0.5,
    key="u_roic_min") / 100
u["margen_op_min"] = st.sidebar.slider(
    _t("Minimum operating margin (%)", "Margen operativo mínimo (%)"), -10.0, 30.0, UMBRALES["margen_op_min"] * 100, 0.5,
    key="u_margen_op_min") / 100
u["deuda_ebitda_max"] = st.sidebar.slider(
    _t("Maximum net debt/EBITDA", "Deuda neta/EBITDA máximo"), 0.0, 6.0, UMBRALES["deuda_ebitda_max"], 0.1,
    key="u_deuda_ebitda_max")
u["cobertura_intereses_min"] = st.sidebar.slider(
    _t("Minimum interest coverage", "Cobertura de intereses mínima"), 0.0, 20.0, UMBRALES["cobertura_intereses_min"], 0.5,
    key="u_cobertura_intereses_min")
u["crecimiento_ingresos_min"] = st.sidebar.slider(
    _t("Minimum revenue CAGR (%)", "Crecimiento mínimo de ingresos, CAGR (%)"), -20.0, 30.0,
    UMBRALES["crecimiento_ingresos_min"] * 100, 0.5,
    key="u_crecimiento_ingresos_min") / 100
u["market_cap_eur_min"] = st.sidebar.slider(
    _t("Minimum market cap (EUR bn)", "Capitalización mínima (miles de M EUR)"), 0.0, 50.0,
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
col1.metric(_t("Companies evaluated", "Empresas evaluadas"), total)
col2.metric(
    _t("Candidates", "Candidatas"), n_candidatas,
    f"{n_candidatas / total:.1%}" if total else None,
)
col3.metric(
    _t("Candidates with unstable ROIC", "Candidatas con ROIC no fiable"),
    int((~candidatas["roic_fiable"].astype(bool)).sum()) if n_candidatas else 0,
    help=_t("Invested capital is too small for a comparable ROIC.", "El capital invertido es demasiado pequeño para comparar el ROIC."),
)
col4.metric(
    _t("Candidates with net cash > 30%", "Candidatas con caja neta > 30%"),
    int((candidatas["caja_neta_pct_mcap"] > 0.30).sum()) if n_candidatas else 0,
    help=_t("A material part of the discount may be idle cash.", "Parte relevante del descuento puede ser tesorería ociosa."),
)

# --- Composición del universo -------------------------------------------
st.subheader(_t("Universe composition", "Composición del universo"))
col_region, col_sector = st.columns(2)
with col_region:
    st.altair_chart(_bar_conteo(metricas, "region", _t("Region", "Región")), width="stretch")
with col_sector:
    st.altair_chart(_bar_conteo(metricas, "sector", "Sector"), width="stretch")

# --- Tabla filtrable ------------------------------------------------------
st.subheader(_t("Screening results", "Resultados del cribado"))
f1, f2, f3 = st.columns(3)
solo_candidatas = f1.checkbox(_t("Candidates only", "Solo candidatas"), value=True)
regiones_sel = f2.multiselect(_t("Region", "Región"), sorted(resultado["region"].dropna().unique()))
sectores_sel = f3.multiselect(_t("Sector", "Sector"), sorted(resultado["sector"].dropna().unique()))

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
        "per_mediana_sector": st.column_config.NumberColumn(_t("median P/E", "mediana PER"), format="%.1f"),
        "ev_ebit": st.column_config.NumberColumn("EV/EBIT", format="%.1f"),
        "fcf_yield": st.column_config.NumberColumn("FCF yield", format="percent"),
        "roic": st.column_config.NumberColumn(format="percent"),
        "caja_neta_pct_mcap": st.column_config.NumberColumn(_t("net cash / market cap", "caja neta / cap."), format="percent"),
        "deuda_ebitda": st.column_config.NumberColumn(_t("debt/EBITDA", "deuda/EBITDA"), format="%.1f"),
        "cobertura_int": st.column_config.NumberColumn(_t("interest coverage", "cobertura int."), format="%.1f"),
        "cagr_ingresos": st.column_config.NumberColumn(_t("revenue CAGR", "CAGR ingresos"), format="percent"),
        "market_cap_eur": st.column_config.NumberColumn(_t("market cap (EUR)", "cap. (EUR)"), format="compact"),
        "motivos_descarte": st.column_config.TextColumn(_t("rejection reasons", "motivos de descarte"), width="large"),
    },
)

st.download_button(
    _t("Download current view (CSV)", "Descargar vista actual (CSV)"),
    vista.to_csv(index=False).encode("utf-8"),
    file_name="candidatos_filtrados.csv",
    mime="text/csv",
)

# --- Gráfico de TradingView ---------------------------------------------
st.subheader(_t("📈 Candidate price chart", "📈 Gráfico de precio de candidatas"))
tickers_grafico = tickers_candidatos_para_grafico(resultado)
if not tickers_grafico:
    st.caption(_t("No candidates are available to chart.", "No hay candidatas que graficar."))
else:
    ticker_grafico = st.selectbox(
        _t("Select a candidate (ordered by rank)", "Selecciona una candidata (ordenadas por ranking)"),
        tickers_grafico,
        key="tv_ticker_vivo",
    )
    _grafico_tradingview(ticker_grafico)

# --- Prompt para interpretar las candidatas con un LLM -----------------------
st.subheader(_t("🤖 Reproducible LLM hand-off", "🤖 Transferencia reproducible a un LLM"))
if n_candidatas == 0:
    st.caption(_t(
        "No company passes the current thresholds; adjust the research criteria "
        "if you need an interpretation prompt.",
        "Ninguna empresa supera los umbrales actuales; ajusta los criterios si "
        "necesitas un prompt de interpretación.",
    ))
else:
    st.caption(_t(
        "Copy this into your preferred LLM. The prompt requests a descriptive "
        "review of calculated evidence, never a buy or sell recommendation.",
        "Cópialo en tu LLM preferido. El prompt solicita una revisión descriptiva "
        "de la evidencia calculada, nunca una recomendación de compra o venta.",
    ))
    st.code(
        generar_prompt_interpretacion(candidatas, idioma=st.session_state["idioma"]),
        language=None,
    )

st.divider()
st.caption(DISCLAIMER_UI[st.session_state["idioma"]])
