"""Tests puros del artefacto de verificación secundaria."""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from providers import Fundamentales
from verificacion_candidatas import (
    CAMPOS_COMPARABLES,
    crear_verificacion,
    registrar_verificacion,
)


def _fundamentales(ticker="AAA", proveedor="primario", **cambios):
    base = dict(
        ticker=ticker,
        proveedor_datos=proveedor,
        tipo_periodo="ANUAL",
        fecha_resultados="2025-12-31",
        fecha_flujo_caja="2025-12-31",
        fecha_balance="2025-12-31",
        divisa_financiera="EUR",
        ingresos=100.0,
        net_income=100.0,
        ebit=100.0,
        free_cash_flow=100.0,
        total_debt=100.0,
        cash=100.0,
        equity=100.0,
        gasto_intereses=100.0,
        url_fuente="https://example.test/fuente",
    )
    base.update(cambios)
    return Fundamentales(**base)


def _crear(primaria, secundaria):
    return crear_verificacion(
        [primaria], [secundaria], "snap_20260901T120000000000Z",
        momento=datetime(2026, 9, 1, tzinfo=timezone.utc),
    ).set_index("metrica")


def test_clasifica_diferencias_sin_mezclar_componentes():
    primaria = _fundamentales()
    secundaria = _fundamentales(
        proveedor="secundario", ingresos=105, net_income=115, ebit=130,
    )

    resultado = _crear(primaria, secundaria)

    assert resultado.loc["ingresos", "estado"] == "verificado"
    assert resultado.loc["net_income", "estado"] == "advertencia"
    assert resultado.loc["ebit", "estado"] == "discrepancia_material"
    assert resultado.loc["ebit", "diferencia_pct"] == 0.30
    assert len(resultado) == len(CAMPOS_COMPARABLES)


def test_periodo_divisa_fecha_y_ausencia_impiden_comparacion():
    primaria = _fundamentales()
    periodo = _crear(primaria, _fundamentales(proveedor="sec", tipo_periodo="TTM"))
    divisa = _crear(primaria, _fundamentales(proveedor="sec", divisa_financiera="USD"))
    fecha = _crear(primaria, _fundamentales(
        proveedor="sec", fecha_resultados="2024-12-31",
    ))
    ausente = _crear(primaria, _fundamentales(proveedor="sec", ingresos=np.nan))

    assert periodo.loc["ingresos", "estado"] == "no_comparable"
    assert divisa.loc["ingresos", "estado"] == "no_comparable"
    assert fecha.loc["ingresos", "estado"] == "no_comparable"
    assert ausente.loc["ingresos", "estado"] == "sin_dato"


def test_error_secundario_se_conserva_como_sin_cobertura():
    secundaria = _fundamentales(
        proveedor="sec_edgar", error_descarga="ticker no registrado",
    )
    resultado = _crear(_fundamentales(), secundaria)

    assert set(resultado["estado"]) == {"sin_cobertura"}
    assert resultado.loc["ingresos", "detalle"] == "ticker no registrado"


def test_registro_es_atomico_e_idempotente_por_snapshot_y_metrica():
    verificacion = crear_verificacion(
        [_fundamentales()], [_fundamentales(proveedor="sec")],
        "snap_20260901T120000000000Z",
    )
    with TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "verificacion.csv"
        registrar_verificacion(verificacion, ruta)
        registrado = registrar_verificacion(verificacion, ruta)

        releido = pd.read_csv(ruta)

    assert len(registrado) == len(CAMPOS_COMPARABLES)
    assert len(releido) == len(CAMPOS_COMPARABLES)
