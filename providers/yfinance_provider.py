"""Adaptador auditable de Yahoo Finance mediante :mod:`yfinance`.

Una respuesta HTTP correcta no implica datos contables utilizables. Este
adaptador registra fechas y periodos, impide mezclar TTM y anual en una misma
empresa y clasifica las incidencias antes de que se calculen ratios.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from .base import TASA_IMPOSITIVA_DEFECTO, Fundamentales


ANTIGUEDAD_MAXIMA_DIAS = 550
# Tras tantos tickers seguidos limitados por Yahoo se dejan de esperar
# reintentos: el servidor está bloqueado y esperar no cambia el resultado.
MAXIMO_FALLOS_CONSECUTIVOS = 3
CAMPOS_DEUDA_FINANCIERA_EV = ("Total Debt",)
CAMPOS_LIQUIDEZ_EV = (
    "Cash Cash Equivalents And Short Term Investments",
    "Cash And Cash Equivalents",
    "Cash",
)
CAMPOS_ESENCIALES = {
    "market_cap": "capitalización",
    "net_income": "beneficio neto",
    "ebit": "EBIT",
    "ebitda": "EBITDA",
    "ingresos": "ingresos",
    "free_cash_flow": "flujo de caja libre",
    "total_debt": "deuda total",
    "cash": "caja",
    "equity": "fondos propios",
    "gasto_intereses": "gasto por intereses",
}


def _es_na(valor) -> bool:
    try:
        return bool(pd.isna(valor))
    except (TypeError, ValueError):
        return True


def _div(a, b):
    if a is None or b is None:
        return np.nan
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return np.nan
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return np.nan
    return a / b


def _es_limite_peticiones(exc: Exception) -> bool:
    """Reconoce el 429 de Yahoo sin importar yfinance (los tests usan dobles)."""
    texto = str(exc).lower()
    return (
        type(exc).__name__ == "YFRateLimitError"
        or "too many requests" in texto
        or "rate limit" in texto
    )


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


def _fecha_reciente(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return ""
    fechas = pd.to_datetime(df.columns, errors="coerce")
    fechas = fechas[~pd.isna(fechas)]
    return "" if len(fechas) == 0 else pd.Timestamp(fechas.max()).date().isoformat()


def _tasa_impositiva(fin_periodo: pd.DataFrame) -> float:
    provision = _valor_reciente(fin_periodo, ["Tax Provision"])
    pre_tax = _valor_reciente(fin_periodo, ["Pretax Income"])
    tasa = _div(provision, pre_tax)
    if _es_na(tasa) or tasa < 0 or tasa > 0.50:
        return TASA_IMPOSITIVA_DEFECTO
    return float(tasa)


def _fecha_utc(reloj: Callable[[], datetime]) -> pd.Timestamp:
    momento = pd.Timestamp(reloj())
    if momento.tzinfo is None:
        return momento.tz_localize("UTC")
    return momento.tz_convert("UTC")


def _evaluar_calidad(datos: Fundamentales, momento: pd.Timestamp) -> None:
    """Clasifica problemas materiales sin ocultarlos ni rellenarlos."""
    if datos.error_descarga:
        datos.calidad_datos = "error"
        datos.incidencias_datos = datos.error_descarga
        return

    incidencias: list[str] = []
    ausentes = [
        etiqueta for campo, etiqueta in CAMPOS_ESENCIALES.items()
        if _es_na(getattr(datos, campo))
    ]
    if ausentes:
        incidencias.append("campos ausentes: " + ", ".join(ausentes))
    if _es_na(datos.market_cap) or float(datos.market_cap) <= 0:
        incidencias.append("capitalización no positiva o ausente")
    for campo, etiqueta in (("total_debt", "deuda"), ("cash", "caja")):
        valor = getattr(datos, campo)
        if not _es_na(valor) and float(valor) < 0:
            incidencias.append(f"{etiqueta} negativa")
    if not datos.divisa_cotizacion or not datos.divisa_financiera:
        incidencias.append("divisa de cotización o financiera ausente")
    elif not datos.divisa_consistente:
        incidencias.append(
            f"divisas incompatibles {datos.divisa_cotizacion}/"
            f"{datos.divisa_financiera}"
        )
    if datos.tipo_periodo not in {"TTM", "ANUAL"}:
        incidencias.append("periodo contable no identificado")

    fechas = {
        "resultados": datos.fecha_resultados,
        "flujo de caja": datos.fecha_flujo_caja,
        "balance": datos.fecha_balance,
    }
    parseadas: dict[str, pd.Timestamp] = {}
    for etiqueta, fecha in fechas.items():
        valor = pd.to_datetime(fecha, errors="coerce", utc=True)
        if pd.isna(valor):
            incidencias.append(f"fecha de {etiqueta} ausente")
        else:
            parseadas[etiqueta] = pd.Timestamp(valor)
            edad = (momento.normalize() - pd.Timestamp(valor).normalize()).days
            if edad > ANTIGUEDAD_MAXIMA_DIAS:
                incidencias.append(f"{etiqueta} obsoleto ({edad} días)")
            elif edad < -7:
                incidencias.append(f"fecha futura de {etiqueta}")
    if "resultados" in parseadas and "flujo de caja" in parseadas:
        desfase = abs((parseadas["resultados"] - parseadas["flujo de caja"]).days)
        if desfase > 45:
            incidencias.append(
                f"resultados y flujo de caja desalineados ({desfase} días)"
            )

    datos.incidencias_datos = "; ".join(dict.fromkeys(incidencias))
    # Si faltan los tres pilares del estado de resultados o la capitalización,
    # la fila ni siquiera puede considerarse una observación utilizable.
    pilares_ausentes = sum(
        _es_na(getattr(datos, campo))
        for campo in ("market_cap", "ingresos", "ebit", "net_income")
    )
    if pilares_ausentes >= 3:
        datos.calidad_datos = "inutilizable"
    else:
        datos.calidad_datos = "revisar" if incidencias else "ok"


class ProveedorYFinance:
    """Obtiene y normaliza fundamentales de Yahoo con trazabilidad explícita."""

    nombre = "yfinance"

    def __init__(
        self,
        cliente=None,
        reloj: Callable[[], datetime] | None = None,
        resolver_fx: Callable[[str], float] | None = None,
        reintentos: int = 0,
        pausa_reintento: float = 15.0,
        pausa_entre_tickers: float = 0.0,
        dormir: Callable[[float], None] | None = None,
    ) -> None:
        if cliente is None:  # importación diferida: los tests puros no necesitan red
            import yfinance as yf
            cliente = yf
        self.cliente = cliente
        self.reloj = reloj or (lambda: datetime.now(timezone.utc))
        self.resolver_fx = resolver_fx or self._tipo_cambio_a_eur
        # Caché por instancia: ``lru_cache`` sobre un método retiene ``self``
        # en una caché global y nunca libera el proveedor.
        self._cache_fx: dict[str, float] = {}
        # Reintentos solo para fallos transitorios; 0 conserva un intento por
        # ticker, que es lo que usa la ejecución oficial semanal.
        self.reintentos = max(int(reintentos), 0)
        self.pausa_reintento = float(pausa_reintento)
        self.pausa_entre_tickers = float(pausa_entre_tickers)
        self.dormir = dormir or time.sleep
        self._fallos_consecutivos = 0

    def _tipo_cambio_a_eur(self, divisa: str) -> float:  # pragma: no cover - red
        divisa = (divisa or "").upper()
        if divisa == "EUR":
            return 1.0
        if not divisa:
            return np.nan
        if divisa not in self._cache_fx:
            self._cache_fx[divisa] = self._descargar_tipo_cambio(divisa)
        return self._cache_fx[divisa]

    def _descargar_tipo_cambio(self, divisa: str) -> float:  # pragma: no cover - red
        historico = self.cliente.download(
            f"{divisa}EUR=X", period="5d", interval="1d", progress=False,
            auto_adjust=False, threads=False,
        )
        if historico is None or historico.empty or "Close" not in historico:
            return np.nan
        valores = pd.to_numeric(
            historico["Close"].squeeze(), errors="coerce",
        ).dropna()
        return np.nan if valores.empty else float(valores.iloc[-1])

    def descargar(self, tickers: list[str]) -> list[Fundamentales]:  # pragma: no cover - red
        salida: list[Fundamentales] = []
        momento = _fecha_utc(self.reloj)
        fecha_consulta = momento.isoformat()
        normalizados = dict.fromkeys(t.strip().upper() for t in tickers if t.strip())
        self._fallos_consecutivos = 0
        for posicion, tkr in enumerate(normalizados):
            if posicion and self.pausa_entre_tickers:
                self.dormir(self.pausa_entre_tickers)
            datos = self._descargar_con_reintentos(tkr, momento, fecha_consulta)
            salida.append(datos)
            if datos.calidad_datos == "error":
                print(f"  ERR {tkr}: {datos.error_descarga}")
            else:
                print(f"  {datos.calidad_datos:>11s}  {tkr}")
        return salida

    def _descargar_con_reintentos(
        self, tkr: str, momento: pd.Timestamp, fecha_consulta: str,
    ) -> Fundamentales:
        """Descarga un ticker reintentando solo fallos transitorios de Yahoo.

        Con ``reintentos=0`` (la ejecución semanal) el comportamiento es el de
        siempre: un intento por ticker. Con reintentos, un límite de peticiones
        se reintenta con espera creciente, y unos estados financieros vacíos
        (yfinance oculta el 429 y devuelve tablas vacías) se reintentan una
        vez. Tras ``MAXIMO_FALLOS_CONSECUTIVOS`` tickers seguidos limitados se
        deja de esperar: si Yahoo bloquea el servidor, reintentar solo alarga
        la espera sin cambiar el resultado.
        """
        intento = 0
        while True:
            puede_reintentar = (
                intento < self.reintentos
                and self._fallos_consecutivos < MAXIMO_FALLOS_CONSECUTIVOS
            )
            try:
                datos, estados_vacios = self._descargar_ticker(
                    tkr, momento, fecha_consulta,
                )
            except Exception as exc:
                if _es_limite_peticiones(exc) and puede_reintentar:
                    intento += 1
                    self.dormir(self.pausa_reintento * intento)
                    continue
                if _es_limite_peticiones(exc):
                    self._fallos_consecutivos += 1
                return self._fila_error(tkr, momento, fecha_consulta, exc)
            if estados_vacios and self.reintentos:
                if intento == 0 and puede_reintentar:
                    intento += 1
                    self.dormir(self.pausa_reintento)
                    continue
                datos.incidencias_datos = "; ".join(filter(None, [
                    datos.incidencias_datos,
                    "estados financieros vacíos (posible límite de peticiones de Yahoo)",
                ]))
            self._fallos_consecutivos = 0
            return datos

    def _fila_error(
        self, tkr: str, momento: pd.Timestamp, fecha_consulta: str, exc: Exception,
    ) -> Fundamentales:
        datos = Fundamentales(
            ticker=tkr,
            divisa_consistente=False,
            proveedor_datos=self.nombre,
            fecha_consulta_utc=fecha_consulta,
            calidad_datos="error",
            error_descarga=f"{type(exc).__name__}: {exc}",
        )
        _evaluar_calidad(datos, momento)
        return datos

    def _descargar_ticker(
        self, tkr: str, momento: pd.Timestamp, fecha_consulta: str,
    ) -> tuple[Fundamentales, bool]:  # pragma: no cover - red
        """Un intento de descarga. Devuelve los datos y si todos los estados
        financieros llegaron vacíos (síntoma de límite de peticiones)."""
        ticker = self.cliente.Ticker(tkr)
        info = ticker.info or {}
        fin_anual = ticker.income_stmt
        cf_anual = ticker.cashflow
        fin_ttm = ticker.ttm_income_stmt
        cf_ttm = ticker.ttm_cashflow
        bs = ticker.balance_sheet

        # Solo se usa TTM cuando resultados Y caja lo ofrecen. Si uno
        # falta, ambos pasan al último anual para no mezclar duraciones.
        usar_ttm = all(
            df is not None and not df.empty for df in (fin_ttm, cf_ttm)
        )
        fin_periodo = fin_ttm if usar_ttm else fin_anual
        cf_periodo = cf_ttm if usar_ttm else cf_anual
        tipo_periodo = "TTM" if usar_ttm else "ANUAL"

        ingresos = _valor_reciente(
            fin_periodo, ["Total Revenue", "Operating Revenue"],
        )
        ebit = _valor_reciente(fin_periodo, ["EBIT", "Operating Income"])
        net_income = _valor_reciente(
            fin_periodo, ["Net Income", "Net Income Common Stockholders"],
        )
        intereses = _valor_reciente(
            fin_periodo,
            ["Interest Expense", "Interest Expense Non Operating"],
        )
        ebitda = _valor_reciente(
            fin_periodo, ["EBITDA", "Normalized EBITDA"],
        )
        fcf = _valor_reciente(cf_periodo, ["Free Cash Flow"])
        # EV netea solo deuda financiera contra liquidez disponible.
        # Caja restringida y pasivos operativos (ingresos diferidos,
        # anticipos o value in circulation) requieren un análisis
        # sectorial separado; no son sustitutos de caja ni deuda.
        deuda, deuda_inicio = _valor_reciente_y_anterior(
            bs, CAMPOS_DEUDA_FINANCIERA_EV,
        )
        caja, caja_inicio = _valor_reciente_y_anterior(
            bs, CAMPOS_LIQUIDEZ_EV,
        )
        equity, equity_inicio = _valor_reciente_y_anterior(
            bs, ["Stockholders Equity", "Total Equity Gross Minority Interest"],
        )
        ingresos_inicio, ingresos_fin, anios = _extremos_historicos(
            fin_anual, ["Total Revenue", "Operating Revenue"],
        )
        comparables_anuales = {
            "ingresos": _valor_reciente(
                fin_anual, ["Total Revenue", "Operating Revenue"],
            ),
            "net_income": _valor_reciente(
                fin_anual,
                ["Net Income", "Net Income Common Stockholders"],
            ),
            "ebit": _valor_reciente(
                fin_anual, ["EBIT", "Operating Income"],
            ),
            "free_cash_flow": _valor_reciente(
                cf_anual, ["Free Cash Flow"],
            ),
            # ``balance_sheet`` ya es anual; se reutiliza ese mismo
            # corte instantáneo en vez de etiquetarlo como TTM.
            "total_debt": deuda,
            "cash": caja,
            "equity": equity,
            "gasto_intereses": _valor_reciente(
                fin_anual,
                ["Interest Expense", "Interest Expense Non Operating"],
            ),
        }

        divisa_cotizacion = str(info.get("currency") or "").upper()
        divisa_financiera = str(info.get("financialCurrency") or "").upper()
        divisa_consistente = bool(
            divisa_cotizacion
            and divisa_financiera
            and divisa_cotizacion == divisa_financiera
        )
        market_cap = info.get("marketCap", np.nan)
        fx_eur = self.resolver_fx(divisa_cotizacion)
        market_cap_eur = (
            float(market_cap) * fx_eur
            if not _es_na(market_cap) and not _es_na(fx_eur)
            else np.nan
        )
        datos = Fundamentales(
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
            proveedor_datos=self.nombre,
            fecha_consulta_utc=fecha_consulta,
            tipo_periodo=tipo_periodo,
            fecha_resultados=_fecha_reciente(fin_periodo),
            fecha_flujo_caja=_fecha_reciente(cf_periodo),
            fecha_balance=_fecha_reciente(bs),
            comparables_anuales=comparables_anuales,
            fecha_resultados_anual=_fecha_reciente(fin_anual),
            fecha_flujo_caja_anual=_fecha_reciente(cf_anual),
        )
        _evaluar_calidad(datos, momento)
        estados_vacios = all(
            df is None or df.empty
            for df in (fin_anual, cf_anual, fin_ttm, cf_ttm, bs)
        )
        return datos, estados_vacios
