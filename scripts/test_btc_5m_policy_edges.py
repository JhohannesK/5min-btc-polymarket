#!/usr/bin/env python3
"""Complementary W3/W4/W5/W6 policy edges not covered on main.

No network. No live execute. Does not duplicate StateTracker/TWAP-core/runner extracts.
"""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_entry_timing import (
    EntryTimingConfig,
    classify_seconds_left,
    evaluate_entry_timing,
)
from btc_5m_maker_pilot import (
    MakerPilotConfig,
    SideBook,
    default_maker_pilot_mapping,
    favored_side_from_signal,
    format_maker_pilot_report,
    post_only_buy_price,
    summarize_maker_pilot,
    taker_fee_usd,
)
from btc_5m_twap_fair import (
    TWAPSnapshot,
    calculate_hold_ev,
    estimate_trade_edge,
    locked_level_from_path,
    p_up_from_projected,
    settle_window_weights,
)
from btc_5m_winmore_gates import (
    DepthSlice,
    cap_size_usd,
    choose_entry_order_type,
    should_block_second_clip,
    winmore_config_from_mapping,
)


class TestMakerConfigAndQuoteMath(unittest.TestCase):
    def test_should_stub_live_path_in_public_dict_and_mode(self):
        disabled = MakerPilotConfig()
        self.assertEqual(disabled.effective_mode(), "disabled")
        self.assertEqual(disabled.as_public_dict()["live_path"], "stubbed")

        shadow = MakerPilotConfig(enabled=True, shadow=True, live_execute=True)
        self.assertEqual(shadow.effective_mode(), "shadow")

        live_off = MakerPilotConfig(enabled=True, shadow=False, live_execute=False)
        self.assertEqual(live_off.effective_mode(), "shadow")

        requested = MakerPilotConfig(enabled=True, shadow=False, live_execute=True)
        self.assertEqual(requested.effective_mode(), "live_requested_but_stubbed")
        self.assertEqual(requested.as_public_dict()["mode"], "live_requested_but_stubbed")
        self.assertEqual(requested.as_public_dict()["live_path"], "stubbed")

    def test_should_use_paper_defaults_when_mapping_is_missing(self):
        cfg = MakerPilotConfig.from_mapping(None)
        self.assertFalse(cfg.enabled)
        self.assertTrue(cfg.shadow)
        self.assertFalse(cfg.live_execute)
        self.assertEqual(default_maker_pilot_mapping()["live_execute"], False)

    def test_should_map_short_and_padded_fair_signals(self):
        self.assertEqual(favored_side_from_signal(" UP "), "UP")
        self.assertEqual(favored_side_from_signal("Down"), "DOWN")
        self.assertIsNone(favored_side_from_signal(""))
        self.assertIsNone(favored_side_from_signal(None))  # type: ignore[arg-type]

    def test_should_refuse_post_only_on_invalid_book_or_tick(self):
        self.assertIsNone(post_only_buy_price(-0.01, 0.46, tick_size=0.01))
        self.assertIsNone(post_only_buy_price(0.44, 0.0, tick_size=0.01))
        px = post_only_buy_price(0.40, 0.45, tick_size=0.0, improve_ticks=1)
        self.assertEqual(px, 0.41)
        self.assertLess(px, 0.45)

    def test_should_clamp_taker_fee_price_to_unit_interval(self):
        self.assertEqual(taker_fee_usd(100.0, 1.5), 0.0)
        self.assertEqual(taker_fee_usd(100.0, -0.2), 0.0)
        self.assertAlmostEqual(taker_fee_usd(100.0, 0.50), 1.75, places=8)

    def test_should_treat_missing_quotes_as_no_spread_and_floor_inverted(self):
        missing = SideBook("UP", best_bid=0.44, best_ask=None)
        self.assertIsNone(missing.spread)
        self.assertIsNone(missing.mid)
        inverted = SideBook("UP", best_bid=0.50, best_ask=0.48)
        self.assertEqual(inverted.spread, 0.0)
        self.assertAlmostEqual(inverted.mid, 0.49, places=8)

    def test_should_summarize_empty_events_without_inventing_fills(self):
        summary = summarize_maker_pilot([])
        self.assertEqual(summary["quotes_posted"], 0)
        self.assertEqual(summary["would_be_fills"], 0)
        self.assertIsNone(summary["mean_mid_move_pp"])
        self.assertEqual(summary["live_posts_attempted"], 0)
        report = format_maker_pilot_report(summary)
        self.assertIn("paper window", report)


class TestWinmoreOrderTypeAndSizeClip(unittest.TestCase):
    def test_should_honor_custom_post_only_and_taker_order_types(self):
        gtc = winmore_config_from_mapping(
            {"taker_delay": {"prefer_post_only": True, "order_type_post_only": "gtc"}}
        )
        self.assertEqual(choose_entry_order_type(gtc), "GTC")
        fak = winmore_config_from_mapping(
            {"taker_delay": {"prefer_post_only": False, "order_type_taker": "fok"}}
        )
        self.assertEqual(choose_entry_order_type(fak), "FOK")

    def test_should_use_paper_defaults_when_winmore_mapping_is_none(self):
        cfg = winmore_config_from_mapping(None)
        self.assertTrue(cfg.midband_enabled)
        self.assertEqual(cfg.midband_taker_policy, "skip")
        self.assertEqual(choose_entry_order_type(cfg), "GTD")

    def test_should_not_decay_block_when_first_edge_is_not_positive(self):
        self.assertFalse(should_block_second_clip(0.0, 0.02, 0.01, decay_ratio=0.50))
        self.assertFalse(should_block_second_clip(-0.04, 0.02, 0.01, decay_ratio=0.50))
        self.assertTrue(should_block_second_clip(0.0, 0.005, 0.01, decay_ratio=0.50))

    def test_should_clip_size_to_depth_shares_when_vwap_walk_overstates_notional(self):
        depth = DepthSlice(
            shares=10.0,
            notional_usd=8.0,
            vwap=0.80,
            depth_cost_pp=0.10,
            levels_used=2,
        )
        sized, shares, deny = cap_size_usd(
            requested_usd=100.0,
            entry_price=0.70,
            depth=depth,
            kelly_cap_usd=100.0,
            min_stake_usd=1.0,
        )
        self.assertEqual(deny, "")
        self.assertAlmostEqual(shares, 10.0, places=8)
        self.assertAlmostEqual(sized, 7.0, places=8)

    def test_should_deny_after_share_clip_when_sized_drops_below_min_stake(self):
        depth = DepthSlice(
            shares=1.0,
            notional_usd=10.0,
            vwap=10.0,
            depth_cost_pp=9.3,
            levels_used=1,
        )
        sized, shares, deny = cap_size_usd(
            requested_usd=10.0,
            entry_price=0.70,
            depth=depth,
            kelly_cap_usd=10.0,
            min_stake_usd=5.0,
        )
        self.assertEqual(deny, "skip_size_below_min_stake")
        self.assertEqual(sized, 0.0)
        self.assertEqual(shares, 0.0)


class TestW4ZoneAndPolarizedBoundaries(unittest.TestCase):
    def test_should_classify_inclusive_hard_last_and_exclusive_soft_open(self):
        cfg = EntryTimingConfig()
        self.assertEqual(classify_seconds_left(280.0001, cfg), "soft_open")
        self.assertEqual(classify_seconds_left(280.0, cfg), "before_window")
        self.assertEqual(classify_seconds_left(20.0001, cfg), "after_window")
        self.assertEqual(classify_seconds_left(20.0, cfg), "hard_last")
        self.assertEqual(classify_seconds_left(0.0, cfg), "hard_last")

    def test_should_treat_exact_polarized_deviation_as_polarized(self):
        cfg = EntryTimingConfig()
        kwargs = dict(
            ask=0.70,
            net_edge_bps=80.0,
            min_edge_bps=5.0,
            top_ask_notional_usd=80.0,
            cfg=cfg,
        )
        on_bar = evaluate_entry_timing(seconds_left=15.0, fair_p=0.75, **kwargs)
        self.assertTrue(on_bar.allow, on_bar.reason)
        self.assertEqual(on_bar.reason, "allow_late_polarized")

        under = evaluate_entry_timing(seconds_left=15.0, fair_p=0.749, **kwargs)
        self.assertFalse(under.allow)
        self.assertEqual(under.reason, "skip_hard_last_seconds_not_polarized")

    def test_should_scale_late_hold_ev_cap_with_redeem_value(self):
        cfg = EntryTimingConfig(redeem_value=0.5, late_hold_ev_buffer_pp=0.02)
        # fair 0.90 -> hold-EV 0.45, cap 0.43. Ask 0.44 is above the halved cap.
        denied = evaluate_entry_timing(
            seconds_left=15.0,
            fair_p=0.90,
            ask=0.44,
            net_edge_bps=80.0,
            min_edge_bps=5.0,
            top_ask_notional_usd=80.0,
            cfg=cfg,
        )
        self.assertFalse(denied.allow)
        self.assertEqual(denied.reason, "skip_hard_last_seconds_ask_above_hold_ev_buffer")

        allowed = evaluate_entry_timing(
            seconds_left=15.0,
            fair_p=0.90,
            ask=0.42,
            net_edge_bps=80.0,
            min_edge_bps=5.0,
            top_ask_notional_usd=80.0,
            cfg=cfg,
        )
        self.assertTrue(allowed.allow, allowed.reason)
        self.assertAlmostEqual(allowed.hold_ev_per_share, 0.45, places=8)


class TestTwapWeightsAndHoldEdge(unittest.TestCase):
    def test_should_lock_fully_outside_or_after_the_settle_window(self):
        self.assertEqual(settle_window_weights(60.0), (0.0, 1.0))
        self.assertEqual(settle_window_weights(120.0), (0.0, 1.0))
        self.assertEqual(settle_window_weights(0.0), (1.0, 0.0))
        self.assertEqual(settle_window_weights(-5.0), (1.0, 0.0))
        self.assertEqual(settle_window_weights(20.0, twap_window_sec=0.0), (0.0, 1.0))

    def test_should_be_a_coin_flip_when_open_twap_is_zero_or_equal_with_vol(self):
        self.assertEqual(p_up_from_projected(0.0, 100_000.0, 0.01), 0.5)
        self.assertEqual(p_up_from_projected(100_000.0, 100_000.0, 0.01), 0.5)
        self.assertEqual(p_up_from_projected(100_000.0, 100_000.0, 0.0), 1.0)
        self.assertEqual(p_up_from_projected(100_000.0, 99_000.0, 0.0), 0.0)

    def test_should_use_timestamp_when_receipt_ts_is_missing_and_skip_early_window(self):
        now = 1_000_000.0
        snap = TWAPSnapshot(
            timestamp=now - 10.0,
            twap_60s=101_000.0,
            window_seconds=60,
            receipt_ts=None,
        )
        locked = locked_level_from_path([snap], now=now, seconds_left=20.0)
        self.assertAlmostEqual(locked, 101_000.0, places=6)
        self.assertIsNone(locked_level_from_path([snap], now=now, seconds_left=240.0))

    def test_should_treat_exactly_five_bps_net_edge_as_marginal(self):
        buy_bar = estimate_trade_edge(1.0005, 1.0, 1.0, shares=1.0)
        self.assertAlmostEqual(buy_bar["net_edge_bps"], 5.0, places=8)
        self.assertEqual(buy_bar["signal"], "marginal")

        avoid_bar = estimate_trade_edge(
            0.0, 0.0005, 0.0005, shares=1.0, taker_fee_rate=0.0
        )
        self.assertAlmostEqual(avoid_bar["net_edge_bps"], -5.0, places=8)
        self.assertEqual(avoid_bar["signal"], "marginal")

    def test_should_zero_spread_when_the_book_is_inverted(self):
        out = estimate_trade_edge(0.60, 0.50, 0.55, shares=1.0)
        self.assertEqual(out["spread_bps"], 0.0)
        self.assertEqual(out["half_spread_bps"], 0.0)

    def test_should_treat_exact_cent_hold_ev_buffer_as_marginal(self):
        hold_bar = calculate_hold_ev(0.01, 0.0, shares=1.0, taker_fee_rate=0.0)
        self.assertAlmostEqual(hold_bar["ev_diff"], 0.01, places=8)
        self.assertEqual(hold_bar["recommendation"], "marginal")

        sell_bar = calculate_hold_ev(0.0, 0.01, shares=1.0, taker_fee_rate=0.0)
        self.assertAlmostEqual(sell_bar["ev_diff"], -0.01, places=8)
        self.assertEqual(sell_bar["recommendation"], "marginal")

    def test_should_scale_hold_ev_with_redeem_value(self):
        out = calculate_hold_ev(0.80, 0.10, shares=2.0, redeem_value=0.5, taker_fee_rate=0.0)
        self.assertAlmostEqual(out["hold_ev_per_share"], 0.40, places=8)
        self.assertAlmostEqual(out["hold_ev"], 0.80, places=8)
        self.assertEqual(out["recommendation"], "hold")


if __name__ == "__main__":
    unittest.main(verbosity=2)
