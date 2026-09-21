#!/usr/bin/env python3
"""Unit tests for W3 projected-final-TWAP fair and W4 entry-timing gates.

No network. No live trading flag.
"""

from __future__ import annotations

import time
import unittest
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_twap_fair import (
    FAIR_MODEL,
    FairValueCalculator,
    TWAPSnapshot,
    locked_level_from_path,
    p_up_from_projected,
    project_final_twap,
    projected_final_twap_fair,
    residual_twap_vol,
    settle_window_weights,
)
from btc_5m_entry_timing import (
    EntryTimingConfig,
    classify_seconds_left,
    entry_timing_from_mapping,
    evaluate_entry_timing,
)


class FakeTWAPTracker:
    def __init__(self, twap: float, source: str = "test_twap_path"):
        self.twap = twap
        self.source = source

    def get_current_twap(self, allow_fallback: bool = True) -> TWAPSnapshot:
        now = time.time()
        return TWAPSnapshot(
            timestamp=now,
            twap_60s=self.twap,
            window_seconds=60,
            source=self.source,
            series_id="btc-usd-twap-60s",
            source_ts=now - 0.25,
            receipt_ts=now,
        )


class TestW3ProjectedFinalTwap(unittest.TestCase):
    def test_settle_weights_early_fully_unlocked(self):
        locked, remaining = settle_window_weights(240.0)
        self.assertAlmostEqual(locked, 0.0, places=9)
        self.assertAlmostEqual(remaining, 1.0, places=9)

    def test_settle_weights_late_partial_lock(self):
        locked, remaining = settle_window_weights(20.0)
        self.assertAlmostEqual(locked, 40.0 / 60.0, places=9)
        self.assertAlmostEqual(remaining, 20.0 / 60.0, places=9)

    def test_early_vs_late_same_endpoint_up_side_consistent(self):
        open_px = 100_000.0
        endpoint = 100_200.0  # same projected endpoint
        early = projected_final_twap_fair(open_px, endpoint, 240.0)
        late = projected_final_twap_fair(open_px, endpoint, 20.0)

        self.assertEqual(early["model"], FAIR_MODEL)
        self.assertAlmostEqual(early["projected_final_twap"], endpoint, places=6)
        self.assertAlmostEqual(late["projected_final_twap"], endpoint, places=6)

        self.assertGreater(early["p_up"], 0.5)
        self.assertGreater(late["p_up"], 0.5)
        self.assertGreater(late["p_up"], early["p_up"])
        self.assertLess(late["residual_vol"], early["residual_vol"])
        self.assertGreater(late["confidence"], early["confidence"])

    def test_early_vs_late_same_endpoint_down_side_consistent(self):
        open_px = 100_000.0
        endpoint = 99_800.0
        early = projected_final_twap_fair(open_px, endpoint, 240.0)
        late = projected_final_twap_fair(open_px, endpoint, 20.0)

        self.assertLess(early["p_up"], 0.5)
        self.assertLess(late["p_up"], 0.5)
        self.assertLess(late["p_up"], early["p_up"])
        self.assertLess(late["residual_vol"], early["residual_vol"])

    def test_spot_momentum_is_ignored(self):
        open_px = 100_000.0
        twap_path = 100_250.0
        dumped_spot = 90_000.0

        with_spot = projected_final_twap_fair(
            open_px, twap_path, 18.0, spot_price=dumped_spot
        )
        without_spot = projected_final_twap_fair(
            open_px, twap_path, 18.0, spot_price=None
        )
        self.assertTrue(with_spot["spot_ignored"])
        self.assertFalse(without_spot["spot_ignored"])
        self.assertAlmostEqual(with_spot["p_up"], without_spot["p_up"], places=12)
        self.assertGreater(with_spot["p_up"], 0.9)

        # If fair used spot momentum, this dump vs open would flip to Down.
        spot_momentum_p_up = p_up_from_projected(
            open_px, dumped_spot, residual_twap_vol(18.0, 3.5)
        )
        self.assertLess(spot_momentum_p_up, 0.1)
        self.assertGreater(with_spot["p_up"] - spot_momentum_p_up, 0.8)

    def test_incomplete_path_locked_level_beats_current_print(self):
        # Late window: 40s locked at 101k, current 60s TWAP still diluted at 100.2k
        locked, remaining = settle_window_weights(20.0)
        projected = project_final_twap(100_200.0, locked, remaining, locked_level=101_000.0)
        current_only = project_final_twap(100_200.0, locked, remaining, locked_level=None)
        self.assertGreater(projected, current_only)
        self.assertAlmostEqual(projected, locked * 101_000.0 + remaining * 100_200.0, places=6)

    def test_path_samples_feed_locked_level(self):
        now = 1_000_000.0
        seconds_left = 20.0
        path = [
            TWAPSnapshot(timestamp=now - 30, twap_60s=101_000.0, window_seconds=60, receipt_ts=now - 30),
            TWAPSnapshot(timestamp=now - 10, twap_60s=101_200.0, window_seconds=60, receipt_ts=now - 10),
        ]
        locked = locked_level_from_path(path, now=now, seconds_left=seconds_left)
        self.assertIsNotNone(locked)
        self.assertAlmostEqual(locked, 101_100.0, places=6)

    def test_calculator_logs_timestamps_and_model(self):
        tracker = FakeTWAPTracker(100_000.0)
        calc = FairValueCalculator(tracker)
        pin = calc.pin_window_open("btc-updown-5m-test")
        self.assertIsNotNone(pin)
        tracker.twap = 100_180.0
        fair = calc.calculate_fair_value(
            "btc-updown-5m-test",
            seconds_left=30.0,
            spot_price=88_000.0,
        )
        self.assertIsNotNone(fair)
        assert fair is not None
        self.assertEqual(fair.model, FAIR_MODEL)
        self.assertTrue(fair.spot_ignored)
        self.assertIsNotNone(fair.source_ts)
        self.assertIsNotNone(fair.receipt_ts)
        self.assertIsNotNone(fair.decision_ts)
        self.assertGreaterEqual(fair.decision_ts, fair.receipt_ts)  # type: ignore[operator]
        self.assertGreater(fair.p_up, 0.5)
        self.assertGreater(fair.locked_frac, 0.0)

    def test_late_residual_vol_smaller_than_spot_style_five_min_scale(self):
        daily = 3.5
        late_twap = residual_twap_vol(20.0, daily)
        five_min_vol = daily / 100.0 * (5.0 / (24.0 * 60.0)) ** 0.5
        spot_style = five_min_vol * (20.0 / 300.0) ** 0.5
        self.assertLess(late_twap, spot_style)


class TestW4EntryTiming(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = EntryTimingConfig()

    def _allow_kwargs(self, **over) -> dict:
        base = dict(
            fair_p=0.68,
            ask=0.58,
            net_edge_bps=25.0,
            min_edge_bps=5.0,
            top_ask_notional_usd=80.0,
            cfg=self.cfg,
        )
        base.update(over)
        return base

    def test_default_window_allows_when_fair_and_edge_clear(self):
        d = evaluate_entry_timing(seconds_left=120.0, **self._allow_kwargs())
        self.assertEqual(classify_seconds_left(120.0, self.cfg), "default_window")
        self.assertTrue(d.allow)
        self.assertEqual(d.reason, "allow_default_entry_window")

    def test_default_window_bounds_t_minus_240_to_45(self):
        lo = evaluate_entry_timing(seconds_left=45.0, **self._allow_kwargs())
        hi = evaluate_entry_timing(seconds_left=240.0, **self._allow_kwargs())
        self.assertTrue(lo.allow)
        self.assertTrue(hi.allow)
        self.assertEqual(lo.zone, "default_window")
        self.assertEqual(hi.zone, "default_window")

    def test_soft_skip_first_20s_of_bucket(self):
        d = evaluate_entry_timing(seconds_left=290.0, **self._allow_kwargs())
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_soft_bucket_open")
        self.assertEqual(d.zone, "soft_open")

    def test_before_window_after_soft_open(self):
        d = evaluate_entry_timing(seconds_left=260.0, **self._allow_kwargs())
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_before_entry_window")

    def test_skip_when_fair_too_close_to_half(self):
        d = evaluate_entry_timing(seconds_left=120.0, **self._allow_kwargs(fair_p=0.505))
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_fair_too_close_to_half")

    def test_skip_when_net_edge_not_clear(self):
        d = evaluate_entry_timing(seconds_left=120.0, **self._allow_kwargs(net_edge_bps=1.0))
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_net_edge_not_clear")

    def test_hard_skip_last_20s_without_exception(self):
        d = evaluate_entry_timing(
            seconds_left=12.0,
            **self._allow_kwargs(fair_p=0.58, ask=0.55),
        )
        self.assertFalse(d.allow)
        self.assertEqual(d.zone, "hard_last")
        self.assertEqual(d.reason, "skip_hard_last_seconds_not_polarized")

    def test_late_polarized_exception_when_ask_le_hold_ev_minus_buffer(self):
        # fair 0.90, hold-EV 0.90, buffer 0.02 => cap 0.88; ask 0.70 clears
        d = evaluate_entry_timing(
            seconds_left=15.0,
            **self._allow_kwargs(fair_p=0.90, ask=0.70, net_edge_bps=80.0),
        )
        self.assertTrue(d.allow, d.reason)
        self.assertEqual(d.reason, "allow_late_polarized")
        self.assertLessEqual(d.ask, d.hold_ev_per_share - self.cfg.late_hold_ev_buffer_pp)

    def test_late_exception_denied_thin_depth(self):
        d = evaluate_entry_timing(
            seconds_left=15.0,
            **self._allow_kwargs(
                fair_p=0.90,
                ask=0.70,
                top_ask_notional_usd=5.0,
            ),
        )
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_hard_last_seconds_thin_depth")

    def test_late_exception_denied_ask_above_hold_ev_buffer(self):
        d = evaluate_entry_timing(
            seconds_left=16.0,
            **self._allow_kwargs(fair_p=0.90, ask=0.895),
        )
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_hard_last_seconds_ask_above_hold_ev_buffer")

    def test_late_exception_denied_when_net_edge_missing(self):
        d = evaluate_entry_timing(
            seconds_left=15.0,
            **self._allow_kwargs(fair_p=0.90, ask=0.70, net_edge_bps=1.0),
        )
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_hard_last_seconds_no_edge")

    def test_disabled_timing_allows_outside_window(self):
        cfg = EntryTimingConfig(enabled=False)
        d = evaluate_entry_timing(seconds_left=12.0, **self._allow_kwargs(cfg=cfg))
        self.assertTrue(d.allow)
        self.assertEqual(d.reason, "timing_disabled")

    def test_after_default_window_before_hard_skip(self):
        d = evaluate_entry_timing(seconds_left=30.0, **self._allow_kwargs())
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_past_default_window")

    def test_yaml_mapping_roundtrip(self):
        cfg = entry_timing_from_mapping(
            {
                "window_max_seconds_left": 240,
                "window_min_seconds_left": 45,
                "soft_skip_open_sec": 20,
                "hard_skip_last_sec": 18,
                "late_hold_ev_buffer_pp": 0.015,
            }
        )
        self.assertEqual(cfg.window_max_seconds_left, 240.0)
        self.assertEqual(cfg.window_min_seconds_left, 45.0)
        self.assertEqual(cfg.hard_skip_last_sec, 18.0)
        self.assertAlmostEqual(cfg.late_hold_ev_buffer_pp, 0.015)

    def test_profiles_yaml_exposes_w4_window(self):
        cfg_path = Path(__file__).resolve().parent.parent / "config" / "btc_5m_profiles.yaml"
        with cfg_path.open() as f:
            raw = yaml.safe_load(f)
        timing = (raw.get("shared_rules") or {}).get("session_timing", {}).get("entry_timing") or {}
        cfg = entry_timing_from_mapping(timing)
        self.assertEqual(cfg.window_max_seconds_left, 240.0)
        self.assertEqual(cfg.window_min_seconds_left, 45.0)
        self.assertEqual(cfg.soft_skip_open_sec, 20.0)
        self.assertEqual(cfg.hard_skip_last_sec, 20.0)
        model = raw["profiles"]["conservative"]["twap_fair_value"]["model"]
        self.assertEqual(model, "projected_final_twap")


if __name__ == "__main__":
    unittest.main()
