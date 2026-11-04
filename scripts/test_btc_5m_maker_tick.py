#!/usr/bin/env python3
"""Runner always denies live maker on missing creds. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_maker_pilot import MakerPilotConfig
from btc_5m_maker_tick import (
    MAKER_TICK_CREDS_READY,
    maker_tick_live_gate,
    parse_maker_tick_source,
)

RUNNER = Path(__file__).resolve().parent / "test_btc_5m_session_exit_sl.py"


class MakerTickCredsReadyTests(unittest.TestCase):
    def test_should_hardcode_creds_ready_false(self):
        self.assertIs(MAKER_TICK_CREDS_READY, False)

    def test_should_deny_live_when_execute_and_rtds_are_up_but_creds_are_not(self):
        cfg = MakerPilotConfig(enabled=True, shadow=False, live_execute=True)
        ok, reason = maker_tick_live_gate(cfg, execute=True, rtds_ready=True)
        self.assertFalse(ok)
        self.assertEqual(reason, "creds_not_ready")

    def test_should_still_deny_shadow_before_the_creds_gate(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, live_execute=True)
        ok, reason = maker_tick_live_gate(cfg, execute=True, rtds_ready=True)
        self.assertFalse(ok)
        self.assertEqual(reason, "shadow_mode")


class MakerTickSourceTests(unittest.TestCase):
    def test_should_lock_run_maker_pilot_tick_creds_ready_false(self):
        flags = parse_maker_tick_source(RUNNER.read_text(encoding="utf-8"))
        self.assertTrue(flags["disabled_short_circuit"])
        self.assertTrue(flags["creds_ready_false"])
        self.assertTrue(flags["creds_ready_true_absent"])
        self.assertEqual(flags["creds_literal_values"], ["False"])
        self.assertTrue(flags["forwards_execute"])
        self.assertTrue(flags["forwards_rtds_ready"])
        self.assertTrue(flags["never_sets_creds_from_env"])


if __name__ == "__main__":
    unittest.main()
