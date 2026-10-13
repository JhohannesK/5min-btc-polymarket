#!/usr/bin/env python3
"""Complementary W6/W3 integrity gaps not covered on main or in open drafts.

Locks:
  unknown top-of-book size skips the depth walk (looks cheaper than a known thin book)
  open_ticket does not re-check can_open_ticket, so a duplicate bucket still
    increments trades_count and can exhaust the daily trade budget
  summarize_maker_pilot(quotes=engine.quotes) counts rebate on cancelled unfilled
    quotes because the runner always passes the full quote list

No network.
"""

from __future__ import annotations

import io
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.modules.setdefault("requests", types.ModuleType("requests"))

from btc_5m_maker_pilot import (
    MakerPilotConfig,
    MakerPilotEngine,
    SideBook,
    summarize_maker_pilot,
)
from btc_5m_state_tracker import StateTracker, TicketState
from btc_5m_twap_fair import estimate_trade_edge


def _book(side: str, bid: float, ask: float, token_id: str = "tok") -> SideBook:
    return SideBook(side=side, token_id=token_id, best_bid=bid, best_ask=ask)


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


class UnknownBookAskSizeTests(unittest.TestCase):
    def test_should_skip_depth_walk_when_top_of_book_size_is_unknown(self):
        unknown = estimate_trade_edge(
            0.80, 0.70, 0.69, shares=10_000.0, book_ask_size=0.0
        )
        known_thin = estimate_trade_edge(
            0.80, 0.70, 0.69, shares=10_000.0, book_ask_size=1.0
        )
        self.assertEqual(unknown["depth_cost_bps"], 0.0)
        self.assertEqual(known_thin["depth_cost_bps"], 200.0)
        self.assertGreater(known_thin["cost_bps"], unknown["cost_bps"])
        self.assertGreater(unknown["net_edge_bps"], known_thin["net_edge_bps"])
        self.assertEqual(unknown["signal"], "buy")


class DuplicateOpenTicketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tracker = StateTracker(
            Path(self.tmp.name),
            daily_loss_limit_usd=50.0,
            max_trades_per_day=2,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_should_inflate_trades_count_when_open_ticket_skips_the_gate(self):
        self.tracker.open_ticket(_ticket(1, side="UP"))
        self.tracker.open_ticket(_ticket(1, side="DOWN"))

        state = self.tracker.load_state()
        self.assertEqual(state.trades_count, 2)
        self.assertEqual(len(state.active_tickets), 1)
        self.assertEqual(state.active_tickets[1].side, "DOWN")

        ok, reason = self.tracker.can_open_ticket(2)
        self.assertFalse(ok)
        self.assertIn("daily_trade_limit_reached_2/2", reason)


class CancelledQuoteRebateTests(unittest.TestCase):
    def test_should_include_cancelled_quote_rebate_when_summarizing_engine_quotes(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        eng = MakerPilotEngine(cfg)
        up = _book("UP", 0.44, 0.46, token_id="up-tok")
        dn = _book("DOWN", 0.54, 0.56, token_id="dn-tok")

        with redirect_stdout(io.StringIO()):
            posted = eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=up,
                down_book=dn,
                stake_usd=5.0,
                bucket=4,
            )
            cancelled = eng.on_tick(
                now=1_001.0,
                fair_signal="down_favored",
                up_book=up,
                down_book=dn,
                stake_usd=5.0,
                bucket=4,
            )
            later = eng.on_tick(
                now=1_002.0,
                fair_signal="down_favored",
                up_book=up,
                down_book=dn,
                stake_usd=5.0,
                bucket=4,
            )

        self.assertEqual(posted[0]["event"], "shadow_post")
        self.assertEqual(cancelled[0]["event"], "would_cancel")
        self.assertEqual(later[0]["event"], "shadow_post")
        self.assertEqual(len(eng.quotes), 2)

        live_rebate = sum(
            float(q.rebate_estimate_usd or 0.0)
            for q in eng.quotes
            if q.status == "live"
        )
        filled_rebate = sum(
            float(q.rebate_estimate_usd or 0.0)
            for q in eng.quotes
            if q.status == "filled"
        )
        summary = summarize_maker_pilot(eng.events, eng.quotes)
        self.assertEqual(summary["quotes_posted"], 2)
        self.assertEqual(summary["would_be_cancels"], 1)
        self.assertEqual(summary["would_be_fills"], 0)
        self.assertGreater(summary["rebate_estimate_usd"], live_rebate + filled_rebate)
        self.assertAlmostEqual(
            summary["rebate_estimate_usd"],
            sum(float(q.rebate_estimate_usd or 0.0) for q in eng.quotes),
            places=8,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
