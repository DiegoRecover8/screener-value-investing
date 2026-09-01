"""Contrato y modelo normalizado para proveedores fundamentales.

El screener consume este modelo, no objetos propios de una API. Así se evita
que nombres, periodos o convenciones de un proveedor se filtren hasta las
métricas y se deja preparada una futura verificación cruzada.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


TASA_IMPOSITIVA_DEFECTO = 0.25

COLUMNAS_PROCEDENCIA = [
    "proveedor_datos",
    "fecha_consulta_utc",
    "tipo_periodo",
    "fecha_resultados",
    "fecha_flujo_caja",
    "fecha_balance",
    "calidad_datos",
    "incidencias_datos",
]


@dataclass
class Fundamentales:
    """Datos normalizados junto con su procedencia y calidad observable."""

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
    proveedor_datos: str = ""
    fecha_consulta_utc: str = ""
    tipo_periodo: str = ""
    fecha_resultados: str = ""
    fecha_flujo_caja: str = ""
    fecha_balance: str = ""
    calidad_datos: str = "ok"
    incidencias_datos: str = ""


class ProveedorFundamentales(Protocol):
    """Interfaz mínima que debe cumplir cualquier fuente de fundamentales."""

    nombre: str

    def descargar(self, tickers: list[str]) -> list[Fundamentales]:
        """Devuelve una observación por ticker normalizado y único."""
        ...
