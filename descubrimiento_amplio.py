"""Snapshots amplios y reanudables para descubrir acciones.

Este pipeline consulta buckets independientes de país×sector. Sus resultados
son material de revisión: nunca modifican el universo oficial ni el manifest.
El checkpoint se actualiza después de cada bucket para que un fallo de Yahoo
no obligue a repetir las consultas que ya terminaron correctamente.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable

import pandas as pd

from universos_versionados import calcular_hash_universo, comparar_tickers
from universos_yfinance import (
    SECTORES_NO_FINANCIEROS,
    UNIVERSOS_YAHOO,
    _consultar_bucket,
    construir_consulta,
)


DIRECTORIO_DESCUBRIMIENTO = Path("universos/descubrimiento")
UMBRAL_BUCKETS_DEFECTO = 0.90
REINTENTOS_DEFECTO = 2
FUENTE_DESCUBRIMIENTO = "yfinance.screen"
COLUMNAS_DESCUBRIMIENTO = [
    "ticker", "tipo_activo", "region_descubrimiento",
    "sector_descubrimiento", "rank_bucket", "cuota_bucket", "fuente",
    "fecha_descubrimiento",
]
PATRON_DISCOVERY_ID = re.compile(r"^disc_\d{8}T\d{12}Z$")

# Máximo teórico: 2.070 resultados antes de deduplicar o encontrar buckets
# con menos empresas. Las cuotas buscan profundidad sin dar el mismo peso a
# EE. UU. y a mercados mucho menores.
CUOTAS_DESARROLLADOS_V1 = {
    "us": 30,
    "ca": 15,
    "gb": 15,
    "ch": 10,
    "de": 15,
    "fr": 15,
    "nl": 10,
    "be": 6,
    "at": 6,
    "ie": 6,
    "es": 10,
    "pt": 6,
    "it": 10,
    "dk": 6,
    "se": 10,
    "no": 6,
    "fi": 6,
    "jp": 15,
    "au": 15,
    "nz": 6,
    "sg": 6,
    "hk": 6,
}


class ErrorDescubrimiento(ValueError):
    """El snapshot no alcanza la cobertura o coherencia requerida."""


@dataclass(frozen=True)
class ConfiguracionDescubrimiento:
    perfil: str
    cuotas_region: dict[str, int]
    sectores: tuple[str, ...]
    precio_minimo: float = 2.0
    volumen_medio_minimo: int = 100_000
    umbral_buckets: float = UMBRAL_BUCKETS_DEFECTO
    reintentos: int = REINTENTOS_DEFECTO


@dataclass(frozen=True)
class SnapshotDescubrimiento:
    discovery_id: str
    ruta_csv: Path
    ruta_json: Path
    ticker_count: int
    sha256: str
    control: dict
    diferencias_activo: dict | None = None


def configuracion_desarrollados_v1() -> ConfiguracionDescubrimiento:
    return ConfiguracionDescubrimiento(
        perfil="desarrollados_v1",
        cuotas_region=dict(CUOTAS_DESARROLLADOS_V1),
        sectores=tuple(SECTORES_NO_FINANCIEROS),
    )


def validar_configuracion(config: ConfiguracionDescubrimiento) -> None:
    regiones_esperadas = set(UNIVERSOS_YAHOO["desarrollados_aproximado"])
    regiones = set(config.cuotas_region)
    if config.perfil == "desarrollados_v1" and regiones != regiones_esperadas:
        raise ErrorDescubrimiento(
            "el perfil desarrollados_v1 debe cubrir exactamente sus 22 regiones"
        )
    if not regiones or any(cuota <= 0 for cuota in config.cuotas_region.values()):
        raise ErrorDescubrimiento("todas las cuotas regionales deben ser positivas")
    if not config.sectores or len(set(config.sectores)) != len(config.sectores):
        raise ErrorDescubrimiento("los sectores deben ser no vacíos y únicos")
    if config.precio_minimo < 0 or config.volumen_medio_minimo < 0:
        raise ErrorDescubrimiento("precio y volumen mínimos no pueden ser negativos")
    if not 0 <= config.umbral_buckets <= 1:
        raise ErrorDescubrimiento("umbral_buckets debe estar entre 0 y 1")
    if config.reintentos < 1:
        raise ErrorDescubrimiento("reintentos debe ser al menos 1")


def _config_a_dict(config: ConfiguracionDescubrimiento) -> dict:
    datos = asdict(config)
    datos["sectores"] = list(config.sectores)
    return datos


def _config_desde_dict(datos: dict) -> ConfiguracionDescubrimiento:
    return ConfiguracionDescubrimiento(
        perfil=datos["perfil"],
        cuotas_region={str(k): int(v) for k, v in datos["cuotas_region"].items()},
        sectores=tuple(datos["sectores"]),
        precio_minimo=float(datos["precio_minimo"]),
        volumen_medio_minimo=int(datos["volumen_medio_minimo"]),
        umbral_buckets=float(datos["umbral_buckets"]),
        reintentos=int(datos["reintentos"]),
    )


def _hash_config(config: ConfiguracionDescubrimiento) -> str:
    contenido = json.dumps(
        _config_a_dict(config), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


def _escribir_atomico(ruta: Path, contenido: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    modo = ruta.stat().st_mode if ruta.exists() else None
    with NamedTemporaryFile(
        "w", delete=False, dir=ruta.parent, encoding="utf-8", newline="",
    ) as temporal:
        temporal.write(contenido)
        temporal.flush()
        os.fsync(temporal.fileno())
        ruta_temporal = Path(temporal.name)
    if modo is not None:
        os.chmod(ruta_temporal, modo)
    os.replace(ruta_temporal, ruta)


def _consultar_bucket_yahoo(
    region: str,
    sector: str,
    cuota: int,
    precio_minimo: float,
    volumen_medio_minimo: int,
) -> list[str]:
    consulta = construir_consulta(
        [region], precio_minimo=precio_minimo,
        volumen_medio_minimo=volumen_medio_minimo, sectores=[sector],
    )
    return _consultar_bucket(consulta, cuota, set())


def _nuevo_estado(
    config: ConfiguracionDescubrimiento,
    discovery_id: str,
    creado_en: str,
) -> dict:
    return {
        "schema_version": 1,
        "discovery_id": discovery_id,
        "created_at": creado_en,
        "config": _config_a_dict(config),
        "config_sha256": _hash_config(config),
        "completed_buckets": [],
        "rows": [],
        "failures": {},
        "raw_results": 0,
        "duplicates": 0,
    }


def _cargar_checkpoint(ruta: Path) -> tuple[ConfiguracionDescubrimiento, dict]:
    try:
        estado = json.loads(ruta.read_text(encoding="utf-8"))
        config = _config_desde_dict(estado["config"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ErrorDescubrimiento(f"checkpoint incompatible en {ruta}: {exc}") from exc
    validar_configuracion(config)
    if estado.get("schema_version") != 1 or estado.get("config_sha256") != _hash_config(config):
        raise ErrorDescubrimiento(f"checkpoint incoherente en {ruta}")
    return config, estado


def _csv_descubrimiento(filas: list[dict]) -> str:
    salida = io.StringIO(newline="")
    escritor = csv.DictWriter(salida, fieldnames=COLUMNAS_DESCUBRIMIENTO)
    escritor.writeheader()
    escritor.writerows(filas)
    return salida.getvalue()


def calcular_hash_catalogo(filas: list[dict]) -> str:
    """Hash canónico de todos los campos que condicionan la selección."""
    canonico = [
        {columna: str(fila.get(columna, "")) for columna in COLUMNAS_DESCUBRIMIENTO}
        for fila in filas
    ]
    contenido = json.dumps(
        canonico, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


def generar_snapshot_descubrimiento(
    config: ConfiguracionDescubrimiento | None = None,
    directorio: str | Path = DIRECTORIO_DESCUBRIMIENTO,
    momento: pd.Timestamp | None = None,
    discovery_id: str | None = None,
    ruta_checkpoint: str | Path | None = None,
    consultar_bucket: Callable[..., list[str]] = _consultar_bucket_yahoo,
    tickers_activos: list[str] | None = None,
    verbose: bool = False,
) -> SnapshotDescubrimiento:
    """Genera o reanuda un snapshot y solo lo publica si supera el umbral."""
    directorio = Path(directorio)
    momento = pd.Timestamp.now(tz="UTC") if momento is None else pd.Timestamp(momento)
    if momento.tzinfo is None:
        momento = momento.tz_localize("UTC")
    else:
        momento = momento.tz_convert("UTC")

    if ruta_checkpoint is not None:
        ruta_checkpoint = Path(ruta_checkpoint)
        directorio = ruta_checkpoint.parent
        config_checkpoint, estado = _cargar_checkpoint(ruta_checkpoint)
        if config is not None and _hash_config(config) != _hash_config(config_checkpoint):
            raise ErrorDescubrimiento("la configuración no coincide con el checkpoint")
        config = config_checkpoint
        discovery_id = estado["discovery_id"]
        creado_en = estado["created_at"]
    else:
        config = config or configuracion_desarrollados_v1()
        validar_configuracion(config)
        discovery_id = discovery_id or (
            "disc_" + momento.strftime("%Y%m%dT%H%M%S%fZ")
        )
        creado_en = momento.isoformat()
        ruta_checkpoint = directorio / f"checkpoint_{discovery_id}.json"
        estado = _nuevo_estado(config, discovery_id, creado_en)

    validar_configuracion(config)
    if not PATRON_DISCOVERY_ID.fullmatch(str(discovery_id)):
        raise ErrorDescubrimiento(f"discovery_id no válido: {discovery_id!r}")
    completados = set(estado["completed_buckets"])
    vistos = {fila["ticker"] for fila in estado["rows"]}
    total_buckets = len(config.cuotas_region) * len(config.sectores)

    for region, cuota in config.cuotas_region.items():
        for sector in config.sectores:
            bucket_id = f"{region}|{sector}"
            if bucket_id in completados:
                continue
            ultimo_error = None
            tickers = None
            for _ in range(config.reintentos):
                try:
                    tickers = consultar_bucket(
                        region, sector, cuota, config.precio_minimo,
                        config.volumen_medio_minimo,
                    )
                    break
                except Exception as exc:  # el checkpoint conserva el resto
                    ultimo_error = f"{type(exc).__name__}: {exc}"

            if tickers is None:
                estado["failures"][bucket_id] = ultimo_error
                _escribir_atomico(
                    ruta_checkpoint,
                    json.dumps(estado, ensure_ascii=False, indent=2) + "\n",
                )
                if verbose:
                    print(f"ERR {bucket_id}: {ultimo_error}", flush=True)
                continue

            estado["failures"].pop(bucket_id, None)
            estado["raw_results"] += len(tickers)
            nuevos = 0
            for rank, ticker in enumerate(tickers, start=1):
                ticker = str(ticker).strip().upper()
                if not ticker or ticker in vistos:
                    estado["duplicates"] += 1
                    continue
                vistos.add(ticker)
                nuevos += 1
                estado["rows"].append({
                    "ticker": ticker,
                    "tipo_activo": "accion",
                    "region_descubrimiento": region,
                    "sector_descubrimiento": sector,
                    "rank_bucket": rank,
                    "cuota_bucket": cuota,
                    "fuente": FUENTE_DESCUBRIMIENTO,
                    "fecha_descubrimiento": creado_en,
                })
            estado["completed_buckets"].append(bucket_id)
            completados.add(bucket_id)
            _escribir_atomico(
                ruta_checkpoint,
                json.dumps(estado, ensure_ascii=False, indent=2) + "\n",
            )
            if verbose:
                print(
                    f"OK  {bucket_id}: {len(tickers)} resultados, "
                    f"{nuevos} nuevos ({len(vistos)} acumulados)",
                    flush=True,
                )

    buckets_exitosos = len(completados)
    tasa_exito = buckets_exitosos / total_buckets
    if tasa_exito < config.umbral_buckets:
        raise ErrorDescubrimiento(
            f"cobertura de buckets {tasa_exito:.1%} por debajo del mínimo "
            f"{config.umbral_buckets:.1%}; reanuda con {ruta_checkpoint}"
        )

    tickers_finales = [fila["ticker"] for fila in estado["rows"]]
    if not tickers_finales:
        raise ErrorDescubrimiento("el descubrimiento no devolvió ningún ticker")
    hash_universo = calcular_hash_universo(tickers_finales)
    ruta_csv = directorio / f"{discovery_id}.csv"
    ruta_json = directorio / f"{discovery_id}.json"
    control = {
        "buckets_solicitados": total_buckets,
        "buckets_exitosos": buckets_exitosos,
        "buckets_fallidos": total_buckets - buckets_exitosos,
        "tasa_exito_buckets": tasa_exito,
        "umbral_exito_buckets": config.umbral_buckets,
        "resultados_brutos": estado["raw_results"],
        "duplicados": estado["duplicates"],
        "tickers_unicos": len(tickers_finales),
        "capacidad_teorica": sum(config.cuotas_region.values()) * len(config.sectores),
        "fallos": estado["failures"],
    }
    diferencias = (
        comparar_tickers(tickers_activos, tickers_finales)
        if tickers_activos else None
    )
    metadatos = {
        "schema_version": 1,
        "discovery_id": discovery_id,
        "created_at": creado_en,
        "source": FUENTE_DESCUBRIMIENTO,
        "csv_path": ruta_csv.as_posix(),
        "sha256": hash_universo,
        "catalog_sha256": calcular_hash_catalogo(estado["rows"]),
        "config": _config_a_dict(config),
        "config_sha256": _hash_config(config),
        "control": control,
        "diferencias_universo_activo": diferencias,
    }
    _escribir_atomico(ruta_csv, _csv_descubrimiento(estado["rows"]))
    _escribir_atomico(
        ruta_json, json.dumps(metadatos, ensure_ascii=False, indent=2) + "\n",
    )
    ruta_checkpoint.unlink(missing_ok=True)
    return SnapshotDescubrimiento(
        discovery_id, ruta_csv, ruta_json, len(tickers_finales),
        hash_universo, control, diferencias,
    )
