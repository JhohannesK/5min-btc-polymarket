#!/usr/bin/env python3
"""
Fee formula must stay identical across maker, winmore, and TWAP edge.

Polymarket crypto taker: shares * 0.07 * p * (1-p). A later edit that
switches C from shares to USDC notional (or drops the p*(1-p) term in one
module) silently mis-sizes entries and PnL.
No network.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from btc_5m_maker_pilot import taker_fee_usd
from btc_5m_twap_fair import estimate_trade_edge
from btc_5m_winmore_gates import fee_pp


def _edge_fee(shares: float, price: float) -> float:
    d = estimate_trade_edge(
        fair_p=price,
        book_ask=price,
        book_bid=price,
        shares=shares,
        book_ask_size=shares,
    )
    return float(d["taker_fee_total_usd"])


class CryptoFeeParityTests(unittest.TestCase):
    def test_should_match_peak_fee_at_fifty_cents(self):
        shares, price = 100.0, 0.50
        expected = 1.75
        maker = taker_fee_usd(shares, price)
        winmore = fee_pp(price, 0.0, 0.0) * shares
        edge = _edge_fee(shares, price)
        self.assertAlmostEqual(maker, expected, places=10)
        self.assertAlmostEqual(winmore, expected, places=10)
        self.assertAlmostEqual(edge, expected, places=10)

    def test_should_match_at_seventy_cents(self):
        shares, price = 100.0, 0.70
        expected = 1.47
        maker = taker_fee_usd(shares, price)
        winmore = fee_pp(price, 0.0, 0.0) * shares
        edge = _edge_fee(shares, price)
        self.assertAlmostEqual(maker, expected, places=10)
        self.assertAlmostEqual(winmore, expected, places=10)
        self.assertAlmostEqual(edge, expected, places=10)

    def test_should_match_per_share_at_one_cent(self):
        shares, price = 1.0, 0.01
        expected = 0.07 * 0.01 * 0.99
        maker = taker_fee_usd(shares, price)
        winmore = fee_pp(price, 0.0, 0.0) * shares
        edge = _edge_fee(shares, price)
        self.assertAlmostEqual(maker, expected, places=10)
        self.assertAlmostEqual(winmore, expected, places=10)
        self.assertAlmostEqual(edge, expected, places=10)

    def test_should_be_zero_at_locked_zero_and_one(self):
        for price in (0.0, 1.0):
            maker = taker_fee_usd(100.0, price)
            winmore = fee_pp(price, 0.0, 0.0) * 100.0
            edge = _edge_fee(100.0, price)
            self.assertEqual(maker, 0.0)
            self.assertEqual(winmore, 0.0)
            self.assertEqual(edge, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
