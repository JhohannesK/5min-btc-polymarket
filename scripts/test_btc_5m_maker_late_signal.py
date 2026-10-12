#!/usr/bin/env python3
"""Maker late-window signal and spread-post edges.

The live runner feeds fair_signal='unavailable' into the shadow engine on the
legacy late-entry path, and the session max_spread gate does not wrap maker
posts. Lock those contracts so a later wiring change cannot silently drop
resting quotes or size against a wide book.

No network. No execute / live posting.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from btc_5m_maker_pilot import (
    MakerPilotConfig,
    MakerPilotEngine,
    ShadowQuote,
    SideBook,
    evaluate_cancel_rules,
    favored_side_from_signal,
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


class UnavailableFairFlipTests(unittest.TestCase):
    def test_should_treat_unavailable_as_no_favored_side(self):
        self.assertIsNone(favored_side_from_signal("unavailable"))
        self.assertIsNone(favored_side_from_signal(""))

    def test_should_cancel_live_up_quote_when_runner_feeds_unavailable(self):
        quote = _quote(side="UP")
        book = _book("UP", 0.44, 0.45)
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        d = evaluate_cancel_rules(quote, book, "unavailable", now=1_005.0, config=cfg)
        self.assertTrue(d.cancel)
        self.assertEqual(d.reason, "fair_flip")

    def test_should_keep_quote_when_same_side_stays_favored(self):
        quote = _quote(side="UP")
        book = _book("UP", 0.44, 0.45)
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        d = evaluate_cancel_rules(quote, book, "up_favored", now=1_005.0, config=cfg)
        self.assertFalse(d.cancel)
        self.assertIsNone(d.reason)

    def test_engine_should_cancel_and_not_requote_on_unavailable_tick(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, gtd_ttl_sec=15.0)
        eng = MakerPilotEngine(cfg)
        up = _book("UP", 0.44, 0.46, token_id="up-tok")
        dn = _book("DOWN", 0.54, 0.56, token_id="dn-tok")
        with redirect_stdout(io.StringIO()):
            posted = eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=up,
                down_book=dn,
                stake_usd=5.0,
                bucket=1,
            )
            late = eng.on_tick(
                now=1_002.0,
                fair_signal="unavailable",
                up_book=up,
                down_book=dn,
                stake_usd=5.0,
                bucket=1,
            )
        self.assertEqual(posted[0]["event"], "shadow_post")
        cancel_ev = [e for e in late if e.get("event") == "would_cancel"]
        self.assertEqual(len(cancel_ev), 1)
        self.assertEqual(cancel_ev[0]["reason"], "fair_flip")
        self.assertFalse(any(e.get("event") == "shadow_post" for e in late))
        self.assertEqual(eng.active.status, "cancelled")


class WideBookPostAndStakeTests(unittest.TestCase):
    def test_should_still_shadow_post_when_spread_exceeds_session_max(self):
        """Session max_spread=0.03 blocks taker; maker posts anyway on a new quote."""
        cfg = MakerPilotConfig(
            enabled=True,
            shadow=True,
            cancel_spread_widen_abs=0.03,
        )
        eng = MakerPilotEngine(cfg)
        wide_up = _book("UP", 0.40, 0.48)
        with redirect_stdout(io.StringIO()):
            out = eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=wide_up,
                down_book=_book("DOWN", 0.52, 0.60),
                stake_usd=5.0,
                bucket=4,
            )
        posts = [e for e in out if e.get("event") == "shadow_post"]
        self.assertEqual(len(posts), 1)
        self.assertGreater(posts[0]["spread"], 0.03)

    def test_should_size_shares_from_full_profile_stake(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            out = eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=2,
            )
        post = out[0]
        self.assertEqual(post["event"], "shadow_post")
        self.assertAlmostEqual(post["size_shares"], 5.0 / post["price"], places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
