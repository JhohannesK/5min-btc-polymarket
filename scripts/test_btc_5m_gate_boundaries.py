#!/usr/bin/env python3
"""
Boundary cases for W4/W5 gates and degraded-book maker cancels.

Locks inclusive vs exclusive thresholds that flip entries, size, and cancels.
No network. No execute / live posting.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_entry_timing import (
    EntryTimingConfig,
    classify_seconds_left,
    evaluate_entry_timing,
)
from btc_5m_maker_pilot import (
    MakerPilotConfig,
    ShadowQuote,
    SideBook,
    evaluate_cancel_rules,
)
from btc_5m_winmore_gates import (
    BookLevel,
    WinmoreConfig,
    evaluate_side,
    fractional_kelly_usd,
    should_block_second_clip,
)


def _timing(**over) -> dict:
    base = dict(
        fair_p=0.68,
        ask=0.58,
        net_edge_bps=25.0,
        min_edge_bps=5.0,
        top_ask_notional_usd=80.0,
        cfg=EntryTimingConfig(),
    )
    base.update(over)
    return base


def _quote(**kwargs) -> ShadowQuote:
    base = dict(
        quote_id="sh-1",
        side="UP",
        token_id="tok",
        price=0.44,
        size_shares=10.0,
        order_type="GTD",
        post_only=True,
        posted_at=1_000.0,
        gtd_expire_at=1_015.0,
        quote_mid=0.445,
        quote_spread=0.01,
        fair_signal_at_post="up_favored",
        status="live",
    )
    base.update(kwargs)
    return ShadowQuote(**base)


class TestW4DefaultWindowInclusiveBars(unittest.TestCase):
    def test_should_allow_when_abs_fair_dev_equals_min(self):
        cfg = EntryTimingConfig()
        d = evaluate_entry_timing(
            seconds_left=120.0,
            **_timing(fair_p=0.50 + cfg.min_abs_fair_dev, cfg=cfg),
        )
        self.assertEqual(d.zone, "default_window")
        self.assertAlmostEqual(d.abs_fair_dev, cfg.min_abs_fair_dev)
        self.assertTrue(d.allow, d.reason)
        self.assertEqual(d.reason, "allow_default_entry_window")

    def test_should_deny_when_abs_fair_dev_is_just_under_min(self):
        cfg = EntryTimingConfig()
        d = evaluate_entry_timing(
            seconds_left=120.0,
            **_timing(fair_p=0.50 + cfg.min_abs_fair_dev - 1e-9, cfg=cfg),
        )
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_fair_too_close_to_half")

    def test_should_allow_when_net_edge_equals_required_bps(self):
        d = evaluate_entry_timing(seconds_left=120.0, **_timing(net_edge_bps=5.0))
        self.assertTrue(d.allow, d.reason)
        self.assertEqual(d.reason, "allow_default_entry_window")

    def test_should_deny_when_net_edge_is_just_under_required_bps(self):
        d = evaluate_entry_timing(seconds_left=120.0, **_timing(net_edge_bps=5.0 - 1e-9))
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_net_edge_not_clear")


class TestW4NegativeSecondsLeft(unittest.TestCase):
    def test_should_classify_negative_seconds_as_hard_last(self):
        cfg = EntryTimingConfig()
        self.assertEqual(classify_seconds_left(-5.0, cfg), "hard_last")

    def test_should_still_allow_late_exception_after_expiry(self):
        d = evaluate_entry_timing(
            seconds_left=-1.0,
            **_timing(fair_p=0.90, ask=0.70, net_edge_bps=80.0),
        )
        self.assertEqual(d.zone, "hard_last")
        self.assertTrue(d.allow, d.reason)
        self.assertEqual(d.reason, "allow_late_polarized")

    def test_should_deny_unpolarized_negative_seconds(self):
        d = evaluate_entry_timing(seconds_left=-1.0, **_timing(fair_p=0.58, ask=0.55))
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_hard_last_seconds_not_polarized")


class TestW5SecondClipDecayEquality(unittest.TestCase):
    def test_should_allow_second_clip_when_edge_equals_decay_bar(self):
        self.assertFalse(should_block_second_clip(0.04, 0.02, 0.01, decay_ratio=0.50))

    def test_should_block_second_clip_when_edge_is_just_under_decay_bar(self):
        self.assertTrue(should_block_second_clip(0.04, 0.02 - 1e-12, 0.01, decay_ratio=0.50))


class TestW5KellyFractionCap(unittest.TestCase):
    def test_should_cap_kelly_fraction_at_one(self):
        budget = 40.0
        frac = 0.25
        at_cap = fractional_kelly_usd(1.0, 0.20, frac, budget)
        over_cap = fractional_kelly_usd(1.5, 0.20, frac, budget)
        self.assertAlmostEqual(at_cap, 10.0, places=8)
        self.assertAlmostEqual(over_cap, 10.0, places=8)


class TestW5MissingBidFee(unittest.TestCase):
    def _cfg(self) -> WinmoreConfig:
        return WinmoreConfig(
            midband_enabled=False,
            min_edge_pp=0.001,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.001,
            kelly_fraction=0.5,
            min_stake_usd=0.5,
        )

    def test_should_treat_missing_bid_as_zero_half_spread(self):
        cfg = self._cfg()
        levels = [BookLevel(0.70, 100.0)]
        kwargs = dict(
            side="UP",
            fair_p=0.73,
            ask=0.70,
            ask_levels=levels,
            cfg=cfg,
            stake_usd=5.0,
            remaining_loss_budget_usd=50.0,
            btc_daily_vol_pct=3.5,
        )
        no_bid = evaluate_side(bid=None, **kwargs)
        with_bid = evaluate_side(bid=0.60, **kwargs)
        self.assertAlmostEqual(no_bid.details["half_spread"], 0.0)
        self.assertAlmostEqual(with_bid.details["half_spread"], 0.05)
        self.assertLess(no_bid.fee_pp, with_bid.fee_pp)
        self.assertTrue(no_bid.allow, no_bid.reason)
        self.assertFalse(with_bid.allow)
        self.assertEqual(with_bid.reason, "skip_net_edge_below_fee_aware_min")


class TestMakerOneSidedBookCancel(unittest.TestCase):
    def test_should_not_spread_widen_cancel_when_bid_is_missing(self):
        quote = _quote()
        book = SideBook(side="UP", token_id="tok", best_bid=None, best_ask=0.90)
        cfg = MakerPilotConfig(enabled=True, cancel_spread_widen_abs=0.03)
        self.assertIsNone(book.spread)
        d = evaluate_cancel_rules(quote, book, "up_favored", now=1_005.0, config=cfg)
        self.assertFalse(d.cancel)
        self.assertIsNone(d.reason)

    def test_should_spread_widen_cancel_once_both_sides_are_back(self):
        quote = _quote()
        book = SideBook(side="UP", token_id="tok", best_bid=0.10, best_ask=0.90)
        cfg = MakerPilotConfig(enabled=True, cancel_spread_widen_abs=0.03)
        d = evaluate_cancel_rules(quote, book, "up_favored", now=1_005.0, config=cfg)
        self.assertTrue(d.cancel)
        self.assertEqual(d.reason, "spread_widen")


if __name__ == "__main__":
    unittest.main()
