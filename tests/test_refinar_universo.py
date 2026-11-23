"""Tests sin red para excluir duales observados y rellenar sus plazas."""

import csv
import json
import shutil
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from refinar_universo import (
    ErrorRefinadoUniverso,
    cargar_observacion_oficial,
    generar_draft_refinado,
    refinar_seleccion,
)
from selector_universo import cargar_snapshot_para_seleccion
from universos_versionados import cargar_manifest, cargar_tickers, validar_manifest


SNAPSHOT_OFICIAL = "snap_20260831T155950914633Z"
DISCOVERY_ID = "disc_20260831T103225269745Z"
RUTA_DISCOVERY = Path("universos/descubrimiento") / f"{DISCOVERY_ID}.csv"
RUTA_CONTROL_DISCOVERY = RUTA_DISCOVERY.with_suffix(".json")


def _filas_csv(ruta: str | Path) -> list[dict]:
    with Path(ruta).open(newline="", encoding="utf-8") as archivo:
        return list(csv.DictReader(archivo))


class TestRefinarUniverso(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.observacion = cargar_observacion_oficial(
            SNAPSHOT_OFICIAL, "uv_2026q3_r02",
        )
        cls.snapshot = cargar_snapshot_para_seleccion(
            RUTA_DISCOVERY, RUTA_CONTROL_DISCOVERY,
        )
        cls.filas_origen = _filas_csv("universos/oficiales/uv_2026q3_r02.csv")

    def test_excluye_119_duales_y_rellena_las_119_plazas(self):
        resultado = refinar_seleccion(
            self.observacion, self.filas_origen, self.snapshot,
        )
        estrategias = [r["strategy"] for r in resultado.reemplazos]
        tickers_finales = {fila["ticker"] for fila in resultado.filas_oficiales}

        self.assertEqual(len(resultado.tickers_supervivientes), 551)
        self.assertEqual(len(resultado.tickers_descartados), 119)
        self.assertEqual(estrategias.count("mismo_bucket"), 114)
        self.assertEqual(estrategias.count("misma_region"), 5)
        self.assertEqual(len(tickers_finales), 670)
        self.assertTrue(tickers_finales.isdisjoint(resultado.tickers_descartados))
        self.assertEqual(len(resultado.diferencias_origen["comunes"]), 551)
        self.assertEqual(len(resultado.diferencias_origen["anadidos"]), 119)
        self.assertEqual(len(resultado.diferencias_origen["eliminados"]), 119)

    def test_refinado_no_depende_del_orden_del_snapshot(self):
        original = refinar_seleccion(
            self.observacion, self.filas_origen, self.snapshot,
        )
        invertido = refinar_seleccion(
            self.observacion,
            list(reversed(self.filas_origen)),
            replace(self.snapshot, filas=tuple(reversed(self.snapshot.filas))),
        )

        self.assertEqual(invertido.filas_oficiales, original.filas_oficiales)
        self.assertEqual(invertido.reemplazos, original.reemplazos)
        self.assertEqual(invertido.sha256, original.sha256)

    def test_rechaza_una_observacion_no_oficial(self):
        with TemporaryDirectory() as tmp:
            ruta_control = Path(tmp) / "ejecuciones.csv"
            columnas, filas = None, []
            with Path("ejecuciones_screener.csv").open(
                newline="", encoding="utf-8",
            ) as archivo:
                lector = csv.DictReader(archivo)
                columnas = lector.fieldnames
                filas = list(lector)
            for fila in filas:
                if fila["snapshot_id"] == SNAPSHOT_OFICIAL:
                    fila["oficial"] = "False"
            with ruta_control.open("w", newline="", encoding="utf-8") as archivo:
                escritor = csv.DictWriter(archivo, fieldnames=columnas)
                escritor.writeheader()
                escritor.writerows(filas)

            with self.assertRaisesRegex(ErrorRefinadoUniverso, "debe ser oficial"):
                cargar_observacion_oficial(
                    SNAPSHOT_OFICIAL, "uv_2026q3_r02",
                    ruta_ejecuciones=ruta_control,
                )

    def test_genera_r03_como_draft_sin_alterar_r02(self):
        with TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            universo_dir = raiz / "universos"
            oficiales = universo_dir / "oficiales"
            oficiales.mkdir(parents=True)
            manifest_real = cargar_manifest("universos/manifest.json")
            versiones = [
                json.loads(json.dumps(version))
                for version in manifest_real["universes"]
                if version["universe_id"] in {"uv_2026q3_r01", "uv_2026q3_r02"}
            ]
            for version in versiones:
                version["status"] = (
                    "active" if version["universe_id"] == "uv_2026q3_r02"
                    else "retired"
                )
            (universo_dir / "manifest.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "active_universe_id": "uv_2026q3_r02",
                    "universes": versiones,
                }),
                encoding="utf-8",
            )
            for universe_id in ("uv_2026q3_r01", "uv_2026q3_r02"):
                shutil.copyfile(
                    f"universos/oficiales/{universe_id}.csv",
                    oficiales / f"{universe_id}.csv",
                )
            espejo = raiz / "universo.txt"
            espejo.write_text(
                "\n".join(cargar_tickers(oficiales / "uv_2026q3_r02.csv")) + "\n",
                encoding="utf-8",
            )

            draft = generar_draft_refinado(
                universe_id="uv_2026q3_r03",
                universe_id_origen="uv_2026q3_r02",
                snapshot_id_oficial=SNAPSHOT_OFICIAL,
                ruta_snapshot_descubrimiento=RUTA_DISCOVERY,
                ruta_control_descubrimiento=RUTA_CONTROL_DISCOVERY,
                ruta_manifest=universo_dir / "manifest.json",
                ruta_espejo=espejo,
                directorio_auditoria=universo_dir / "selecciones",
                created_at="2026-09-01T09:00:00+00:00",
            )
            manifest = cargar_manifest(universo_dir / "manifest.json")
            activo = validar_manifest(universo_dir / "manifest.json", espejo)
            auditoria = json.loads(draft.ruta_auditoria.read_text(encoding="utf-8"))

        self.assertEqual(draft.ticker_count, 670)
        self.assertEqual(activo.universe_id, "uv_2026q3_r02")
        self.assertEqual(manifest["universes"][-1]["status"], "draft")
        self.assertEqual(auditoria["summary"]["rellenos_mismo_bucket"], 114)
        self.assertEqual(auditoria["summary"]["rellenos_misma_region"], 5)
        self.assertFalse(auditoria["activation_performed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
