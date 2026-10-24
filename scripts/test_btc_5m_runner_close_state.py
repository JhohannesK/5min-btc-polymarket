#!/usr/bin/env python3
"""Unit tests for failed-close PnL, wide-spread maker signal, and GTC px.

No network. No execute / live posting.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_maker_pilot import (
    LIVE_STUB_REASON,
    MakerPilotConfig,
    MakerPilotEngine,
    SideBook,
    live_maker_allowed,
)
from btc_5m_runner_close_state import (
    maker_fair_signal_when_spread_blocked,
    maker_tick_rtds_ready,
    pnl_for_state_tracker,
    resolve_gtc_fallback_reference_px,
    twap_position_wins,
    twap_settle_result,
)
from btc_5m_state_tracker import StateTracker, TicketState


def _ticket(bucket: int) -> TicketState:
    return TicketState(
        bucket=bucket,
        market_slug="btc-updown-5m-1",
        opened_at=1.0,
        side="UP",
        entry_price=0.60,
        shares=10.0,
        cost_usdc=6.0,
        token_id="tok",
        status="open",
    )


class FailedClosePnlTests(unittest.TestCase):
    def test_should_write_zero_when_close_pnl_is_missing(self):
        self.assertEqual(pnl_for_state_tracker(None), 0.0)

    def test_should_keep_a_realized_loss(self):
        self.assertEqual(pnl_for_state_tracker(-12.5), -12.5)

    def test_should_free_the_bucket_and_skip_the_loss_when_pnl_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = StateTracker(Path(tmp), daily_loss_limit_usd=50.0)
            tracker.open_ticket(_ticket(100))
            tracker.close_ticket(100, pnl_for_state_tracker(None), "failed")

            summary = tracker.get_daily_summary()
            self.assertEqual(summary["realized_pnl_usdc"], 0.0)
            self.assertEqual(summary["remaining_loss_budget_usd"], 50.0)
            self.assertEqual(summary["active_tickets_count"], 0)

            can_open, reason = tracker.can_open_ticket(100)
            self.assertTrue(can_open, reason)


class WideSpreadMakerSignalTests(unittest.TestCase):
    def test_should_keep_the_fair_signal_when_taker_is_blocked(self):
        self.assertEqual(
            maker_fair_signal_when_spread_blocked("up_favored"),
            "up_favored",
        )
        self.assertEqual(
            maker_fair_signal_when_spread_blocked("down_favored"),
            "down_favored",
        )

    def test_should_fall_back_to_unavailable_without_a_fair_value(self):
        self.assertEqual(maker_fair_signal_when_spread_blocked(None), "unavailable")
        self.assertEqual(maker_fair_signal_when_spread_blocked(""), "unavailable")

    def test_should_post_a_shadow_quote_only_when_the_fair_signal_is_kept(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        up = SideBook(side="UP", token_id="up", best_bid=0.44, best_ask=0.52)
        down = SideBook(side="DOWN", token_id="dn", best_bid=0.48, best_ask=0.56)

        blocked = MakerPilotEngine(cfg)
        blocked.on_tick(
            now=1_000.0,
            fair_signal=maker_fair_signal_when_spread_blocked(None),
            up_book=up,
            down_book=down,
            stake_usd=10.0,
            execute=False,
            rtds_ready=maker_tick_rtds_ready("spread_wide", False),
            bucket=1,
            market_slug="m",
            seconds_left=120.0,
        )
        self.assertIsNone(blocked.active)

        live = MakerPilotEngine(cfg)
        live.on_tick(
            now=1_000.0,
            fair_signal=maker_fair_signal_when_spread_blocked("up_favored"),
            up_book=up,
            down_book=down,
            stake_usd=10.0,
            execute=False,
            rtds_ready=maker_tick_rtds_ready("spread_wide", False),
            bucket=1,
            market_slug="m",
            seconds_left=120.0,
        )
        self.assertIsNotNone(live.active)
        self.assertEqual(live.active.side, "UP")


class GtcFallbackPxTests(unittest.TestCase):
    def test_should_prefer_gamma_over_last_print_and_entry(self):
        self.assertAlmostEqual(
            resolve_gtc_fallback_reference_px(0.41, 0.39, 0.60),
            0.41,
        )

    def test_should_use_last_print_when_gamma_is_missing(self):
        self.assertAlmostEqual(
            resolve_gtc_fallback_reference_px(None, 0.39, 0.60),
            0.39,
        )

    def test_should_use_entry_when_gamma_and_last_print_are_missing(self):
        self.assertAlmostEqual(
            resolve_gtc_fallback_reference_px(None, None, 0.60),
            0.60,
        )


class MakerRtdsReadyTests(unittest.TestCase):
    def test_should_deny_rtds_on_too_late_even_when_execute_is_on(self):
        self.assertFalse(maker_tick_rtds_ready("too_late", True))
        self.assertTrue(maker_tick_rtds_ready("spread_wide", True))
        self.assertTrue(maker_tick_rtds_ready("normal", True))
        self.assertFalse(maker_tick_rtds_ready("spread_wide", False))

    def test_should_fail_live_maker_on_rtds_when_the_late_path_is_used(self):
        cfg = MakerPilotConfig(enabled=True, shadow=False, live_execute=True)
        late_ok, late_reason = live_maker_allowed(
            cfg,
            True,
            rtds_ready=maker_tick_rtds_ready("too_late", True),
            creds_ready=True,
        )
        wide_ok, wide_reason = live_maker_allowed(
            cfg,
            True,
            rtds_ready=maker_tick_rtds_ready("spread_wide", True),
            creds_ready=True,
        )
        self.assertFalse(late_ok)
        self.assertEqual(late_reason, "rtds_not_ready")
        self.assertFalse(wide_ok)
        self.assertEqual(wide_reason, LIVE_STUB_REASON)


class TwapSettleCompareTests(unittest.TestCase):
    def test_should_call_an_exact_tie_up(self):
        self.assertEqual(twap_settle_result(100.0, 100.0), "UP")
        self.assertEqual(twap_settle_result(99.9, 100.0), "DOWN")

    def test_should_treat_side_case_as_a_loss(self):
        self.assertTrue(twap_position_wins("UP", "UP"))
        self.assertFalse(twap_position_wins("up", "UP"))
        self.assertFalse(twap_position_wins("down", "DOWN"))


if __name__ == "__main__":
    unittest.main()
