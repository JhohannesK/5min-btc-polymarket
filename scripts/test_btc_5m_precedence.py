#!/usr/bin/env python3
"""
Cancel/fill precedence and leftover degenerate-book gates.

Locks the order of GTD expiry, fair-flip, spread-widen, and fill so cancel
reasons and shadow fills cannot swap. No network. No live posting.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_entry_timing import EntryTimingConfig, evaluate_entry_timing
from btc_5m_maker_pilot import (
    MakerPilotConfig,
    MakerPilotEngine,
    ShadowQuote,
    SideBook,
    evaluate_cancel_rules,
    post_only_buy_price,
)
from btc_5m_winmore_gates import BookLevel, WinmoreConfig, evaluate_side


def _book(side: str, bid: Optional[float], ask: Optional[float], token_id: str = "tok") -> SideBook:
    return SideBook(side=side, token_id=token_id, best_bid=bid, best_ask=ask)


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


def _timing(**over) -> dict:
    base = dict(
        fair_p=0.90,
        ask=0.70,
        net_edge_bps=25.0,
        min_edge_bps=5.0,
        top_ask_notional_usd=80.0,
        cfg=EntryTimingConfig(),
    )
    base.update(over)
    return base


class TestCancelFillPrecedence(unittest.TestCase):
    def test_should_fill_not_expire_when_ask_crosses_on_the_gtd_deadline(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, gtd_ttl_sec=15.0)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            posted = eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46, "up-tok"),
                down_book=_book("DOWN", 0.54, 0.56, "dn-tok"),
                stake_usd=5.0,
                bucket=1,
            )
            crossed = eng.on_tick(
                now=1_015.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.42, 0.44, "up-tok"),
                down_book=_book("DOWN", 0.54, 0.56, "dn-tok"),
                stake_usd=5.0,
                bucket=1,
            )
        self.assertEqual(posted[0]["event"], "shadow_post")
        self.assertEqual(posted[0]["gtd_expire_at"], 1_015.0)
        fill_ev = [e for e in crossed if e.get("event") == "would_fill"]
        cancel_ev = [e for e in crossed if e.get("event") == "would_cancel"]
        self.assertEqual(len(fill_ev), 1)
        self.assertEqual(cancel_ev, [])
        self.assertEqual(eng.active.status, "would_fill")

    def test_should_attribute_gtd_expiry_not_fair_flip_when_both_fire(self):
        quote = _quote()
        book = _book("UP", 0.44, 0.45)
        cfg = MakerPilotConfig(enabled=True, cancel_on_fair_flip=True)
        d = evaluate_cancel_rules(quote, book, "down_favored", now=1_015.0, config=cfg)
        self.assertTrue(d.cancel)
        self.assertEqual(d.reason, "gtd_expired")

    def test_should_attribute_fair_flip_not_spread_widen_when_both_fire(self):
        quote = _quote()
        book = _book("UP", 0.10, 0.90)
        cfg = MakerPilotConfig(enabled=True, cancel_spread_widen_abs=0.03)
        d = evaluate_cancel_rules(quote, book, "down_favored", now=1_005.0, config=cfg)
        self.assertTrue(d.cancel)
        self.assertEqual(d.reason, "fair_flip")
        self.assertGreater(book.spread, cfg.cancel_spread_widen_abs)

    def test_should_still_gtd_expire_when_fair_flip_cancel_is_disabled(self):
        quote = _quote()
        book = _book("UP", 0.44, 0.45)
        cfg = MakerPilotConfig(enabled=True, cancel_on_fair_flip=False)
        d = evaluate_cancel_rules(quote, book, "down_favored", now=1_015.0, config=cfg)
        self.assertTrue(d.cancel)
        self.assertEqual(d.reason, "gtd_expired")


class TestZeroBidFeeAndNegativeStake(unittest.TestCase):
    def _cfg(self) -> WinmoreConfig:
        return WinmoreConfig(
            midband_enabled=False,
            min_edge_pp=0.001,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.001,
            kelly_fraction=0.5,
            min_stake_usd=0.5,
        )

    def test_should_treat_zero_bid_like_a_missing_bid_for_fees(self):
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
        zero_bid = evaluate_side(bid=0.0, **kwargs)
        missing = evaluate_side(bid=None, **kwargs)
        with_bid = evaluate_side(bid=0.60, **kwargs)
        self.assertAlmostEqual(zero_bid.details["half_spread"], 0.0)
        self.assertAlmostEqual(missing.details["half_spread"], 0.0)
        self.assertAlmostEqual(zero_bid.fee_pp, missing.fee_pp)
        self.assertAlmostEqual(with_bid.details["half_spread"], 0.05)
        self.assertTrue(zero_bid.allow, zero_bid.reason)
        self.assertFalse(with_bid.allow)
        self.assertEqual(with_bid.reason, "skip_net_edge_below_fee_aware_min")

    def test_should_not_post_when_stake_is_negative(self):
        eng = MakerPilotEngine(MakerPilotConfig(enabled=True, shadow=True))
        with redirect_stdout(io.StringIO()) as buf:
            out = eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=-5.0,
                bucket=1,
            )
        self.assertEqual(out, [])
        self.assertIsNone(eng.active)
        self.assertEqual(buf.getvalue(), "")

    def test_should_join_the_bid_when_improve_ticks_is_negative(self):
        px = post_only_buy_price(0.44, 0.46, tick_size=0.01, improve_ticks=-3)
        self.assertEqual(px, 0.44)


class TestDownSideFillAndLateEdgeEquality(unittest.TestCase):
    def test_should_fill_a_down_quote_against_the_down_book(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            posted = eng.on_tick(
                now=1_000.0,
                fair_signal="down_favored",
                up_book=_book("UP", 0.40, 0.42, "up-tok"),
                down_book=_book("DOWN", 0.58, 0.60, "dn-tok"),
                stake_usd=5.0,
                bucket=2,
            )
            filled = eng.on_tick(
                now=1_001.0,
                fair_signal="down_favored",
                up_book=_book("UP", 0.40, 0.42, "up-tok"),
                down_book=_book("DOWN", 0.56, 0.58, "dn-tok"),
                stake_usd=5.0,
                bucket=2,
            )
        self.assertEqual(posted[0]["side"], "DOWN")
        self.assertEqual(posted[0]["token_id"], "dn-tok")
        fill_ev = [e for e in filled if e.get("event") == "would_fill"]
        self.assertEqual(len(fill_ev), 1)
        self.assertEqual(fill_ev[0]["side"], "DOWN")
        self.assertEqual(eng.active.token_id, "dn-tok")

    def test_should_allow_late_exception_when_net_edge_equals_min_bps(self):
        d = evaluate_entry_timing(seconds_left=10.0, **_timing(net_edge_bps=5.0))
        self.assertEqual(d.zone, "hard_last")
        self.assertTrue(d.allow, d.reason)
        self.assertEqual(d.reason, "allow_late_polarized")

    def test_should_deny_late_exception_when_net_edge_is_just_under_min_bps(self):
        d = evaluate_entry_timing(seconds_left=10.0, **_timing(net_edge_bps=5.0 - 1e-9))
        self.assertEqual(d.zone, "hard_last")
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_hard_last_seconds_no_edge")


if __name__ == "__main__":
    unittest.main()
