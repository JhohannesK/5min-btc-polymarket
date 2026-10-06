#!/usr/bin/env python3
"""
Complementary W5/W6 unit tests: same-bucket requote, missing-mid fills,
post-only price clamps, and depth/delay bounds not covered on main.

No network. No execute / live posting.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from btc_5m_maker_pilot import (
    MakerPilotConfig,
    MakerPilotEngine,
    SideBook,
    post_only_buy_price,
    summarize_maker_pilot,
)
from btc_5m_winmore_gates import (
    BookLevel,
    WinmoreConfig,
    choose_entry_order_type,
    depth_within_n_ticks,
    taker_delay_buffer_pp,
)


def _book(side: str, bid, ask, token_id: str = "tok") -> SideBook:
    return SideBook(side=side, token_id=token_id, best_bid=bid, best_ask=ask)


class RequoteAfterCancelTests(unittest.TestCase):
    def test_should_requote_on_a_later_tick_after_fair_flip_cancel(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, gtd_ttl_sec=15.0)
        eng = MakerPilotEngine(cfg)
        up = _book("UP", 0.44, 0.46, "up-tok")
        dn = _book("DOWN", 0.54, 0.56, "dn-tok")
        with redirect_stdout(io.StringIO()):
            posted = eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=up,
                down_book=dn,
                stake_usd=5.0,
                bucket=7,
            )
            flipped = eng.on_tick(
                now=1_001.0,
                fair_signal="down_favored",
                up_book=up,
                down_book=dn,
                stake_usd=5.0,
                bucket=7,
            )
            requote = eng.on_tick(
                now=1_002.0,
                fair_signal="down_favored",
                up_book=up,
                down_book=dn,
                stake_usd=5.0,
                bucket=7,
            )

        self.assertEqual(posted[0]["event"], "shadow_post")
        self.assertEqual(posted[0]["live_reason"], "shadow_mode")
        self.assertFalse(posted[0]["live_submit"])
        cancel_ev = [e for e in flipped if e.get("event") == "would_cancel"]
        self.assertEqual(len(cancel_ev), 1)
        self.assertEqual(cancel_ev[0]["reason"], "fair_flip")
        self.assertFalse(
            any(e.get("event") == "shadow_post" for e in flipped),
            "must not requote on the cancel tick",
        )
        self.assertEqual(requote[0]["event"], "shadow_post")
        self.assertEqual(requote[0]["side"], "DOWN")
        self.assertEqual(requote[0]["quote_id"], "sh-2")


class MissingMidFillTests(unittest.TestCase):
    def test_should_fill_against_quote_mid_when_the_book_has_no_mid(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True)
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
            filled = eng.on_tick(
                now=1_001.0,
                fair_signal="up_favored",
                up_book=_book("UP", None, 0.44),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=2,
            )

        quote_mid = posted[0]["mid"]
        fill_ev = [e for e in filled if e.get("event") == "would_fill"]
        self.assertEqual(len(fill_ev), 1)
        self.assertAlmostEqual(fill_ev[0]["mid_at_fill"], quote_mid, places=8)
        self.assertAlmostEqual(fill_ev[0]["mid_move_pp"], 0.0, places=8)
        self.assertAlmostEqual(fill_ev[0]["adverse_usd"], 0.0, places=8)
        self.assertFalse(fill_ev[0]["live_fill"])


class PostOnlyClampTests(unittest.TestCase):
    def test_should_clamp_a_sub_tick_bid_up_to_the_tick_floor(self):
        px = post_only_buy_price(0.001, 0.05, tick_size=0.01)
        self.assertEqual(px, 0.01)
        self.assertLess(px, 0.05)

    def test_should_clamp_a_near_one_bid_down_to_one_minus_tick(self):
        px = post_only_buy_price(0.995, 1.05, tick_size=0.01)
        self.assertEqual(px, 0.99)
        self.assertLess(px, 1.05)


class FirstBucketAndGtdEngineTests(unittest.TestCase):
    def test_should_not_bucket_roll_on_the_first_assigned_bucket(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            out = eng.on_tick(
                now=50.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=11,
            )
        self.assertEqual([e["event"] for e in out], ["shadow_post"])
        self.assertFalse(any(e.get("event") == "would_cancel" for e in out))

    def test_should_gtd_expire_then_requote_on_the_next_tick(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, gtd_ttl_sec=15.0)
        eng = MakerPilotEngine(cfg)
        up = _book("UP", 0.44, 0.46)
        dn = _book("DOWN", 0.54, 0.56)
        with redirect_stdout(io.StringIO()):
            eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=up,
                down_book=dn,
                stake_usd=4.0,
                bucket=3,
            )
            expired = eng.on_tick(
                now=1_015.0,
                fair_signal="up_favored",
                up_book=up,
                down_book=dn,
                stake_usd=4.0,
                bucket=3,
            )
            requote = eng.on_tick(
                now=1_016.0,
                fair_signal="up_favored",
                up_book=up,
                down_book=dn,
                stake_usd=4.0,
                bucket=3,
            )
        cancel_ev = [e for e in expired if e.get("event") == "would_cancel"]
        self.assertEqual(cancel_ev[0]["reason"], "gtd_expired")
        self.assertFalse(any(e.get("event") == "shadow_post" for e in expired))
        self.assertEqual(requote[0]["event"], "shadow_post")


class MakerSummaryUnknownReasonTests(unittest.TestCase):
    def test_should_count_unknown_cancel_reasons_without_calling_them_flips(self):
        summary = summarize_maker_pilot(
            [{"event": "would_cancel", "reason": "manual", "adverse_usd": 0.1}]
        )
        self.assertEqual(summary["would_be_cancels"], 1)
        self.assertEqual(summary["cancels_fair_flip"], 0)
        self.assertEqual(summary["cancels_spread_widen"], 0)
        self.assertEqual(summary["cancels_gtd_expired"], 0)
        self.assertEqual(summary["cancel_reasons"], {"manual": 1})
        self.assertEqual(summary["live_posts_attempted"], 0)


class DepthBandAndDelayRefTests(unittest.TestCase):
    def test_should_include_only_the_touch_when_n_ticks_is_zero(self):
        levels = [BookLevel(0.70, 10.0), BookLevel(0.71, 40.0)]
        slice_ = depth_within_n_ticks(levels, 0.70, n_ticks=0, tick_size=0.01, side="ask")
        self.assertEqual(slice_.shares, 10.0)
        self.assertEqual(slice_.levels_used, 1)
        self.assertAlmostEqual(slice_.vwap, 0.70, places=8)
        self.assertEqual(slice_.depth_cost_pp, 0.0)

    def test_should_drop_ask_levels_outside_the_n_tick_band(self):
        levels = [BookLevel(0.70, 8.0), BookLevel(0.73, 12.0), BookLevel(0.74, 90.0)]
        slice_ = depth_within_n_ticks(levels, 0.70, n_ticks=3, tick_size=0.01, side="ask")
        self.assertEqual(slice_.shares, 20.0)
        self.assertEqual(slice_.levels_used, 2)
        self.assertAlmostEqual(slice_.vwap, (8.0 * 0.70 + 12.0 * 0.73) / 20.0, places=8)

    def test_should_skip_vol_scale_when_the_ref_pct_is_zero(self):
        cfg = WinmoreConfig(
            taker_delay_buffer_pp=0.005,
            taker_delay_buffer_vol_scale=True,
            taker_delay_vol_ref_pct=0.0,
            taker_delay_buffer_min_pp=0.005,
            taker_delay_buffer_max_pp=0.010,
        )
        self.assertAlmostEqual(taker_delay_buffer_pp(cfg, 7.0), 0.005, places=8)

    def test_should_fall_back_when_order_type_strings_are_blank(self):
        self.assertEqual(
            choose_entry_order_type(WinmoreConfig(entry_order_type_post_only="")),
            "GTD",
        )
        self.assertEqual(
            choose_entry_order_type(
                WinmoreConfig(prefer_post_only=False, entry_order_type_taker="")
            ),
            "FAK",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
