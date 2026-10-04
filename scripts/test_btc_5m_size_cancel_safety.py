#!/usr/bin/env python3
"""Complementary W5/W6 safety tests: size denies, flip opt-out, fill-before-cancel.

No network. No execute / live posting.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_maker_pilot import (
    MakerPilotConfig,
    MakerPilotEngine,
    ShadowQuote,
    SideBook,
    book_would_fill_buy,
    evaluate_cancel_rules,
    rebate_estimate_usd,
)
from btc_5m_winmore_gates import (
    BookLevel,
    DepthSlice,
    EntryDecision,
    cap_size_usd,
    evaluate_side,
    fee_pp,
    fractional_kelly_usd,
    select_entry,
    WinmoreConfig,
)


def _book(side: str, bid: float, ask: float, token_id: str = "tok") -> SideBook:
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


def _cfg(**kwargs) -> WinmoreConfig:
    return WinmoreConfig(**kwargs)


class FairFlipOptOutTests(unittest.TestCase):
    def test_should_leave_quote_live_when_fair_flip_cancel_is_disabled(self):
        quote = _quote(side="UP")
        book = _book("UP", 0.44, 0.45)
        cfg = MakerPilotConfig(enabled=True, cancel_on_fair_flip=False)
        d = evaluate_cancel_rules(quote, book, "down_favored", now=1_005.0, config=cfg)
        self.assertFalse(d.cancel)
        self.assertIsNone(d.reason)

    def test_engine_should_keep_resting_quote_through_a_flip_when_opted_out(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, cancel_on_fair_flip=False)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            posted = eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=1,
            )
            flipped = eng.on_tick(
                now=1_002.0,
                fair_signal="down_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=1,
            )
        self.assertEqual(posted[0]["event"], "shadow_post")
        self.assertEqual(flipped, [])
        self.assertIsNotNone(eng.active)
        self.assertEqual(eng.active.status, "live")
        self.assertEqual(eng.active.side, "UP")


class FillBeatsCancelTests(unittest.TestCase):
    def test_should_fill_not_cancel_when_ask_crosses_on_a_fair_flip_tick(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=2,
            )
            crossed = eng.on_tick(
                now=1_001.0,
                fair_signal="down_favored",
                up_book=_book("UP", 0.42, 0.44),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=2,
            )
        events = [e.get("event") for e in crossed]
        self.assertIn("would_fill", events)
        self.assertNotIn("would_cancel", events)
        self.assertEqual(eng.active.status, "would_fill")


class NonLiveQuoteSafetyTests(unittest.TestCase):
    def test_should_ignore_cancel_rules_once_a_quote_is_already_closed(self):
        quote = _quote(status="cancelled", gtd_expire_at=1_000.0)
        book = _book("UP", 0.10, 0.90)
        cfg = MakerPilotConfig(enabled=True, cancel_spread_widen_abs=0.03)
        d = evaluate_cancel_rules(quote, book, "down_favored", now=2_000.0, config=cfg)
        self.assertFalse(d.cancel)
        self.assertIsNone(d.reason)

    def test_should_not_fill_a_quote_that_is_no_longer_live(self):
        quote = _quote(status="would_fill", price=0.44)
        book = _book("UP", 0.43, 0.44)
        self.assertFalse(book_would_fill_buy(quote, book))


class RebateMakerFeeTests(unittest.TestCase):
    def test_should_add_rebate_bps_and_subtract_maker_fee(self):
        rebate, saved = rebate_estimate_usd(
            100.0,
            0.50,
            rebate_bps=10.0,
            maker_fee_rate=0.01,
        )
        self.assertAlmostEqual(saved, 1.75, places=8)
        # 10 bps of 50 USDC notional = 0.05; maker fee 1% of 50 = 0.50
        self.assertAlmostEqual(rebate, 1.75 + 0.05 - 0.50, places=8)

    def test_live_requested_mode_stays_stubbed(self):
        cfg = MakerPilotConfig(enabled=True, shadow=False, live_execute=True)
        self.assertEqual(cfg.effective_mode(), "live_requested_but_stubbed")
        public = cfg.as_public_dict()
        self.assertEqual(public["live_path"], "stubbed")
        self.assertEqual(public["mode"], "live_requested_but_stubbed")


class SizeDenyTests(unittest.TestCase):
    def test_should_deny_invalid_size_inputs(self):
        depth = DepthSlice(10.0, 7.0, 0.70, 0.0, 1)
        self.assertEqual(cap_size_usd(0.0, 0.70, depth, 10.0, 1.0)[2], "skip_invalid_size_inputs")
        self.assertEqual(cap_size_usd(5.0, 0.0, depth, 10.0, 1.0)[2], "skip_invalid_size_inputs")

    def test_should_deny_empty_depth_and_zero_kelly(self):
        empty = DepthSlice(0.0, 0.0, 0.70, 0.0, 0)
        depth = DepthSlice(10.0, 7.0, 0.70, 0.0, 1)
        self.assertEqual(
            cap_size_usd(5.0, 0.70, empty, 10.0, 1.0)[2], "skip_no_depth_within_ticks"
        )
        self.assertEqual(cap_size_usd(5.0, 0.70, depth, 0.0, 1.0)[2], "skip_kelly_cap_zero")

    def test_should_deny_when_capped_size_is_below_min_stake(self):
        depth = DepthSlice(2.0, 1.4, 0.70, 0.0, 1)
        sized, shares, deny = cap_size_usd(5.0, 0.70, depth, 1.4, min_stake_usd=2.0)
        self.assertEqual(deny, "skip_size_below_min_stake")
        self.assertEqual(sized, 0.0)
        self.assertEqual(shares, 0.0)

    def test_evaluate_side_should_deny_when_daily_loss_budget_is_gone(self):
        d = evaluate_side(
            side="UP",
            fair_p=0.85,
            ask=0.70,
            bid=0.69,
            ask_levels=[BookLevel(0.70, 100.0)],
            cfg=_cfg(
                midband_enabled=False,
                min_edge_pp=0.001,
                taker_delay_buffer_vol_scale=False,
                taker_delay_buffer_pp=0.001,
                kelly_fraction=0.5,
            ),
            stake_usd=5.0,
            remaining_loss_budget_usd=0.0,
            btc_daily_vol_pct=3.5,
        )
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_kelly_cap_zero")

    def test_evaluate_side_should_deny_when_kelly_size_is_below_min_stake(self):
        # f* = (0.80-0.70)/0.30 = 1/3; 0.25 * 1/3 * 12 = 1.0 < min_stake 2.0
        d = evaluate_side(
            side="UP",
            fair_p=0.80,
            ask=0.70,
            bid=0.69,
            ask_levels=[BookLevel(0.70, 500.0)],
            cfg=_cfg(
                midband_enabled=False,
                min_edge_pp=0.001,
                taker_delay_buffer_vol_scale=False,
                taker_delay_buffer_pp=0.001,
                kelly_fraction=0.25,
                min_stake_usd=2.0,
            ),
            stake_usd=5.0,
            remaining_loss_budget_usd=12.0,
            btc_daily_vol_pct=3.5,
        )
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_size_below_min_stake")
        self.assertAlmostEqual(d.kelly_cap_usd, 1.0, places=6)


class SelectEntryAndKellyEdgeTests(unittest.TestCase):
    def test_should_pick_the_richer_deny_when_both_sides_are_blocked(self):
        up = EntryDecision(allow=False, reason="skip_midband_taker_ban", side="UP", net_edge_pp=0.01)
        down = EntryDecision(
            allow=False, reason="skip_net_edge_below_fee_aware_min", side="DOWN", net_edge_pp=0.02
        )
        picked = select_entry(up, down)
        self.assertFalse(picked.allow)
        self.assertEqual(picked.side, "DOWN")
        self.assertEqual(picked.reason, "skip_net_edge_below_fee_aware_min")

    def test_kelly_should_be_zero_at_extreme_or_negative_entry(self):
        self.assertEqual(fractional_kelly_usd(0.80, 0.0, 0.25, 50.0), 0.0)
        self.assertEqual(fractional_kelly_usd(0.80, 1.0, 0.25, 50.0), 0.0)
        self.assertEqual(fractional_kelly_usd(0.60, 0.70, 0.25, 50.0), 0.0)

    def test_fee_pp_should_clamp_price_and_ignore_negative_spread(self):
        self.assertAlmostEqual(fee_pp(1.8, 0.0, 0.0), 0.0, places=8)
        self.assertAlmostEqual(fee_pp(-0.4, 0.0, 0.0), 0.0, places=8)
        self.assertAlmostEqual(fee_pp(0.50, -0.01, -0.02), 0.0175, places=8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
