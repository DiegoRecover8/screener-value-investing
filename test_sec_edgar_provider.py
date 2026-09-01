"""Tests sin red del adaptador selectivo SEC EDGAR."""

from datetime import datetime, timezone

import pytest

from providers import ProveedorSecEdgar
from providers.sec_edgar_provider import URL_COMPANYFACTS, URL_TICKERS_SEC


def _duracion(valor, concepto="", unidad="USD"):
    return {
        "units": {unidad: [{
            "start": "2025-01-01", "end": "2025-12-31", "val": valor,
            "form": "10-K", "fp": "FY", "filed": "2026-02-15",
            "accn": "0001",
        }]},
    }


def _instante(valor, unidad="USD"):
    return {
        "units": {unidad: [{
            "end": "2025-12-31", "val": valor, "form": "10-K", "fp": "FY",
            "filed": "2026-02-15", "accn": "0001",
        }]},
    }


def _companyfacts():
    return {
        "entityName": "Acme Corporation",
        "facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": _duracion(1_000),
            "NetIncomeLoss": _duracion(100),
            "OperatingIncomeLoss": _duracion(150),
            "NetCashProvidedByUsedInOperatingActivities": _duracion(130),
            "PaymentsToAcquirePropertyPlantAndEquipment": _duracion(30),
            "LongTermDebtAndFinanceLeaseObligations": _instante(200),
            "CashAndCashEquivalentsAtCarryingValue": _instante(80),
            "StockholdersEquity": _instante(500),
            "InterestExpenseNonOperating": _duracion(10),
        }},
    }


class Transporte:
    def __init__(self):
        self.urls = []

    def __call__(self, url, user_agent):
        self.urls.append((url, user_agent))
        if url == URL_TICKERS_SEC:
            return {"0": {"ticker": "AAA", "cik_str": 1234}}
        if url == URL_COMPANYFACTS.format(cik=1234):
            return _companyfacts()
        raise AssertionError(f"URL inesperada: {url}")


def _proveedor(transporte):
    return ProveedorSecEdgar(
        "screener-educativo contacto@example.com",
        transport=transporte,
        reloj=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def test_extrae_componentes_anuales_y_deriva_fcf():
    transporte = Transporte()

    datos = _proveedor(transporte).descargar(["AAA"])[0]

    assert datos.ticker == "AAA"
    assert datos.proveedor_datos == "sec_edgar"
    assert datos.tipo_periodo == "ANUAL"
    assert datos.divisa_financiera == "USD"
    assert datos.ingresos == 1_000
    assert datos.ebit == 150
    assert datos.free_cash_flow == 100
    assert datos.total_debt == 200
    assert datos.fecha_resultados == "2025-12-31"
    assert datos.fecha_balance == "2025-12-31"
    assert datos.url_fuente.endswith("CIK0000001234.json")
    assert datos.conceptos_fuente["ebit"] == "OperatingIncomeLoss"
    assert "NetCashProvided" in datos.conceptos_fuente["free_cash_flow"]


def test_no_elimina_sufijos_yahoo_ni_enlaza_otra_empresa():
    transporte = Transporte()

    datos = _proveedor(transporte).descargar(["AAA.MC"])[0]

    assert datos.calidad_datos == "error"
    assert "no registrado literalmente" in datos.error_descarga
    assert [url for url, _ in transporte.urls] == [URL_TICKERS_SEC]


def test_conserva_una_fila_si_no_hay_cobertura():
    datos = _proveedor(Transporte()).descargar(["ZZZ"])[0]

    assert datos.ticker == "ZZZ"
    assert datos.proveedor_datos == "sec_edgar"
    assert datos.tipo_periodo == "ANUAL"
    assert datos.error_descarga


def test_exige_user_agent_declarado():
    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        ProveedorSecEdgar("")
