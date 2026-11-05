#!/usr/bin/env python3
"""W5 top-of-book notional and min-spread. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_book_notional import best_ask_notional, min_spread
from btc_5m_maker_pilot import SideBook


class BestAskNotionalTests(unittest.TestCase):
    def test_should_return_zero_when_asks_are_empty(self):
        self.assertEqual(best_ask_notional(SimpleNamespace(asks=[])), 0.0)
        self.assertEqual(best_ask_notional(SimpleNamespace(asks=None)), 0.0)
        self.assertEqual(best_ask_notional(SimpleNamespace()), 0.0)

    def test_should_use_lowest_ask_price_times_its_size(self):
        book = SimpleNamespace(
            asks=[
                SimpleNamespace(price=0.48, size=10.0),
                SimpleNamespace(price=0.44, size=5.0),
                SimpleNamespace(price=0.50, size=100.0),
            ]
        )
        self.assertAlmostEqual(best_ask_notional(book), 0.44 * 5.0, places=8)

    def test_should_skip_non_positive_prices(self):
        book = SimpleNamespace(
            asks=[
                SimpleNamespace(price=0.0, size=50.0),
                SimpleNamespace(price=-0.10, size=50.0),
                SimpleNamespace(price=0.40, size=2.0),
            ]
        )
        self.assertAlmostEqual(best_ask_notional(book), 0.80, places=8)

    def test_should_treat_missing_size_as_zero_notional_at_touch(self):
        book = SimpleNamespace(asks=[SimpleNamespace(price=0.55)])
        self.assertEqual(best_ask_notional(book), 0.0)


class MinSpreadTests(unittest.TestCase):
    def test_should_return_none_when_both_books_lack_a_spread(self):
        up = SideBook(side="UP", best_bid=None, best_ask=None)
        dn = SideBook(side="DOWN", best_bid=0.50, best_ask=None)
        self.assertIsNone(min_spread(up, dn))

    def test_should_use_the_one_sided_spread_when_the_other_is_missing(self):
        up = SideBook(side="UP", best_bid=0.44, best_ask=0.46)
        dn = SideBook(side="DOWN", best_bid=None, best_ask=0.56)
        self.assertAlmostEqual(min_spread(up, dn), 0.02, places=8)

    def test_should_pick_the_tighter_of_two_spreads(self):
        up = SideBook(side="UP", best_bid=0.40, best_ask=0.48)
        dn = SideBook(side="DOWN", best_bid=0.51, best_ask=0.53)
        self.assertAlmostEqual(min_spread(up, dn), 0.02, places=8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
