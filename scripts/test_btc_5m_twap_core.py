#!/usr/bin/env python3
"""Unit tests for TWAP fee math, hold-EV, RTDS gates, and settlement.

No network. RTDS/HTTP is mocked. Spot fallback is never used.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_twap_fair import (
    ChainlinkTWAPTracker,
    FairValueCalculator,
    TWAPSnapshot,
    calculate_hold_ev,
    estimate_trade_edge,
    p_up_from_projected,
    parse_source_ts,
    projected_final_twap_fair,
    settle_window_weights,
)
from log_twap_settle import log_twap_settle


def _snap(
    twap: float,
    window_seconds: int = 60,
    source: str = "test",
    timestamp: float = 1_000.0,
    source_ts: Optional[float] = 999.0,
) -> TWAPSnapshot:
    return TWAPSnapshot(
        timestamp=timestamp,
        twap_60s=twap,
        window_seconds=window_seconds,
        source=source,
        series_id="btc-usd-twap-60s",
        source_ts=source_ts,
        receipt_ts=timestamp,
    )


class FakeTWAPTracker:
    def __init__(self, snapshots: list[TWAPSnapshot]):
        self._snapshots = list(snapshots)
        self.calls = 0

    def get_current_twap(self, allow_fallback: bool = True) -> TWAPSnapshot:
        self.calls += 1
        idx = min(self.calls - 1, len(self._snapshots) - 1)
        return self._snapshots[idx]


class ParseSourceTsTests(unittest.TestCase):
    def test_should_prefer_source_ts_camel_case(self):
        self.assertEqual(parse_source_ts({"sourceTs": 12.5, "ts": 1}), 12.5)

    def test_should_fall_through_keys_and_skip_unparseable(self):
        self.assertEqual(parse_source_ts({"sourceTs": "nope", "timestamp": "1700"}), 1700.0)

    def test_should_return_none_instead_of_inventing_a_timestamp(self):
        self.assertIsNone(parse_source_ts({}))
        self.assertIsNone(parse_source_ts({"sourceTs": None, "ts": "bad"}))


class FeeFormulaTests(unittest.TestCase):
    def test_should_use_shares_not_usdc_cost_as_c_in_fee(self):
        # Research: C = shares. 100 shares at 50c => $1.75, not 0.07 * $50 * 0.25 = $0.875.
        edge = estimate_trade_edge(0.55, 0.50, 0.49, shares=100.0)
        self.assertAlmostEqual(edge["taker_fee_total_usd"], 1.75, places=8)
        self.assertNotAlmostEqual(edge["taker_fee_total_usd"], 0.875, places=4)

    def test_should_peak_fee_at_fifty_cents_vs_seventy(self):
        at_50 = estimate_trade_edge(0.55, 0.50, 0.49, shares=100.0)
        at_70 = estimate_trade_edge(0.80, 0.70, 0.69, shares=100.0)
        self.assertAlmostEqual(at_50["taker_fee_total_usd"], 1.75, places=6)
        self.assertAlmostEqual(at_70["taker_fee_total_usd"], 1.47, places=6)
        self.assertGreater(at_50["taker_fee_total_usd"], at_70["taker_fee_total_usd"])

    def test_should_signal_buy_when_net_edge_clears_five_bps(self):
        edge = estimate_trade_edge(0.80, 0.60, 0.59, shares=1.0)
        self.assertGreater(edge["net_edge_bps"], 5)
        self.assertEqual(edge["signal"], "buy")
        self.assertAlmostEqual(edge["net_edge_pp"], edge["net_edge_bps"] / 10000.0, places=10)

    def test_should_signal_avoid_when_ask_is_above_fair_after_fees(self):
        edge = estimate_trade_edge(0.50, 0.70, 0.69, shares=1.0)
        self.assertLess(edge["net_edge_bps"], -5)
        self.assertEqual(edge["signal"], "avoid")

    def test_should_cap_depth_cost_and_not_divide_by_zero_shares(self):
        huge = estimate_trade_edge(0.80, 0.70, 0.69, shares=10_000.0, book_ask_size=1.0)
        self.assertEqual(huge["depth_cost_bps"], 200)
        zero = estimate_trade_edge(0.80, 0.70, 0.69, shares=0.0)
        self.assertEqual(zero["taker_fee_per_share_usd"], 0)
        self.assertEqual(zero["taker_fee_total_usd"], 0)


class HoldEvTests(unittest.TestCase):
    def test_should_recommend_hold_when_redeem_ev_beats_bid_after_fees(self):
        # fair 0.90 * 1 = 0.90 vs selling 0.85 after fee
        got = calculate_hold_ev(0.90, 0.85, shares=10.0)
        self.assertEqual(got["recommendation"], "hold")
        self.assertGreater(got["ev_diff"], 0.01)
        self.assertAlmostEqual(got["hold_ev"], 9.0, places=8)

    def test_should_recommend_sell_when_bid_beats_hold_ev(self):
        got = calculate_hold_ev(0.55, 0.80, shares=10.0)
        self.assertEqual(got["recommendation"], "sell")
        self.assertLess(got["ev_diff"], -0.01)
        sell_fee = 10.0 * 0.07 * 0.80 * 0.20
        self.assertAlmostEqual(got["sell_fee"], sell_fee, places=8)

    def test_should_subtract_taker_fee_from_sell_proceeds(self):
        got = calculate_hold_ev(0.50, 0.50, shares=100.0)
        self.assertAlmostEqual(got["sell_fee"], 1.75, places=8)
        self.assertAlmostEqual(got["sell_ev"], 50.0 - 1.75, places=8)


class ProjectedFairEdgeCasesTests(unittest.TestCase):
    def test_should_treat_zero_prices_as_coin_flip(self):
        self.assertEqual(p_up_from_projected(0.0, 100.0, 0.01), 0.5)
        self.assertEqual(p_up_from_projected(100.0, 0.0, 0.01), 0.5)

    def test_should_be_deterministic_when_residual_vol_is_zero(self):
        self.assertEqual(p_up_from_projected(100.0, 100.1, 0.0), 1.0)
        self.assertEqual(p_up_from_projected(100.0, 99.9, 0.0), 0.0)
        self.assertEqual(p_up_from_projected(100.0, 100.0, 0.0), 1.0)

    def test_should_fully_lock_at_zero_seconds_left(self):
        locked, remaining = settle_window_weights(0.0)
        self.assertAlmostEqual(locked, 1.0)
        self.assertAlmostEqual(remaining, 0.0)

    def test_should_be_neutral_inside_five_bps_and_favored_at_the_bound(self):
        open_px = 100_000.0
        inside = projected_final_twap_fair(open_px, open_px * 1.0004, 240.0)
        at_bound = projected_final_twap_fair(open_px, open_px * 1.0005, 240.0)
        down = projected_final_twap_fair(open_px, open_px * 0.9994, 240.0)
        self.assertEqual(inside["edge_signal"], "neutral")
        self.assertEqual(at_bound["edge_signal"], "up_favored")
        self.assertEqual(down["edge_signal"], "down_favored")


class RtdsTrackerTests(unittest.TestCase):
    def test_should_raise_when_execute_path_has_no_rtds(self):
        tracker = ChainlinkTWAPTracker(rtds_endpoint=None, rtds_api_key=None)
        with self.assertRaises(RuntimeError) as ctx:
            tracker.get_current_twap(allow_fallback=False)
        self.assertIn("RTDS", str(ctx.exception))
        self.assertIn("Never use spot fallback", str(ctx.exception))

    def test_should_reject_non_60s_window_on_execute_path(self):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"windowSeconds": 30, "value": 100000.0}

        tracker = ChainlinkTWAPTracker(
            rtds_endpoint="https://rtds.example/v1",
            rtds_api_key="test-key",
        )
        with patch("btc_5m_twap_fair.requests.post", return_value=resp) as post:
            with self.assertRaises(RuntimeError) as ctx:
                tracker.get_current_twap(allow_fallback=False)
        self.assertIn("wrong window", str(ctx.exception).lower())
        post.assert_called_once()
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["topic"], "crypto_prices_twap_sixty")
        self.assertEqual(payload["filter"], {"symbol": "btc/usd"})

    def test_should_parse_rtds_value_and_source_ts_when_window_is_60(self):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "windowSeconds": 60,
            "value": "76123.45",
            "series": "btc-usd-twap-60s",
            "sourceTs": 1_700_000_000.25,
        }
        tracker = ChainlinkTWAPTracker(
            rtds_endpoint="https://rtds.example/v1",
            rtds_api_key="test-key",
        )
        with patch("btc_5m_twap_fair.requests.post", return_value=resp) as post:
            with patch("btc_5m_twap_fair.time.time", return_value=1_700_000_001.0):
                with redirect_stdout(io.StringIO()):
                    snap = tracker.get_current_twap(allow_fallback=False)
                    cached = tracker.get_current_twap(allow_fallback=False)
        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertEqual(snap.window_seconds, 60)
        self.assertAlmostEqual(snap.twap_60s, 76123.45)
        self.assertEqual(snap.source, "chainlink_rtds")
        self.assertAlmostEqual(snap.source_ts, 1_700_000_000.25)
        self.assertIs(cached, snap)
        self.assertEqual(post.call_count, 1)


class FairValueWindowGuardTests(unittest.TestCase):
    def test_should_refuse_to_pin_non_60s_series(self):
        tracker = FakeTWAPTracker([_snap(100_000.0, window_seconds=30)])
        calc = FairValueCalculator(tracker)  # type: ignore[arg-type]
        with redirect_stdout(io.StringIO()) as buf:
            pin = calc.pin_window_open("btc-updown-5m-test")
        self.assertIsNone(pin)
        self.assertIsNone(calc.get_window_open_twap("btc-updown-5m-test"))
        self.assertIn("WRONG WINDOW", buf.getvalue())

    def test_should_return_none_on_current_vs_open_window_mismatch(self):
        tracker = FakeTWAPTracker(
            [
                _snap(100_000.0, window_seconds=60, timestamp=1_000.0),
                _snap(100_100.0, window_seconds=30, timestamp=1_010.0),
            ]
        )
        calc = FairValueCalculator(tracker)  # type: ignore[arg-type]
        with redirect_stdout(io.StringIO()) as buf:
            pin = calc.pin_window_open("m")
            fair = calc.calculate_fair_value("m", seconds_left=40.0)
        self.assertIsNotNone(pin)
        self.assertIsNone(fair)
        self.assertIn("WINDOW MISMATCH", buf.getvalue())

    def test_should_reuse_pinned_open_twap_without_re_fetching_pin(self):
        tracker = FakeTWAPTracker([_snap(100_000.0), _snap(100_050.0)])
        calc = FairValueCalculator(tracker)  # type: ignore[arg-type]
        with redirect_stdout(io.StringIO()):
            first = calc.pin_window_open("m")
            second = calc.pin_window_open("m")
        self.assertIs(first, second)
        self.assertEqual(tracker.calls, 1)


class SettleLogTests(unittest.TestCase):
    def _run(self, final_twap: float, open_twap: float, side: str):
        snap = _snap(final_twap)
        tracker = MagicMock()
        tracker.get_current_twap.return_value = snap
        with patch("log_twap_settle.ChainlinkTWAPTracker", return_value=tracker):
            with redirect_stdout(io.StringIO()) as buf:
                result = log_twap_settle("btc-updown-5m-1", open_twap, side, allow_fallback=False)
        return result, buf.getvalue(), tracker

    def test_should_call_up_the_winner_when_final_equals_open(self):
        result, log, tracker = self._run(100_000.0, 100_000.0, "UP")
        self.assertEqual(result["result"], "UP")
        self.assertTrue(result["position_wins"])
        self.assertIn("windowSeconds=60", log)
        tracker.get_current_twap.assert_called_once_with(allow_fallback=False)

    def test_should_mark_down_win_when_final_prints_below_open(self):
        result, _, _ = self._run(99_999.99, 100_000.0, "DOWN")
        self.assertEqual(result["result"], "DOWN")
        self.assertTrue(result["position_wins"])

    def test_should_mark_up_position_loss_when_final_prints_below_open(self):
        result, _, _ = self._run(99_999.99, 100_000.0, "UP")
        self.assertEqual(result["result"], "DOWN")
        self.assertFalse(result["position_wins"])

    def test_should_return_none_when_final_twap_is_missing(self):
        tracker = MagicMock()
        tracker.get_current_twap.return_value = None
        with patch("log_twap_settle.ChainlinkTWAPTracker", return_value=tracker):
            with redirect_stdout(io.StringIO()) as buf:
                result = log_twap_settle("m", 100.0, "UP", allow_fallback=False)
        self.assertIsNone(result)
        self.assertIn("TWAP_SETTLE_ERROR", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
