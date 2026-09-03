"""Stable, side-effect-free application API for external clients.

This module is the boundary intended for user interfaces and other Python
applications.  It does not write CSV files, update the official journal or
read GitHub Actions environment variables.  Network access only happens when
``analyze_universe`` is called and is delegated to an injectable provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from prompt_llm import generar_prompt_interpretacion
from providers import Fundamentales, ProveedorFundamentales
from screener_value import (
    UMBRALES,
    aplicar_filtros,
    calcular_metricas,
    deduplicar_listings,
    descargar_fundamentales,
    incorporar_ranking_candidatos,
    resumir_incidencias_calidad,
)


API_SCHEMA_VERSION = "1.0"


def normalize_tickers(tickers: Iterable[str]) -> list[str]:
    """Normalize, de-duplicate and validate ticker input preserving order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        ticker = str(raw).strip().upper()
        if not ticker or ticker in seen:
            continue
        if len(ticker) > 32 or any(char.isspace() for char in ticker):
            raise ValueError(f"Invalid ticker: {raw!r}")
        normalized.append(ticker)
        seen.add(ticker)
    if not normalized:
        raise ValueError("At least one ticker is required")
    return normalized


def resolve_thresholds(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a validated copy of the default thresholds with overrides."""
    thresholds = dict(UMBRALES)
    if not overrides:
        return thresholds
    unknown = sorted(set(overrides) - set(thresholds))
    if unknown:
        raise ValueError(f"Unknown thresholds: {', '.join(unknown)}")
    thresholds.update(overrides)
    if int(thresholds["min_empresas_sector"]) < 1:
        raise ValueError("min_empresas_sector must be at least 1")
    return thresholds


def _json_value(value: Any) -> Any:
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    return value


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {key: _json_value(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


@dataclass(frozen=True)
class AnalysisResult:
    """Structured output returned to a UI or API transport layer."""

    requested_tickers: tuple[str, ...]
    thresholds: dict[str, Any]
    evaluated: pd.DataFrame
    raw_result_count: int
    download_errors: int
    deduplicated_listings: int

    @property
    def candidates(self) -> pd.DataFrame:
        return self.evaluated[self.evaluated["pasa"]].copy()

    @property
    def discarded(self) -> pd.DataFrame:
        return self.evaluated[~self.evaluated["pasa"]].copy()

    @property
    def summary(self) -> dict[str, Any]:
        quality = resumir_incidencias_calidad(self.evaluated)
        return {
            "requested_tickers": len(self.requested_tickers),
            "raw_results": self.raw_result_count,
            "evaluated_companies": len(self.evaluated),
            "candidates": len(self.candidates),
            "discarded": len(self.discarded),
            "download_errors": self.download_errors,
            "deduplicated_listings": self.deduplicated_listings,
            "quality_incidents": quality,
        }

    @property
    def conclusion(self) -> dict[str, Any]:
        """Return a deterministic screening conclusion, never investment advice."""
        candidate_count = len(self.candidates)
        evaluated_count = len(self.evaluated)
        return {
            "status": "candidates_found" if candidate_count else "no_candidates",
            "text": (
                f"{candidate_count} of {evaluated_count} evaluated companies "
                "passed every configured quantitative criterion. This result "
                "requires primary-source review and is not an investment recommendation."
            ),
        }

    def to_dict(self, include_discarded: bool = True) -> dict[str, Any]:
        """Return a JSON-compatible representation with an explicit schema."""
        payload = {
            "schema_version": API_SCHEMA_VERSION,
            "requested_tickers": list(self.requested_tickers),
            "thresholds": dict(self.thresholds),
            "summary": self.summary,
            "conclusion": self.conclusion,
            "candidates": _records(self.candidates),
            "prompt": generar_prompt_interpretacion(self.candidates),
        }
        if include_discarded:
            payload["discarded"] = _records(self.discarded)
        return payload


def analyze_fundamentals(
    fundamentals: list[Fundamentales] | pd.DataFrame,
    *,
    requested_tickers: Iterable[str] | None = None,
    thresholds: Mapping[str, Any] | None = None,
) -> AnalysisResult:
    """Run the deterministic calculation/filter/ranking pipeline."""
    resolved = resolve_thresholds(thresholds)
    metrics = calcular_metricas(fundamentals, resolved)
    raw_count = len(metrics)
    errors = int(
        metrics.get("error_descarga", pd.Series(dtype=str))
        .fillna("").astype(str).str.strip().ne("").sum()
    )
    metrics = deduplicar_listings(metrics)
    evaluated = incorporar_ranking_candidatos(aplicar_filtros(metrics, resolved))
    if requested_tickers is None:
        requested = tuple(evaluated.get("ticker", pd.Series(dtype=str)).astype(str))
    else:
        requested = tuple(normalize_tickers(requested_tickers))
    return AnalysisResult(
        requested_tickers=requested,
        thresholds=resolved,
        evaluated=evaluated,
        raw_result_count=raw_count,
        download_errors=errors,
        deduplicated_listings=raw_count - len(metrics),
    )


def analyze_universe(
    tickers: Iterable[str],
    *,
    thresholds: Mapping[str, Any] | None = None,
    provider: ProveedorFundamentales | None = None,
) -> AnalysisResult:
    """Download and analyze an ad-hoc universe without persisting artifacts."""
    normalized = normalize_tickers(tickers)
    fundamentals = descargar_fundamentales(normalized, proveedor=provider)
    return analyze_fundamentals(
        fundamentals,
        requested_tickers=normalized,
        thresholds=thresholds,
    )
