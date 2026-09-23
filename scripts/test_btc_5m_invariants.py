#!/usr/bin/env python3
"""Remaining high-risk invariants not covered on main after W3-W6.

Complementary to draft PRs #8 and #10 (different files). No network.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_maker_pilot import post_only_buy_price, taker_fee_usd
from btc_5m_twap_fair import (
    FairValueCalculator,
    TWAPSnapshot,
    locked_level_from_path,
)
from btc_5m_winmore_gates import (
    BookLevel,
    WinmoreConfig,
    depth_within_n_ticks,
    parse_book_levels,
    should_block_second_clip,
    taker_delay_buffer_pp,
)


class FakeTWAPTracker:
    def __init__(self, twap: float = 100_000.0):
        self.twap = twap

    def get_current_twap(self, allow_fallback: bool = True) -> TWAPSnapshot:
        return TWAPSnapshot(
            timestamp=1_000.0,
            twap_60s=self.twap,
            window_seconds=60,
            source="test",
        )


class ParseBookLevelsTests(unittest.TestCase):
    def test_should_accept_clob_dicts_tuples_and_booklevel(self):
        levels = parse_book_levels(
            [
                {"price": "0.44", "size": "12"},
                (0.45, 8.0),
                BookLevel(0.46, 3.0),
            ]
        )
        self.assertEqual([(lvl.price, lvl.size) for lvl in levels], [
            (0.44, 12.0),
            (0.45, 8.0),
            (0.46, 3.0),
        ])

    def test_should_drop_junk_zero_and_non_positive_size(self):
        levels = parse_book_levels(
            [
                None,
                "skip",
                {"price": 0.50, "size": 0},
                {"price": 0, "size": 10},
                {"price": -0.10, "size": 4},
                object(),
            ]
        )
        self.assertEqual(levels, [])

    def test_should_return_empty_for_missing_book(self):
        self.assertEqual(parse_book_levels(None), [])
        self.assertEqual(parse_book_levels([]), [])


class DepthGuardTests(unittest.TestCase):
    def test_should_return_empty_slice_for_invalid_touch_ticks_or_tick_size(self):
        levels = [BookLevel(0.70, 10.0)]
        for kwargs in (
            {"touch": 0.0, "n_ticks": 3, "tick_size": 0.01},
            {"touch": 0.70, "n_ticks": -1, "tick_size": 0.01},
            {"touch": 0.70, "n_ticks": 3, "tick_size": 0.0},
        ):
            slice_ = depth_within_n_ticks(levels, **kwargs)
            self.assertEqual(slice_.shares, 0.0)
            self.assertEqual(slice_.notional_usd, 0.0)
            self.assertEqual(slice_.levels_used, 0)

    def test_should_raise_on_unknown_book_side(self):
        with self.assertRaises(ValueError):
            depth_within_n_ticks([BookLevel(0.70, 10.0)], 0.70, 3, side="mid")  # type: ignore[arg-type]


class DelayBufferSwapTests(unittest.TestCase):
    def test_should_swap_inverted_min_max_before_clamping(self):
        cfg = WinmoreConfig(
            taker_delay_buffer_pp=0.007,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_min_pp=0.010,
            taker_delay_buffer_max_pp=0.005,
        )
        self.assertAlmostEqual(taker_delay_buffer_pp(cfg, 3.5), 0.007, places=8)


class SecondClipZeroFirstEdgeTests(unittest.TestCase):
    def test_should_not_apply_decay_ratio_when_first_edge_is_not_positive(self):
        self.assertFalse(should_block_second_clip(0.0, 0.02, 0.01, decay_ratio=0.50))
        self.assertTrue(should_block_second_clip(0.0, 0.005, 0.01, decay_ratio=0.50))


class TakerFeeClampTests(unittest.TestCase):
    def test_should_peak_at_fifty_cents_for_one_hundred_shares(self):
        self.assertAlmostEqual(taker_fee_usd(100.0, 0.50), 1.75, places=8)

    def test_should_clamp_price_to_unit_interval(self):
        self.assertAlmostEqual(taker_fee_usd(100.0, 1.8), 0.0, places=8)
        self.assertAlmostEqual(taker_fee_usd(100.0, -0.4), 0.0, places=8)


class PostOnlyInvalidBookTests(unittest.TestCase):
    def test_should_refuse_non_positive_ask_or_negative_bid(self):
        self.assertIsNone(post_only_buy_price(0.44, 0.0, tick_size=0.01))
        self.assertIsNone(post_only_buy_price(-0.01, 0.46, tick_size=0.01))


class PathSampleCutoffTests(unittest.TestCase):
    def test_should_drop_path_samples_older_than_sixty_seconds(self):
        calc = FairValueCalculator(FakeTWAPTracker())
        now = 1_000_000.0
        stale = TWAPSnapshot(
            timestamp=now - 90.0,
            twap_60s=90_000.0,
            window_seconds=60,
            receipt_ts=now - 90.0,
        )
        fresh = TWAPSnapshot(
            timestamp=now,
            twap_60s=101_000.0,
            window_seconds=60,
            receipt_ts=now,
        )
        calc.record_path_sample("btc-updown-5m-cutoff", stale)
        calc.record_path_sample("btc-updown-5m-cutoff", fresh)

        path = calc._paths["btc-updown-5m-cutoff"]
        self.assertEqual([snap.twap_60s for snap in path], [101_000.0])

        locked = locked_level_from_path(path, now=now, seconds_left=20.0)
        self.assertAlmostEqual(locked, 101_000.0, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
