#!/usr/bin/env python3
"""Unit tests for TWAP cache vs execute fallback policy.

No network. Seeds the in-memory cache; never calls spot or RTDS.
"""

from __future__ import annotations

import time
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_twap_cache import (
    cache_entry_fresh,
    cached_twap_allowed,
    twap_cache_key,
)
from btc_5m_twap_fair import ChainlinkTWAPTracker, TWAPSnapshot


def _snapshot(source: str, twap: float = 100_000.0, ts: float = 1_000.0) -> TWAPSnapshot:
    return TWAPSnapshot(
        timestamp=ts,
        twap_60s=twap,
        window_seconds=60,
        source=source,
        series_id="test",
        source_ts=ts,
        receipt_ts=ts,
    )


class TwapCachePolicyTests(unittest.TestCase):
    def test_should_use_a_single_current_cache_key(self):
        self.assertEqual(twap_cache_key(), "current")

    def test_should_treat_ttl_as_strictly_less_than(self):
        self.assertTrue(cache_entry_fresh(1_000.0, 1_004.9, 5.0))
        self.assertFalse(cache_entry_fresh(1_000.0, 1_005.0, 5.0))
        self.assertFalse(cache_entry_fresh(1_000.0, 1_006.0, 5.0))

    def test_should_block_spot_fallback_when_execute_forbids_it(self):
        self.assertFalse(
            cached_twap_allowed("spot_fallback_dry_run_only", allow_fallback=False)
        )
        self.assertTrue(
            cached_twap_allowed("spot_fallback_dry_run_only", allow_fallback=True)
        )

    def test_should_reuse_rtds_snapshots_on_execute(self):
        self.assertTrue(cached_twap_allowed("chainlink_rtds", allow_fallback=False))
        self.assertTrue(cached_twap_allowed("chainlink_rtds", allow_fallback=True))


class CachedTwapLookupTests(unittest.TestCase):
    def _tracker_with(self, snapshot: TWAPSnapshot) -> ChainlinkTWAPTracker:
        tracker = ChainlinkTWAPTracker(rtds_endpoint=None, rtds_api_key=None)
        tracker._cache[twap_cache_key()] = snapshot
        return tracker

    def test_should_reuse_a_fresh_dry_run_fallback_when_fallback_is_allowed(self):
        tracker = self._tracker_with(
            _snapshot("spot_fallback_dry_run_only", twap=99_001.0, ts=time.time())
        )
        got = tracker.get_current_twap(allow_fallback=True)
        self.assertIsNotNone(got)
        self.assertEqual(got.twap_60s, 99_001.0)
        self.assertEqual(got.source, "spot_fallback_dry_run_only")

    def test_should_raise_on_execute_instead_of_reusing_cached_spot_fallback(self):
        tracker = self._tracker_with(
            _snapshot("spot_fallback_dry_run_only", twap=99_001.0, ts=time.time())
        )
        with self.assertRaises(RuntimeError) as ctx:
            tracker.get_current_twap(allow_fallback=False)
        self.assertIn("RTDS", str(ctx.exception))
        self.assertIn("execute", str(ctx.exception).lower())

    def test_should_reuse_a_fresh_rtds_snapshot_on_execute(self):
        tracker = self._tracker_with(
            _snapshot("chainlink_rtds", twap=101_250.0, ts=time.time())
        )
        got = tracker.get_current_twap(allow_fallback=False)
        self.assertIsNotNone(got)
        self.assertEqual(got.twap_60s, 101_250.0)
        self.assertEqual(got.source, "chainlink_rtds")

    def test_should_ignore_a_stale_rtds_snapshot_and_require_rtds_on_execute(self):
        tracker = self._tracker_with(
            _snapshot("chainlink_rtds", twap=101_250.0, ts=time.time() - 6.0)
        )
        with self.assertRaises(RuntimeError):
            tracker.get_current_twap(allow_fallback=False)


if __name__ == "__main__":
    unittest.main()
