#!/usr/bin/env python3
"""Unit tests for runner entry pick / open-fill / spread helpers.

No network. No execute / live posting.
"""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_runner_entry import (
    clob_picked_spread,
    extract_open_post,
    open_fill_matched,
    opened_from_fill,
    pick_legacy_threshold_side,
)


class LegacyThresholdPickTests(unittest.TestCase):
    def test_should_pick_higher_ask_when_both_clear_threshold(self):
        self.assertEqual(pick_legacy_threshold_side(0.72, 0.81, 0.70), ("DOWN", 0.81))
        self.assertEqual(pick_legacy_threshold_side(0.80, 0.71, 0.70), ("UP", 0.80))

    def test_should_pick_only_side_that_clears_threshold(self):
        self.assertEqual(pick_legacy_threshold_side(0.69, 0.71, 0.70), ("DOWN", 0.71))
        self.assertEqual(pick_legacy_threshold_side(0.70, 0.69, 0.70), ("UP", 0.70))

    def test_should_skip_when_both_below_or_missing(self):
        self.assertIsNone(pick_legacy_threshold_side(0.69, 0.60, 0.70))
        self.assertIsNone(pick_legacy_threshold_side(None, None, 0.70))
        self.assertIsNone(pick_legacy_threshold_side(0.69, None, 0.70))

    def test_should_break_tie_by_stable_sort_order(self):
        # equal asks: sort is stable, UP was appended first
        self.assertEqual(pick_legacy_threshold_side(0.75, 0.75, 0.70), ("UP", 0.75))


class ClobSpreadTests(unittest.TestCase):
    def test_should_take_min_of_both_defined_spreads(self):
        self.assertAlmostEqual(clob_picked_spread(0.49, 0.51, 0.40, 0.46), 0.02)

    def test_should_ignore_missing_side_instead_of_zeroing(self):
        self.assertAlmostEqual(clob_picked_spread(None, 0.51, 0.40, 0.46), 0.06)
        self.assertAlmostEqual(clob_picked_spread(0.49, 0.51, None, 0.46), 0.02)

    def test_should_return_none_when_neither_side_has_a_book(self):
        self.assertIsNone(clob_picked_spread(None, None, None, None))
        self.assertIsNone(clob_picked_spread(0.49, None, 0.40, None))

    def test_should_floor_inverted_book_at_zero(self):
        self.assertEqual(clob_picked_spread(0.52, 0.50, None, None), 0.0)


class OpenFillParseTests(unittest.TestCase):
    def test_should_take_last_order_post_result_blob(self):
        objs = [
            {"noise": 1},
            {"order_post_result": {"success": False, "status": "live"}},
            {"order_post_result": {"success": True, "status": "MATCHED"}, "token_id": "tok-up"},
        ]
        runner, post = extract_open_post(objs)
        self.assertEqual(runner["token_id"], "tok-up")
        self.assertTrue(open_fill_matched(post))

    def test_should_reject_unmatched_or_missing_post(self):
        self.assertFalse(open_fill_matched(None))
        self.assertFalse(open_fill_matched({}))
        self.assertFalse(open_fill_matched({"success": True, "status": "live"}))
        self.assertFalse(open_fill_matched({"success": False, "status": "matched"}))

    def test_should_treat_empty_order_post_result_as_unmatched(self):
        runner, post = extract_open_post([{"order_post_result": None, "token_id": "x"}])
        self.assertEqual(post, {})
        self.assertFalse(open_fill_matched(post))
        self.assertEqual(runner["token_id"], "x")

    def test_should_build_opened_from_taking_and_making_amounts(self):
        runner = {"token_id": "t-up", "entry_price": 0.71}
        post = {
            "success": True,
            "status": "matched",
            "takingAmount": "7.5",
            "makingAmount": "5.325",
            "orderID": "oid-1",
            "transactionsHashes": ["0xabc"],
        }
        opened = opened_from_fill(
            runner,
            post,
            side="UP",
            up_token="fallback-up",
            down_token="fallback-dn",
            trigger_price=0.70,
            slug="btc-updown-5m-1",
            end_iso="2026-09-25T10:05:00Z",
            bucket=1,
            opened_at="2026-09-25T10:00:00Z",
        )
        self.assertEqual(opened["token_id"], "t-up")
        self.assertEqual(opened["shares"], 7.5)
        self.assertEqual(opened["cost_usdc"], 5.325)
        self.assertEqual(opened["entry_price"], 0.71)
        self.assertEqual(opened["open_tx"], "0xabc")
        self.assertEqual(opened["open_order_id"], "oid-1")

    def test_should_fall_back_to_side_token_and_trigger_price(self):
        opened = opened_from_fill(
            {},
            {"takingAmount": 0, "makingAmount": 0, "transactionsHashes": []},
            side="DOWN",
            up_token="up",
            down_token="dn",
            trigger_price=0.73,
            slug="s",
            end_iso="e",
            bucket=2,
            opened_at="t",
        )
        self.assertEqual(opened["token_id"], "dn")
        self.assertEqual(opened["entry_price"], 0.73)
        self.assertIsNone(opened["open_tx"])
        self.assertEqual(opened["shares"], 0.0)


if __name__ == "__main__":
    unittest.main()
