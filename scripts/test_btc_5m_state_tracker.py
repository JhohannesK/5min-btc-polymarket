#!/usr/bin/env python3
"""Unit tests for one-ticket-per-bucket and daily risk limits.

No network. Persistence uses a temp dir.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_state_tracker import StateTracker, TicketState


def _ticket(bucket: int = 1, side: str = "UP") -> TicketState:
    return TicketState(
        bucket=bucket,
        market_slug=f"btc-updown-5m-{bucket}",
        opened_at=1_000.0,
        side=side,
        entry_price=0.42,
        shares=10.0,
        cost_usdc=4.20,
        token_id="tok",
        status="open",
    )


class TestCanOpenTicket(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tracker = StateTracker(
            Path(self.tmp.name),
            daily_loss_limit_usd=50.0,
            max_trades_per_day=2,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_should_allow_first_ticket_for_bucket(self):
        ok, reason = self.tracker.can_open_ticket(1)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_should_block_second_open_ticket_in_same_bucket(self):
        self.tracker.open_ticket(_ticket(1))
        ok, reason = self.tracker.can_open_ticket(1)
        self.assertFalse(ok)
        self.assertIn("ticket_already_open_for_bucket_1", reason)

    def test_should_allow_different_bucket_while_first_is_open(self):
        self.tracker.open_ticket(_ticket(1))
        ok, reason = self.tracker.can_open_ticket(2)
        self.assertTrue(ok, reason)

    def test_should_block_when_daily_trade_limit_reached(self):
        self.tracker.open_ticket(_ticket(1))
        self.tracker.open_ticket(_ticket(2))
        ok, reason = self.tracker.can_open_ticket(3)
        self.assertFalse(ok)
        self.assertIn("daily_trade_limit_reached_2/2", reason)

    def test_should_block_when_daily_loss_limit_exceeded(self):
        self.tracker.open_ticket(_ticket(1))
        self.tracker.close_ticket(1, pnl_usdc=-50.01)
        ok, reason = self.tracker.can_open_ticket(2)
        self.assertFalse(ok)
        self.assertIn("daily_loss_limit_exceeded", reason)

    def test_should_still_open_when_pnl_equals_loss_limit(self):
        # realized_pnl < -limit is the gate; equality is not exceeded.
        self.tracker.open_ticket(_ticket(1))
        self.tracker.close_ticket(1, pnl_usdc=-50.0)
        ok, reason = self.tracker.can_open_ticket(2)
        self.assertTrue(ok, reason)
        self.assertAlmostEqual(self.tracker.remaining_loss_budget_usd(), 0.0)


class TestRemainingLossBudget(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tracker = StateTracker(
            Path(self.tmp.name),
            daily_loss_limit_usd=50.0,
            max_trades_per_day=20,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_should_start_at_full_daily_limit(self):
        self.assertAlmostEqual(self.tracker.remaining_loss_budget_usd(), 50.0)

    def test_should_shrink_budget_after_realized_loss(self):
        self.tracker.open_ticket(_ticket(1))
        self.tracker.close_ticket(1, pnl_usdc=-12.5)
        self.assertAlmostEqual(self.tracker.remaining_loss_budget_usd(), 37.5)

    def test_should_floor_budget_at_zero_after_limit_breach(self):
        self.tracker.open_ticket(_ticket(1))
        self.tracker.close_ticket(1, pnl_usdc=-80.0)
        self.assertAlmostEqual(self.tracker.remaining_loss_budget_usd(), 0.0)

    def test_should_grow_budget_after_realized_win(self):
        self.tracker.open_ticket(_ticket(1))
        self.tracker.close_ticket(1, pnl_usdc=8.0)
        self.assertAlmostEqual(self.tracker.remaining_loss_budget_usd(), 58.0)


class TestOpenClosePersistence(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_should_persist_open_ticket_across_reload(self):
        a = StateTracker(self.state_dir, daily_loss_limit_usd=50.0, max_trades_per_day=20)
        a.open_ticket(_ticket(9, side="DOWN"))

        b = StateTracker(self.state_dir, daily_loss_limit_usd=50.0, max_trades_per_day=20)
        state = b.load_state()
        self.assertIn(9, state.active_tickets)
        self.assertEqual(state.active_tickets[9].side, "DOWN")
        self.assertEqual(state.trades_count, 1)
        self.assertEqual(state.active_tickets[9].status, "open")

    def test_should_move_ticket_to_closed_and_accumulate_pnl(self):
        tracker = StateTracker(self.state_dir)
        tracker.open_ticket(_ticket(3))
        tracker.close_ticket(3, pnl_usdc=-2.25, status="closed")
        state = tracker.load_state()
        self.assertNotIn(3, state.active_tickets)
        self.assertEqual(len(state.closed_tickets), 1)
        self.assertEqual(state.closed_tickets[0].status, "closed")
        self.assertAlmostEqual(state.realized_pnl_usdc, -2.25)

    def test_should_no_op_close_for_unknown_bucket(self):
        tracker = StateTracker(self.state_dir)
        tracker.close_ticket(99, pnl_usdc=-5.0)
        state = tracker.load_state()
        self.assertEqual(state.realized_pnl_usdc, 0.0)
        self.assertEqual(state.closed_tickets, [])

    def test_should_recover_empty_state_from_corrupt_json(self):
        tracker = StateTracker(self.state_dir)
        today = tracker.get_current_date_utc()
        path = self.state_dir / f"state_{today}.json"
        path.write_text("{not json", encoding="utf-8")

        tracker._state = None
        state = tracker.load_state()
        self.assertEqual(state.date, today)
        self.assertEqual(state.trades_count, 0)
        self.assertEqual(state.active_tickets, {})

    def test_should_roundtrip_ticket_fields_through_json(self):
        tracker = StateTracker(self.state_dir)
        tracker.open_ticket(_ticket(4, side="UP"))
        today = tracker.get_current_date_utc()
        raw = json.loads((self.state_dir / f"state_{today}.json").read_text())
        ticket = raw["active_tickets"]["4"]
        self.assertEqual(ticket["market_slug"], "btc-updown-5m-4")
        self.assertEqual(ticket["token_id"], "tok")
        self.assertAlmostEqual(ticket["cost_usdc"], 4.20)

    def test_should_report_can_trade_false_when_loss_limit_hit(self):
        tracker = StateTracker(self.state_dir, daily_loss_limit_usd=10.0, max_trades_per_day=5)
        tracker.open_ticket(_ticket(1))
        tracker.close_ticket(1, pnl_usdc=-11.0)
        summary = tracker.get_daily_summary()
        self.assertFalse(summary["can_trade"])
        self.assertEqual(summary["active_tickets_count"], 0)
        self.assertAlmostEqual(summary["remaining_loss_budget_usd"], 0.0)

    def test_should_cache_in_memory_until_date_changes(self):
        tracker = StateTracker(self.state_dir)
        first = tracker.load_state()
        first.trades_count = 7
        second = tracker.load_state()
        self.assertIs(first, second)
        self.assertEqual(second.trades_count, 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
