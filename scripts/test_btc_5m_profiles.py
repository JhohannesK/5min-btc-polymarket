#!/usr/bin/env python3
"""Unit tests for YAML profile → runtime mapping.

No network. Loads the checked-in config as a fixture.
"""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_profiles import profiles_from_yaml_config, runtime_profile_from_yaml
from btc_5m_winmore_gates import WinmoreConfig


def _load_repo_yaml() -> dict:
    path = Path(__file__).resolve().parents[1] / "config" / "btc_5m_profiles.yaml"
    with path.open() as f:
        return yaml.safe_load(f)


class ProfileYamlMappingTests(unittest.TestCase):
    def setUp(self):
        self.config = _load_repo_yaml()
        self.profiles = profiles_from_yaml_config(self.config)

    def test_should_map_conservative_and_aggressive_risk_knobs(self):
        cons = self.profiles["conservative"]
        agg = self.profiles["aggressive"]
        self.assertEqual(cons["threshold"], 0.70)
        self.assertEqual(cons["stake_usd"], 5)
        self.assertEqual(cons["stop_loss_pct"], 0.25)
        self.assertEqual(cons["min_edge_bps"], 5.0)
        self.assertEqual(cons["max_trades_per_day"], 12)
        self.assertTrue(cons["hold_to_redeem"])
        self.assertTrue(cons["use_twap_fair_value"])

        self.assertEqual(agg["stop_loss_pct"], 0.30)
        self.assertEqual(agg["min_edge_bps"], 3.0)
        self.assertEqual(agg["max_trades_per_day"], 20)
        self.assertEqual(agg["daily_max_loss_usd"], 50.0)

    def test_should_merge_shared_then_profile_maker_pilot(self):
        cons = self.profiles["conservative"]
        agg = self.profiles["aggressive"]
        self.assertEqual(cons["maker_pilot"]["gtd_ttl_sec"], 15)
        self.assertEqual(agg["maker_pilot"]["gtd_ttl_sec"], 12)
        self.assertFalse(cons["maker_pilot"]["enabled"])
        self.assertTrue(cons["maker_pilot"]["shadow"])
        self.assertFalse(cons["maker_pilot"]["live_execute"])
        self.assertEqual(cons["maker_pilot"]["taker_fee_rate"], 0.07)
        self.assertEqual(cons["maker_pilot"]["max_quotes_per_bucket"], 4)

    def test_should_inherit_shared_entry_timing_window(self):
        et = self.profiles["conservative"]["entry_timing"]
        self.assertTrue(et["enabled"])
        self.assertEqual(et["window_max_seconds_left"], 240)
        self.assertEqual(et["window_min_seconds_left"], 45)
        self.assertEqual(et["hard_skip_last_sec"], 20)

    def test_should_let_profile_entry_timing_override_shared(self):
        shared = {"session_timing": {"entry_timing": {"enabled": True, "hard_skip_last_sec": 20}}}
        profile = {"twap_fair_value": {"entry_timing": {"hard_skip_last_sec": 15}}}
        mapped = runtime_profile_from_yaml(profile, shared)
        self.assertEqual(mapped["entry_timing"]["hard_skip_last_sec"], 15)
        self.assertTrue(mapped["entry_timing"]["enabled"])

    def test_should_parse_winmore_midband_policy_per_profile(self):
        cons_wm = self.profiles["conservative"]["winmore"]
        agg_wm = self.profiles["aggressive"]["winmore"]
        self.assertIsInstance(cons_wm, WinmoreConfig)
        self.assertEqual(cons_wm.midband_taker_policy, "skip")
        self.assertEqual(agg_wm.midband_taker_policy, "raise_min_edge")
        self.assertAlmostEqual(cons_wm.min_edge_pp, 0.005)
        self.assertAlmostEqual(agg_wm.min_edge_pp, 0.003)
        self.assertAlmostEqual(cons_wm.kelly_fraction, 0.25)
        self.assertAlmostEqual(agg_wm.kelly_fraction, 0.30)

    def test_should_return_empty_when_profiles_block_missing(self):
        self.assertEqual(profiles_from_yaml_config({}), {})
        self.assertEqual(profiles_from_yaml_config({"profiles": None}), {})

    def test_should_default_hold_and_edge_when_twap_block_missing(self):
        mapped = runtime_profile_from_yaml({}, {})
        self.assertTrue(mapped["use_twap_fair_value"])
        self.assertEqual(mapped["min_edge_bps"], 5.0)
        self.assertTrue(mapped["hold_to_redeem"])
        self.assertEqual(mapped["max_trades_per_day"], 20)
        self.assertEqual(mapped["stop_loss_pct"], 0.25)


if __name__ == "__main__":
    unittest.main()
