"""Selección reproducible desde un snapshot amplio hacia un draft oficial.

El selector trabaja exclusivamente con artefactos ya publicados: no consulta
la red y no activa universos. La decisión queda fijada por el hash del
catálogo de entrada, el perfil versionado y el universo activo usado para la
retención limitada de incumbentes.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from descubrimiento_amplio import (
    COLUMNAS_DESCUBRIMIENTO,
    FUENTE_DESCUBRIMIENTO,
    PATRON_DISCOVERY_ID,
    calcular_hash_catalogo,
)
from universos_versionados import (
    COLUMNAS_UNIVERSO,
    RUTA_ESPEJO_DEFECTO,
    RUTA_MANIFEST_DEFECTO,
    UniversoResuelto,
    calcular_hash_universo,
    cargar_universo_activo,
    comparar_tickers,
    normalizar_tickers,
    registrar_universo_draft,
)


PERFIL_BALANCED_RANK_V1 = "balanced_rank_v1"
SECTORES_BALANCED_RANK_V1 = (
    "Technology",
    "Industrials",
    "Healthcare",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Communication Services",
    "Energy",
    "Basic Materials",
    "Utilities",
)
CUOTAS_BALANCED_RANK_V1 = {
    "us": 12,
    "ca": 6,
    "gb": 6,
    "ch": 4,
    "de": 6,
    "fr": 6,
    "nl": 4,
    "be": 2,
    "at": 2,
    "ie": 2,
    "es": 4,
    "pt": 2,
    "it": 4,
    "dk": 2,
    "se": 4,
    "no": 2,
    "fi": 2,
    "jp": 6,
    "au": 6,
    "nz": 2,
    "sg": 2,
    "hk": 2,
}
DIRECTORIO_AUDITORIA = Path("universos/selecciones")


class ErrorSeleccionUniverso(ValueError):
    """El snapshot, el perfil o el resultado incumplen el contrato."""


@dataclass(frozen=True)
class ConfiguracionSeleccion:
    perfil: str
    cuotas_region: dict[str, int]
    sectores: tuple[str, ...]
    margen_retencion_incumbente: int
    minimo_tickers: int
    maximo_tickers: int
    perfil_descubrimiento_requerido: str


@dataclass(frozen=True)
class SnapshotEntrada:
    discovery_id: str
    ruta_csv: Path
    ruta_json: Path
    filas: tuple[dict, ...]
    sha256: str
    catalog_sha256: str
    metadatos: dict


@dataclass(frozen=True)
class ResultadoSeleccion:
    filas_oficiales: tuple[dict, ...]
    tickers_base: tuple[str, ...]
    tickers_retencion: tuple[str, ...]
    diferencias_activo: dict
    sha256: str


@dataclass(frozen=True)
class DraftGenerado:
    universe_id: str
    ruta_csv: Path
    ruta_auditoria: Path
    ticker_count: int
    sha256: str
    activo_sin_cambios: str
    resumen: dict


def configuracion_balanced_rank_v1() -> ConfiguracionSeleccion:
    return ConfiguracionSeleccion(
        perfil=PERFIL_BALANCED_RANK_V1,
        cuotas_region=dict(CUOTAS_BALANCED_RANK_V1),
        sectores=SECTORES_BALANCED_RANK_V1,
        margen_retencion_incumbente=3,
        minimo_tickers=400,
        maximo_tickers=800,
        perfil_descubrimiento_requerido="desarrollados_v1",
    )


def _config_a_dict(config: ConfiguracionSeleccion) -> dict:
    datos = asdict(config)
    datos["sectores"] = list(config.sectores)
    return datos


def calcular_hash_configuracion(config: ConfiguracionSeleccion) -> str:
    contenido = json.dumps(
        _config_a_dict(config), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


def validar_configuracion(config: ConfiguracionSeleccion) -> None:
    if config.perfil != PERFIL_BALANCED_RANK_V1:
        raise ErrorSeleccionUniverso(f"perfil no soportado: {config.perfil!r}")
    if set(config.cuotas_region) != set(CUOTAS_BALANCED_RANK_V1):
        raise ErrorSeleccionUniverso("el perfil debe cubrir exactamente 22 regiones")
    if any(not isinstance(cuota, int) or cuota <= 0 for cuota in config.cuotas_region.values()):
        raise ErrorSeleccionUniverso("todas las cuotas deben ser enteros positivos")
    if tuple(config.sectores) != SECTORES_BALANCED_RANK_V1:
        raise ErrorSeleccionUniverso("los sectores o su orden no coinciden con el perfil")
    if config.margen_retencion_incumbente < 0:
        raise ErrorSeleccionUniverso("el margen de retención no puede ser negativo")
    if not 0 < config.minimo_tickers <= config.maximo_tickers:
        raise ErrorSeleccionUniverso("el intervalo objetivo no es válido")


def _hash_json_canonico(valor) -> str:
    contenido = json.dumps(
        valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


def cargar_snapshot_para_seleccion(
    ruta_csv: str | Path,
    ruta_json: str | Path | None = None,
) -> SnapshotEntrada:
    """Carga y valida la pertenencia y los ranks de un snapshot publicado."""
    ruta_csv = Path(ruta_csv)
    ruta_json = Path(ruta_json) if ruta_json is not None else ruta_csv.with_suffix(".json")
    if not ruta_csv.exists() or not ruta_json.exists():
        raise ErrorSeleccionUniverso("faltan el CSV o el JSON del snapshot")
    try:
        metadatos = json.loads(ruta_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ErrorSeleccionUniverso(f"no se pudo leer el control del snapshot: {exc}") from exc
    discovery_id = metadatos.get("discovery_id")
    if (
        metadatos.get("schema_version") != 1
        or not PATRON_DISCOVERY_ID.fullmatch(str(discovery_id))
        or ruta_csv.stem != discovery_id
        or ruta_json.stem != discovery_id
    ):
        raise ErrorSeleccionUniverso("identidad incoherente del snapshot")

    try:
        with ruta_csv.open(newline="", encoding="utf-8") as archivo:
            lector = csv.DictReader(archivo)
            if lector.fieldnames != COLUMNAS_DESCUBRIMIENTO:
                raise ErrorSeleccionUniverso(
                    f"esquema de descubrimiento incompatible: {lector.fieldnames}"
                )
            filas = list(lector)
    except OSError as exc:
        raise ErrorSeleccionUniverso(f"no se pudo leer el snapshot: {exc}") from exc
    if not filas:
        raise ErrorSeleccionUniverso("el snapshot está vacío")

    vistos: set[str] = set()
    buckets_rank: set[tuple[str, str, int]] = set()
    for posicion, fila in enumerate(filas, start=2):
        try:
            ticker = normalizar_tickers([fila["ticker"]])[0]
            rank = int(fila["rank_bucket"])
            cuota = int(fila["cuota_bucket"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ErrorSeleccionUniverso(f"fila {posicion} no válida: {exc}") from exc
        if ticker != fila["ticker"] or ticker in vistos:
            raise ErrorSeleccionUniverso(f"ticker no canónico o duplicado: {fila['ticker']!r}")
        if fila["tipo_activo"] != "accion" or fila["fuente"] != FUENTE_DESCUBRIMIENTO:
            raise ErrorSeleccionUniverso(f"tipo o fuente no válidos en la fila {posicion}")
        if rank < 1 or cuota < rank:
            raise ErrorSeleccionUniverso(f"rank/cuota incoherentes en la fila {posicion}")
        clave_rank = (fila["region_descubrimiento"], fila["sector_descubrimiento"], rank)
        if clave_rank in buckets_rank:
            raise ErrorSeleccionUniverso(f"rank duplicado dentro del bucket: {clave_rank}")
        vistos.add(ticker)
        buckets_rank.add(clave_rank)

    hash_tickers = calcular_hash_universo(fila["ticker"] for fila in filas)
    hash_catalogo = calcular_hash_catalogo(filas)
    if metadatos.get("sha256") != hash_tickers:
        raise ErrorSeleccionUniverso("el hash de pertenencia del snapshot no coincide")
    if metadatos.get("catalog_sha256") != hash_catalogo:
        raise ErrorSeleccionUniverso("el hash del catálogo/ranking del snapshot no coincide")
    config_descubrimiento = metadatos.get("config")
    if not isinstance(config_descubrimiento, dict) or metadatos.get("config_sha256") != _hash_json_canonico(config_descubrimiento):
        raise ErrorSeleccionUniverso("la configuración del snapshot no es íntegra")
    control = metadatos.get("control", {})
    if control.get("tickers_unicos") != len(filas):
        raise ErrorSeleccionUniverso("el recuento del snapshot no coincide")

    return SnapshotEntrada(
        discovery_id=str(discovery_id),
        ruta_csv=ruta_csv,
        ruta_json=ruta_json,
        filas=tuple(filas),
        sha256=hash_tickers,
        catalog_sha256=hash_catalogo,
        metadatos=metadatos,
    )


def _validar_compatibilidad_snapshot(
    snapshot: SnapshotEntrada,
    config: ConfiguracionSeleccion,
) -> None:
    metadatos = snapshot.metadatos
    config_origen = metadatos["config"]
    control = metadatos["control"]
    total_buckets = len(config.cuotas_region) * len(config.sectores)
    if config_origen.get("perfil") != config.perfil_descubrimiento_requerido:
        raise ErrorSeleccionUniverso("el perfil de descubrimiento no es compatible")
    if set(config_origen.get("cuotas_region", {})) != set(config.cuotas_region):
        raise ErrorSeleccionUniverso("el snapshot no cubre las 22 regiones requeridas")
    if tuple(config_origen.get("sectores", ())) != config.sectores:
        raise ErrorSeleccionUniverso("el snapshot no cubre los sectores requeridos")
    if (
        control.get("buckets_solicitados") != total_buckets
        or control.get("buckets_exitosos") != total_buckets
        or control.get("buckets_fallidos") != 0
    ):
        raise ErrorSeleccionUniverso("la selección exige los 198 buckets completos")
    cuotas_origen = config_origen["cuotas_region"]
    for fila in snapshot.filas:
        region = fila["region_descubrimiento"]
        sector = fila["sector_descubrimiento"]
        if region not in config.cuotas_region or sector not in config.sectores:
            raise ErrorSeleccionUniverso(f"bucket fuera del perfil: {region}|{sector}")
        if int(fila["cuota_bucket"]) != int(cuotas_origen[region]):
            raise ErrorSeleccionUniverso(f"cuota de descubrimiento incoherente para {region}")


def seleccionar_snapshot(
    snapshot: SnapshotEntrada,
    tickers_activos: list[str] | tuple[str, ...],
    config: ConfiguracionSeleccion | None = None,
) -> ResultadoSeleccion:
    """Aplica cuotas y retención limitada con un orden canónico."""
    config = config or configuracion_balanced_rank_v1()
    validar_configuracion(config)
    _validar_compatibilidad_snapshot(snapshot, config)
    activos = set(normalizar_tickers(tickers_activos))
    orden_region = {region: i for i, region in enumerate(config.cuotas_region)}
    orden_sector = {sector: i for i, sector in enumerate(config.sectores)}
    filas_ordenadas = sorted(
        snapshot.filas,
        key=lambda fila: (
            orden_region[fila["region_descubrimiento"]],
            orden_sector[fila["sector_descubrimiento"]],
            int(fila["rank_bucket"]),
            fila["ticker"],
        ),
    )
    base: list[dict] = []
    retencion: list[dict] = []
    for fila in filas_ordenadas:
        cuota = config.cuotas_region[fila["region_descubrimiento"]]
        rank = int(fila["rank_bucket"])
        if rank <= cuota:
            base.append(fila)
        elif fila["ticker"] in activos and rank <= cuota + config.margen_retencion_incumbente:
            retencion.append(fila)

    tickers_base = {fila["ticker"] for fila in base}
    retencion = [fila for fila in retencion if fila["ticker"] not in tickers_base]
    tipo_inclusion = {
        **{fila["ticker"]: "cuota_base" for fila in base},
        **{fila["ticker"]: "retencion_incumbente" for fila in retencion},
    }
    seleccionadas = sorted(
        base + retencion,
        key=lambda fila: (
            orden_region[fila["region_descubrimiento"]],
            orden_sector[fila["sector_descubrimiento"]],
            int(fila["rank_bucket"]),
            fila["ticker"],
        ),
    )
    if not config.minimo_tickers <= len(seleccionadas) <= config.maximo_tickers:
        raise ErrorSeleccionUniverso(
            f"la selección produjo {len(seleccionadas)} tickers; se exigían "
            f"{config.minimo_tickers}-{config.maximo_tickers}"
        )
    filas_oficiales = tuple({
        "ticker": fila["ticker"],
        "origen_inclusion": f"{config.perfil}:{tipo_inclusion[fila['ticker']]}",
        "region_descubrimiento": fila["region_descubrimiento"],
        "sector_descubrimiento": fila["sector_descubrimiento"],
        "nota": (
            f"{snapshot.discovery_id}; rank_bucket={fila['rank_bucket']}; "
            f"cuota_seleccion={config.cuotas_region[fila['region_descubrimiento']]}"
        ),
    } for fila in seleccionadas)
    if any(set(fila) != set(COLUMNAS_UNIVERSO) for fila in filas_oficiales):
        raise AssertionError("esquema oficial construido de forma incoherente")
    tickers_finales = [fila["ticker"] for fila in filas_oficiales]
    return ResultadoSeleccion(
        filas_oficiales=filas_oficiales,
        tickers_base=tuple(fila["ticker"] for fila in base),
        tickers_retencion=tuple(fila["ticker"] for fila in retencion),
        diferencias_activo=comparar_tickers(activos, tickers_finales),
        sha256=calcular_hash_universo(tickers_finales),
    )


def _escribir_json_nuevo(ruta: Path, contenido: dict) -> None:
    if ruta.exists():
        raise ErrorSeleccionUniverso(f"el informe de selección ya existe: {ruta}")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", delete=False, dir=ruta.parent, encoding="utf-8", newline="",
    ) as temporal:
        temporal.write(json.dumps(contenido, ensure_ascii=False, indent=2) + "\n")
        temporal.flush()
        os.fsync(temporal.fileno())
        ruta_temporal = Path(temporal.name)
    os.replace(ruta_temporal, ruta)


def generar_draft_desde_snapshot(
    universe_id: str,
    ruta_snapshot: str | Path,
    ruta_control_snapshot: str | Path | None = None,
    config: ConfiguracionSeleccion | None = None,
    ruta_manifest: str | Path = RUTA_MANIFEST_DEFECTO,
    ruta_espejo: str | Path = RUTA_ESPEJO_DEFECTO,
    directorio_auditoria: str | Path = DIRECTORIO_AUDITORIA,
    created_at: str | None = None,
) -> DraftGenerado:
    """Genera CSV, auditoría y entrada draft; nunca llama a activar_universo."""
    config = config or configuracion_balanced_rank_v1()
    validar_configuracion(config)
    activo: UniversoResuelto = cargar_universo_activo(ruta_manifest, ruta_espejo)
    snapshot = cargar_snapshot_para_seleccion(ruta_snapshot, ruta_control_snapshot)
    resultado = seleccionar_snapshot(snapshot, activo.tickers, config)
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ErrorSeleccionUniverso("created_at debe ser una fecha ISO") from exc

    ruta_manifest = Path(ruta_manifest)
    ruta_auditoria = Path(directorio_auditoria) / f"{universe_id}.json"
    ruta_csv = ruta_manifest.parent / "oficiales" / f"{universe_id}.csv"
    if ruta_auditoria.exists() or ruta_csv.exists():
        raise ErrorSeleccionUniverso(f"ya existen artefactos para {universe_id}")
    config_sha256 = calcular_hash_configuracion(config)
    resumen = {
        "snapshot_tickers": len(snapshot.filas),
        "seleccion_base": len(resultado.tickers_base),
        "retencion_incumbentes": len(resultado.tickers_retencion),
        "seleccion_final": len(resultado.filas_oficiales),
        "permanencias": len(resultado.diferencias_activo["comunes"]),
        "altas": len(resultado.diferencias_activo["anadidos"]),
        "bajas": len(resultado.diferencias_activo["eliminados"]),
    }
    auditoria = {
        "schema_version": 1,
        "universe_id": universe_id,
        "status": "draft",
        "created_at": created_at,
        "activation_performed": False,
        "selection_profile": config.perfil,
        "selection_config": _config_a_dict(config),
        "selection_config_sha256": config_sha256,
        "source_snapshot": {
            "discovery_id": snapshot.discovery_id,
            "csv_path": snapshot.ruta_csv.as_posix(),
            "control_path": snapshot.ruta_json.as_posix(),
            "ticker_sha256": snapshot.sha256,
            "catalog_sha256": snapshot.catalog_sha256,
        },
        "active_reference": {
            "universe_id": activo.universe_id,
            "sha256": activo.sha256,
            "ticker_count": len(activo.tickers),
        },
        "output": {
            "csv_path": ruta_csv.as_posix(),
            "ticker_count": len(resultado.filas_oficiales),
            "sha256": resultado.sha256,
        },
        "summary": resumen,
        "incumbents_retained_by_buffer": list(resultado.tickers_retencion),
        "added_tickers": resultado.diferencias_activo["anadidos"],
        "removed_tickers": resultado.diferencias_activo["eliminados"],
        "common_tickers": resultado.diferencias_activo["comunes"],
    }
    _escribir_json_nuevo(ruta_auditoria, auditoria)
    try:
        version = registrar_universo_draft(
            universe_id=universe_id,
            filas=resultado.filas_oficiales,
            selection_method=config.perfil,
            notes=(
                f"Selección reproducible desde {snapshot.discovery_id}; "
                f"auditoría en selecciones/{universe_id}.json; "
                f"config_sha256={config_sha256}"
            ),
            supersedes=activo.universe_id,
            created_at=created_at,
            ruta_manifest=ruta_manifest,
            ruta_espejo=ruta_espejo,
        )
    except Exception:
        ruta_auditoria.unlink(missing_ok=True)
        raise
    activo_despues = cargar_universo_activo(ruta_manifest, ruta_espejo)
    if activo_despues.universe_id != activo.universe_id:
        raise AssertionError("la generación del draft alteró el universo activo")
    return DraftGenerado(
        universe_id=universe_id,
        ruta_csv=ruta_csv,
        ruta_auditoria=ruta_auditoria,
        ticker_count=version["ticker_count"],
        sha256=version["sha256"],
        activo_sin_cambios=activo_despues.universe_id,
        resumen=resumen,
    )
