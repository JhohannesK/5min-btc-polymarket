#!/usr/bin/env python3
"""RTDS dry-run fallback and degraded fair-calc paths.

Complements draft twap_core (which never uses spot fallback) and model_bounds.
No network. No --execute.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import btc_5m_twap_fair as twap
from btc_5m_twap_fair import (
    ChainlinkTWAPTracker,
    FairValueCalculator,
    TWAPSnapshot,
)


class FakeRtdsResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _snapshot(twap_60s: float = 100_000.0, window_seconds: int = 60) -> TWAPSnapshot:
    return TWAPSnapshot(
        timestamp=1_000.0,
        twap_60s=twap_60s,
        window_seconds=window_seconds,
        source="chainlink_rtds",
        series_id="btc-usd-twap-60s",
        source_ts=999.0,
        receipt_ts=1_000.0,
    )


class StarvingTracker:
    def __init__(self, first: TWAPSnapshot):
        self.first = first
        self.calls = 0

    def get_current_twap(self, allow_fallback: bool = True) -> TWAPSnapshot | None:
        del allow_fallback
        self.calls += 1
        if self.calls == 1:
            return self.first
        return None


class TestRtdsDryRunFallback(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = ChainlinkTWAPTracker(
            rtds_endpoint="https://rtds.example/twap",
            rtds_api_key="test-key",
        )

    def test_should_mark_spot_fallback_when_rtds_window_is_not_60s(self):
        with (
            patch.object(
                twap.requests,
                "post",
                return_value=FakeRtdsResponse({"windowSeconds": 30, "value": 99_000}),
            ) as post,
            patch.object(
                ChainlinkTWAPTracker,
                "_estimate_twap_from_spot",
                return_value=100_000.0,
            ),
            redirect_stdout(io.StringIO()),
        ):
            snap = self.tracker.get_current_twap(allow_fallback=True)

        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertEqual(snap.source, "spot_fallback_dry_run_only")
        self.assertEqual(snap.window_seconds, 60)
        self.assertAlmostEqual(snap.twap_60s, 100_000.0)
        self.assertNotEqual(snap.source, "chainlink_rtds")
        post.assert_called_once()

    def test_should_fall_back_to_spot_when_rtds_post_raises(self):
        with (
            patch.object(
                twap.requests,
                "post",
                side_effect=twap.requests.ConnectionError("rtds down"),
            ),
            patch.object(
                ChainlinkTWAPTracker,
                "_estimate_twap_from_spot",
                return_value=101_250.0,
            ),
            redirect_stdout(io.StringIO()),
        ):
            snap = self.tracker.get_current_twap(allow_fallback=True)

        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertEqual(snap.source, "spot_fallback_dry_run_only")
        self.assertAlmostEqual(snap.twap_60s, 101_250.0)

    def test_should_raise_when_rtds_fails_and_fallback_is_forbidden(self):
        with (
            patch.object(
                twap.requests,
                "post",
                side_effect=twap.requests.Timeout("slow"),
            ),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.tracker.get_current_twap(allow_fallback=False)
        self.assertIn("RTDS required", str(ctx.exception))

    def test_should_return_none_when_rtds_and_spot_both_fail(self):
        self.tracker.rtds_endpoint = None
        self.tracker.rtds_api_key = None
        with (
            patch.object(
                ChainlinkTWAPTracker,
                "_estimate_twap_from_spot",
                return_value=None,
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertIsNone(self.tracker.get_current_twap(allow_fallback=True))

    def test_should_refresh_rtds_cache_after_ttl(self):
        ok = FakeRtdsResponse({"windowSeconds": 60, "value": 100_500, "sourceTs": 50.0})
        times = iter([1_000.0, 1_004.0, 1_006.0])
        with (
            patch.object(twap.requests, "post", return_value=ok) as post,
            patch.object(twap.time, "time", side_effect=lambda: next(times)),
            redirect_stdout(io.StringIO()),
        ):
            first = self.tracker.get_current_twap(allow_fallback=False)
            second = self.tracker.get_current_twap(allow_fallback=False)
            third = self.tracker.get_current_twap(allow_fallback=False)

        self.assertIs(first, second)
        self.assertIsNot(first, third)
        self.assertEqual(post.call_count, 2)


class TestFairCalcDegraded(unittest.TestCase):
    def test_should_return_none_when_current_twap_vanishes_after_pin(self):
        tracker = StarvingTracker(_snapshot(100_000.0))
        calc = FairValueCalculator(tracker)
        with redirect_stdout(io.StringIO()):
            pin = calc.pin_window_open("btc-updown-5m-degraded")
            fair = calc.calculate_fair_value("btc-updown-5m-degraded", 120.0)

        self.assertIsNotNone(pin)
        self.assertIsNone(fair)
        self.assertEqual(tracker.calls, 2)


if __name__ == "__main__":
    unittest.main()
