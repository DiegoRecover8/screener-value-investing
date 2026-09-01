"""Punto de entrada para la ejecución automatizada semanal (GitHub Actions).

A propósito NO reconstruye el universo desde Yahoo cada semana: usa la
versión oficial activa en `universos/manifest.json`. `universo.txt` se
mantiene como espejo compatible y debe coincidir exactamente con ella.
Reconstruir el universo en cada ejecución produciría un objetivo móvil -la Fase 4
(seguimiento longitudinal) necesita comparar la MISMA cesta de candidatas a
lo largo del tiempo, no una recalculada cada vez con criterios de
descubrimiento distintos. Activar una versión nueva sigue siendo una decisión
manual y deliberada, con las herramientas de `universos_yfinance.py` y
`gestionar_universos.py`.

Uso: python ejecutar_semanal.py <archivo_tickers.txt> [ruta_journal.csv]
       [ruta_ejecuciones.csv]
     python ejecutar_semanal.py --universo-activo [ruta_journal.csv]
       [ruta_ejecuciones.csv]
     python ejecutar_semanal.py --universo-id <uv_...> [ruta_journal.csv]
       [ruta_ejecuciones.csv]  # solo pruebas no oficiales
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

from journal import (
    RUTA_EJECUCIONES_DEFECTO,
    RUTA_JOURNAL_DEFECTO,
    crear_snapshot_id,
    registrar_control_integridad,
    registrar_ejecucion,
    validar_integridad_ejecucion,
)
from screener_value import ejecutar
from providers import ProveedorSecEdgar
from verificacion_candidatas import (
    RUTA_VERIFICACION_DEFECTO,
    crear_verificacion,
    imprimir_resumen_verificacion,
    registrar_verificacion,
)
from universos_versionados import (
    RUTA_ESPEJO_DEFECTO,
    RUTA_MANIFEST_DEFECTO,
    ErrorUniversoVersionado,
    calcular_hash_universo,
    cargar_tickers,
    cargar_universo_activo,
    cargar_universo_registrado,
)


def _variable_booleana(nombre: str, defecto: bool = False) -> bool:
    valor = os.environ.get(nombre)
    if valor is None:
        return defecto
    return valor.strip().lower() in {"true", "1", "sí", "si", "yes"}


def _metadatos_github() -> dict[str, str]:
    """Devuelve la identidad del run actual, o valores vacíos fuera de GitHub."""
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    servidor = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    repositorio = os.environ.get("GITHUB_REPOSITORY", "").strip("/")
    run_url = (
        f"{servidor}/{repositorio}/actions/runs/{run_id}"
        if servidor and repositorio and run_id else ""
    )
    return {
        "github_run_id": run_id,
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "github_run_url": run_url,
        "github_sha": os.environ.get("GITHUB_SHA", ""),
    }


def _verificar_candidatas_en_sombra(
    resultado: pd.DataFrame,
    snapshot_id: str,
    momento: pd.Timestamp,
) -> None:
    """Verifica solo candidatas; cualquier fallo queda aislado del oficial."""
    if not _variable_booleana("SCREENER_VERIFICAR_CANDIDATAS"):
        print("Verificación secundaria: desactivada.")
        return
    primarias = resultado.attrs.get("fundamentales_candidatas")
    if primarias is None or primarias.empty:
        print("Verificación secundaria: no hay candidatas que consultar.")
        return
    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not user_agent:
        print(
            "AVISO verificación secundaria omitida: falta SEC_USER_AGENT "
            "(nombre del proyecto y contacto)."
        )
        return
    try:
        proveedor = ProveedorSecEdgar(user_agent=user_agent)
        secundarias = proveedor.descargar(
            primarias["ticker"].astype(str).tolist(),
        )
        verificacion = crear_verificacion(
            primarias, secundarias, snapshot_id=snapshot_id, momento=momento,
        )
        ruta = os.environ.get(
            "SCREENER_RUTA_VERIFICACION", RUTA_VERIFICACION_DEFECTO,
        )
        registrar_verificacion(verificacion, ruta)
        imprimir_resumen_verificacion(verificacion)
        print(f"Verificación: {ruta}")
    except Exception as exc:  # el modo sombra nunca invalida el snapshot
        print(
            "AVISO verificación secundaria no completada; el resultado "
            f"oficial no cambia: {type(exc).__name__}: {exc}"
        )


def _resolver_universo(
    argumento: str,
    universe_id: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Resuelve el universo oficial o identifica una lista ad hoc por su hash."""
    ruta_manifest = Path(os.environ.get(
        "SCREENER_MANIFEST", str(RUTA_MANIFEST_DEFECTO),
    ))
    ruta_espejo = Path(os.environ.get(
        "SCREENER_UNIVERSO_ESPEJO", str(RUTA_ESPEJO_DEFECTO),
    ))
    if argumento == "--universo-activo":
        activo = cargar_universo_activo(ruta_manifest, ruta_espejo)
        return list(activo.tickers), {
            "universe_id": activo.universe_id,
            "universe_sha256": activo.sha256,
            "universe_path": activo.ruta.as_posix(),
        }
    if argumento == "--universo-id":
        if universe_id is None:
            raise ErrorUniversoVersionado("--universo-id exige un ID")
        registrado = cargar_universo_registrado(
            universe_id, ruta_manifest, ruta_espejo,
        )
        return list(registrado.tickers), {
            "universe_id": registrado.universe_id,
            "universe_sha256": registrado.sha256,
            "universe_path": registrado.ruta.as_posix(),
        }

    ruta = Path(argumento)
    tickers = cargar_tickers(ruta)
    hash_universo = calcular_hash_universo(tickers)
    return tickers, {
        "universe_id": f"adhoc_{hash_universo[:12]}",
        "universe_sha256": hash_universo,
        "universe_path": ruta.as_posix(),
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(
            f"Uso: python {Path(__file__).name} "
            "<archivo_tickers.txt|--universo-activo|--universo-id ID> "
            "[ruta_journal.csv] [ruta_ejecuciones.csv]"
        )
        sys.exit(1)

    argumento_universo = sys.argv[1]
    if argumento_universo == "--universo-id":
        if len(sys.argv) < 3:
            raise ErrorUniversoVersionado("--universo-id exige un ID")
        universe_id = sys.argv[2]
        indice_rutas = 3
    else:
        universe_id = None
        indice_rutas = 2
    ruta_journal = (
        sys.argv[indice_rutas]
        if len(sys.argv) > indice_rutas else RUTA_JOURNAL_DEFECTO
    )
    ruta_ejecuciones = (
        sys.argv[indice_rutas + 1]
        if len(sys.argv) > indice_rutas + 1 else RUTA_EJECUCIONES_DEFECTO
    )
    origen = os.environ.get(
        "SCREENER_ORIGEN", os.environ.get("GITHUB_EVENT_NAME", "local"),
    )
    oficial = _variable_booleana("SCREENER_OFICIAL")
    tickers, metadatos_universo = _resolver_universo(
        argumento_universo, universe_id,
    )
    if oficial and argumento_universo != "--universo-activo":
        raise ErrorUniversoVersionado(
            "una ejecución oficial exige --universo-activo; "
            "las listas ad hoc y los IDs explícitos solo pueden usarse como prueba"
        )

    resultado = ejecutar(tickers, salida_csv="candidatos.csv")
    control = validar_integridad_ejecucion(resultado, len(tickers))
    momento = pd.Timestamp.now(tz="UTC")
    snapshot_id = crear_snapshot_id(momento)

    filas_nuevas = registrar_ejecucion(
        resultado, ruta_journal, momento=momento, snapshot_id=snapshot_id,
    )
    metadatos = registrar_control_integridad(
        control, snapshot_id, momento, ruta_ejecuciones,
        origen=origen, oficial=oficial,
        **_metadatos_github(), **metadatos_universo,
    )
    _verificar_candidatas_en_sombra(resultado, snapshot_id, momento)
    print(
        f"\nSnapshot válido {snapshot_id}: {len(filas_nuevas)} empresas, "
        f"{control['descargas_correctas']}/{control['tickers_solicitados']} "
        f"descargas correctas ({control['tasa_exito_descarga']:.1%})."
    )
    print(
        "Calidad contable: "
        f"ok={control['datos_ok']}, revisar={control['datos_revisar']}, "
        f"inutilizables/error={control['datos_inutilizables']} "
        f"(proveedor: {control['proveedor_datos'] or 'no registrado'})."
    )
    estado = "oficial" if oficial else "prueba no oficial"
    print(f"Clasificación: {estado}, revisión {metadatos['revision'].iloc[0]}.")
    print(
        f"Universo: {metadatos_universo['universe_id']} "
        f"({metadatos_universo['universe_sha256'][:12]})."
    )
    print(f"Journal: {ruta_journal}\nControl: {ruta_ejecuciones}")


if __name__ == "__main__":  # pragma: no cover - CLI
    main()
