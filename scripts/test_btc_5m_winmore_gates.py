#!/usr/bin/env python3
"""Unit tests for W1/W2/W5 win-more gates. No network, no --execute."""

from __future__ import annotations

import argparse
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_winmore_gates import (
    BookLevel,
    DepthSlice,
    WinmoreConfig,
    cap_size_usd,
    choose_entry_order_type,
    decision_log_fields,
    depth_within_n_ticks,
    evaluate_side,
    fee_pp,
    fractional_kelly_usd,
    is_midband,
    required_min_edge_pp,
    select_entry,
    should_block_second_clip,
    taker_delay_buffer_pp,
    winmore_config_from_mapping,
)


def _cfg(**kwargs) -> WinmoreConfig:
    return WinmoreConfig(**kwargs)


class TestW1FeeAwareMidband(unittest.TestCase):
    def test_fee_pp_formula_at_50c(self):
        # 0.07 * 0.5 * 0.5 = 0.0175; half_spread 0.01; depth 0.002
        got = fee_pp(0.50, half_spread=0.01, depth_cost=0.002)
        self.assertAlmostEqual(got, 0.0175 + 0.01 + 0.002, places=10)

    def test_fee_pp_peaks_at_mid_not_extreme(self):
        mid = fee_pp(0.50, 0.0, 0.0)
        wing = fee_pp(0.90, 0.0, 0.0)
        self.assertGreater(mid, wing)
        self.assertAlmostEqual(mid, 0.0175, places=6)

    def test_is_midband_inclusive_bounds(self):
        self.assertTrue(is_midband(0.40))
        self.assertTrue(is_midband(0.50))
        self.assertTrue(is_midband(0.60))
        self.assertFalse(is_midband(0.39))
        self.assertFalse(is_midband(0.61))

    def test_midband_skip_blocks_even_with_edge(self):
        cfg = _cfg(midband_taker_policy="skip", prefer_post_only=True)
        levels = [BookLevel(0.50, 200.0)]
        d = evaluate_side(
            side="UP",
            fair_p=0.72,
            ask=0.50,
            bid=0.48,
            ask_levels=levels,
            cfg=cfg,
            stake_usd=5.0,
            remaining_loss_budget_usd=50.0,
            btc_daily_vol_pct=3.5,
        )
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_midband_taker_ban")
        self.assertTrue(d.in_midband)

    def test_midband_raise_requires_higher_net_edge(self):
        cfg = _cfg(
            midband_taker_policy="raise_min_edge",
            midband_extra_min_edge_pp=0.02,
            min_edge_pp=0.005,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.005,
        )
        delay = taker_delay_buffer_pp(cfg, 3.5)
        required = cfg.min_edge_pp + delay + cfg.midband_extra_min_edge_pp
        self.assertAlmostEqual(required, 0.030, places=6)

        # Tiny edge at 50c cannot clear fee + extra 2c
        levels = [BookLevel(0.50, 200.0)]
        d = evaluate_side(
            side="UP",
            fair_p=0.52,
            ask=0.50,
            bid=0.49,
            ask_levels=levels,
            cfg=cfg,
            stake_usd=5.0,
            remaining_loss_budget_usd=50.0,
            btc_daily_vol_pct=3.5,
        )
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_net_edge_below_fee_aware_min")
        self.assertGreater(d.required_min_edge_pp, d.net_edge_pp)

    def test_wing_price_passes_when_net_edge_clears_fees(self):
        cfg = _cfg(
            midband_taker_policy="skip",
            min_edge_pp=0.005,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.005,
            kelly_fraction=0.5,
        )
        levels = [BookLevel(0.70, 100.0)]
        d = evaluate_side(
            side="UP",
            fair_p=0.85,
            ask=0.70,
            bid=0.69,
            ask_levels=levels,
            cfg=cfg,
            stake_usd=5.0,
            remaining_loss_budget_usd=50.0,
            btc_daily_vol_pct=3.5,
        )
        self.assertTrue(d.allow, d.reason)
        self.assertFalse(d.in_midband)
        self.assertGreaterEqual(d.net_edge_pp, d.required_min_edge_pp)
        # fee_pp = 0.07*0.7*0.3 + 0.005 + 0 = 0.0147 + 0.005
        self.assertAlmostEqual(d.fee_pp, 0.07 * 0.70 * 0.30 + 0.005, places=8)


class TestW2DelayBufferOrderType(unittest.TestCase):
    def test_delay_buffer_clamped_0_5_to_1_0_cents(self):
        cfg = _cfg(
            taker_delay_buffer_pp=0.005,
            taker_delay_buffer_vol_scale=True,
            taker_delay_vol_ref_pct=3.5,
            taker_delay_buffer_min_pp=0.005,
            taker_delay_buffer_max_pp=0.010,
        )
        self.assertAlmostEqual(taker_delay_buffer_pp(cfg, 3.5), 0.005, places=8)
        self.assertAlmostEqual(taker_delay_buffer_pp(cfg, 7.0), 0.010, places=8)
        self.assertAlmostEqual(taker_delay_buffer_pp(cfg, 1.0), 0.005, places=8)

    def test_delay_buffer_documented_250ms(self):
        cfg = _cfg()
        self.assertEqual(cfg.taker_delay_ms, 250)

    def test_prefer_gtd_post_only_default(self):
        self.assertEqual(choose_entry_order_type(_cfg()), "GTD")
        self.assertEqual(choose_entry_order_type(_cfg(prefer_post_only=False)), "FAK")

    def test_delay_buffer_raises_required_min_edge(self):
        cfg = _cfg(
            min_edge_pp=0.005,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.010,
            midband_enabled=False,
            kelly_fraction=0.5,
        )
        levels = [BookLevel(0.70, 100.0)]
        # gross 0.03, fee ~0.0147+0.005=0.0197, net ~0.0103; required 0.015 -> deny
        d = evaluate_side(
            side="DOWN",
            fair_p=0.73,
            ask=0.70,
            bid=0.69,
            ask_levels=levels,
            cfg=cfg,
            stake_usd=5.0,
            remaining_loss_budget_usd=50.0,
            btc_daily_vol_pct=3.5,
        )
        self.assertAlmostEqual(d.delay_buffer_pp, 0.010, places=8)
        self.assertAlmostEqual(d.required_min_edge_pp, 0.015, places=8)
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_net_edge_below_fee_aware_min")


class TestW5DepthKellySecondClip(unittest.TestCase):
    def test_depth_within_n_ticks_caps_walk(self):
        levels = [
            BookLevel(0.70, 10.0),
            BookLevel(0.71, 10.0),
            BookLevel(0.72, 10.0),
            BookLevel(0.73, 10.0),
            BookLevel(0.80, 999.0),
        ]
        slice_ = depth_within_n_ticks(levels, touch=0.70, n_ticks=3, tick_size=0.01)
        self.assertAlmostEqual(slice_.shares, 40.0)
        self.assertEqual(slice_.levels_used, 4)
        self.assertAlmostEqual(slice_.notional_usd, 10 * (0.70 + 0.71 + 0.72 + 0.73))
        self.assertGreater(slice_.depth_cost_pp, 0.0)

    def test_size_capped_by_depth_notional(self):
        depth = depth_within_n_ticks([BookLevel(0.70, 4.0)], 0.70, 3)
        sized, shares, deny = cap_size_usd(5.0, 0.70, depth, kelly_cap_usd=50.0, min_stake_usd=1.0)
        self.assertEqual(deny, "")
        self.assertAlmostEqual(sized, 2.8)  # 4 shares * 0.70
        self.assertAlmostEqual(shares, 4.0)

    def test_kelly_cap_uses_remaining_budget(self):
        # f* = (0.80-0.70)/(0.30) = 1/3; 0.25 * 1/3 * 12 = 1.0
        cap = fractional_kelly_usd(0.80, 0.70, 0.25, 12.0)
        self.assertAlmostEqual(cap, 1.0, places=8)
        self.assertEqual(fractional_kelly_usd(0.80, 0.70, 0.25, 0.0), 0.0)
        self.assertEqual(fractional_kelly_usd(0.60, 0.70, 0.25, 50.0), 0.0)

    def test_evaluate_sizes_to_kelly_not_full_stake(self):
        cfg = _cfg(
            midband_enabled=False,
            min_edge_pp=0.001,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.001,
            kelly_fraction=0.25,
            min_stake_usd=0.5,
        )
        levels = [BookLevel(0.70, 500.0)]
        d = evaluate_side(
            side="UP",
            fair_p=0.80,
            ask=0.70,
            bid=0.69,
            ask_levels=levels,
            cfg=cfg,
            stake_usd=5.0,
            remaining_loss_budget_usd=12.0,
            btc_daily_vol_pct=3.5,
        )
        self.assertTrue(d.allow, d.reason)
        self.assertAlmostEqual(d.kelly_cap_usd, 1.0, places=6)
        self.assertLess(d.size_usd, 5.0)
        self.assertAlmostEqual(d.size_usd, 1.0, places=6)

    def test_second_clip_blocked_when_edge_decayed(self):
        self.assertTrue(should_block_second_clip(0.04, 0.01, 0.01, decay_ratio=0.50))
        self.assertTrue(should_block_second_clip(0.04, 0.005, 0.01, decay_ratio=0.50))
        self.assertFalse(should_block_second_clip(0.04, 0.03, 0.01, decay_ratio=0.50))

    def test_evaluate_blocks_second_clip_after_decay(self):
        cfg = _cfg(
            midband_enabled=False,
            min_edge_pp=0.001,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.001,
            edge_decay_ratio=0.50,
            kelly_fraction=0.5,
        )
        levels = [BookLevel(0.70, 100.0)]
        kwargs = dict(
            side="UP",
            ask=0.70,
            bid=0.69,
            ask_levels=levels,
            cfg=cfg,
            stake_usd=5.0,
            remaining_loss_budget_usd=50.0,
            btc_daily_vol_pct=3.5,
        )
        first = evaluate_side(fair_p=0.85, **kwargs)
        self.assertTrue(first.allow, first.reason)
        second = evaluate_side(fair_p=0.73, prior_net_edge_pp=first.net_edge_pp, **kwargs)
        self.assertFalse(second.allow)
        self.assertEqual(second.reason, "skip_second_clip_edge_decayed")

    def test_select_entry_picks_higher_net_edge(self):
        cfg = _cfg(
            midband_enabled=False,
            min_edge_pp=0.001,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.001,
            kelly_fraction=0.5,
        )
        up = evaluate_side(
            "UP", 0.80, 0.70, 0.69, [BookLevel(0.70, 100.0)], cfg, 5.0, 50.0, 3.5
        )
        down = evaluate_side(
            "DOWN", 0.90, 0.72, 0.71, [BookLevel(0.72, 100.0)], cfg, 5.0, 50.0, 3.5
        )
        picked = select_entry(up, down)
        self.assertTrue(picked.allow)
        self.assertEqual(picked.side, "DOWN")

    def test_select_entry_keeps_the_only_allowed_side(self):
        cfg = _cfg(
            midband_enabled=False,
            min_edge_pp=0.001,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.001,
            kelly_fraction=0.5,
        )
        up = evaluate_side(
            "UP", 0.80, 0.70, 0.69, [BookLevel(0.70, 100.0)], cfg, 5.0, 50.0, 3.5
        )
        down = evaluate_side(
            "DOWN", 0.51, None, 0.49, [BookLevel(0.50, 100.0)], cfg, 5.0, 50.0, 3.5
        )
        self.assertTrue(up.allow, up.reason)
        self.assertFalse(down.allow)
        self.assertEqual(select_entry(up, down).side, "UP")

    def test_cap_size_denies_invalid_inputs_and_clipped_size_below_min_stake(self):
        depth = DepthSlice(
            shares=5.0,
            notional_usd=10.0,
            vwap=0.50,
            depth_cost_pp=0.0,
            levels_used=1,
        )
        sized, shares, deny = cap_size_usd(0.0, 0.50, depth, 50.0, 1.0)
        self.assertEqual(deny, "skip_invalid_size_inputs")
        self.assertEqual(sized, 0.0)
        self.assertEqual(shares, 0.0)
        _, _, deny = cap_size_usd(5.0, 0.0, depth, 50.0, 1.0)
        self.assertEqual(deny, "skip_invalid_size_inputs")

        # sized clears min stake, then share-clip drops notional below it
        _, _, deny = cap_size_usd(10.0, 0.50, depth, 50.0, 3.0)
        self.assertEqual(deny, "skip_size_below_min_stake")

    def test_evaluate_skips_non_positive_ask(self):
        cfg = _cfg(midband_enabled=False)
        d = evaluate_side(
            "UP", 0.80, 0.0, 0.69, [BookLevel(0.70, 100.0)], cfg, 5.0, 50.0, 3.5
        )
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "skip_no_ask")

    def test_required_min_edge_adds_midband_raise_and_bans_skip_policy(self):
        skip_cfg = _cfg(midband_taker_policy="skip", taker_delay_buffer_vol_scale=False)
        req, deny = required_min_edge_pp(skip_cfg, 0.50, 3.5, "GTD")
        self.assertEqual(deny, "skip_midband_taker_ban")
        self.assertAlmostEqual(req, skip_cfg.min_edge_pp + skip_cfg.taker_delay_buffer_pp)

        raise_cfg = _cfg(
            midband_taker_policy="raise_min_edge",
            midband_extra_min_edge_pp=0.02,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.005,
            min_edge_pp=0.005,
        )
        req, deny = required_min_edge_pp(raise_cfg, 0.50, 3.5, "GTD")
        self.assertEqual(deny, "")
        self.assertAlmostEqual(req, 0.030, places=6)
        req, deny = required_min_edge_pp(raise_cfg, 0.70, 3.5, "GTD")
        self.assertEqual(deny, "")
        self.assertAlmostEqual(req, 0.010, places=6)

        with self.assertRaises(ValueError):
            required_min_edge_pp(
                _cfg(midband_taker_policy="yolo"),  # type: ignore[arg-type]
                0.50,
                3.5,
                "GTD",
            )

    def test_decision_log_fields_are_json_ready(self):
        cfg = _cfg(
            midband_enabled=False,
            min_edge_pp=0.001,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.001,
            kelly_fraction=0.5,
        )
        d = evaluate_side(
            "UP", 0.80, 0.70, 0.69, [BookLevel(0.70, 100.0)], cfg, 5.0, 50.0, 3.5
        )
        fields = decision_log_fields(d)
        self.assertTrue(fields["allow"])
        self.assertEqual(fields["side"], "UP")
        self.assertEqual(fields["reason"], "enter")
        for key in (
            "entry_price",
            "fair_p",
            "net_edge_pp",
            "required_min_edge_pp",
            "size_usd",
            "kelly_cap_usd",
            "order_type",
            "in_midband",
        ):
            self.assertIn(key, fields)


class TestConfigAndDryRunDefault(unittest.TestCase):
    def test_yaml_mapping_roundtrip_policies(self):
        cfg = winmore_config_from_mapping(
            {
                "min_edge_pp": 0.006,
                "midband": {"taker_policy": "raise_min_edge", "extra_min_edge_pp": 0.015},
                "taker_delay": {
                    "delay_ms": 250,
                    "buffer_pp": 0.008,
                    "prefer_post_only": True,
                    "order_type_post_only": "GTD",
                },
                "sizing": {"depth_ticks": 2, "kelly_fraction": 0.2},
            }
        )
        self.assertEqual(cfg.midband_taker_policy, "raise_min_edge")
        self.assertEqual(cfg.taker_delay_ms, 250)
        self.assertEqual(cfg.depth_ticks, 2)
        self.assertEqual(choose_entry_order_type(cfg), "GTD")

    def test_unknown_midband_policy_raises(self):
        with self.assertRaises(ValueError):
            winmore_config_from_mapping({"midband": {"taker_policy": "yolo"}})

    def test_profiles_yaml_loads_w1_w2_w5(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("pyyaml not installed")
        path = Path(__file__).resolve().parents[1] / "config" / "btc_5m_profiles.yaml"
        with open(path) as f:
            cfg = yaml.safe_load(f)
        cons = winmore_config_from_mapping(cfg["profiles"]["conservative"]["winmore"])
        agg = winmore_config_from_mapping(cfg["profiles"]["aggressive"]["winmore"])
        self.assertEqual(cons.midband_taker_policy, "skip")
        self.assertEqual(agg.midband_taker_policy, "raise_min_edge")
        self.assertEqual(cons.taker_delay_ms, 250)
        self.assertEqual(agg.taker_delay_ms, 250)
        self.assertTrue(cons.prefer_post_only)
        self.assertEqual(choose_entry_order_type(cons), "GTD")
        self.assertEqual(cons.depth_ticks, 3)
        self.assertGreater(agg.taker_delay_buffer_pp, cons.taker_delay_buffer_pp)

    def test_runner_execute_flag_defaults_false(self):
        """Dry-run remains default; --execute is opt-in only."""
        runner = Path(__file__).with_name("test_btc_5m_session_exit_sl.py")
        src = runner.read_text()
        self.assertIn("ap.add_argument('--execute', action='store_true')", src)
        self.assertNotIn("execute=True", src.replace("args.execute", ""))

        ap = argparse.ArgumentParser()
        ap.add_argument("--execute", action="store_true")
        ns = ap.parse_args([])
        self.assertFalse(ns.execute)

    def test_empty_mapping_uses_paper_defaults(self):
        cfg = winmore_config_from_mapping(None)
        self.assertTrue(cfg.midband_enabled)
        self.assertEqual(cfg.midband_taker_policy, "skip")
        self.assertEqual(cfg.taker_delay_ms, 250)
        self.assertEqual(choose_entry_order_type(cfg), "GTD")


if __name__ == "__main__":
    unittest.main()
