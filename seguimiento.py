"""Seguimiento longitudinal del rendimiento real de las candidatas (Fase 4).

Responde a la pregunta que las fases anteriores no contestan: de las
empresas que el screener marcó como candidatas, ¿cómo les fue de verdad
después? Sigue la misma disciplina que el backtest de cartera separado del
autor: retorno TWR encadenado sobre precio de cierre AJUSTADO (splits +
dividendos), nunca sobre "valor de la posición" con flujos.

DISCIPLINA CONTRA EL SESGO RETROSPECTIVO (look-ahead bias):

1. La decisión de qué es candidata nunca se recalcula. `journal.py` ya
   congela ese veredicto en el momento en que se emitió; este módulo solo
   LEE esas fechas, nunca las recalcula con fundamentales de hoy.
2. El precio de entrada es el primer cierre ajustado disponible EN o
   DESPUÉS de la fecha en que el ticker apareció por primera vez como
   candidata -nunca un precio anterior a esa fecha, que supondría usar
   información que todavía no existía cuando se emitió la señal.
3. Si un ticker vuelve a aparecer como candidata en semanas posteriores sin
   haber dejado de serlo, no se abre una entrada nueva: sigue siendo la
   misma señal original, con la misma fecha de entrada.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

RUTA_SEGUIMIENTO_DEFECTO = "seguimiento_candidatas.csv"

COLUMNAS_CANDIDATA = [
    "ticker", "nombre", "sector", "region", "fecha_entrada",
    "per", "ev_ebit", "roic", "puntuacion",
]


def extraer_candidatas_unicas(journal: pd.DataFrame) -> pd.DataFrame:
    """Una fila por ticker: su primera aparición como candidata (pasa=True).

    Reapariciones posteriores del mismo ticker no generan una entrada
    nueva -ver disciplina anti-look-ahead en el docstring del módulo.
    """
    if journal.empty:
        return pd.DataFrame(columns=COLUMNAS_CANDIDATA)
    candidatas = journal[journal["pasa"].astype(bool)].copy()
    if candidatas.empty:
        return pd.DataFrame(columns=COLUMNAS_CANDIDATA)
    candidatas["fecha_ejecucion"] = pd.to_datetime(candidatas["fecha_ejecucion"])
    idx_primera = candidatas.groupby("ticker")["fecha_ejecucion"].idxmin()
    primeras = candidatas.loc[idx_primera].rename(columns={"fecha_ejecucion": "fecha_entrada"})
    return primeras[COLUMNAS_CANDIDATA].reset_index(drop=True)


def calcular_rendimiento(precios: pd.Series) -> dict:
    """Retorno TWR encadenado y drawdown máximo sobre una serie de precios.

    `precios` debe ser cierre ajustado, ordenado por fecha ascendente, ya
    recortado para empezar en la fecha de entrada (inclusive) -esta función
    no sabe nada de fechas de candidatura, solo opera sobre la serie que se
    le pasa. Separarla de la descarga es lo que la hace testeable sin red.
    """
    campos_vacios = {
        "precio_entrada": np.nan, "precio_actual": np.nan,
        "retorno_total": np.nan, "max_drawdown": np.nan,
        "dias_en_seguimiento": np.nan,
    }
    if precios is None or len(precios) < 2:
        return campos_vacios

    retornos_diarios = precios.pct_change().dropna()
    if retornos_diarios.empty:
        return campos_vacios

    # Índice de riqueza encadenado (TWR): parte de 1.0 en la fecha de
    # entrada y compone los retornos diarios uno a uno. Para una única
    # posición sin flujos intermedios coincide con el ratio precio final /
    # precio inicial, pero encadenar dejar la puerta abierta a introducir
    # sub-periodos (rebalanceos, ampliaciones) sin cambiar la fórmula.
    indice_riqueza = (1 + retornos_diarios).cumprod()
    indice_riqueza = pd.concat([pd.Series([1.0], index=[precios.index[0]]), indice_riqueza])

    drawdown = indice_riqueza / indice_riqueza.cummax() - 1

    return {
        "precio_entrada": float(precios.iloc[0]),
        "precio_actual": float(precios.iloc[-1]),
        "retorno_total": float(indice_riqueza.iloc[-1] - 1),
        "max_drawdown": float(drawdown.min()),
        "dias_en_seguimiento": int((precios.index[-1] - precios.index[0]).days),
    }


def descargar_precios_ajustados(
    ticker: str, fecha_entrada, hasta: pd.Timestamp | None = None,
) -> pd.Series:  # pragma: no cover - red
    """Cierre ajustado (splits + dividendos) desde `fecha_entrada` hasta hoy.

    Nunca devuelve precios anteriores a `fecha_entrada`: es la barrera que
    impide que el cálculo de rendimiento use información previa a la fecha
    en que el ticker se convirtió en candidata.
    """
    import yfinance as yf

    inicio = pd.Timestamp(fecha_entrada)
    if inicio.tzinfo is not None:
        inicio = inicio.tz_localize(None)
    inicio = inicio.normalize()
    hasta = pd.Timestamp.now(tz="UTC") if hasta is None else pd.Timestamp(hasta)
    if hasta.tzinfo is not None:
        hasta = hasta.tz_localize(None)
    fin = hasta.normalize() + pd.Timedelta(days=1)

    historico = yf.download(
        ticker, start=inicio, end=fin, auto_adjust=True, progress=False, threads=False,
    )
    if historico is None or historico.empty or "Close" not in historico:
        return pd.Series(dtype=float)
    cierre = historico["Close"]
    # `.squeeze()` colapsaría también las filas (no solo las columnas) si
    # la ventana solo cubre un día -exactamente el caso de una candidata
    # recién detectada- devolviendo un escalar en vez de una Serie.
    if isinstance(cierre, pd.DataFrame):
        cierre = cierre.iloc[:, 0]
    precios = pd.to_numeric(cierre, errors="coerce").dropna()
    return precios[precios.index >= inicio]


def evaluar_seguimiento(candidatas: pd.DataFrame) -> pd.DataFrame:  # pragma: no cover - red
    """Descarga precios y calcula rendimiento para cada candidata única.

    Conserva también los fallos de descarga (ticker deslistado, sin datos)
    en vez de abortar el resto, igual que `descargar_fundamentales`.
    """
    filas = []
    for _, fila in candidatas.iterrows():
        base = fila.to_dict()
        try:
            precios = descargar_precios_ajustados(fila["ticker"], fila["fecha_entrada"])
            base.update(calcular_rendimiento(precios))
            base["error_descarga"] = ""
        except Exception as exc:
            base.update({
                "precio_entrada": np.nan, "precio_actual": np.nan,
                "retorno_total": np.nan, "max_drawdown": np.nan,
                "dias_en_seguimiento": np.nan,
            })
            base["error_descarga"] = f"{type(exc).__name__}: {exc}"
        filas.append(base)
        print(f"  {'ERR' if base['error_descarga'] else 'ok '} {fila['ticker']}")
    return pd.DataFrame(filas)


def registrar_seguimiento(
    rendimientos: pd.DataFrame,
    ruta_seguimiento: str | Path = RUTA_SEGUIMIENTO_DEFECTO,
    momento: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Añade `rendimientos` al histórico de seguimiento, sin sobrescribir.

    Misma mecánica que `journal.registrar_ejecucion`: cada llamada AÑADE
    una fila por candidata con el timestamp de cuándo se calculó ese
    rendimiento, para poder ver más adelante cómo evolucionó semana a
    semana, no solo su estado actual.
    """
    momento = pd.Timestamp.now(tz="UTC") if momento is None else momento
    filas = rendimientos.copy()
    filas.insert(0, "fecha_calculo", momento.isoformat())

    ruta = Path(ruta_seguimiento)
    escribir_cabecera = not ruta.exists()
    filas.to_csv(ruta, mode="a", index=False, header=escribir_cabecera)
    return filas


def leer_seguimiento(ruta_seguimiento: str | Path = RUTA_SEGUIMIENTO_DEFECTO) -> pd.DataFrame:
    """Lee el histórico de seguimiento completo, o vacío si no existe aún."""
    ruta = Path(ruta_seguimiento)
    if not ruta.exists():
        return pd.DataFrame()
    return pd.read_csv(ruta, parse_dates=["fecha_calculo", "fecha_entrada"])
