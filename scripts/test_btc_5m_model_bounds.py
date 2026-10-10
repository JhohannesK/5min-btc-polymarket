#!/usr/bin/env python3
"""Complementary W3/W6 bounds: CLOB objects, window-start vol, pin, fill-below.

No network. No execute / live posting.
"""

from __future__ import annotations

import time
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_maker_pilot import (
    MakerPilotConfig,
    ShadowQuote,
    SideBook,
    book_would_fill_buy,
    evaluate_cancel_rules,
)
from btc_5m_twap_fair import (
    FairValueCalculator,
    TWAPSnapshot,
    project_final_twap,
    residual_twap_vol,
    settle_window_weights,
)
from btc_5m_winmore_gates import parse_book_levels


class _ClobLevel:
    def __init__(self, price, size):
        self.price = price
        self.size = size


class FakeTWAPTracker:
    def __init__(self, twap: float, window_seconds: int = 60):
        self.twap = twap
        self.window_seconds = window_seconds
        self.fetches = 0

    def get_current_twap(self, allow_fallback: bool = True) -> TWAPSnapshot:
        self.fetches += 1
        now = time.time()
        return TWAPSnapshot(
            timestamp=now,
            twap_60s=self.twap,
            window_seconds=self.window_seconds,
            source="test_twap_path",
            series_id="btc-usd-twap-60s",
            source_ts=now - 0.25,
            receipt_ts=now,
        )


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


class TestClobObjectBookParse(unittest.TestCase):
    def test_should_accept_objects_with_price_and_size_attrs(self):
        levels = parse_book_levels(
            [_ClobLevel(0.70, 40.0), _ClobLevel(0.71, 10.0), _ClobLevel(0.0, 99.0)]
        )
        self.assertEqual([(lvl.price, lvl.size) for lvl in levels], [(0.70, 40.0), (0.71, 10.0)])

    def test_should_drop_objects_missing_price_or_size(self):
        class Blank:
            pass

        levels = parse_book_levels([Blank(), _ClobLevel(0.50, 0.0), "junk"])
        self.assertEqual(levels, [])


class TestSettleWindowStartVol(unittest.TestCase):
    def test_should_add_level_vol_only_before_the_settle_window(self):
        at_window = residual_twap_vol(60.0, 3.5)
        before_window = residual_twap_vol(61.0, 3.5)
        self.assertGreater(before_window, at_window)
        self.assertGreater(at_window, 0.0)

    def test_should_ignore_the_current_print_once_the_window_is_fully_locked(self):
        locked, remaining = settle_window_weights(0.0)
        self.assertAlmostEqual(locked, 1.0, places=9)
        self.assertAlmostEqual(remaining, 0.0, places=9)
        projected = project_final_twap(
            100_000.0, locked, remaining, locked_level=101_500.0
        )
        current_only = project_final_twap(100_000.0, locked, remaining, locked_level=None)
        self.assertAlmostEqual(projected, 101_500.0, places=6)
        self.assertAlmostEqual(current_only, 100_000.0, places=6)


class TestTwapPinBounds(unittest.TestCase):
    def test_should_reuse_the_first_pin_without_refetching(self):
        tracker = FakeTWAPTracker(100_000.0)
        calc = FairValueCalculator(tracker)
        first = calc.pin_window_open("btc-updown-5m-pin")
        tracker.twap = 100_400.0
        second = calc.pin_window_open("btc-updown-5m-pin")
        self.assertIs(first, second)
        self.assertAlmostEqual(first.twap_60s, 100_000.0, places=6)
        self.assertEqual(tracker.fetches, 1)
        self.assertAlmostEqual(calc.get_window_open_twap("btc-updown-5m-pin"), 100_000.0)

    def test_should_refuse_to_pin_a_non_sixty_second_window(self):
        tracker = FakeTWAPTracker(100_000.0, window_seconds=30)
        calc = FairValueCalculator(tracker)
        pin = calc.pin_window_open("btc-updown-5m-30s")
        self.assertIsNone(pin)
        self.assertIsNone(calc.get_window_open_twap("btc-updown-5m-30s"))


class TestFillBelowAndGtdJustBefore(unittest.TestCase):
    def test_should_fill_when_the_ask_is_strictly_below_the_resting_buy(self):
        quote = _quote(price=0.44)
        book = SideBook(side="UP", token_id="tok", best_bid=0.42, best_ask=0.43)
        self.assertTrue(book_would_fill_buy(quote, book))

    def test_should_keep_a_live_quote_just_before_gtd_expiry(self):
        quote = _quote(gtd_expire_at=1_015.0)
        book = SideBook(side="UP", token_id="tok", best_bid=0.44, best_ask=0.45)
        cfg = MakerPilotConfig(enabled=True, cancel_spread_widen_abs=0.03)
        decision = evaluate_cancel_rules(
            quote, book, "up_favored", now=1_014.999, config=cfg
        )
        self.assertFalse(decision.cancel)
        self.assertIsNone(decision.reason)


if __name__ == "__main__":
    unittest.main()
