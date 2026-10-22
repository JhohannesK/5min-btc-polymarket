#!/usr/bin/env python3
"""Unit tests for TWAP settle winner and position match. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_settle_cmp import position_won, settle_winner


class TestSettleWinner(unittest.TestCase):
    def test_should_call_up_when_final_twap_is_strictly_above_open(self):
        self.assertEqual(settle_winner(100_010.0, 100_000.0), "UP")

    def test_should_call_up_on_exact_tie(self):
        # Polymarket: Up wins iff final >= open. Equality is an Up win.
        self.assertEqual(settle_winner(76_214.23, 76_214.23), "UP")

    def test_should_call_down_when_final_twap_is_below_open(self):
        self.assertEqual(settle_winner(99_999.99, 100_000.0), "DOWN")


class TestPositionWon(unittest.TestCase):
    def test_should_match_side_case_and_whitespace(self):
        self.assertTrue(position_won("UP", "UP"))
        self.assertTrue(position_won("up", "UP"))
        self.assertTrue(position_won(" DOWN ", "DOWN"))
        self.assertFalse(position_won("UP", "DOWN"))
        self.assertFalse(position_won("down", "UP"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
