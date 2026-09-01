"""Comparación selectiva de candidatas contra una segunda fuente.

Este módulo es deliberadamente *shadow*: describe coincidencias y diferencias,
pero nunca modifica ``pasa``, el ranking ni el journal oficial. Las ratios no
mezclan piezas de proveedores; se comparan componentes normalizados obtenidos
independientemente por cada fuente.
"""

from __future__ import annotations

import csv
import os
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import pandas as pd

from providers import Fundamentales


RUTA_VERIFICACION_DEFECTO = "verificacion_candidatas.csv"
TOLERANCIA_VERIFICADA = 0.10
TOLERANCIA_ADVERTENCIA = 0.25
DESFASE_MAXIMO_DIAS = 45

CAMPOS_COMPARABLES = {
    "ingresos": "fecha_resultados",
    "net_income": "fecha_resultados",
    "ebit": "fecha_resultados",
    "free_cash_flow": "fecha_flujo_caja",
    "total_debt": "fecha_balance",
    "cash": "fecha_balance",
    "equity": "fecha_balance",
    "gasto_intereses": "fecha_resultados",
}

COLUMNAS_VERIFICACION = [
    "snapshot_id", "ticker", "proveedor_primario", "proveedor_secundario",
    "tipo_periodo_primario", "tipo_periodo_secundario",
    "fecha_periodo_primario", "fecha_periodo_secundario",
    "divisa_primaria", "divisa_secundaria", "metrica",
    "valor_primario", "valor_secundario", "diferencia_pct", "estado",
    "detalle", "url_fuente", "verificado_en_utc",
]


class ErrorVerificacion(ValueError):
    """La entrada o el historial de verificación no es coherente."""


def _es_na(valor) -> bool:
    try:
        return bool(pd.isna(valor))
    except (TypeError, ValueError):
        return True


def _texto(valor) -> str:
    return "" if _es_na(valor) else str(valor).strip()


def _fecha(valor) -> pd.Timestamp | None:
    fecha = pd.to_datetime(valor, errors="coerce", utc=True)
    return None if pd.isna(fecha) else pd.Timestamp(fecha)


def _como_dataframe(datos: list[Fundamentales] | pd.DataFrame) -> pd.DataFrame:
    if isinstance(datos, pd.DataFrame):
        return datos.copy()
    return pd.DataFrame([asdict(fila) for fila in datos])


def _comparar_campo(
    primaria: pd.Series,
    secundaria: pd.Series | None,
    campo: str,
    columna_fecha: str,
) -> dict:
    """Compara un componente solo si divisa, periodo y fecha son compatibles."""
    base = {
        "tipo_periodo_primario": _texto(primaria.get("tipo_periodo")),
        "tipo_periodo_secundario": "" if secundaria is None else _texto(
            secundaria.get("tipo_periodo")
        ),
        "fecha_periodo_primario": _texto(primaria.get(columna_fecha)),
        "fecha_periodo_secundario": "" if secundaria is None else _texto(
            secundaria.get(columna_fecha)
        ),
        "divisa_primaria": _texto(primaria.get("divisa_financiera")).upper(),
        "divisa_secundaria": "" if secundaria is None else _texto(
            secundaria.get("divisa_financiera")
        ).upper(),
        "metrica": campo,
        "valor_primario": primaria.get(campo, np.nan),
        "valor_secundario": np.nan if secundaria is None else secundaria.get(
            campo, np.nan
        ),
        "diferencia_pct": np.nan,
        "estado": "",
        "detalle": "",
        "url_fuente": "" if secundaria is None else _texto(
            secundaria.get("url_fuente")
        ),
    }
    if secundaria is None:
        base.update(estado="sin_cobertura", detalle="ticker sin cobertura secundaria")
        return base
    error = _texto(secundaria.get("error_descarga"))
    if error:
        base.update(estado="sin_cobertura", detalle=error)
        return base

    valor_primario = base["valor_primario"]
    valor_secundario = base["valor_secundario"]
    if _es_na(valor_primario) or _es_na(valor_secundario):
        faltan = []
        if _es_na(valor_primario):
            faltan.append("primario")
        if _es_na(valor_secundario):
            faltan.append("secundario")
        base.update(
            estado="sin_dato",
            detalle=f"{campo} ausente en " + " y ".join(faltan),
        )
        return base

    if not base["divisa_primaria"] or not base["divisa_secundaria"]:
        base.update(estado="no_comparable", detalle="divisa financiera ausente")
        return base
    if base["divisa_primaria"] != base["divisa_secundaria"]:
        base.update(
            estado="no_comparable",
            detalle=(
                f"divisas distintas {base['divisa_primaria']}/"
                f"{base['divisa_secundaria']}"
            ),
        )
        return base
    if (
        not base["tipo_periodo_primario"]
        or not base["tipo_periodo_secundario"]
        or base["tipo_periodo_primario"] != base["tipo_periodo_secundario"]
    ):
        base.update(
            estado="no_comparable",
            detalle=(
                "periodos distintos o ausentes "
                f"{base['tipo_periodo_primario'] or '?'}/"
                f"{base['tipo_periodo_secundario'] or '?'}"
            ),
        )
        return base

    fecha_primaria = _fecha(base["fecha_periodo_primario"])
    fecha_secundaria = _fecha(base["fecha_periodo_secundario"])
    if fecha_primaria is None or fecha_secundaria is None:
        base.update(estado="no_comparable", detalle="fecha contable ausente")
        return base
    desfase = abs((fecha_primaria - fecha_secundaria).days)
    if desfase > DESFASE_MAXIMO_DIAS:
        base.update(
            estado="no_comparable",
            detalle=f"fechas contables desalineadas ({desfase} días)",
        )
        return base

    primario = float(valor_primario)
    secundario = float(valor_secundario)
    if not np.isfinite(primario) or not np.isfinite(secundario):
        base.update(estado="sin_dato", detalle="valor no finito")
        return base
    if primario == 0:
        if secundario == 0:
            diferencia = 0.0
        else:
            base.update(
                estado="no_comparable",
                detalle="el valor primario es cero; diferencia relativa indefinida",
            )
            return base
    else:
        diferencia = abs(secundario - primario) / abs(primario)

    base["diferencia_pct"] = diferencia
    if diferencia <= TOLERANCIA_VERIFICADA:
        base.update(estado="verificado", detalle="diferencia dentro del 10 %")
    elif diferencia <= TOLERANCIA_ADVERTENCIA:
        base.update(estado="advertencia", detalle="diferencia entre el 10 % y el 25 %")
    else:
        base.update(estado="discrepancia_material", detalle="diferencia superior al 25 %")
    return base


def crear_verificacion(
    primarias: list[Fundamentales] | pd.DataFrame,
    secundarias: list[Fundamentales] | pd.DataFrame,
    snapshot_id: str,
    momento=None,
) -> pd.DataFrame:
    """Crea una fila por candidata y componente; no calcula un veredicto bursátil."""
    if not snapshot_id or not str(snapshot_id).startswith("snap_"):
        raise ErrorVerificacion("snapshot_id ausente o no válido")
    primaria_df = _como_dataframe(primarias)
    secundaria_df = _como_dataframe(secundarias)
    if primaria_df.empty:
        return pd.DataFrame(columns=COLUMNAS_VERIFICACION)
    if "ticker" not in primaria_df:
        raise ErrorVerificacion("la fuente primaria no contiene ticker")
    if not secundaria_df.empty and "ticker" not in secundaria_df:
        raise ErrorVerificacion("la fuente secundaria no contiene ticker")
    if primaria_df["ticker"].astype(str).duplicated().any():
        raise ErrorVerificacion("hay tickers primarios duplicados")
    if not secundaria_df.empty and secundaria_df["ticker"].astype(str).duplicated().any():
        raise ErrorVerificacion("hay tickers secundarios duplicados")

    secundarias_por_ticker = {
        str(fila["ticker"]).strip().upper(): fila
        for _, fila in secundaria_df.iterrows()
    }
    verificado_en = pd.Timestamp.now(tz="UTC") if momento is None else pd.Timestamp(momento)
    if verificado_en.tzinfo is None:
        verificado_en = verificado_en.tz_localize("UTC")
    else:
        verificado_en = verificado_en.tz_convert("UTC")

    filas: list[dict] = []
    for _, primaria in primaria_df.iterrows():
        ticker = str(primaria["ticker"]).strip().upper()
        secundaria = secundarias_por_ticker.get(ticker)
        proveedor_primario = _texto(primaria.get("proveedor_datos")) or "desconocido"
        proveedor_secundario = (
            "sin_cobertura" if secundaria is None
            else _texto(secundaria.get("proveedor_datos")) or "desconocido"
        )
        for campo, columna_fecha in CAMPOS_COMPARABLES.items():
            fila = _comparar_campo(primaria, secundaria, campo, columna_fecha)
            conceptos = (
                {} if secundaria is None else secundaria.get("conceptos_fuente", {})
            )
            if isinstance(conceptos, dict) and conceptos.get(campo):
                sufijo = f"concepto secundario: {conceptos[campo]}"
                fila["detalle"] = (
                    f"{fila['detalle']}; {sufijo}" if fila["detalle"] else sufijo
                )
            fila.update(
                snapshot_id=snapshot_id,
                ticker=ticker,
                proveedor_primario=proveedor_primario,
                proveedor_secundario=proveedor_secundario,
                verificado_en_utc=verificado_en.isoformat(),
            )
            filas.append(fila)
    return pd.DataFrame(filas, columns=COLUMNAS_VERIFICACION)


def registrar_verificacion(
    nuevas: pd.DataFrame,
    ruta: str | Path = RUTA_VERIFICACION_DEFECTO,
) -> pd.DataFrame:
    """Añade resultados de forma atómica e idempotente por snapshot/campo."""
    ruta = Path(ruta)
    if list(nuevas.columns) != COLUMNAS_VERIFICACION:
        raise ErrorVerificacion("esquema de verificación no válido")
    existentes = pd.DataFrame(columns=COLUMNAS_VERIFICACION)
    if ruta.exists() and ruta.stat().st_size:
        with ruta.open(newline="", encoding="utf-8") as archivo:
            cabecera = next(csv.reader(archivo), [])
        if cabecera != COLUMNAS_VERIFICACION:
            raise ErrorVerificacion(f"esquema incompatible en {ruta}")
        existentes = pd.read_csv(ruta)
    combinado = (
        nuevas.copy().reset_index(drop=True)
        if existentes.empty
        else pd.concat([existentes, nuevas], ignore_index=True)
    )
    combinado = combinado.drop_duplicates(
        ["snapshot_id", "ticker", "proveedor_secundario", "metrica"],
        keep="last",
    )
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", delete=False, dir=ruta.parent, newline="", encoding="utf-8",
    ) as temporal:
        combinado.to_csv(temporal, index=False)
        ruta_temporal = Path(temporal.name)
    os.replace(ruta_temporal, ruta)
    return combinado


def imprimir_resumen_verificacion(verificacion: pd.DataFrame) -> None:
    if verificacion.empty:
        print("Verificación secundaria: no hay candidatas.")
        return
    print(
        "Verificación secundaria en modo sombra "
        "(no altera la selección oficial):"
    )
    for estado, cantidad in verificacion["estado"].value_counts().items():
        print(f"  - {estado}: {cantidad}")
