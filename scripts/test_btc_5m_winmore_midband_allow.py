#!/usr/bin/env python3
"""W1 raise_min_edge allow path. Main only tests the mid-band deny.

No network. No --execute.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_winmore_gates import BookLevel, WinmoreConfig, evaluate_side


class TestMidbandRaiseAllow(unittest.TestCase):
    def test_should_enter_in_midband_when_raise_min_edge_clears(self):
        cfg = WinmoreConfig(
            midband_taker_policy="raise_min_edge",
            midband_extra_min_edge_pp=0.02,
            min_edge_pp=0.005,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.005,
            kelly_fraction=0.5,
        )
        decision = evaluate_side(
            side="UP",
            fair_p=0.82,
            ask=0.50,
            bid=0.49,
            ask_levels=[BookLevel(0.50, 500.0)],
            cfg=cfg,
            stake_usd=5.0,
            remaining_loss_budget_usd=50.0,
            btc_daily_vol_pct=3.5,
        )
        self.assertTrue(decision.allow, decision.reason)
        self.assertEqual(decision.reason, "enter")
        self.assertTrue(decision.in_midband)
        self.assertAlmostEqual(decision.required_min_edge_pp, 0.030, places=6)
        self.assertGreater(decision.net_edge_pp, decision.required_min_edge_pp)


if __name__ == "__main__":
    unittest.main()
