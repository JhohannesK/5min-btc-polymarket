#!/usr/bin/env python3
"""Unit tests for fee/hold-EV math, 5bps edge-signal bound, and execute-path wiring.

No network.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_twap_fair import (
    calculate_hold_ev,
    edge_signal_from_move_bps,
    estimate_trade_edge,
    locked_level_from_path,
    projected_final_twap_fair,
    residual_twap_vol,
    settle_window_weights,
    TWAPSnapshot,
)


def _fair_at_prices(open_px: float, current_px: float, seconds_left: float = 240.0) -> dict:
    return projected_final_twap_fair(open_px, current_px, seconds_left)


class EdgeSignalBoundTests(unittest.TestCase):
    def test_should_be_neutral_inside_five_bps(self):
        up = _fair_at_prices(100_000.0, 100_040.0)
        down = _fair_at_prices(100_000.0, 99_960.0)
        self.assertEqual(up["edge_signal"], "neutral")
        self.assertEqual(down["edge_signal"], "neutral")

    def test_should_favor_the_side_once_the_move_clears_five_bps(self):
        up = _fair_at_prices(100_000.0, 100_060.0)
        down = _fair_at_prices(100_000.0, 99_940.0)
        self.assertEqual(up["edge_signal"], "up_favored")
        self.assertEqual(down["edge_signal"], "down_favored")
        self.assertGreater(up["p_up"], 0.5)
        self.assertLess(down["p_up"], 0.5)

    def test_should_treat_exactly_five_bps_up_as_up_favored(self):
        # +5 used to fall through to down_favored because the check was `> 5`.
        self.assertEqual(edge_signal_from_move_bps(5.0), "up_favored")
        self.assertEqual(edge_signal_from_move_bps(-5.0), "down_favored")
        self.assertEqual(edge_signal_from_move_bps(4.999), "neutral")
        self.assertEqual(edge_signal_from_move_bps(-4.999), "neutral")

    def test_should_stay_neutral_when_open_twap_is_zero(self):
        fair = projected_final_twap_fair(0.0, 100_000.0, 120.0)
        self.assertEqual(fair["edge_signal"], "neutral")


class FeeAndHoldEvTests(unittest.TestCase):
    def test_should_signal_marginal_inside_five_bps_net_edge(self):
        # fair = ask + fee + half-spread, so net_edge_bps is ~0
        ask = 0.70
        bid = 0.69
        fee_pp = 0.07 * ask * (1.0 - ask)
        half_spread = (ask - bid) / 2.0
        fair_p = ask + fee_pp + half_spread
        edge = estimate_trade_edge(fair_p, ask, bid, shares=1.0)
        self.assertLessEqual(abs(edge["net_edge_bps"]), 5)
        self.assertEqual(edge["signal"], "marginal")

    def test_should_recommend_marginal_when_hold_and_sell_ev_are_close(self):
        # hold 0.50 vs sell ~0.4925 is still hold; tighten with bid near hold-EV
        result = calculate_hold_ev(0.50, 0.51, shares=1.0)
        self.assertLessEqual(abs(result["ev_diff"]), 0.01)
        self.assertEqual(result["recommendation"], "marginal")

    def test_should_recommend_hold_when_redeem_ev_clears_the_cent_buffer(self):
        result = calculate_hold_ev(0.80, 0.50, shares=10.0)
        self.assertEqual(result["recommendation"], "hold")
        self.assertGreater(result["ev_diff"], 0.01)

    def test_should_recommend_sell_when_bid_beats_hold_ev_after_fees(self):
        result = calculate_hold_ev(0.40, 0.70, shares=10.0)
        self.assertEqual(result["recommendation"], "sell")
        self.assertLess(result["ev_diff"], -0.01)
        self.assertGreater(result["sell_fee"], 0.0)


class ResidualAndLockTests(unittest.TestCase):
    def test_should_return_unlocked_weights_when_window_length_is_invalid(self):
        self.assertEqual(settle_window_weights(20.0, twap_window_sec=0.0), (0.0, 1.0))
        self.assertEqual(settle_window_weights(20.0, twap_window_sec=-1.0), (0.0, 1.0))

    def test_should_report_zero_residual_vol_once_the_window_is_locked(self):
        self.assertEqual(residual_twap_vol(0.0, 3.5), 0.0)

    def test_should_return_none_locked_level_without_in_window_samples(self):
        self.assertIsNone(locked_level_from_path([], now=1_000.0, seconds_left=20.0))
        early = [
            TWAPSnapshot(
                timestamp=900.0,
                twap_60s=101_000.0,
                window_seconds=60,
                receipt_ts=900.0,
            )
        ]
        self.assertIsNone(
            locked_level_from_path(early, now=1_000.0, seconds_left=20.0)
        )


class RunnerExecutePathWiringTests(unittest.TestCase):
    def test_runner_never_hardcodes_spot_fallback_on_live_paths(self):
        runner = Path(__file__).with_name("test_btc_5m_session_exit_sl.py")
        src = runner.read_text()
        self.assertIn("from log_twap_settle import log_twap_settle", src)
        self.assertIn("settle_result = log_twap_settle(", src)
        self.assertGreaterEqual(src.count("allow_fallback = not args.execute"), 3)
        self.assertIn("get_current_twap(allow_fallback=False)", src)
        self.assertNotIn("allow_fallback=True", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
