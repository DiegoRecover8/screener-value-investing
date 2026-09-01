"""Refina un universo usando un deduplicado oficial ya observado.

La observación oficial permite saber qué tickers sobrevivieron al deduplicado
por nombre y país. Esta etapa excluye los listings descartados antes de la
siguiente descarga y rellena sus plazas desde el mismo snapshot amplio.
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

from selector_universo import (
    DIRECTORIO_AUDITORIA,
    SnapshotEntrada,
    cargar_snapshot_para_seleccion,
)
from universos_versionados import (
    COLUMNAS_UNIVERSO,
    RUTA_ESPEJO_DEFECTO,
    RUTA_MANIFEST_DEFECTO,
    calcular_hash_universo,
    cargar_tickers,
    cargar_universo_activo,
    cargar_universo_registrado,
    comparar_tickers,
    registrar_universo_draft,
)


PERFIL_REFINADO = "refine_observed_v1"
RUTA_JOURNAL_DEFECTO = Path("journal_candidatos.csv")
RUTA_EJECUCIONES_DEFECTO = Path("ejecuciones_screener.csv")


class ErrorRefinadoUniverso(ValueError):
    """La observación o el relleno no son suficientemente fiables."""


@dataclass(frozen=True)
class ConfiguracionRefinado:
    perfil: str = PERFIL_REFINADO
    exigir_ejecucion_oficial: bool = True
    exigir_cobertura_completa: bool = True
    fallback: str = "misma_region"


@dataclass(frozen=True)
class ObservacionDeduplicado:
    snapshot_id: str
    universe_id: str
    universe_sha256: str
    tickers_solicitados: int
    tickers_supervivientes: tuple[str, ...]
    tickers_descartados: tuple[str, ...]
    deduplicados: int
    fila_control: dict


@dataclass(frozen=True)
class ResultadoRefinado:
    filas_oficiales: tuple[dict, ...]
    tickers_supervivientes: tuple[str, ...]
    tickers_descartados: tuple[str, ...]
    reemplazos: tuple[dict, ...]
    diferencias_origen: dict
    sha256: str


@dataclass(frozen=True)
class DraftRefinado:
    universe_id: str
    ruta_csv: Path
    ruta_auditoria: Path
    ticker_count: int
    sha256: str
    activo_sin_cambios: str
    resumen: dict


def _entero(fila: dict, campo: str) -> int:
    try:
        return int(fila[campo])
    except (KeyError, TypeError, ValueError) as exc:
        raise ErrorRefinadoUniverso(f"{campo} no es un entero válido") from exc


def _booleano(valor) -> bool:
    return str(valor).strip().lower() in {"true", "1", "sí", "si", "yes"}


def _leer_csv(ruta: str | Path) -> tuple[list[str], list[dict]]:
    ruta = Path(ruta)
    if not ruta.exists():
        raise ErrorRefinadoUniverso(f"no existe {ruta}")
    with ruta.open(newline="", encoding="utf-8") as archivo:
        lector = csv.DictReader(archivo)
        return list(lector.fieldnames or []), list(lector)


def cargar_observacion_oficial(
    snapshot_id: str,
    universe_id: str,
    ruta_journal: str | Path = RUTA_JOURNAL_DEFECTO,
    ruta_ejecuciones: str | Path = RUTA_EJECUCIONES_DEFECTO,
    ruta_manifest: str | Path = RUTA_MANIFEST_DEFECTO,
    ruta_espejo: str | Path = RUTA_ESPEJO_DEFECTO,
    config: ConfiguracionRefinado | None = None,
) -> ObservacionDeduplicado:
    """Valida que un snapshot oficial identifique exactamente el deduplicado."""
    config = config or ConfiguracionRefinado()
    _, controles = _leer_csv(ruta_ejecuciones)
    coincidencias = [fila for fila in controles if fila.get("snapshot_id") == snapshot_id]
    if len(coincidencias) != 1:
        raise ErrorRefinadoUniverso(
            f"se esperaba un control para {snapshot_id}, hay {len(coincidencias)}"
        )
    control = coincidencias[0]
    if control.get("universe_id") != universe_id:
        raise ErrorRefinadoUniverso("el snapshot no pertenece al universo indicado")
    if config.exigir_ejecucion_oficial and not _booleano(control.get("oficial")):
        raise ErrorRefinadoUniverso("el snapshot de identidad debe ser oficial")

    universo = cargar_universo_registrado(
        universe_id, ruta_manifest, ruta_espejo,
    )
    if control.get("universe_sha256") != universo.sha256:
        raise ErrorRefinadoUniverso("el hash del universo observado no coincide")
    solicitados = _entero(control, "tickers_solicitados")
    resultados_brutos = _entero(control, "resultados_brutos")
    correctas = _entero(control, "descargas_correctas")
    errores = _entero(control, "errores_descarga")
    deduplicados = _entero(control, "deduplicados")
    evaluadas = _entero(control, "empresas_evaluadas")
    if solicitados != len(universo.tickers) or resultados_brutos != solicitados:
        raise ErrorRefinadoUniverso("el recuento bruto no coincide con el universo")
    if config.exigir_cobertura_completa and (correctas != solicitados or errores != 0):
        raise ErrorRefinadoUniverso("se exige una observación con cobertura del 100 %")

    _, journal = _leer_csv(ruta_journal)
    filas_snapshot = [fila for fila in journal if fila.get("snapshot_id") == snapshot_id]
    tickers_journal = [str(fila.get("ticker", "")).strip().upper() for fila in filas_snapshot]
    if not tickers_journal or len(tickers_journal) != len(set(tickers_journal)):
        raise ErrorRefinadoUniverso("el journal observado está vacío o contiene duplicados")
    conjunto_origen = set(universo.tickers)
    conjunto_superviviente = set(tickers_journal)
    if not conjunto_superviviente <= conjunto_origen:
        raise ErrorRefinadoUniverso("el journal contiene tickers ajenos al universo observado")
    descartados = conjunto_origen - conjunto_superviviente
    if (
        len(filas_snapshot) != evaluadas
        or len(descartados) != deduplicados
        or solicitados - deduplicados != evaluadas
    ):
        raise ErrorRefinadoUniverso("el control y las filas deduplicadas no cuadran")
    supervivientes_ordenados = tuple(
        ticker for ticker in universo.tickers if ticker in conjunto_superviviente
    )
    descartados_ordenados = tuple(
        ticker for ticker in universo.tickers if ticker in descartados
    )
    return ObservacionDeduplicado(
        snapshot_id=snapshot_id,
        universe_id=universe_id,
        universe_sha256=universo.sha256,
        tickers_solicitados=solicitados,
        tickers_supervivientes=supervivientes_ordenados,
        tickers_descartados=descartados_ordenados,
        deduplicados=deduplicados,
        fila_control=control,
    )


def _cargar_filas_oficiales(ruta: Path) -> list[dict]:
    columnas, filas = _leer_csv(ruta)
    if columnas != COLUMNAS_UNIVERSO:
        raise ErrorRefinadoUniverso(f"esquema oficial incompatible en {ruta}")
    cargar_tickers(ruta, exigir_esquema_oficial=True)
    return filas


def _clave_orden(fila: dict, snapshot: SnapshotEntrada) -> tuple:
    config = snapshot.metadatos["config"]
    regiones = {region: i for i, region in enumerate(config["cuotas_region"])}
    sectores = {sector: i for i, sector in enumerate(config["sectores"])}
    return (
        regiones[fila["region_descubrimiento"]],
        sectores[fila["sector_descubrimiento"]],
        int(fila["rank_bucket"]),
        fila["ticker"],
    )


def refinar_seleccion(
    observacion: ObservacionDeduplicado,
    filas_origen: list[dict],
    snapshot: SnapshotEntrada,
    config: ConfiguracionRefinado | None = None,
) -> ResultadoRefinado:
    """Excluye listings conocidos y rellena primero dentro del mismo bucket."""
    config = config or ConfiguracionRefinado()
    if config.perfil != PERFIL_REFINADO or config.fallback != "misma_region":
        raise ErrorRefinadoUniverso("configuración de refinado no soportada")
    por_ticker_origen = {fila["ticker"]: fila for fila in filas_origen}
    por_ticker_discovery = {fila["ticker"]: fila for fila in snapshot.filas}
    if set(por_ticker_origen) != (
        set(observacion.tickers_supervivientes) | set(observacion.tickers_descartados)
    ):
        raise ErrorRefinadoUniverso("la observación no coincide con el CSV de origen")
    if not set(por_ticker_origen) <= set(por_ticker_discovery):
        raise ErrorRefinadoUniverso("el universo de origen no pertenece al snapshot amplio")

    descartados = set(observacion.tickers_descartados)
    usados = set(observacion.tickers_supervivientes)
    filas_discovery = sorted(snapshot.filas, key=lambda fila: _clave_orden(fila, snapshot))
    por_bucket: dict[tuple[str, str], list[dict]] = {}
    for fila in filas_discovery:
        bucket = (fila["region_descubrimiento"], fila["sector_descubrimiento"])
        por_bucket.setdefault(bucket, []).append(fila)

    slots = sorted(
        (por_ticker_discovery[ticker] for ticker in observacion.tickers_descartados),
        key=lambda fila: _clave_orden(fila, snapshot),
    )
    reemplazos: list[dict] = []
    pendientes: list[dict] = []
    for slot in slots:
        bucket = (slot["region_descubrimiento"], slot["sector_descubrimiento"])
        candidatos = [
            fila for fila in por_bucket[bucket]
            if fila["ticker"] not in usados and fila["ticker"] not in descartados
        ]
        if not candidatos:
            pendientes.append(slot)
            continue
        elegido = candidatos[0]
        usados.add(elegido["ticker"])
        reemplazos.append({
            "removed_ticker": slot["ticker"],
            "replacement_ticker": elegido["ticker"],
            "strategy": "mismo_bucket",
            "region_descubrimiento": slot["region_descubrimiento"],
            "sector_origen": slot["sector_descubrimiento"],
            "sector_reemplazo": elegido["sector_descubrimiento"],
            "rank_reemplazo": int(elegido["rank_bucket"]),
        })

    orden_sector = {
        sector: i for i, sector in enumerate(snapshot.metadatos["config"]["sectores"])
    }
    for slot in pendientes:
        region = slot["region_descubrimiento"]
        candidatos = [
            fila for fila in filas_discovery
            if fila["region_descubrimiento"] == region
            and fila["ticker"] not in usados
            and fila["ticker"] not in descartados
        ]
        candidatos.sort(key=lambda fila: (
            int(fila["rank_bucket"]) / int(fila["cuota_bucket"]),
            int(fila["rank_bucket"]),
            orden_sector[fila["sector_descubrimiento"]],
            fila["ticker"],
        ))
        if not candidatos:
            raise ErrorRefinadoUniverso(
                f"no hay reserva regional para reemplazar {slot['ticker']}"
            )
        elegido = candidatos[0]
        usados.add(elegido["ticker"])
        reemplazos.append({
            "removed_ticker": slot["ticker"],
            "replacement_ticker": elegido["ticker"],
            "strategy": "misma_region",
            "region_descubrimiento": region,
            "sector_origen": slot["sector_descubrimiento"],
            "sector_reemplazo": elegido["sector_descubrimiento"],
            "rank_reemplazo": int(elegido["rank_bucket"]),
        })

    if len(reemplazos) != observacion.deduplicados:
        raise ErrorRefinadoUniverso("no se rellenaron todas las plazas deduplicadas")
    reemplazos_por_ticker = {
        reemplazo["replacement_ticker"]: reemplazo for reemplazo in reemplazos
    }
    tickers_finales = set(observacion.tickers_supervivientes) | set(reemplazos_por_ticker)
    if len(tickers_finales) != observacion.tickers_solicitados or tickers_finales & descartados:
        raise ErrorRefinadoUniverso("el universo refinado no conserva el tamaño o reintroduce bajas")

    filas_finales: list[dict] = []
    for ticker in tickers_finales:
        discovery = por_ticker_discovery[ticker]
        if ticker in reemplazos_por_ticker:
            estrategia = reemplazos_por_ticker[ticker]["strategy"]
            origen = f"{config.perfil}:relleno_{estrategia}"
        else:
            origen = f"{config.perfil}:superviviente"
        filas_finales.append({
            "ticker": ticker,
            "origen_inclusion": origen,
            "region_descubrimiento": discovery["region_descubrimiento"],
            "sector_descubrimiento": discovery["sector_descubrimiento"],
            "nota": (
                f"{snapshot.discovery_id}; observacion={observacion.snapshot_id}; "
                f"rank_bucket={discovery['rank_bucket']}"
            ),
        })
    filas_finales.sort(
        key=lambda fila: _clave_orden(por_ticker_discovery[fila["ticker"]], snapshot)
    )
    tickers_ordenados = [fila["ticker"] for fila in filas_finales]
    return ResultadoRefinado(
        filas_oficiales=tuple(filas_finales),
        tickers_supervivientes=observacion.tickers_supervivientes,
        tickers_descartados=observacion.tickers_descartados,
        reemplazos=tuple(reemplazos),
        diferencias_origen=comparar_tickers(por_ticker_origen, tickers_ordenados),
        sha256=calcular_hash_universo(tickers_ordenados),
    )


def _hash_config(config: ConfiguracionRefinado) -> str:
    contenido = json.dumps(
        asdict(config), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


def _escribir_json_nuevo(ruta: Path, contenido: dict) -> None:
    if ruta.exists():
        raise ErrorRefinadoUniverso(f"el informe ya existe: {ruta}")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", delete=False, dir=ruta.parent, encoding="utf-8", newline="",
    ) as temporal:
        temporal.write(json.dumps(contenido, ensure_ascii=False, indent=2) + "\n")
        temporal.flush()
        os.fsync(temporal.fileno())
        ruta_temporal = Path(temporal.name)
    os.replace(ruta_temporal, ruta)


def generar_draft_refinado(
    universe_id: str,
    universe_id_origen: str,
    snapshot_id_oficial: str,
    ruta_snapshot_descubrimiento: str | Path,
    ruta_control_descubrimiento: str | Path | None = None,
    ruta_journal: str | Path = RUTA_JOURNAL_DEFECTO,
    ruta_ejecuciones: str | Path = RUTA_EJECUCIONES_DEFECTO,
    ruta_manifest: str | Path = RUTA_MANIFEST_DEFECTO,
    ruta_espejo: str | Path = RUTA_ESPEJO_DEFECTO,
    directorio_auditoria: str | Path = DIRECTORIO_AUDITORIA,
    created_at: str | None = None,
) -> DraftRefinado:
    """Genera y registra un draft refinado sin cambiar el universo activo."""
    config = ConfiguracionRefinado()
    activo = cargar_universo_activo(ruta_manifest, ruta_espejo)
    if activo.universe_id != universe_id_origen:
        raise ErrorRefinadoUniverso(
            f"el origen debe ser el universo activo {activo.universe_id}"
        )
    origen = cargar_universo_registrado(
        universe_id_origen, ruta_manifest, ruta_espejo,
    )
    observacion = cargar_observacion_oficial(
        snapshot_id_oficial, universe_id_origen,
        ruta_journal, ruta_ejecuciones, ruta_manifest, ruta_espejo, config,
    )
    snapshot = cargar_snapshot_para_seleccion(
        ruta_snapshot_descubrimiento, ruta_control_descubrimiento,
    )
    filas_origen = _cargar_filas_oficiales(origen.ruta)
    resultado = refinar_seleccion(observacion, filas_origen, snapshot, config)
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ErrorRefinadoUniverso("created_at debe ser una fecha ISO") from exc

    ruta_manifest = Path(ruta_manifest)
    ruta_csv = ruta_manifest.parent / "oficiales" / f"{universe_id}.csv"
    ruta_auditoria = Path(directorio_auditoria) / f"{universe_id}.json"
    if ruta_csv.exists() or ruta_auditoria.exists():
        raise ErrorRefinadoUniverso(f"ya existen artefactos para {universe_id}")
    config_sha = _hash_config(config)
    mismos_bucket = sum(r["strategy"] == "mismo_bucket" for r in resultado.reemplazos)
    misma_region = sum(r["strategy"] == "misma_region" for r in resultado.reemplazos)
    resumen = {
        "tickers_origen": len(filas_origen),
        "supervivientes_observados": len(resultado.tickers_supervivientes),
        "listings_excluidos_antes_descarga": len(resultado.tickers_descartados),
        "rellenos_mismo_bucket": mismos_bucket,
        "rellenos_misma_region": misma_region,
        "tickers_finales": len(resultado.filas_oficiales),
    }
    auditoria = {
        "schema_version": 1,
        "universe_id": universe_id,
        "status": "draft",
        "created_at": created_at,
        "activation_performed": False,
        "selection_profile": config.perfil,
        "selection_config": asdict(config),
        "selection_config_sha256": config_sha,
        "source_universe": {
            "universe_id": origen.universe_id,
            "sha256": origen.sha256,
            "ticker_count": len(origen.tickers),
        },
        "official_observation": {
            "snapshot_id": observacion.snapshot_id,
            "universe_id": observacion.universe_id,
            "deduplicados": observacion.deduplicados,
            "github_run_id": observacion.fila_control.get("github_run_id", ""),
            "github_run_url": observacion.fila_control.get("github_run_url", ""),
            "survivors_sha256": calcular_hash_universo(
                observacion.tickers_supervivientes
            ),
        },
        "source_discovery": {
            "discovery_id": snapshot.discovery_id,
            "ticker_sha256": snapshot.sha256,
            "catalog_sha256": snapshot.catalog_sha256,
        },
        "output": {
            "csv_path": ruta_csv.as_posix(),
            "ticker_count": len(resultado.filas_oficiales),
            "sha256": resultado.sha256,
        },
        "summary": resumen,
        "known_dual_listings_removed": list(resultado.tickers_descartados),
        "replacements": list(resultado.reemplazos),
    }
    _escribir_json_nuevo(ruta_auditoria, auditoria)
    try:
        version = registrar_universo_draft(
            universe_id=universe_id,
            filas=resultado.filas_oficiales,
            selection_method=config.perfil,
            notes=(
                f"Refinado desde {universe_id_origen} con observación oficial "
                f"{snapshot_id_oficial}; auditoría en selecciones/{universe_id}.json; "
                f"config_sha256={config_sha}"
            ),
            supersedes=universe_id_origen,
            created_at=created_at,
            ruta_manifest=ruta_manifest,
            ruta_espejo=ruta_espejo,
        )
    except Exception:
        ruta_auditoria.unlink(missing_ok=True)
        raise
    activo_despues = cargar_universo_activo(ruta_manifest, ruta_espejo)
    if activo_despues.universe_id != activo.universe_id:
        raise AssertionError("la generación del refinado alteró el activo")
    return DraftRefinado(
        universe_id=universe_id,
        ruta_csv=ruta_csv,
        ruta_auditoria=ruta_auditoria,
        ticker_count=version["ticker_count"],
        sha256=version["sha256"],
        activo_sin_cambios=activo_despues.universe_id,
        resumen=resumen,
    )
