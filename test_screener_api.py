import json
import unittest

from screener_api import (
    API_SCHEMA_VERSION,
    analyze_fundamentals,
    analyze_universe,
    normalize_tickers,
    resolve_thresholds,
)
from test_screener import empresa_ideal


class FakeProvider:
    def __init__(self, rows):
        self.rows = rows
        self.received = None

    def descargar(self, tickers):
        self.received = tickers
        return self.rows


class TestPublicAPI(unittest.TestCase):
    def test_normalizes_and_deduplicates_preserving_order(self):
        self.assertEqual(normalize_tickers([" aapl ", "MSFT", "AAPL"]), ["AAPL", "MSFT"])

    def test_rejects_empty_and_unknown_thresholds(self):
        with self.assertRaises(ValueError):
            normalize_tickers([" "])
        with self.assertRaisesRegex(ValueError, "Unknown thresholds"):
            resolve_thresholds({"not_a_threshold": 1})

    def test_analyzes_with_injected_provider_without_writing(self):
        provider = FakeProvider([empresa_ideal(ticker="OK")])
        result = analyze_universe([" ok ", "OK"], provider=provider)
        self.assertEqual(provider.received, ["OK"])
        self.assertEqual(result.summary["candidates"], 1)
        self.assertEqual(result.requested_tickers, ("OK",))

    def test_json_contract_is_serializable_and_versioned(self):
        result = analyze_fundamentals(
            [empresa_ideal(ticker="OK")], requested_tickers=["OK"]
        )
        payload = result.to_dict()
        self.assertEqual(payload["schema_version"], API_SCHEMA_VERSION)
        self.assertEqual(payload["summary"]["candidates"], 1)
        self.assertEqual(payload["conclusion"]["status"], "candidates_found")
        self.assertTrue(payload["prompt"])
        json.dumps(payload, allow_nan=False)

    def test_can_omit_discarded_rows(self):
        result = analyze_fundamentals(
            [empresa_ideal(ticker="NO", net_income=-1)],
            requested_tickers=["NO"],
        )
        payload = result.to_dict(include_discarded=False)
        self.assertNotIn("discarded", payload)
        self.assertEqual(payload["summary"]["discarded"], 1)


if __name__ == "__main__":
    unittest.main()
