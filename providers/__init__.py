"""Proveedores intercambiables de datos fundamentales."""

from .base import (
    COLUMNAS_PROCEDENCIA,
    TASA_IMPOSITIVA_DEFECTO,
    Fundamentales,
    ProveedorFundamentales,
)
from .yfinance_provider import ProveedorYFinance

__all__ = [
    "COLUMNAS_PROCEDENCIA",
    "TASA_IMPOSITIVA_DEFECTO",
    "Fundamentales",
    "ProveedorFundamentales",
    "ProveedorYFinance",
]
