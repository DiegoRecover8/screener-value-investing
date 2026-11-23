"""Configuración común de los tests.

Varios tests leen artefactos versionados con rutas relativas a la raíz del
repositorio (manifest de universos, workflow, .gitignore), algunos ya en
``setUpClass``. Fijar el directorio de trabajo para toda la sesión hace que
la suite funcione se lance desde donde se lance.
"""

import os
from pathlib import Path

import pytest

RAIZ_REPOSITORIO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _directorio_raiz():
    anterior = Path.cwd()
    os.chdir(RAIZ_REPOSITORIO)
    yield
    os.chdir(anterior)
