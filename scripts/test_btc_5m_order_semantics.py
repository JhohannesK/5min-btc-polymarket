#!/usr/bin/env python3
"""
Leftover W5/W6 order semantics: GTC still GTD-expires, zero-bid size,
live-submit stub, inverted-book fees, and equal-edge pick.

No network. No execute / live posting.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_maker_pilot import (
    LIVE_STUB_REASON,
    MakerPilotConfig,
    MakerPilotEngine,
    SideBook,
    post_only_buy_price,
)
from btc_5m_winmore_gates import BookLevel, WinmoreConfig, evaluate_side, select_entry


def _book(side: str, bid, ask, token_id: str = "tok") -> SideBook:
    return SideBook(side=side, token_id=token_id, best_bid=bid, best_ask=ask)


class TestGtcStillHonorsGtdTtl(unittest.TestCase):
    def test_should_gtd_expire_a_gtc_quote_when_ttl_elapses(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, order_type="GTC", gtd_ttl_sec=15.0)
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
            expired = eng.on_tick(
                now=1_015.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46, "up-tok"),
                down_book=_book("DOWN", 0.54, 0.56, "dn-tok"),
                stake_usd=5.0,
                bucket=1,
            )
        self.assertEqual(posted[0]["order_type"], "GTC")
        self.assertEqual(posted[0]["gtd_expire_at"], 1_015.0)
        cancel_ev = [e for e in expired if e.get("event") == "would_cancel"]
        self.assertEqual(len(cancel_ev), 1)
        self.assertEqual(cancel_ev[0]["reason"], "gtd_expired")
        self.assertFalse(any(e.get("event") == "shadow_post" for e in expired))

    def test_should_not_gtd_expire_gtc_when_ttl_is_zero(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, order_type="GTC", gtd_ttl_sec=0.0)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            posted = eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=2,
            )
            later = eng.on_tick(
                now=2_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=2,
            )
        self.assertEqual(posted[0]["order_type"], "GTC")
        self.assertIsNone(posted[0]["gtd_expire_at"])
        self.assertEqual(later, [])
        self.assertEqual(eng.active.status, "live")


class TestZeroBidJoinSize(unittest.TestCase):
    def test_should_join_a_zero_bid_at_the_tick_floor_not_at_zero(self):
        px = post_only_buy_price(0.0, 0.46, tick_size=0.01)
        self.assertEqual(px, 0.01)
        self.assertLess(px, 0.46)

    def test_should_size_zero_bid_posts_from_the_tick_floor(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            posted = eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.0, 0.46, "up-tok"),
                down_book=_book("DOWN", 0.54, 0.56, "dn-tok"),
                stake_usd=5.0,
                bucket=3,
            )
        self.assertEqual(posted[0]["event"], "shadow_post")
        self.assertAlmostEqual(posted[0]["price"], 0.01, places=8)
        self.assertAlmostEqual(posted[0]["size_shares"], 500.0, places=8)
        self.assertFalse(posted[0]["live_submit"])


class TestShortAliasAndLiveStub(unittest.TestCase):
    def test_should_post_up_when_the_fair_signal_is_the_short_alias(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            posted = eng.on_tick(
                now=50.0,
                fair_signal="up",
                up_book=_book("UP", 0.44, 0.46, "up-tok"),
                down_book=_book("DOWN", 0.54, 0.56, "dn-tok"),
                stake_usd=4.0,
                bucket=4,
            )
        self.assertEqual(posted[0]["event"], "shadow_post")
        self.assertEqual(posted[0]["side"], "UP")
        self.assertEqual(posted[0]["token_id"], "up-tok")

    def test_should_keep_live_submit_false_when_every_live_gate_is_on(self):
        cfg = MakerPilotConfig(enabled=True, shadow=False, live_execute=True)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            posted = eng.on_tick(
                now=50.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                execute=True,
                rtds_ready=True,
                creds_ready=True,
                bucket=5,
            )
        self.assertEqual(posted[0]["event"], "shadow_post")
        self.assertFalse(posted[0]["live_submit"])
        self.assertEqual(posted[0]["live_reason"], LIVE_STUB_REASON)
        self.assertEqual(posted[0]["mode"], "live_requested_but_stubbed")


class TestInvertedBookFeesAndEqualEdgePick(unittest.TestCase):
    def _cfg(self) -> WinmoreConfig:
        return WinmoreConfig(
            midband_enabled=False,
            min_edge_pp=0.001,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.001,
            kelly_fraction=0.5,
            min_stake_usd=0.5,
        )

    def test_should_treat_an_inverted_book_as_zero_half_spread(self):
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
        inverted = evaluate_side(bid=0.80, **kwargs)
        wide = evaluate_side(bid=0.60, **kwargs)
        self.assertAlmostEqual(inverted.details["half_spread"], 0.0)
        self.assertAlmostEqual(wide.details["half_spread"], 0.05)
        self.assertLess(inverted.fee_pp, wide.fee_pp)
        self.assertTrue(inverted.allow, inverted.reason)
        self.assertFalse(wide.allow)
        self.assertEqual(wide.reason, "skip_net_edge_below_fee_aware_min")

    def test_should_keep_up_when_both_sides_clear_with_equal_net_edge(self):
        cfg = self._cfg()
        levels = [BookLevel(0.70, 100.0)]
        kwargs = dict(
            fair_p=0.80,
            ask=0.70,
            bid=0.69,
            ask_levels=levels,
            cfg=cfg,
            stake_usd=5.0,
            remaining_loss_budget_usd=50.0,
            btc_daily_vol_pct=3.5,
        )
        up = evaluate_side(side="UP", **kwargs)
        down = evaluate_side(side="DOWN", **kwargs)
        self.assertTrue(up.allow, up.reason)
        self.assertTrue(down.allow, down.reason)
        self.assertAlmostEqual(up.net_edge_pp, down.net_edge_pp)
        picked = select_entry(up, down)
        self.assertEqual(picked.side, "UP")


if __name__ == "__main__":
    unittest.main()
