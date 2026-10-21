#!/usr/bin/env python3
"""Unit tests for legacy threshold/SL, open-match, and hold-EV exit wiring.

No network. Run: python3 scripts/test_btc_5m_legacy_gates.py
"""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_legacy_gates import (
    is_runner_open_matched,
    pick_legacy_threshold_side,
    shares_from_open_post,
    should_exit_for_hold_ev,
    stop_loss_hit,
    stop_loss_trigger_price,
)


class TestLegacyStopLoss(unittest.TestCase):
    def test_stop_loss_price_scales_entry_by_one_minus_pct(self):
        self.assertAlmostEqual(stop_loss_trigger_price(0.80, 0.25), 0.60, places=10)

    def test_stop_loss_fires_when_bid_equals_trigger(self):
        self.assertTrue(stop_loss_hit(0.60, 0.60))

    def test_stop_loss_does_not_fire_when_bid_above_trigger(self):
        self.assertFalse(stop_loss_hit(0.61, 0.60))

    def test_stop_loss_ignores_missing_bid(self):
        self.assertFalse(stop_loss_hit(None, 0.60))


class TestLegacyThreshold(unittest.TestCase):
    def test_legacy_threshold_picks_higher_ask_when_both_clear(self):
        self.assertEqual(
            pick_legacy_threshold_side(0.72, 0.71, 0.70),
            ('UP', 0.72),
        )

    def test_legacy_threshold_returns_none_when_both_below(self):
        self.assertIsNone(pick_legacy_threshold_side(0.69, 0.68, 0.70))

    def test_legacy_threshold_single_side_only(self):
        self.assertEqual(
            pick_legacy_threshold_side(0.65, 0.71, 0.70),
            ('DOWN', 0.71),
        )

    def test_legacy_threshold_missing_asks_are_skipped(self):
        self.assertEqual(
            pick_legacy_threshold_side(None, 0.80, 0.70),
            ('DOWN', 0.80),
        )
        self.assertIsNone(pick_legacy_threshold_side(None, None, 0.70))


class TestOpenMatch(unittest.TestCase):
    def test_open_fill_requires_success_and_matched_status(self):
        self.assertFalse(
            is_runner_open_matched({'success': True, 'status': 'live'})
        )
        self.assertFalse(
            is_runner_open_matched({'success': False, 'status': 'matched'})
        )
        self.assertFalse(is_runner_open_matched(None))
        self.assertFalse(is_runner_open_matched('matched'))

    def test_open_fill_accepts_matched_even_when_taking_amount_zero(self):
        post = {'success': True, 'status': 'MATCHED', 'takingAmount': 0}
        self.assertTrue(is_runner_open_matched(post))
        self.assertEqual(shares_from_open_post(post), 0.0)

    def test_open_fill_treats_missing_taking_amount_as_zero_shares(self):
        post = {'success': True, 'status': 'matched'}
        self.assertTrue(is_runner_open_matched(post))
        self.assertEqual(shares_from_open_post(post), 0.0)


class TestHoldEvExitPolicy(unittest.TestCase):
    def test_hold_to_redeem_exit_only_on_sell_recommendation(self):
        self.assertFalse(should_exit_for_hold_ev('hold'))
        self.assertFalse(should_exit_for_hold_ev('marginal'))
        self.assertFalse(should_exit_for_hold_ev(''))

    def test_hold_to_redeem_exit_on_sell_recommendation(self):
        self.assertTrue(should_exit_for_hold_ev('sell'))


if __name__ == '__main__':
    unittest.main()
