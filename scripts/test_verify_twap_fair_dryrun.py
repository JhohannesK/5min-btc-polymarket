#!/usr/bin/env python3
"""Fail-closed exit contract for verify_twap_fair_dryrun.py. No network."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_twap_fair import TWAPSnapshot
import verify_twap_fair_dryrun as verify


def _snapshot() -> TWAPSnapshot:
    return TWAPSnapshot(
        timestamp=1_700_000_000.0,
        twap_60s=100_000.0,
        window_seconds=60,
        source="test_stub",
        series_id="btc-usd-twap-60s",
        source_ts=1_700_000_000.0,
        receipt_ts=1_700_000_000.0,
    )


def _decision(**kwargs):
    base = dict(
        allow=False,
        reason="skip_midband_taker_ban",
        size_usd=0.0,
        net_edge_pp=0.0,
        order_type="GTD",
        in_midband=True,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestVerifyTwapFairDryrunExit(unittest.TestCase):
    def _run_main(self) -> int:
        buf = io.StringIO()
        with redirect_stdout(buf):
            return verify.main()

    def test_missing_twap_exits_1(self):
        with patch.object(
            verify.ChainlinkTWAPTracker,
            "get_current_twap",
            return_value=None,
        ):
            self.assertEqual(self._run_main(), 1)

    def test_midband_allow_exits_1(self):
        allowed = _decision(allow=True, reason="forced_allow")
        with patch.object(
            verify.ChainlinkTWAPTracker,
            "get_current_twap",
            return_value=_snapshot(),
        ), patch.object(verify, "evaluate_side", return_value=allowed):
            self.assertEqual(self._run_main(), 1)

    def test_maker_shadow_live_post_exits_1(self):
        summary = {
            "cancels_fair_flip": 1,
            "live_posts_attempted": 1,
        }
        with patch.object(
            verify.ChainlinkTWAPTracker,
            "get_current_twap",
            return_value=_snapshot(),
        ), patch.object(verify, "summarize_maker_pilot", return_value=summary):
            self.assertEqual(self._run_main(), 1)

    def test_happy_path_exits_0_when_twap_is_stubbed(self):
        with patch.object(
            verify.ChainlinkTWAPTracker,
            "get_current_twap",
            return_value=_snapshot(),
        ):
            self.assertEqual(self._run_main(), 0)


if __name__ == "__main__":
    unittest.main()
