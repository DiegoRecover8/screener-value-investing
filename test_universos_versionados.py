"""Tests sin red para el registro de universos inmutables."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from universos_versionados import (
    COLUMNAS_UNIVERSO,
    ErrorUniversoVersionado,
    activar_universo,
    calcular_hash_universo,
    cargar_manifest,
    cargar_tickers,
    comparar_tickers,
    validar_manifest,
)


def _crear_version(
    raiz: Path,
    universe_id: str,
    tickers: list[str],
    estado: str,
    supersedes=None,
) -> dict:
    ruta = raiz / "universos" / "oficiales" / f"{universe_id}.csv"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    lineas = [",".join(COLUMNAS_UNIVERSO)] + [f"{t},manual,,," for t in tickers]
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return {
        "universe_id": universe_id,
        "path": f"oficiales/{universe_id}.csv",
        "status": estado,
        "created_at": "2026-08-31T00:00:00Z",
        "effective_from": "2026-08-31" if estado == "active" else None,
        "supersedes": supersedes,
        "asset_type": "equity",
        "ticker_count": len(tickers),
        "sha256": calcular_hash_universo(tickers),
        "selection_method": "test",
        "notes": "",
    }


def _crear_registro(raiz: Path, con_draft: bool = False):
    actual = ["AAA", "BBB.MC"]
    versiones = [_crear_version(raiz, "uv_2026q3_r01", actual, "active")]
    if con_draft:
        versiones.append(_crear_version(
            raiz, "uv_2026q4_r01", ["BBB.MC", "CCC"], "draft",
            supersedes="uv_2026q3_r01",
        ))
    manifest = {
        "schema_version": 1,
        "active_universe_id": "uv_2026q3_r01",
        "universes": versiones,
    }
    ruta_manifest = raiz / "universos" / "manifest.json"
    ruta_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    ruta_espejo = raiz / "universo.txt"
    ruta_espejo.write_text("AAA\nBBB.MC\n", encoding="utf-8")
    return ruta_manifest, ruta_espejo


class TestUniversosVersionados(unittest.TestCase):
    def test_gitignore_no_excluye_los_universos_oficiales(self):
        reglas = Path(".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("!universos/oficiales/*.csv", reglas)
        self.assertIn("!universos/descubrimiento/disc_*.csv", reglas)

    def test_manifest_real_registra_exactamente_el_universo_legacy(self):
        activo = validar_manifest()
        self.assertEqual(activo.universe_id, "uv_2026q3_r01")
        self.assertEqual(len(activo.tickers), 205)
        self.assertEqual(
            activo.sha256,
            "3aadf46d002d1e98dc663ed7e21ae6b1e3a21281df8b4db47905082379c63120",
        )

    def test_hash_no_depende_del_orden_pero_rechaza_duplicados(self):
        self.assertEqual(
            calcular_hash_universo(["AAA", "BBB.MC"]),
            calcular_hash_universo(["BBB.MC", "AAA"]),
        )
        with self.assertRaisesRegex(ErrorUniversoVersionado, "duplicado"):
            calcular_hash_universo(["AAA", "aaa"])

    def test_detecta_si_un_archivo_registrado_fue_modificado(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            manifest, espejo = _crear_registro(raiz)
            ruta_universo = raiz / "universos/oficiales/uv_2026q3_r01.csv"
            ruta_universo.write_text(
                ruta_universo.read_text(encoding="utf-8") + "CCC,manual,,,\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ErrorUniversoVersionado, "ticker_count"):
                validar_manifest(manifest, espejo)

    def test_detecta_un_espejo_distinto_del_activo(self):
        with TemporaryDirectory() as tmp:
            manifest, espejo = _crear_registro(Path(tmp))
            espejo.write_text("AAA\n", encoding="utf-8")
            with self.assertRaisesRegex(ErrorUniversoVersionado, "no coincide"):
                validar_manifest(manifest, espejo)

    def test_compara_altas_bajas_y_permanencias(self):
        diferencias = comparar_tickers(["AAA", "BBB"], ["BBB", "CCC"])
        self.assertEqual(diferencias["anadidos"], ["CCC"])
        self.assertEqual(diferencias["eliminados"], ["AAA"])
        self.assertEqual(diferencias["comunes"], ["BBB"])

    def test_acepta_catalogo_de_descubrimiento_para_comparar(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "discovery.csv"
            ruta.write_text(
                "ticker,tipo_activo,categoria,region\nAAA,accion,Technology,us\n",
                encoding="utf-8",
            )
            self.assertEqual(cargar_tickers(ruta), ["AAA"])

    def test_activar_draft_retira_anterior_y_actualiza_espejo(self):
        with TemporaryDirectory() as tmp:
            manifest, espejo = _crear_registro(Path(tmp), con_draft=True)
            activo = activar_universo("uv_2026q4_r01", manifest, espejo)
            registro = cargar_manifest(manifest)

        self.assertEqual(activo.universe_id, "uv_2026q4_r01")
        self.assertEqual(list(activo.tickers), ["BBB.MC", "CCC"])
        self.assertEqual(registro["active_universe_id"], "uv_2026q4_r01")
        self.assertEqual(
            [v["status"] for v in registro["universes"]],
            ["retired", "active"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
