#!/usr/bin/env python3
"""Unit tests for session timeout results, settle fallback, and guard split.

No network. No execute / live posting.
"""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_entry_timing import EntryTimingConfig
from btc_5m_session_flags import (
    POLL_MAX_SPREAD,
    SUBPROCESS_MAX_SPREAD,
    SUBPROCESS_MIN_TOP_ASK_NOTIONAL_USD,
    W4_LATE_MIN_ASK_NOTIONAL_USD,
    classify_no_entry_result,
    notional_w4_blocks_open_allows,
    open_env_defaults,
    poll_spread_too_wide,
    settle_allow_fallback,
    spread_poll_skips_open_allows,
)


class NoEntryResultTests(unittest.TestCase):
    def test_should_mark_maker_shadow_complete_when_pilot_is_on(self):
        self.assertEqual(
            classify_no_entry_result(True),
            "maker_pilot_shadow_complete",
        )

    def test_should_mark_timeout_when_maker_is_off(self):
        self.assertEqual(classify_no_entry_result(False), "no_entry_timeout")


class SettleFallbackTests(unittest.TestCase):
    def test_should_forbid_spot_fallback_on_live_execute(self):
        self.assertFalse(settle_allow_fallback(True))

    def test_should_allow_spot_fallback_on_dry_run(self):
        self.assertTrue(settle_allow_fallback(False))


class GuardSplitTests(unittest.TestCase):
    def test_should_keep_poll_tighter_than_subprocess_spread(self):
        self.assertLess(POLL_MAX_SPREAD, SUBPROCESS_MAX_SPREAD)
        self.assertAlmostEqual(POLL_MAX_SPREAD, 0.03, places=10)
        self.assertAlmostEqual(SUBPROCESS_MAX_SPREAD, 0.05, places=10)

    def test_should_skip_poll_and_still_allow_open_in_the_3c_to_5c_band(self):
        self.assertFalse(poll_spread_too_wide(0.03))
        self.assertTrue(spread_poll_skips_open_allows(0.031))
        self.assertTrue(spread_poll_skips_open_allows(0.05))
        self.assertFalse(spread_poll_skips_open_allows(0.051))
        self.assertFalse(spread_poll_skips_open_allows(0.02))

    def test_should_keep_w4_late_depth_stricter_than_subprocess_notional(self):
        self.assertGreater(
            W4_LATE_MIN_ASK_NOTIONAL_USD,
            SUBPROCESS_MIN_TOP_ASK_NOTIONAL_USD,
        )
        self.assertAlmostEqual(
            W4_LATE_MIN_ASK_NOTIONAL_USD,
            EntryTimingConfig().late_min_ask_notional_usd,
            places=10,
        )
        self.assertTrue(notional_w4_blocks_open_allows(10.0))
        self.assertTrue(notional_w4_blocks_open_allows(29.99))
        self.assertFalse(notional_w4_blocks_open_allows(30.0))
        self.assertFalse(notional_w4_blocks_open_allows(9.99))

    def test_should_export_subprocess_env_defaults_as_strings(self):
        self.assertEqual(
            open_env_defaults(),
            {"PM_MAX_SPREAD": "0.05", "PM_MIN_TOP_ASK_NOTIONAL_USD": "10"},
        )


if __name__ == "__main__":
    unittest.main()
