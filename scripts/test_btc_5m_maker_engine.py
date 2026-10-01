#!/usr/bin/env python3
"""W6 maker engine edges not covered by the shadow-log unit file.

Bucket-roll leftover quotes, per-bucket quote cap, post-only skip, live
deny reasons. No network.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from btc_5m_maker_pilot import (
    LIVE_STUB_REASON,
    MakerPilotConfig,
    MakerPilotEngine,
    SideBook,
    live_maker_allowed,
    post_only_buy_price,
)


def _book(side: str, bid: float, ask: float, token_id: str = "tok") -> SideBook:
    return SideBook(side=side, token_id=token_id, best_bid=bid, best_ask=ask)


def _tick(eng: MakerPilotEngine, *, now: float, signal: str, bucket: int, **kwargs):
    up = kwargs.pop("up", _book("UP", 0.44, 0.46, "up-tok"))
    dn = kwargs.pop("dn", _book("DOWN", 0.54, 0.56, "dn-tok"))
    with redirect_stdout(io.StringIO()):
        return eng.on_tick(
            now=now,
            fair_signal=signal,
            up_book=up,
            down_book=dn,
            stake_usd=kwargs.pop("stake_usd", 5.0),
            bucket=bucket,
            **kwargs,
        )


class PostOnlyCapTests(unittest.TestCase):
    def test_should_return_none_when_ask_is_non_positive(self):
        self.assertIsNone(post_only_buy_price(0.40, 0.0, tick_size=0.01))
        self.assertIsNone(post_only_buy_price(0.40, -0.01, tick_size=0.01))

    def test_should_return_none_when_bid_is_negative(self):
        self.assertIsNone(post_only_buy_price(-0.01, 0.50, tick_size=0.01))

    def test_should_return_none_when_one_tick_book_has_no_post_only_room(self):
        self.assertIsNone(post_only_buy_price(0.01, 0.01, tick_size=0.01))

    def test_should_cap_improve_ticks_so_the_quote_never_crosses_the_ask(self):
        px = post_only_buy_price(0.44, 0.46, tick_size=0.01, improve_ticks=20)
        self.assertEqual(px, 0.45)
        self.assertLess(px, 0.46)


class LiveDenyReasonTests(unittest.TestCase):
    def test_should_deny_when_pilot_is_disabled(self):
        ok, reason = live_maker_allowed(MakerPilotConfig(enabled=False), execute=True)
        self.assertFalse(ok)
        self.assertEqual(reason, "maker_pilot_disabled")

    def test_should_deny_when_live_execute_flag_is_off(self):
        cfg = MakerPilotConfig(enabled=True, shadow=False, live_execute=False)
        ok, reason = live_maker_allowed(cfg, execute=True, rtds_ready=True, creds_ready=True)
        self.assertFalse(ok)
        self.assertEqual(reason, "live_execute_flag_off")

    def test_should_deny_when_rtds_is_not_ready(self):
        cfg = MakerPilotConfig(enabled=True, shadow=False, live_execute=True)
        ok, reason = live_maker_allowed(cfg, execute=True, rtds_ready=False, creds_ready=True)
        self.assertFalse(ok)
        self.assertEqual(reason, "rtds_not_ready")

    def test_should_deny_when_creds_are_not_ready(self):
        cfg = MakerPilotConfig(enabled=True, shadow=False, live_execute=True)
        ok, reason = live_maker_allowed(cfg, execute=True, rtds_ready=True, creds_ready=False)
        self.assertFalse(ok)
        self.assertEqual(reason, "creds_not_ready")

    def test_should_still_stub_when_every_live_gate_passes(self):
        cfg = MakerPilotConfig(enabled=True, shadow=False, live_execute=True)
        ok, reason = live_maker_allowed(cfg, execute=True, rtds_ready=True, creds_ready=True)
        self.assertFalse(ok)
        self.assertEqual(reason, LIVE_STUB_REASON)


class BucketRollAndQuoteCapTests(unittest.TestCase):
    def test_should_cancel_a_resting_quote_when_the_5m_bucket_rolls(self):
        eng = MakerPilotEngine(MakerPilotConfig(enabled=True, shadow=True))
        posted = _tick(eng, now=1_000.0, signal="up_favored", bucket=1)
        self.assertEqual(posted[0]["event"], "shadow_post")
        self.assertEqual(eng.active.status, "live")

        rolled = _tick(eng, now=1_300.0, signal="up_favored", bucket=2)
        cancel = [e for e in rolled if e.get("event") == "would_cancel"]
        self.assertEqual(len(cancel), 1)
        self.assertEqual(cancel[0]["reason"], "bucket_roll")
        posts = [e for e in rolled if e.get("event") == "shadow_post"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["bucket"], 2)

    def test_should_not_post_more_quotes_than_max_quotes_per_bucket(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, max_quotes_per_bucket=1)
        eng = MakerPilotEngine(cfg)
        first = _tick(eng, now=1_000.0, signal="up_favored", bucket=9)
        self.assertEqual(first[0]["event"], "shadow_post")

        # Fill the quote, then the same bucket must not re-quote.
        filled = _tick(
            eng,
            now=1_001.0,
            signal="up_favored",
            bucket=9,
            up=_book("UP", 0.42, 0.44, "up-tok"),
        )
        self.assertTrue(any(e.get("event") == "would_fill" for e in filled))
        self.assertFalse(any(e.get("event") == "shadow_post" for e in filled))

        again = _tick(eng, now=1_002.0, signal="up_favored", bucket=9)
        self.assertEqual(again, [])

    def test_should_emit_post_skip_when_the_book_has_no_post_only_price(self):
        eng = MakerPilotEngine(MakerPilotConfig(enabled=True, shadow=True))
        events = _tick(
            eng,
            now=1.0,
            signal="up_favored",
            bucket=3,
            up=_book("UP", 0.01, 0.01, "up-tok"),
        )
        self.assertEqual(events[0]["event"], "post_skip")
        self.assertEqual(events[0]["reason"], "no_post_only_price")
        self.assertIsNone(eng.active)


if __name__ == "__main__":
    unittest.main(verbosity=2)
