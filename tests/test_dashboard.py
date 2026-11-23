"""Smoke tests for the bilingual Streamlit research interface."""

from pathlib import Path

from streamlit.testing.v1 import AppTest


RUTA_DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard.py"


def test_dashboard_arranca_en_ingles_sin_consultar_la_red():
    app = AppTest.from_file(str(RUTA_DASHBOARD), default_timeout=15).run()

    assert not app.exception
    assert any("Reproducible equity research" in bloque.value for bloque in app.markdown)
    assert any(boton.label == "🔄 Download & calculate" for boton in app.button)


def test_selector_cambia_la_interfaz_a_espanol():
    app = AppTest.from_file(str(RUTA_DASHBOARD), default_timeout=15).run()
    boton_es = next(boton for boton in app.button if boton.label == "🇪🇸 ES")
    boton_es.click().run()

    assert not app.exception
    assert any("Investigación bursátil" in bloque.value for bloque in app.markdown)
    assert any(boton.label == "🔄 Descargar y calcular" for boton in app.button)


def test_bloqueo_del_proveedor_muestra_error_y_no_queda_en_cache():
    """Regresión: un bloqueo de Yahoo aparecía como "0 candidatas" y se cacheaba 24 h."""
    from unittest.mock import patch

    from providers import Fundamentales

    bloqueado = [
        Fundamentales(ticker=t, error_descarga="CONNECT tunnel failed, response 403",
                      calidad_datos="error", divisa_consistente=False)
        for t in ("SAN.MC", "ITX.MC", "TEF.MC", "SIE.DE", "ASML.AS")
    ]
    sano = [Fundamentales(ticker=f.ticker, nombre=f.ticker, divisa_cotizacion="EUR",
                          divisa_financiera="EUR") for f in bloqueado]
    app = AppTest.from_file(str(RUTA_DASHBOARD), default_timeout=15).run()
    descargar = next(b for b in app.button if b.label == "🔄 Download & calculate")

    with patch("screener_value.descargar_fundamentales", return_value=bloqueado) as mock:
        descargar.click().run()
    assert not app.exception
    assert mock.call_count == 1
    assert any("data provider failed for 5 of 5" in e.value for e in app.error)
    assert not any(m.label == "Companies evaluated" for m in app.metric)

    with patch("screener_value.descargar_fundamentales", return_value=sano) as mock:
        next(b for b in app.button if b.label == "🔄 Download & calculate").click().run()
    assert not app.exception
    assert mock.call_count == 1  # no se sirvió el fallo desde la caché
    assert not app.error
    assert any(m.label == "Companies evaluated" for m in app.metric)
