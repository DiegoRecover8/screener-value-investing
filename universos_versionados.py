"""Universos oficiales inmutables y auditables del screener.

El descubrimiento puede producir listas amplias y cambiantes, pero una
ejecución oficial siempre resuelve una versión registrada en
``universos/manifest.json``. El hash se calcula sobre los tickers
normalizados, únicos y ordenados, por lo que representa la pertenencia al
universo y no detalles accidentales como el orden o los saltos de línea.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable


RUTA_MANIFEST_DEFECTO = Path("universos/manifest.json")
RUTA_ESPEJO_DEFECTO = Path("universo.txt")
COLUMNAS_UNIVERSO = [
    "ticker", "origen_inclusion", "region_descubrimiento",
    "sector_descubrimiento", "nota",
]
ESTADOS_UNIVERSO = {"draft", "active", "retired"}
PATRON_ID_UNIVERSO = re.compile(r"^uv_\d{4}q[1-4]_r\d{2}$")
PATRON_TICKER = re.compile(r"^[A-Z0-9.^=\-]+$")
CAMPOS_VERSION = {
    "universe_id", "path", "status", "created_at", "effective_from",
    "supersedes", "asset_type", "ticker_count", "sha256",
    "selection_method", "notes",
}


class ErrorUniversoVersionado(ValueError):
    """El manifest o uno de sus universos incumple el contrato."""


@dataclass(frozen=True)
class UniversoResuelto:
    universe_id: str
    ruta: Path
    tickers: tuple[str, ...]
    sha256: str


def normalizar_tickers(tickers: Iterable[str]) -> list[str]:
    """Normaliza y valida una lista sin ocultar duplicados."""
    normalizados: list[str] = []
    vistos: set[str] = set()
    for posicion, valor in enumerate(tickers, start=1):
        ticker = str(valor).strip().upper()
        if not ticker:
            raise ErrorUniversoVersionado(f"ticker vacío en la posición {posicion}")
        if not PATRON_TICKER.fullmatch(ticker):
            raise ErrorUniversoVersionado(f"ticker con formato no permitido: {valor!r}")
        if ticker in vistos:
            raise ErrorUniversoVersionado(f"ticker duplicado: {ticker}")
        vistos.add(ticker)
        normalizados.append(ticker)
    if not normalizados:
        raise ErrorUniversoVersionado("el universo no contiene tickers")
    return normalizados


def calcular_hash_universo(tickers: Iterable[str]) -> str:
    """SHA-256 canónico de la pertenencia al universo."""
    normalizados = normalizar_tickers(tickers)
    canonico = "\n".join(sorted(normalizados)) + "\n"
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def cargar_tickers(
    ruta: str | Path,
    exigir_esquema_oficial: bool = False,
) -> list[str]:
    """Lee miembros desde el CSV oficial o desde una lista TXT ad hoc."""
    ruta = Path(ruta)
    if not ruta.exists():
        raise ErrorUniversoVersionado(f"no existe el universo {ruta}")
    if ruta.suffix.lower() == ".csv":
        with ruta.open(newline="", encoding="utf-8") as archivo:
            lector = csv.DictReader(archivo)
            if exigir_esquema_oficial and lector.fieldnames != COLUMNAS_UNIVERSO:
                raise ErrorUniversoVersionado(
                    f"esquema incompatible en {ruta}: se esperaban "
                    f"{COLUMNAS_UNIVERSO}, pero contiene {lector.fieldnames}"
                )
            if not lector.fieldnames or "ticker" not in lector.fieldnames:
                raise ErrorUniversoVersionado(f"{ruta} no contiene la columna ticker")
            originales = [fila.get("ticker", "") for fila in lector]
    else:
        originales = ruta.read_text(encoding="utf-8").splitlines()

    tickers = normalizar_tickers(originales)
    for original, normalizado in zip(originales, tickers):
        if original != normalizado:
            raise ErrorUniversoVersionado(
                f"ticker no canónico en {ruta}: {original!r}; usa {normalizado!r}"
            )
    return tickers


def cargar_manifest(
    ruta_manifest: str | Path = RUTA_MANIFEST_DEFECTO,
) -> dict:
    ruta = Path(ruta_manifest)
    if not ruta.exists():
        raise ErrorUniversoVersionado(f"no existe el manifest {ruta}")
    try:
        manifest = json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ErrorUniversoVersionado(f"no se pudo leer {ruta}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ErrorUniversoVersionado("el manifest debe ser un objeto JSON")
    return manifest


def _resolver_ruta_manifest(ruta_manifest: Path, ruta_declarada: str) -> Path:
    relativa = Path(ruta_declarada)
    if relativa.is_absolute() or ".." in relativa.parts:
        raise ErrorUniversoVersionado(
            f"ruta de universo no segura en el manifest: {ruta_declarada}"
        )
    return ruta_manifest.parent / relativa


def _validar_fecha_iso(valor, campo: str, universe_id: str, opcional=False) -> None:
    if opcional and valor is None:
        return
    if not isinstance(valor, str) or not valor:
        raise ErrorUniversoVersionado(
            f"{campo} ausente o no válido para {universe_id}"
        )
    try:
        datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ErrorUniversoVersionado(
            f"{campo} no es una fecha ISO válida para {universe_id}: {valor!r}"
        ) from exc


def validar_manifest(
    ruta_manifest: str | Path = RUTA_MANIFEST_DEFECTO,
    ruta_espejo: str | Path | None = RUTA_ESPEJO_DEFECTO,
) -> UniversoResuelto:
    """Valida todo el registro y devuelve su único universo activo."""
    ruta_manifest = Path(ruta_manifest)
    manifest = cargar_manifest(ruta_manifest)
    if manifest.get("schema_version") != 1:
        raise ErrorUniversoVersionado("schema_version del manifest debe ser 1")
    versiones = manifest.get("universes")
    if not isinstance(versiones, list) or not versiones:
        raise ErrorUniversoVersionado("el manifest no contiene universos")

    ids: set[str] = set()
    rutas: set[str] = set()
    activos: list[UniversoResuelto] = []
    ids_declarados = {v.get("universe_id") for v in versiones if isinstance(v, dict)}
    for version in versiones:
        if not isinstance(version, dict):
            raise ErrorUniversoVersionado("cada universo del manifest debe ser un objeto")
        faltantes = sorted(CAMPOS_VERSION - set(version))
        if faltantes:
            raise ErrorUniversoVersionado(
                "faltan campos en una versión del manifest: " + ", ".join(faltantes)
            )
        universe_id = version.get("universe_id", "")
        if not PATRON_ID_UNIVERSO.fullmatch(str(universe_id)):
            raise ErrorUniversoVersionado(f"universe_id no válido: {universe_id!r}")
        if universe_id in ids:
            raise ErrorUniversoVersionado(f"universe_id duplicado: {universe_id}")
        ids.add(universe_id)

        estado = version.get("status")
        if estado not in ESTADOS_UNIVERSO:
            raise ErrorUniversoVersionado(
                f"estado no válido para {universe_id}: {estado!r}"
            )
        _validar_fecha_iso(version.get("created_at"), "created_at", universe_id)
        _validar_fecha_iso(
            version.get("effective_from"), "effective_from", universe_id,
            opcional=estado == "draft",
        )
        if not isinstance(version.get("selection_method"), str) or not version[
            "selection_method"
        ]:
            raise ErrorUniversoVersionado(
                f"selection_method ausente para {universe_id}"
            )
        if version.get("asset_type") != "equity":
            raise ErrorUniversoVersionado(
                f"{universe_id} debe declarar asset_type='equity'"
            )
        supersedes = version.get("supersedes")
        if supersedes is not None and (
            supersedes == universe_id or supersedes not in ids_declarados
        ):
            raise ErrorUniversoVersionado(
                f"supersedes no válido para {universe_id}: {supersedes!r}"
            )

        ruta_declarada = version.get("path", "")
        if not ruta_declarada or ruta_declarada in rutas:
            raise ErrorUniversoVersionado(
                f"ruta ausente o duplicada para {universe_id}: {ruta_declarada!r}"
            )
        rutas.add(ruta_declarada)
        ruta_universo = _resolver_ruta_manifest(ruta_manifest, ruta_declarada)
        tickers = cargar_tickers(ruta_universo, exigir_esquema_oficial=True)
        hash_real = calcular_hash_universo(tickers)
        if version.get("ticker_count") != len(tickers):
            raise ErrorUniversoVersionado(
                f"ticker_count incoherente para {universe_id}: "
                f"{version.get('ticker_count')} != {len(tickers)}"
            )
        if version.get("sha256") != hash_real:
            raise ErrorUniversoVersionado(
                f"hash incoherente para {universe_id}: el archivo fue modificado"
            )
        if estado == "active":
            activos.append(UniversoResuelto(
                universe_id, ruta_universo, tuple(tickers), hash_real,
            ))

    active_id = manifest.get("active_universe_id")
    if len(activos) != 1 or activos[0].universe_id != active_id:
        raise ErrorUniversoVersionado(
            "debe existir un único universo active y coincidir con active_universe_id"
        )
    activo = activos[0]
    if ruta_espejo is not None:
        espejo = cargar_tickers(ruta_espejo)
        if espejo != list(activo.tickers):
            raise ErrorUniversoVersionado(
                f"{ruta_espejo} no coincide exactamente con {activo.universe_id}"
            )
    return activo


def cargar_universo_activo(
    ruta_manifest: str | Path = RUTA_MANIFEST_DEFECTO,
    ruta_espejo: str | Path | None = RUTA_ESPEJO_DEFECTO,
) -> UniversoResuelto:
    return validar_manifest(ruta_manifest, ruta_espejo)


def comparar_tickers(origen: Iterable[str], destino: Iterable[str]) -> dict:
    """Devuelve altas, bajas y permanencias entre dos listas."""
    conjunto_origen = set(normalizar_tickers(origen))
    conjunto_destino = set(normalizar_tickers(destino))
    return {
        "anadidos": sorted(conjunto_destino - conjunto_origen),
        "eliminados": sorted(conjunto_origen - conjunto_destino),
        "comunes": sorted(conjunto_origen & conjunto_destino),
    }


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


def activar_universo(
    universe_id: str,
    ruta_manifest: str | Path = RUTA_MANIFEST_DEFECTO,
    ruta_espejo: str | Path = RUTA_ESPEJO_DEFECTO,
) -> UniversoResuelto:
    """Activa un draft y retira el activo, manteniendo el espejo TXT."""
    ruta_manifest = Path(ruta_manifest)
    actual = validar_manifest(ruta_manifest, ruta_espejo)
    if universe_id == actual.universe_id:
        return actual

    manifest = cargar_manifest(ruta_manifest)
    indice = {
        version["universe_id"]: version for version in manifest["universes"]
    }
    if universe_id not in indice:
        raise ErrorUniversoVersionado(f"universo no registrado: {universe_id}")
    destino = indice[universe_id]
    if destino["status"] != "draft":
        raise ErrorUniversoVersionado(
            f"solo se puede activar un draft; {universe_id} está {destino['status']}"
        )
    for version in manifest["universes"]:
        if version["universe_id"] == actual.universe_id:
            version["status"] = "retired"
        elif version["universe_id"] == universe_id:
            version["status"] = "active"
            version["effective_from"] = date.today().isoformat()
    manifest["active_universe_id"] = universe_id

    ruta_destino = _resolver_ruta_manifest(ruta_manifest, destino["path"])
    tickers_destino = cargar_tickers(ruta_destino, exigir_esquema_oficial=True)
    contenido_espejo = "\n".join(tickers_destino) + "\n"
    contenido_manifest = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    # Si el proceso se interrumpe entre reemplazos, la validación posterior
    # bloquea la Action en vez de ejecutar un universo ambiguo.
    _escribir_atomico(Path(ruta_espejo), contenido_espejo)
    _escribir_atomico(ruta_manifest, contenido_manifest)
    return validar_manifest(ruta_manifest, ruta_espejo)
