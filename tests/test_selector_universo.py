"""Tests sin red para la selección reproducible y el registro del draft."""

import json
import shutil
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from selector_universo import (
    ErrorSeleccionUniverso,
    cargar_snapshot_para_seleccion,
    configuracion_balanced_rank_v1,
    generar_draft_desde_snapshot,
    seleccionar_snapshot,
)
from universos_versionados import (
    cargar_manifest,
    cargar_tickers,
    cargar_universo_registrado,
    validar_manifest,
)


DISCOVERY_ID = "disc_20260831T103225269745Z"
RUTA_SNAPSHOT = Path("universos/descubrimiento") / f"{DISCOVERY_ID}.csv"
RUTA_CONTROL = RUTA_SNAPSHOT.with_suffix(".json")


class TestSelectorUniverso(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = cargar_snapshot_para_seleccion(RUTA_SNAPSHOT, RUTA_CONTROL)
        # La reproducibilidad de r02 se mide siempre contra la versión que
        # sirvió de referencia al generarlo, aunque r02 se active después.
        cls.referencia_legacy = cargar_universo_registrado("uv_2026q3_r01")

    def test_perfil_real_reduce_1419_a_670_con_retencion_limitada(self):
        resultado = seleccionar_snapshot(
            self.snapshot, self.referencia_legacy.tickers,
        )

        self.assertEqual(len(self.snapshot.filas), 1419)
        self.assertEqual(len(resultado.tickers_base), 669)
        self.assertEqual(resultado.tickers_retencion, ("ITX.MC",))
        self.assertEqual(len(resultado.filas_oficiales), 670)
        self.assertEqual(len(resultado.diferencias_activo["comunes"]), 203)
        self.assertEqual(len(resultado.diferencias_activo["anadidos"]), 467)
        self.assertEqual(resultado.diferencias_activo["eliminados"], ["ONWD.BR", "SAN.MC"])

    def test_resultado_no_depende_del_orden_del_csv(self):
        original = seleccionar_snapshot(
            self.snapshot, self.referencia_legacy.tickers,
        )
        invertido = seleccionar_snapshot(
            replace(self.snapshot, filas=tuple(reversed(self.snapshot.filas))),
            self.referencia_legacy.tickers,
        )

        self.assertEqual(invertido.filas_oficiales, original.filas_oficiales)
        self.assertEqual(invertido.sha256, original.sha256)

    def test_rechaza_catalogo_cuyo_ranking_fue_modificado(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            csv_destino = raiz / RUTA_SNAPSHOT.name
            json_destino = raiz / RUTA_CONTROL.name
            contenido = RUTA_SNAPSHOT.read_text(encoding="utf-8")
            contenido = contenido.replace(
                "yfinance.screen,2026-08-31T10:32:25.269745+00:00",
                "yfinance.screen,2026-08-30T10:32:25.269745+00:00",
                1,
            )
            csv_destino.write_text(contenido, encoding="utf-8")
            shutil.copyfile(RUTA_CONTROL, json_destino)

            with self.assertRaisesRegex(ErrorSeleccionUniverso, "catálogo/ranking"):
                cargar_snapshot_para_seleccion(csv_destino, json_destino)

    def test_exige_todos_los_buckets_para_seleccionar(self):
        metadatos = json.loads(json.dumps(self.snapshot.metadatos))
        metadatos["control"]["buckets_exitosos"] = 197
        metadatos["control"]["buckets_fallidos"] = 1
        incompleto = replace(self.snapshot, metadatos=metadatos)

        with self.assertRaisesRegex(ErrorSeleccionUniverso, "198 buckets"):
            seleccionar_snapshot(incompleto, self.referencia_legacy.tickers)

    def test_genera_draft_registrado_sin_activar_ni_cambiar_espejo(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            universo_dir = raiz / "universos"
            (universo_dir / "oficiales").mkdir(parents=True)
            manifest_real = cargar_manifest("universos/manifest.json")
            version_legacy = next(
                version for version in manifest_real["universes"]
                if version["universe_id"] == "uv_2026q3_r01"
            )
            version_legacy = json.loads(json.dumps(version_legacy))
            version_legacy["status"] = "active"
            manifest_base = {
                "schema_version": 1,
                "active_universe_id": "uv_2026q3_r01",
                "universes": [version_legacy],
            }
            (universo_dir / "manifest.json").write_text(
                json.dumps(manifest_base), encoding="utf-8",
            )
            ruta_legacy = Path("universos/oficiales/uv_2026q3_r01.csv")
            shutil.copyfile(ruta_legacy, universo_dir / "oficiales/uv_2026q3_r01.csv")
            espejo = raiz / "universo.txt"
            espejo.write_text(
                "\n".join(cargar_tickers(ruta_legacy)) + "\n",
                encoding="utf-8",
            )
            contenido_espejo = espejo.read_text(encoding="utf-8")

            draft = generar_draft_desde_snapshot(
                "uv_2026q3_r02",
                RUTA_SNAPSHOT,
                RUTA_CONTROL,
                ruta_manifest=universo_dir / "manifest.json",
                ruta_espejo=espejo,
                directorio_auditoria=universo_dir / "selecciones",
                created_at="2026-08-31T13:30:00+00:00",
            )
            manifest = cargar_manifest(universo_dir / "manifest.json")
            activo = validar_manifest(universo_dir / "manifest.json", espejo)
            auditoria = json.loads(draft.ruta_auditoria.read_text(encoding="utf-8"))

            self.assertEqual(draft.ticker_count, 670)
            self.assertEqual(activo.universe_id, "uv_2026q3_r01")
            self.assertEqual(manifest["active_universe_id"], "uv_2026q3_r01")
            self.assertEqual(manifest["universes"][-1]["status"], "draft")
            self.assertEqual(espejo.read_text(encoding="utf-8"), contenido_espejo)
            self.assertFalse(auditoria["activation_performed"])
            self.assertEqual(auditoria["summary"]["seleccion_final"], 670)

    def test_perfil_fija_intervalo_objetivo_400_800(self):
        config = configuracion_balanced_rank_v1()
        self.assertEqual((config.minimo_tickers, config.maximo_tickers), (400, 800))


if __name__ == "__main__":
    unittest.main(verbosity=2)
