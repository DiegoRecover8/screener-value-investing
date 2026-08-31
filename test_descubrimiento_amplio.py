"""Tests sin red para el universo amplio de descubrimiento."""

import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from descubrimiento_amplio import (
    CUOTAS_DESARROLLADOS_V1,
    ConfiguracionDescubrimiento,
    ErrorDescubrimiento,
    configuracion_desarrollados_v1,
    generar_snapshot_descubrimiento,
)


DISCOVERY_ID = "disc_20260831T120000000000Z"


def _config(umbral=0.75):
    return ConfiguracionDescubrimiento(
        perfil="test",
        cuotas_region={"us": 2, "es": 1},
        sectores=("Technology", "Energy"),
        umbral_buckets=umbral,
        reintentos=1,
    )


class TestDescubrimientoAmplio(unittest.TestCase):
    def test_perfil_v1_cubre_22_regiones_y_capacidad_2070(self):
        config = configuracion_desarrollados_v1()
        self.assertEqual(len(CUOTAS_DESARROLLADOS_V1), 22)
        self.assertEqual(
            sum(config.cuotas_region.values()) * len(config.sectores),
            2070,
        )

    def test_genera_csv_json_controlado_sin_activar_nada(self):
        def consultar(region, sector, cuota, precio, volumen):
            respuestas = {
                ("us", "Technology"): ["AAA", "DUP"],
                ("us", "Energy"): ["BBB", "DUP"],
                ("es", "Technology"): ["CCC"],
            }
            if (region, sector) == ("es", "Energy"):
                raise TimeoutError("Yahoo no responde")
            return respuestas[(region, sector)][:cuota]

        with TemporaryDirectory() as tmp:
            snapshot = generar_snapshot_descubrimiento(
                _config(), tmp,
                momento=pd.Timestamp("2026-08-31T12:00:00Z"),
                discovery_id=DISCOVERY_ID,
                consultar_bucket=consultar,
                tickers_activos=["AAA", "OLD"],
            )
            with snapshot.ruta_csv.open(newline="", encoding="utf-8") as archivo:
                filas = list(csv.DictReader(archivo))
            metadatos = json.loads(snapshot.ruta_json.read_text(encoding="utf-8"))
            checkpoint = Path(tmp) / f"checkpoint_{DISCOVERY_ID}.json"
            self.assertFalse(checkpoint.exists())

        self.assertEqual([f["ticker"] for f in filas], ["AAA", "DUP", "BBB", "CCC"])
        self.assertEqual(snapshot.control["buckets_exitosos"], 3)
        self.assertEqual(snapshot.control["buckets_fallidos"], 1)
        self.assertEqual(snapshot.control["resultados_brutos"], 5)
        self.assertEqual(snapshot.control["duplicados"], 1)
        self.assertEqual(snapshot.control["capacidad_teorica"], 6)
        self.assertEqual(metadatos["sha256"], snapshot.sha256)
        self.assertEqual(snapshot.diferencias_activo["anadidos"], ["BBB", "CCC", "DUP"])
        self.assertEqual(snapshot.diferencias_activo["eliminados"], ["OLD"])

    def test_fallo_bajo_umbral_deja_checkpoint_y_se_puede_reanudar(self):
        llamadas_primera = []

        def primera(region, sector, cuota, precio, volumen):
            llamadas_primera.append((region, sector))
            if (region, sector) == ("es", "Energy"):
                raise ConnectionError("caído")
            return [f"{region}{sector[0]}".upper()]

        with TemporaryDirectory() as tmp:
            ruta_checkpoint = Path(tmp) / f"checkpoint_{DISCOVERY_ID}.json"
            with self.assertRaisesRegex(ErrorDescubrimiento, "reanuda con"):
                generar_snapshot_descubrimiento(
                    _config(umbral=1.0), tmp,
                    discovery_id=DISCOVERY_ID,
                    consultar_bucket=primera,
                )
            self.assertTrue(ruta_checkpoint.exists())
            self.assertFalse((Path(tmp) / f"{DISCOVERY_ID}.csv").exists())

            llamadas_reanudacion = []

            def segunda(region, sector, cuota, precio, volumen):
                llamadas_reanudacion.append((region, sector))
                return ["RECUPERADA"]

            snapshot = generar_snapshot_descubrimiento(
                ruta_checkpoint=ruta_checkpoint,
                consultar_bucket=segunda,
            )
            self.assertFalse(ruta_checkpoint.exists())

        self.assertEqual(len(llamadas_primera), 4)
        self.assertEqual(llamadas_reanudacion, [("es", "Energy")])
        self.assertEqual(snapshot.control["buckets_exitosos"], 4)
        self.assertEqual(snapshot.ticker_count, 4)

    def test_rechaza_un_id_que_pueda_escapar_del_directorio(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ErrorDescubrimiento, "discovery_id"):
                generar_snapshot_descubrimiento(
                    _config(), tmp, discovery_id="../../fuera",
                    consultar_bucket=lambda *args: ["AAA"],
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
