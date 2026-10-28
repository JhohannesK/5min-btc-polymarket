#!/usr/bin/env python3
"""Binance/Coinbase spot JSON -> price. No network."""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_twap_fair import spot_price_from_payload


class SpotPriceFromPayloadTests(unittest.TestCase):
    def test_should_parse_binance_string_price(self):
        self.assertAlmostEqual(
            spot_price_from_payload("binance", {"price": "101234.50"}),
            101234.5,
        )

    def test_should_parse_coinbase_nested_amount(self):
        self.assertAlmostEqual(
            spot_price_from_payload("coinbase", {"data": {"amount": "99000.25"}}),
            99000.25,
        )

    def test_should_reject_zero_and_negative_prices(self):
        self.assertIsNone(spot_price_from_payload("binance", {"price": 0}))
        self.assertIsNone(spot_price_from_payload("binance", {"price": "-1"}))
        self.assertIsNone(spot_price_from_payload("coinbase", {"data": {"amount": 0}}))

    def test_should_reject_missing_fields_instead_of_inventing_spot(self):
        self.assertIsNone(spot_price_from_payload("binance", {}))
        self.assertIsNone(spot_price_from_payload("coinbase", {}))
        self.assertIsNone(spot_price_from_payload("coinbase", {"data": {}}))

    def test_should_reject_unknown_source_and_unusable_payloads(self):
        self.assertIsNone(spot_price_from_payload("kraken", {"price": "1"}))
        self.assertIsNone(spot_price_from_payload("binance", {"price": "n/a"}))
        self.assertIsNone(spot_price_from_payload("binance", ["not", "an", "object"]))
        self.assertIsNone(spot_price_from_payload("coinbase", {"data": "101000"}))


if __name__ == "__main__":
    unittest.main()
