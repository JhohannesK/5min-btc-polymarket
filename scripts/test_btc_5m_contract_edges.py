#!/usr/bin/env python3
"""Complementary money-path contracts still untested on main.

No network. No live posting.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_maker_pilot import adverse_selection_usd, rebate_estimate_usd
from btc_5m_state_tracker import StateTracker, TicketState
from btc_5m_twap_fair import (
    TWAPSnapshot,
    calculate_hold_ev,
    estimate_trade_edge,
    locked_level_from_path,
)
from log_twap_settle import log_twap_settle


def _ticket(bucket: int = 1) -> TicketState:
    return TicketState(
        bucket=bucket,
        market_slug=f"btc-updown-5m-{bucket}",
        opened_at=1_000.0,
        side="UP",
        entry_price=0.42,
        shares=10.0,
        cost_usdc=4.20,
        token_id="tok",
        status="open",
    )


class FakeSettleTracker:
    def __init__(self, twap: float) -> None:
        self.twap = twap

    def get_current_twap(self, allow_fallback: bool = True) -> TWAPSnapshot:
        del allow_fallback
        return TWAPSnapshot(
            timestamp=1.0,
            twap_60s=self.twap,
            window_seconds=60,
            series_id="btc-usd-twap-60s",
        )


class TestSameBucketReopen(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tracker = StateTracker(
            Path(self.tmp.name),
            daily_loss_limit_usd=50.0,
            max_trades_per_day=20,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_should_allow_reopen_of_the_same_bucket_after_close(self):
        self.tracker.open_ticket(_ticket(42))
        self.tracker.close_ticket(42, pnl_usdc=-1.0)

        ok, reason = self.tracker.can_open_ticket(42)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")

        self.tracker.open_ticket(_ticket(42))
        state = self.tracker.load_state()
        self.assertEqual(state.trades_count, 2)
        self.assertIn(42, state.active_tickets)
        self.assertEqual(len(state.closed_tickets), 1)


class TestUnclampedFeeMath(unittest.TestCase):
    def test_should_treat_ask_above_one_as_negative_taker_fee_and_buy(self):
        out = estimate_trade_edge(1.2, 1.2, 1.2, shares=1.0, taker_fee_rate=0.07)
        unclamped_fee = 0.07 * 1.2 * (1.0 - 1.2)
        self.assertLess(unclamped_fee, 0.0)
        self.assertAlmostEqual(out["taker_fee_per_share_usd"], unclamped_fee, places=10)
        self.assertGreater(out["net_edge_bps"], 5.0)
        self.assertEqual(out["signal"], "buy")

    def test_should_flip_hold_to_sell_when_bid_above_one_credits_fee(self):
        out = calculate_hold_ev(1.2, 1.2, shares=1.0, taker_fee_rate=0.07)
        self.assertLess(out["sell_fee"], 0.0)
        self.assertEqual(out["recommendation"], "sell")

    def test_should_leave_rebate_bps_leg_unclamped_when_taker_saved_clamps(self):
        rebate, saved = rebate_estimate_usd(
            100.0,
            1.25,
            rebate_bps=100.0,
            taker_fee_rate=0.07,
            maker_fee_rate=0.0,
        )
        self.assertAlmostEqual(saved, 0.0, places=10)
        self.assertAlmostEqual(rebate, 1.25, places=10)


class TestLockedLevelCutoff(unittest.TestCase):
    def test_should_include_path_sample_at_settle_start_and_drop_earlier(self):
        now = 1_000_000.0
        seconds_left = 20.0
        settle_start = now + seconds_left - 60.0
        at_bar = TWAPSnapshot(
            timestamp=settle_start,
            twap_60s=101_000.0,
            window_seconds=60,
            receipt_ts=settle_start,
        )
        before_bar = TWAPSnapshot(
            timestamp=settle_start - 1e-6,
            twap_60s=99_000.0,
            window_seconds=60,
            receipt_ts=settle_start - 1e-6,
        )
        included = locked_level_from_path([at_bar], now=now, seconds_left=seconds_left)
        excluded = locked_level_from_path([before_bar], now=now, seconds_left=seconds_left)
        self.assertAlmostEqual(included, 101_000.0, places=6)
        self.assertIsNone(excluded)

    def test_should_fall_back_to_timestamp_when_receipt_ts_is_missing(self):
        now = 1_000_000.0
        seconds_left = 20.0
        settle_start = now + seconds_left - 60.0
        snap = TWAPSnapshot(
            timestamp=settle_start,
            twap_60s=100_500.0,
            window_seconds=60,
            receipt_ts=None,
        )
        locked = locked_level_from_path([snap], now=now, seconds_left=seconds_left)
        self.assertAlmostEqual(locked, 100_500.0, places=6)


class TestSettleSideCase(unittest.TestCase):
    def test_should_mark_lowercase_up_as_a_loss_on_an_up_tie(self):
        tracker = FakeSettleTracker(100_000.0)
        with patch("log_twap_settle.ChainlinkTWAPTracker", return_value=tracker):
            lower = log_twap_settle("btc-updown-5m-1", 100_000.0, "up", allow_fallback=False)
            upper = log_twap_settle("btc-updown-5m-1", 100_000.0, "UP", allow_fallback=False)
        self.assertIsNotNone(lower)
        self.assertIsNotNone(upper)
        assert lower is not None and upper is not None
        self.assertEqual(lower["result"], "UP")
        self.assertFalse(lower["position_wins"])
        self.assertTrue(upper["position_wins"])


class TestAdverseSideAlias(unittest.TestCase):
    def test_should_invert_adverse_sign_for_side_outside_up_down_buy(self):
        mid_drop = adverse_selection_usd("UP", 10.0, mid_at_quote=0.50, mid_now=0.48)
        inverted = adverse_selection_usd("SELL", 10.0, mid_at_quote=0.50, mid_now=0.48)
        self.assertAlmostEqual(mid_drop, 0.20, places=6)
        self.assertAlmostEqual(inverted, -0.20, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
