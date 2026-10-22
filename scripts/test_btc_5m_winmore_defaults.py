#!/usr/bin/env python3
"""Unit tests for winmore yaml defaults and decision log fields. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_winmore_gates import (
    EntryDecision,
    decision_log_fields,
    winmore_config_from_mapping,
)


class TestWinmoreConfigDefaults(unittest.TestCase):
    def test_should_use_conservative_defaults_when_mapping_is_missing(self):
        cfg = winmore_config_from_mapping(None)
        self.assertTrue(cfg.midband_enabled)
        self.assertEqual(cfg.midband_lower, 0.40)
        self.assertEqual(cfg.midband_upper, 0.60)
        self.assertEqual(cfg.midband_taker_policy, "skip")
        self.assertEqual(cfg.min_edge_pp, 0.005)
        self.assertEqual(cfg.taker_delay_ms, 250)
        self.assertTrue(cfg.prefer_post_only)
        self.assertEqual(cfg.entry_order_type_post_only, "GTD")
        self.assertEqual(cfg.entry_order_type_taker, "FAK")
        self.assertEqual(cfg.depth_ticks, 3)
        self.assertEqual(cfg.kelly_fraction, 0.25)
        self.assertEqual(cfg.edge_decay_ratio, 0.50)
        self.assertEqual(cfg.min_stake_usd, 1.0)

    def test_should_treat_empty_nested_maps_as_defaults(self):
        cfg = winmore_config_from_mapping({"midband": {}, "taker_delay": {}, "sizing": {}})
        self.assertEqual(cfg.midband_taker_policy, "skip")
        self.assertEqual(cfg.taker_delay_buffer_pp, 0.005)
        self.assertEqual(cfg.tick_size, 0.01)


class TestDecisionLogFields(unittest.TestCase):
    def test_should_export_the_fields_the_runner_persists(self):
        d = EntryDecision(
            allow=False,
            reason="skip_midband_taker_ban",
            side="UP",
            entry_price=0.50,
            fair_p=0.62,
            gross_edge_pp=0.12,
            fee_pp=0.03,
            net_edge_pp=0.09,
            required_min_edge_pp=0.01,
            delay_buffer_pp=0.005,
            size_usd=0.0,
            size_shares=0.0,
            depth_shares=10.0,
            depth_notional_usd=5.0,
            kelly_cap_usd=2.0,
            order_type="GTD",
            in_midband=True,
            details={"policy": "skip"},
        )
        fields = decision_log_fields(d)
        self.assertEqual(
            set(fields),
            {
                "allow",
                "reason",
                "side",
                "entry_price",
                "fair_p",
                "gross_edge_pp",
                "fee_pp",
                "net_edge_pp",
                "required_min_edge_pp",
                "delay_buffer_pp",
                "size_usd",
                "size_shares",
                "depth_shares",
                "depth_notional_usd",
                "kelly_cap_usd",
                "order_type",
                "in_midband",
                "details",
            },
        )
        self.assertFalse(fields["allow"])
        self.assertEqual(fields["reason"], "skip_midband_taker_ban")
        self.assertEqual(fields["details"], {"policy": "skip"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
