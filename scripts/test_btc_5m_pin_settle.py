#!/usr/bin/env python3
"""Complementary pin/settle/cancel contracts still untested on main.

No network. No live posting.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_twap_fair import (
    FairValueCalculator,
    TWAPSnapshot,
    locked_level_from_path,
    p_up_from_projected,
)
from log_twap_settle import log_twap_settle


class SequentialTWAPTracker:
    def __init__(self, snapshots: list[TWAPSnapshot]) -> None:
        self._snapshots = list(snapshots)

    def get_current_twap(self, allow_fallback: bool = True) -> TWAPSnapshot:
        del allow_fallback
        if not self._snapshots:
            raise AssertionError("no TWAP snapshots left")
        return self._snapshots.pop(0)


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


def _snap(ts: float, twap: float, window_seconds: int = 60) -> TWAPSnapshot:
    return TWAPSnapshot(
        timestamp=ts,
        twap_60s=twap,
        window_seconds=window_seconds,
        receipt_ts=ts,
        series_id="btc-usd-twap-60s",
    )


class TestPinRejectLeavesPathPoison(unittest.TestCase):
    def test_should_keep_rejected_30s_sample_on_the_path(self):
        bad = _snap(1_000.0, 90_000.0, window_seconds=30)
        calc = FairValueCalculator(SequentialTWAPTracker([bad]))

        pinned = calc.pin_window_open("btc-updown-5m-1")

        self.assertIsNone(pinned)
        self.assertNotIn("btc-updown-5m-1", calc._window_pins)
        path = calc._paths["btc-updown-5m-1"]
        self.assertEqual(len(path), 1)
        self.assertEqual(path[0].window_seconds, 30)
        self.assertAlmostEqual(path[0].twap_60s, 90_000.0, places=6)

    def test_should_let_rejected_30s_sample_skew_later_locked_level(self):
        bad = _snap(1_000.0, 90_000.0, window_seconds=30)
        good = _snap(1_001.0, 100_000.0)
        current = _snap(1_002.0, 100_000.0)
        calc = FairValueCalculator(SequentialTWAPTracker([bad, good]))

        self.assertIsNone(calc.pin_window_open("btc-updown-5m-1"))
        pinned = calc.pin_window_open("btc-updown-5m-1")
        self.assertIsNotNone(pinned)
        calc.record_path_sample("btc-updown-5m-1", current)

        poisoned = locked_level_from_path(
            calc._paths["btc-updown-5m-1"],
            now=1_002.0,
            seconds_left=20.0,
        )
        clean = locked_level_from_path(
            [good, current],
            now=1_002.0,
            seconds_left=20.0,
        )
        self.assertAlmostEqual(poisoned, (90_000.0 + 100_000.0 + 100_000.0) / 3.0, places=6)
        self.assertAlmostEqual(clean, 100_000.0, places=6)
        self.assertLess(poisoned, clean)


class TestProjectedNonPositiveFair(unittest.TestCase):
    def test_should_return_coin_flip_when_projected_is_not_positive(self):
        self.assertEqual(p_up_from_projected(100_000.0, 0.0, 0.02), 0.5)
        self.assertEqual(p_up_from_projected(100_000.0, -1.0, 0.02), 0.5)


class TestSettleTieAndCase(unittest.TestCase):
    def test_should_count_down_as_a_loss_on_an_up_tie(self):
        tracker = FakeSettleTracker(100_000.0)
        with patch("log_twap_settle.ChainlinkTWAPTracker", return_value=tracker):
            out = log_twap_settle(
                "btc-updown-5m-1",
                100_000.0,
                "DOWN",
                allow_fallback=False,
            )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["result"], "UP")
        self.assertFalse(out["position_wins"])

    def test_should_mark_lowercase_down_as_a_loss_when_down_wins(self):
        tracker = FakeSettleTracker(99_000.0)
        with patch("log_twap_settle.ChainlinkTWAPTracker", return_value=tracker):
            lower = log_twap_settle(
                "btc-updown-5m-1",
                100_000.0,
                "down",
                allow_fallback=False,
            )
            upper = log_twap_settle(
                "btc-updown-5m-1",
                100_000.0,
                "DOWN",
                allow_fallback=False,
            )
        self.assertIsNotNone(lower)
        self.assertIsNotNone(upper)
        assert lower is not None and upper is not None
        self.assertEqual(lower["result"], "DOWN")
        self.assertFalse(lower["position_wins"])
        self.assertTrue(upper["position_wins"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
