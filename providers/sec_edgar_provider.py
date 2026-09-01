"""Proveedor secundario selectivo basado en SEC EDGAR Company Facts.

La cobertura se limita intencionadamente a tickers registrados literalmente
por la SEC. No se elimina un sufijo de bolsa de Yahoo (por ejemplo ``.MC``),
porque convertir ``IAG.MC`` en ``IAG`` podría enlazar otra empresa de EE. UU.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from time import sleep
from typing import Callable
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from .base import Fundamentales


URL_TICKERS_SEC = "https://www.sec.gov/files/company_tickers.json"
URL_COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FORMULARIOS_ANUALES = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

CONCEPTOS = {
    "ingresos": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues", "SalesRevenueNet", "Revenue",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    # OperatingIncomeLoss es una aproximación auditable a EBIT; la tabla de
    # verificación conserva el origen y nunca mezcla este valor con Yahoo.
    "ebit": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "cash_operaciones": [
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PurchaseOfPropertyPlantAndEquipment",
    ],
    "total_debt": [
        "LongTermDebtAndFinanceLeaseObligations",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebt",
    ],
    "cash": [
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndCashEquivalentsAtCarryingValue",
        "CashAndCashEquivalents",
    ],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "Equity",
    ],
    "gasto_intereses": [
        "InterestExpenseNonOperating", "InterestExpense",
        "FinanceCosts",
    ],
}


def _descarga_json(url: str, user_agent: str, timeout: int = 30) -> dict:
    peticion = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json",
        },
    )
    with urlopen(peticion, timeout=timeout) as respuesta:  # nosec B310 - URL fija SEC
        return json.loads(respuesta.read().decode("utf-8"))


def _fecha_utc(reloj: Callable[[], datetime]) -> str:
    momento = pd.Timestamp(reloj())
    if momento.tzinfo is None:
        momento = momento.tz_localize("UTC")
    else:
        momento = momento.tz_convert("UTC")
    return momento.isoformat()


def _candidatos_hechos(concepto: dict, instantaneo: bool) -> list[dict]:
    candidatos: list[dict] = []
    for unidad, hechos in concepto.get("units", {}).items():
        for hecho in hechos:
            if hecho.get("form") not in FORMULARIOS_ANUALES:
                continue
            if hecho.get("fp") not in {None, "", "FY"}:
                continue
            if "val" not in hecho or not hecho.get("end"):
                continue
            if instantaneo:
                if hecho.get("start"):
                    continue
            else:
                inicio = pd.to_datetime(hecho.get("start"), errors="coerce")
                fin = pd.to_datetime(hecho.get("end"), errors="coerce")
                if pd.isna(inicio) or pd.isna(fin):
                    continue
                duracion = (fin - inicio).days
                if not 300 <= duracion <= 430:
                    continue
            candidatos.append({**hecho, "unidad": unidad})
    return candidatos


def _hecho_reciente(
    facts: dict,
    conceptos: list[str],
    instantaneo: bool = False,
    unidad_preferida: str = "",
) -> dict | None:
    candidatos: list[dict] = []
    for taxonomia in ("us-gaap", "ifrs-full"):
        disponibles = facts.get(taxonomia, {})
        for prioridad, nombre in enumerate(conceptos):
            if nombre not in disponibles:
                continue
            for hecho in _candidatos_hechos(disponibles[nombre], instantaneo):
                if unidad_preferida and hecho["unidad"] != unidad_preferida:
                    continue
                candidatos.append({**hecho, "concepto": nombre, "prioridad": prioridad})
    if not candidatos:
        return None
    return max(
        candidatos,
        key=lambda h: (
            str(h.get("end", "")), str(h.get("filed", "")), -h["prioridad"],
        ),
    )


def _valor(hecho: dict | None):
    if hecho is None:
        return np.nan
    try:
        valor = float(hecho["val"])
    except (KeyError, TypeError, ValueError):
        return np.nan
    return valor if np.isfinite(valor) else np.nan


class ProveedorSecEdgar:
    """Descarga el último ejercicio anual disponible para candidatas SEC."""

    nombre = "sec_edgar"

    def __init__(
        self,
        user_agent: str,
        transport: Callable[[str, str], dict] | None = None,
        reloj: Callable[[], datetime] | None = None,
        pausa: Callable[[float], None] | None = None,
    ) -> None:
        if not str(user_agent or "").strip():
            raise ValueError(
                "SEC_USER_AGENT es obligatorio (nombre del proyecto y contacto)"
            )
        self.user_agent = str(user_agent).strip()
        self.transport = transport or _descarga_json
        self.reloj = reloj or (lambda: datetime.now(timezone.utc))
        # 0,12 s mantiene el cliente por debajo del límite SEC de 10 req/s.
        # Los transportes falsos de tests no necesitan esperar.
        self.pausa = pausa or (sleep if transport is None else lambda _s: None)

    @lru_cache(maxsize=1)
    def _mapa_tickers(self) -> dict[str, int]:
        payload = self.transport(URL_TICKERS_SEC, self.user_agent)
        return {
            str(fila.get("ticker", "")).strip().upper(): int(fila["cik_str"])
            for fila in payload.values()
            if fila.get("ticker") and fila.get("cik_str") is not None
        }

    def _sin_cobertura(self, ticker: str, fecha: str, detalle: str) -> Fundamentales:
        return Fundamentales(
            ticker=ticker,
            proveedor_datos=self.nombre,
            fecha_consulta_utc=fecha,
            tipo_periodo="ANUAL",
            calidad_datos="error",
            error_descarga=detalle,
        )

    def _normalizar(self, ticker: str, cik: int, payload: dict, fecha: str) -> Fundamentales:
        facts = payload.get("facts", {})
        ingresos = _hecho_reciente(facts, CONCEPTOS["ingresos"])
        unidad = "" if ingresos is None else str(ingresos.get("unidad", ""))

        hechos = {
            "ingresos": ingresos,
            "net_income": _hecho_reciente(
                facts, CONCEPTOS["net_income"], unidad_preferida=unidad,
            ),
            "ebit": _hecho_reciente(
                facts, CONCEPTOS["ebit"], unidad_preferida=unidad,
            ),
            "cash_operaciones": _hecho_reciente(
                facts, CONCEPTOS["cash_operaciones"], unidad_preferida=unidad,
            ),
            "capex": _hecho_reciente(
                facts, CONCEPTOS["capex"], unidad_preferida=unidad,
            ),
            "total_debt": _hecho_reciente(
                facts, CONCEPTOS["total_debt"], instantaneo=True,
                unidad_preferida=unidad,
            ),
            "cash": _hecho_reciente(
                facts, CONCEPTOS["cash"], instantaneo=True,
                unidad_preferida=unidad,
            ),
            "equity": _hecho_reciente(
                facts, CONCEPTOS["equity"], instantaneo=True,
                unidad_preferida=unidad,
            ),
            "gasto_intereses": _hecho_reciente(
                facts, CONCEPTOS["gasto_intereses"], unidad_preferida=unidad,
            ),
        }
        cfo, capex = _valor(hechos["cash_operaciones"]), _valor(hechos["capex"])
        fcf = cfo - abs(capex) if not pd.isna(cfo) and not pd.isna(capex) else np.nan
        fecha_resultados = "" if ingresos is None else str(ingresos.get("end", ""))
        fecha_flujo = (
            "" if hechos["cash_operaciones"] is None
            else str(hechos["cash_operaciones"].get("end", ""))
        )
        fechas_balance = [
            str(hechos[campo].get("end", ""))
            for campo in ("total_debt", "cash", "equity")
            if hechos[campo] is not None
        ]
        fecha_balance = max(fechas_balance, default="")
        ausentes = [
            campo for campo in (
                "ingresos", "net_income", "ebit", "total_debt", "cash", "equity"
            ) if hechos[campo] is None
        ]
        incidencias = (
            "conceptos SEC ausentes: " + ", ".join(ausentes) if ausentes else ""
        )
        conceptos_fuente = {
            campo: str(hecho.get("concepto", ""))
            for campo, hecho in hechos.items()
            if hecho is not None and campo not in {"cash_operaciones", "capex"}
        }
        if hechos["cash_operaciones"] is not None and hechos["capex"] is not None:
            conceptos_fuente["free_cash_flow"] = (
                f"{hechos['cash_operaciones']['concepto']} - "
                f"abs({hechos['capex']['concepto']})"
            )
        return Fundamentales(
            ticker=ticker,
            nombre=str(payload.get("entityName", "")),
            divisa_financiera=unidad,
            ingresos=_valor(hechos["ingresos"]),
            net_income=_valor(hechos["net_income"]),
            ebit=_valor(hechos["ebit"]),
            free_cash_flow=fcf,
            total_debt=_valor(hechos["total_debt"]),
            cash=_valor(hechos["cash"]),
            equity=_valor(hechos["equity"]),
            gasto_intereses=_valor(hechos["gasto_intereses"]),
            proveedor_datos=self.nombre,
            fecha_consulta_utc=fecha,
            tipo_periodo="ANUAL",
            fecha_resultados=fecha_resultados,
            fecha_flujo_caja=fecha_flujo,
            fecha_balance=fecha_balance,
            calidad_datos="revisar" if incidencias else "ok",
            incidencias_datos=incidencias,
            url_fuente=URL_COMPANYFACTS.format(cik=cik),
            conceptos_fuente=conceptos_fuente,
        )

    def descargar(self, tickers: list[str]) -> list[Fundamentales]:  # pragma: no cover - red
        fecha = _fecha_utc(self.reloj)
        mapa = self._mapa_tickers()
        salida: list[Fundamentales] = []
        for ticker_original in dict.fromkeys(tickers):
            ticker = str(ticker_original).strip().upper()
            # Los sufijos de Yahoo representan mercados locales y no deben
            # reinterpretarse como otro ticker estadounidense.
            cik = None if "." in ticker else mapa.get(ticker)
            if cik is None:
                salida.append(self._sin_cobertura(
                    ticker, fecha, "ticker no registrado literalmente en SEC EDGAR",
                ))
                continue
            url = URL_COMPANYFACTS.format(cik=cik)
            try:
                self.pausa(0.12)
                salida.append(self._normalizar(
                    ticker, cik, self.transport(url, self.user_agent), fecha,
                ))
            except Exception as exc:
                salida.append(self._sin_cobertura(
                    ticker, fecha, f"{type(exc).__name__}: {exc}",
                ))
        return salida
