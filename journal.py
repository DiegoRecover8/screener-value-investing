"""Histórico acumulado de ejecuciones del screener (Fase 3).

Cada ejecución AÑADE filas al journal, nunca lo sobrescribe -a diferencia de
`candidatos.csv`, que es una foto de la última ejecución. Es la base para
medir en el futuro (Fase 4) cómo rindieron realmente las candidatas
pasadas: sin este histórico no hay con qué comparar el precio de entrada.

Cada fila lleva el timestamp de cuándo se calculó, no solo qué se calculó
-la misma disciplina de auditabilidad que `motivos_descarte` en
`screener_value.py`, aplicada a lo largo del tiempo en vez de a un filtro.
"""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd

RUTA_JOURNAL_DEFECTO = "journal_candidatos.csv"
RUTA_EJECUCIONES_DEFECTO = "ejecuciones_screener.csv"
UMBRAL_EXITO_DESCARGA = 0.80

COLUMNAS_CONTROL = [
    "snapshot_id", "fecha_ejecucion", "semana_iso", "origen", "oficial",
    "revision",
    "tickers_solicitados", "resultados_brutos", "descargas_correctas",
    "errores_descarga", "deduplicados", "empresas_evaluadas", "candidatas",
    "tasa_exito_descarga", "umbral_exito_minimo",
]


class ErrorIntegridadEjecucion(ValueError):
    """La ejecución no es suficientemente fiable para entrar en el histórico."""


def _momento_utc(momento) -> pd.Timestamp:
    momento = pd.Timestamp(momento)
    if momento.tzinfo is None:
        return momento.tz_localize("UTC")
    return momento.tz_convert("UTC")


def crear_snapshot_id(momento) -> str:
    """ID estable y legible para un instante de ejecución UTC."""
    return "snap_" + _momento_utc(momento).strftime("%Y%m%dT%H%M%S%fZ")


def _es_verdadero(valor) -> bool:
    """Interpreta booleanos nativos y serializados en CSV o variables de entorno."""
    if pd.isna(valor):
        return False
    if isinstance(valor, str):
        return valor.strip().lower() in {"true", "1", "sí", "si", "yes"}
    return bool(valor)


def validar_integridad_ejecucion(
    resultado: pd.DataFrame,
    tickers_solicitados: int,
    resumen_descarga: dict | None = None,
    umbral_exito: float = UMBRAL_EXITO_DESCARGA,
) -> dict:
    """Valida una ejecución antes de permitir que se añada al journal.

    Cero candidatas es un resultado válido. Lo que se rechaza es una
    ejecución estructuralmente incoherente o con demasiados fallos de
    descarga, porque convertiría una caída de Yahoo en una falsa semana sin
    candidatas.
    """
    requeridas = {"ticker", "pasa", "error_descarga", "motivos_descarte"}
    ausentes = sorted(requeridas - set(resultado.columns))
    if ausentes:
        raise ErrorIntegridadEjecucion(
            "faltan columnas requeridas: " + ", ".join(ausentes)
        )
    if tickers_solicitados <= 0:
        raise ErrorIntegridadEjecucion("el universo de entrada está vacío")
    if not 0 <= umbral_exito <= 1:
        raise ValueError("umbral_exito debe estar entre 0 y 1")
    if resultado["ticker"].astype(str).duplicated().any():
        duplicados = resultado.loc[
            resultado["ticker"].astype(str).duplicated(keep=False), "ticker"
        ].astype(str).unique()
        raise ErrorIntegridadEjecucion(
            "tickers duplicados tras deduplicar listings: " + ", ".join(duplicados)
        )
    if resultado["pasa"].isna().any():
        raise ErrorIntegridadEjecucion("hay veredictos 'pasa' sin valor")

    resumen = resumen_descarga or resultado.attrs.get("control_integridad", {})
    resultados_brutos = int(resumen.get("resultados_brutos", len(resultado)))
    errores_descarga = int(resumen.get(
        "errores_descarga",
        resultado["error_descarga"].fillna("").astype(str).str.strip().ne("").sum(),
    ))
    deduplicados = int(resumen.get(
        "deduplicados", max(resultados_brutos - len(resultado), 0),
    ))

    if resultados_brutos != tickers_solicitados:
        raise ErrorIntegridadEjecucion(
            f"el descargador devolvió {resultados_brutos} resultados para "
            f"{tickers_solicitados} tickers solicitados"
        )
    if not 0 <= errores_descarga <= resultados_brutos:
        raise ErrorIntegridadEjecucion("recuento de errores de descarga incoherente")
    if resultados_brutos - deduplicados != len(resultado):
        raise ErrorIntegridadEjecucion("recuento de listings deduplicados incoherente")

    descargas_correctas = resultados_brutos - errores_descarga
    tasa_exito = descargas_correctas / tickers_solicitados
    if tasa_exito < umbral_exito:
        raise ErrorIntegridadEjecucion(
            f"tasa de descargas correctas {tasa_exito:.1%} por debajo del "
            f"mínimo {umbral_exito:.1%} ({errores_descarga} errores de "
            f"{tickers_solicitados} tickers)"
        )

    return {
        "tickers_solicitados": tickers_solicitados,
        "resultados_brutos": resultados_brutos,
        "descargas_correctas": descargas_correctas,
        "errores_descarga": errores_descarga,
        "deduplicados": deduplicados,
        "empresas_evaluadas": len(resultado),
        "candidatas": int(resultado["pasa"].astype(bool).sum()),
        "tasa_exito_descarga": tasa_exito,
        "umbral_exito_minimo": umbral_exito,
    }


def migrar_snapshot_ids_journal(
    ruta_journal: str | Path = RUTA_JOURNAL_DEFECTO,
) -> bool:
    """Añade `snapshot_id` a un journal antiguo mediante reemplazo atómico.

    Los IDs se derivan de `fecha_ejecucion`, por lo que todas las filas de
    un snapshot reciben exactamente el mismo valor.
    """
    ruta = Path(ruta_journal)
    if not ruta.exists() or ruta.stat().st_size == 0:
        return False
    modo_original = ruta.stat().st_mode

    with ruta.open("r", newline="", encoding="utf-8") as archivo:
        texto = archivo.read()
    lineas = texto.splitlines(keepends=True)
    filas = list(csv.reader(io.StringIO(texto)))
    if not filas:
        return False
    columnas = filas[0]
    if "snapshot_id" in columnas:
        return False
    if "fecha_ejecucion" not in columnas:
        raise ErrorIntegridadEjecucion(
            f"{ruta} no contiene la columna fecha_ejecucion"
        )
    indice_fecha = columnas.index("fecha_ejecucion")
    if len(lineas) != len(filas):
        raise ErrorIntegridadEjecucion(
            f"{ruta} contiene saltos de línea dentro de campos CSV; "
            "no se puede migrar preservando literalmente sus filas"
        )

    with NamedTemporaryFile(
        "w", delete=False, dir=ruta.parent, newline="", encoding="utf-8",
    ) as temporal:
        for indice, (linea, fila) in enumerate(zip(lineas, filas)):
            cuerpo = linea.rstrip("\r\n")
            salto = linea[len(cuerpo):]
            valor = (
                "snapshot_id" if indice == 0
                else crear_snapshot_id(fila[indice_fecha])
            )
            temporal.write(f"{cuerpo},{valor}{salto}")
        ruta_temporal = Path(temporal.name)

    os.chmod(ruta_temporal, modo_original)
    os.replace(ruta_temporal, ruta)
    return True


def _validar_cabecera_csv(ruta: Path, columnas_esperadas: list[str]) -> None:
    """Impide anexar filas con un esquema distinto al ya versionado."""
    if not ruta.exists() or ruta.stat().st_size == 0:
        return
    with ruta.open(newline="", encoding="utf-8") as archivo:
        cabecera = next(csv.reader(archivo), [])
    if cabecera != columnas_esperadas:
        raise ErrorIntegridadEjecucion(
            f"esquema incompatible en {ruta}: se esperaban "
            f"{columnas_esperadas}, pero contiene {cabecera}"
        )


def registrar_ejecucion(
    resultado: pd.DataFrame,
    ruta_journal: str | Path = RUTA_JOURNAL_DEFECTO,
    momento: pd.Timestamp | None = None,
    snapshot_id: str | None = None,
) -> pd.DataFrame:
    """Añade `resultado` (salida de `incorporar_ranking_candidatos`) al journal.

    Añade `fecha_ejecucion` (UTC), `semana_iso` y `snapshot_id`. Este último
    identifica todas las filas producidas por una misma ejecución, incluso
    si hay varias en la misma semana. Escribe la cabecera solo si el archivo
    no existe; un journal antiguo se migra de forma atómica antes de añadir.

    `momento` es inyectable para tests deterministas; en producción se usa
    el instante actual en UTC. Devuelve las filas añadidas (con las tres
    columnas de auditoría), no el journal completo -para eso, léelo aparte.
    """
    momento = pd.Timestamp.now(tz="UTC") if momento is None else _momento_utc(momento)
    snapshot_id = snapshot_id or crear_snapshot_id(momento)
    filas = resultado.copy()
    iso = momento.isocalendar()
    filas.insert(0, "fecha_ejecucion", momento.isoformat())
    filas.insert(1, "semana_iso", f"{iso.year}-W{iso.week:02d}")
    filas["snapshot_id"] = snapshot_id

    ruta = Path(ruta_journal)
    migrar_snapshot_ids_journal(ruta)
    _validar_cabecera_csv(ruta, list(filas.columns))
    if ruta.exists() and ruta.stat().st_size > 0 and snapshot_id in set(
        pd.read_csv(ruta, usecols=["snapshot_id"])["snapshot_id"].astype(str)
    ):
        raise ErrorIntegridadEjecucion(f"el snapshot {snapshot_id} ya existe en {ruta}")
    escribir_cabecera = not ruta.exists() or ruta.stat().st_size == 0
    filas.to_csv(ruta, mode="a", index=False, header=escribir_cabecera)
    return filas


def registrar_control_integridad(
    control: dict,
    snapshot_id: str,
    momento,
    ruta_ejecuciones: str | Path = RUTA_EJECUCIONES_DEFECTO,
    origen: str = "local",
    oficial: bool = False,
    revision: int | None = None,
) -> pd.DataFrame:
    """Añade una fila de metadatos auditables para un snapshot válido."""
    momento = _momento_utc(momento)
    iso = momento.isocalendar()
    semana_iso = f"{iso.year}-W{iso.week:02d}"
    ruta = Path(ruta_ejecuciones)
    _validar_cabecera_csv(ruta, COLUMNAS_CONTROL)
    existentes = leer_ejecuciones(ruta)
    if snapshot_id in set(existentes.get("snapshot_id", pd.Series(dtype=str)).astype(str)):
        raise ErrorIntegridadEjecucion(
            f"el snapshot {snapshot_id} ya existe en {ruta}"
        )
    if revision is None:
        revisiones_semana = pd.to_numeric(
            existentes.loc[existentes.get("semana_iso") == semana_iso, "revision"],
            errors="coerce",
        ) if not existentes.empty else pd.Series(dtype=float)
        revision = int(revisiones_semana.max()) + 1 if revisiones_semana.notna().any() else 1
    if revision < 1:
        raise ErrorIntegridadEjecucion("revision debe ser un entero positivo")

    fila = {
        "snapshot_id": snapshot_id,
        "fecha_ejecucion": momento.isoformat(),
        "semana_iso": semana_iso,
        "origen": origen,
        "oficial": bool(oficial),
        "revision": revision,
        **control,
    }
    salida = pd.DataFrame([fila], columns=COLUMNAS_CONTROL)
    escribir_cabecera = not ruta.exists() or ruta.stat().st_size == 0
    salida.to_csv(ruta, mode="a", index=False, header=escribir_cabecera)
    return salida


def leer_ejecuciones(
    ruta_ejecuciones: str | Path = RUTA_EJECUCIONES_DEFECTO,
) -> pd.DataFrame:
    """Lee los controles de snapshots, incluidos archivos aún sin filas."""
    ruta = Path(ruta_ejecuciones)
    if not ruta.exists() or ruta.stat().st_size == 0:
        return pd.DataFrame(columns=COLUMNAS_CONTROL)
    ejecuciones = pd.read_csv(ruta)
    if ejecuciones.empty:
        return pd.DataFrame(columns=COLUMNAS_CONTROL)
    ejecuciones["fecha_ejecucion"] = pd.to_datetime(
        ejecuciones["fecha_ejecucion"], utc=True,
    )
    ejecuciones["oficial"] = ejecuciones["oficial"].map(_es_verdadero)
    ejecuciones["revision"] = pd.to_numeric(
        ejecuciones["revision"], errors="coerce",
    ).astype("Int64")
    return ejecuciones


def snapshot_ids_oficiales_efectivos(
    journal: pd.DataFrame,
    ejecuciones: pd.DataFrame,
) -> set[str]:
    """IDs que alimentan señales: una revisión oficial efectiva por semana.

    Si una semana tiene varias ejecuciones marcadas como oficiales, prevalece
    la de mayor revisión (y, en empate, la más reciente). Los snapshots
    anteriores a `ejecuciones_screener.csv` se conservan como oficiales
    legacy para no reescribir el historial ya publicado.
    """
    if journal.empty or "snapshot_id" not in journal:
        return set()
    ids_journal = set(journal["snapshot_id"].dropna().astype(str))
    if ejecuciones.empty:
        return ids_journal

    ids_controlados = set(ejecuciones["snapshot_id"].dropna().astype(str))
    ids_legacy = ids_journal - ids_controlados
    oficiales = ejecuciones[ejecuciones["oficial"].map(_es_verdadero)].copy()
    if oficiales.empty:
        return ids_legacy
    oficiales["revision"] = pd.to_numeric(oficiales["revision"], errors="coerce").fillna(0)
    oficiales["fecha_ejecucion"] = pd.to_datetime(
        oficiales["fecha_ejecucion"], utc=True,
    )
    efectivas = (
        oficiales.sort_values(["semana_iso", "revision", "fecha_ejecucion"])
        .groupby("semana_iso", as_index=False).tail(1)
    )
    return ids_legacy | set(efectivas["snapshot_id"].astype(str))


def filtrar_journal_oficial(
    journal: pd.DataFrame,
    ejecuciones: pd.DataFrame,
) -> pd.DataFrame:
    """Devuelve solo snapshots legacy u oficiales efectivos."""
    ids = snapshot_ids_oficiales_efectivos(journal, ejecuciones)
    if not ids:
        return journal.iloc[0:0].copy()
    return journal[journal["snapshot_id"].astype(str).isin(ids)].copy()


def leer_journal(ruta_journal: str | Path = RUTA_JOURNAL_DEFECTO) -> pd.DataFrame:
    """Lee el histórico completo, o un DataFrame vacío si no existe todavía."""
    ruta = Path(ruta_journal)
    if not ruta.exists():
        return pd.DataFrame()
    journal = pd.read_csv(ruta, parse_dates=["fecha_ejecucion"])
    if "snapshot_id" not in journal:
        journal["snapshot_id"] = [crear_snapshot_id(f) for f in journal["fecha_ejecucion"]]
    return journal


def extraer_ultima_ejecucion(journal: pd.DataFrame) -> pd.DataFrame:
    """Devuelve solo las filas del snapshot con el timestamp más reciente.

    Una semana ISO puede contener varias ejecuciones (por ejemplo, una prueba
    manual y la Action programada). Por eso la última ejecución se identifica
    por ``fecha_ejecucion`` exacta y no por ``semana_iso``.
    """
    if journal.empty:
        return journal.copy()
    fechas = pd.to_datetime(journal["fecha_ejecucion"], utc=True)
    mascara = fechas.eq(fechas.max())
    ultima = journal.loc[mascara].copy()
    ultima["fecha_ejecucion"] = fechas.loc[mascara]
    return ultima
