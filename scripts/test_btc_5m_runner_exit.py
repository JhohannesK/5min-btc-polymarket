#!/usr/bin/env python3
"""Unit tests for runner exit / close-limit / PnL helpers.

No network. No execute / live posting.
"""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_runner_exit import (
    classify_close_result,
    close_succeeded,
    clamp_limit_price,
    crypto_taker_fee_usdc,
    force_close_limit_price,
    gtc_fallback_limit_price,
    mark_held_to_redeem_on_time_exit,
    session_pnl_bundle,
    should_exit_on_hold_ev,
    stop_loss_price,
    stop_loss_triggered,
)


class StopLossTests(unittest.TestCase):
    def test_should_compute_sl_from_entry_pct(self):
        self.assertAlmostEqual(stop_loss_price(0.70, 0.25), 0.525)
        self.assertAlmostEqual(stop_loss_price(0.70, 0.30), 0.49)

    def test_should_trigger_when_bid_at_or_below_sl(self):
        sl = stop_loss_price(0.70, 0.25)
        self.assertTrue(stop_loss_triggered(sl, sl))
        self.assertTrue(stop_loss_triggered(0.50, sl))
        self.assertFalse(stop_loss_triggered(0.53, sl))

    def test_should_not_trigger_when_bid_missing(self):
        self.assertFalse(stop_loss_triggered(None, 0.525))


class HoldRedeemExitTests(unittest.TestCase):
    def test_should_mark_held_only_when_exit_window_is_20s_or_less(self):
        self.assertTrue(mark_held_to_redeem_on_time_exit(20))
        self.assertTrue(mark_held_to_redeem_on_time_exit(5))
        self.assertFalse(mark_held_to_redeem_on_time_exit(21))

    def test_should_exit_only_on_sell_recommendation(self):
        self.assertTrue(should_exit_on_hold_ev("sell"))
        self.assertFalse(should_exit_on_hold_ev("hold"))
        self.assertFalse(should_exit_on_hold_ev("HOLD"))
        self.assertFalse(should_exit_on_hold_ev(""))


class CloseLimitTests(unittest.TestCase):
    def test_should_clamp_limit_to_tick_band(self):
        self.assertEqual(clamp_limit_price(-0.05), 0.01)
        self.assertEqual(clamp_limit_price(1.2), 0.99)
        self.assertEqual(clamp_limit_price(0.44), 0.44)

    def test_should_post_gtc_one_cent_below_bid(self):
        self.assertAlmostEqual(gtc_fallback_limit_price(0.62, 0.70), 0.61)

    def test_should_use_fallback_px_when_bid_missing(self):
        self.assertAlmostEqual(gtc_fallback_limit_price(None, 0.70), 0.70)

    def test_should_clamp_gtc_when_bid_is_near_zero(self):
        self.assertEqual(gtc_fallback_limit_price(0.0, 0.70), 0.01)

    def test_should_force_close_two_cents_below_bid(self):
        self.assertAlmostEqual(force_close_limit_price(0.62), 0.60)

    def test_should_force_close_at_floor_when_bid_missing(self):
        self.assertEqual(force_close_limit_price(None), 0.01)


class CloseResultTests(unittest.TestCase):
    def test_should_succeed_on_matched_status(self):
        self.assertTrue(close_succeeded(True, "matched", 0.0))

    def test_should_succeed_when_cash_returned_without_matched(self):
        self.assertTrue(close_succeeded(True, "live", 4.2))

    def test_should_fail_when_success_flag_is_not_true(self):
        self.assertFalse(close_succeeded(True, "live", 0.0))
        self.assertFalse(close_succeeded("true", "matched", 5.0))
        self.assertFalse(close_succeeded(False, "matched", 5.0))

    def test_should_classify_done_skipped_and_failed(self):
        self.assertEqual(classify_close_result(True, None), ("done", "closed"))
        self.assertEqual(
            classify_close_result(False, "zero_effective_shares"),
            ("incomplete_close_skipped", "failed"),
        )
        self.assertEqual(
            classify_close_result(False, None),
            ("incomplete_close_failed", "failed"),
        )
        self.assertEqual(
            classify_close_result(False, ""),
            ("incomplete_close_failed", "failed"),
        )


class SessionPnlTests(unittest.TestCase):
    def test_should_leave_pnl_none_when_close_usdc_is_zero(self):
        bundle = session_pnl_bundle(
            {"cost_usdc": 5.0, "shares": 10.0, "entry_price": 0.50},
            {"close_usdc": 0},
        )
        self.assertIsNone(bundle["realized_cashflow_pnl_usdc"])
        self.assertIsNone(bundle["fee_estimates"])

    def test_should_compute_gross_and_net_pnl(self):
        opened = {"cost_usdc": 5.0, "shares": 10.0, "entry_price": 0.50}
        closed = {"close_usdc": 7.0}
        bundle = session_pnl_bundle(opened, closed)
        self.assertEqual(bundle["realized_cashflow_pnl_usdc"], 2.0)
        entry_fee = crypto_taker_fee_usdc(10.0, 0.50)
        close_fee = crypto_taker_fee_usdc(10.0, 0.70)
        self.assertEqual(bundle["fee_estimates"]["entry_fee_usdc"], entry_fee)
        self.assertEqual(bundle["fee_estimates"]["close_fee_usdc"], close_fee)
        self.assertEqual(
            bundle["net_pnl_estimate_usdc"],
            round(2.0 - entry_fee - close_fee, 6),
        )

    def test_should_treat_loss_as_negative_gross(self):
        bundle = session_pnl_bundle(
            {"cost_usdc": 5.0, "shares": 10.0, "entry_price": 0.50},
            {"close_usdc": 3.0},
        )
        self.assertEqual(bundle["realized_cashflow_pnl_usdc"], -2.0)

    def test_should_skip_close_fee_when_shares_are_zero(self):
        bundle = session_pnl_bundle(
            {"cost_usdc": 5.0, "shares": 0.0, "entry_price": 0.50},
            {"close_usdc": 1.0},
        )
        self.assertEqual(bundle["fee_estimates"]["close_fee_usdc"], 0.0)


if __name__ == "__main__":
    unittest.main()
