"""Smoke tests for the bilingual Streamlit research interface."""

from pathlib import Path

from streamlit.testing.v1 import AppTest


RUTA_DASHBOARD = Path(__file__).with_name("dashboard.py")


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
