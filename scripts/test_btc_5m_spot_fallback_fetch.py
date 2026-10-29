#!/usr/bin/env python3
"""Dry-run spot TWAP fetch orchestration. No network. No execute."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_spot_fallback import SPOT_TWAP_SOURCES, spot_twap_from_sources


class SpotTwapFromSourcesTests(unittest.TestCase):
    def test_should_average_positive_prices_from_both_sources(self):
        fetched: list[tuple[str, str]] = []

        def fetch_price(name: str, url: str) -> float:
            fetched.append((name, url))
            return {"binance": 100_000.0, "coinbase": 102_000.0}[name]

        self.assertEqual(spot_twap_from_sources(fetch_price), 101_000.0)
        self.assertEqual(fetched, list(SPOT_TWAP_SOURCES))

    def test_should_use_the_surviving_source_when_the_other_raises(self):
        def fetch_price(name: str, url: str) -> float:
            del url
            if name == "binance":
                raise TimeoutError("binance down")
            return 99_500.0

        self.assertEqual(spot_twap_from_sources(fetch_price), 99_500.0)

    def test_should_return_none_when_every_source_fails(self):
        def fetch_price(name: str, url: str) -> float:
            del name, url
            raise ConnectionError("offline")

        self.assertIsNone(spot_twap_from_sources(fetch_price))

    def test_should_ignore_zero_and_negative_ticks(self):
        def fetch_price(name: str, url: str):
            del url
            if name == "binance":
                return 0.0
            return -1.0

        self.assertIsNone(spot_twap_from_sources(fetch_price))

    def test_should_skip_none_and_average_the_rest(self):
        def fetch_price(name: str, url: str):
            del url
            if name == "binance":
                return None
            return 88_000.0

        self.assertEqual(spot_twap_from_sources(fetch_price), 88_000.0)

    def test_should_not_fetch_unknown_sources_in_the_default_list(self):
        names = [name for name, _url in SPOT_TWAP_SOURCES]
        self.assertEqual(names, ["binance", "coinbase"])
        self.assertTrue(
            SPOT_TWAP_SOURCES[0][1].startswith("https://api.binance.com/")
        )
        self.assertTrue(
            SPOT_TWAP_SOURCES[1][1].startswith("https://api.coinbase.com/")
        )


if __name__ == "__main__":
    unittest.main()
