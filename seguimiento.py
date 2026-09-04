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
   misma señal original, con la misma fecha de entrada. Si existe una
   observación válida intermedia con `pasa=False`, la siguiente candidatura
   sí abre una señal nueva. Una descarga fallida o la ausencia del ticker en
   un snapshot no se interpretan como una salida.
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


def _es_verdadero(valor) -> bool:
    """Interpreta booleanos procedentes tanto de pandas como de un CSV."""
    if pd.isna(valor):
        return False
    if isinstance(valor, str):
        return valor.strip().lower() in {"true", "1", "sí", "si"}
    return bool(valor)


def extraer_senales_candidatas(journal: pd.DataFrame) -> pd.DataFrame:
    """Una fila por episodio en que un ticker se convierte en candidata.

    Una secuencia ``True, True`` es una sola señal; ``True, False, True``
    contiene dos. Solo las observaciones válidas cambian el estado: un fallo
    de descarga conserva el estado anterior y un ticker ausente de una
    ejecución ni siquiera aparece en su secuencia. La pareja
    ``(ticker, fecha_entrada)`` identifica la señal sin cambiar el esquema
    existente de ``seguimiento_candidatas.csv``.
    """
    if journal.empty:
        return pd.DataFrame(columns=COLUMNAS_CANDIDATA)

    observaciones = journal.copy()
    observaciones["fecha_ejecucion"] = pd.to_datetime(
        observaciones["fecha_ejecucion"], utc=True,
    )
    observaciones = observaciones.sort_values(
        ["ticker", "fecha_ejecucion"], kind="stable",
    )

    indices_entrada = []
    for _, grupo in observaciones.groupby("ticker", sort=False):
        senal_activa = False
        for indice, fila in grupo.iterrows():
            error = fila.get("error_descarga", "")
            if pd.notna(error) and str(error).strip():
                continue
            pasa = _es_verdadero(fila.get("pasa", False))
            if pasa and not senal_activa:
                indices_entrada.append(indice)
            senal_activa = pasa

    if not indices_entrada:
        return pd.DataFrame(columns=COLUMNAS_CANDIDATA)

    entradas = observaciones.loc[indices_entrada].rename(
        columns={"fecha_ejecucion": "fecha_entrada"},
    )
    return entradas[COLUMNAS_CANDIDATA].reset_index(drop=True)


def extraer_candidatas_unicas(journal: pd.DataFrame) -> pd.DataFrame:
    """Alias compatible: devuelve señales únicas, no solo tickers únicos."""
    return extraer_senales_candidatas(journal)


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
    """Descarga precios y calcula rendimiento para cada señal candidata.

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


def preparar_historial_graficable(
    seguimiento: pd.DataFrame,
    minimo_observaciones: int = 2,
) -> pd.DataFrame:
    """Conserva solo señales con suficientes retornos observados para una línea.

    Una fila de seguimiento puede tener precios todavía no disponibles y, por
    tanto, ``retorno_total`` nulo. Esas filas son auditables y permanecen en la
    tabla, pero no deben hacer que el dashboard ofrezca un gráfico vacío.
    """
    columnas_clave = ["ticker", "fecha_entrada", "fecha_calculo", "retorno_total"]
    if (
        seguimiento is None
        or seguimiento.empty
        or minimo_observaciones < 2
        or any(columna not in seguimiento for columna in columnas_clave)
    ):
        return pd.DataFrame(columns=list(seguimiento.columns) if isinstance(seguimiento, pd.DataFrame) else [])

    validas = seguimiento.dropna(subset=columnas_clave).copy()
    if validas.empty:
        return validas
    validas = (
        validas.sort_values("fecha_calculo")
        .drop_duplicates(["ticker", "fecha_entrada", "fecha_calculo"], keep="last")
    )
    tamanos = validas.groupby(["ticker", "fecha_entrada"])["fecha_calculo"].transform("size")
    return validas[tamanos >= minimo_observaciones].reset_index(drop=True)
