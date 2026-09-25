#!/usr/bin/env python3
"""Unit tests for btc5m report tail-JSON parse and PnL rollup.

No network. No filesystem.
"""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_report_agg import aggregate_run_objects, parse_tail_json_text


class TailJsonParseTests(unittest.TestCase):
    def test_should_parse_leading_object(self):
        obj = parse_tail_json_text('{"result": "done", "realized_cashflow_pnl_usdc": 1.5}')
        self.assertEqual(obj["result"], "done")
        self.assertEqual(obj["realized_cashflow_pnl_usdc"], 1.5)

    def test_should_take_last_object_after_log_noise(self):
        txt = 'log line\n{"result": "skip"}\nmore noise\n{"result": "done", "realized_cashflow_pnl_usdc": -0.2}\n'
        obj = parse_tail_json_text(txt)
        self.assertEqual(obj["result"], "done")
        self.assertEqual(obj["realized_cashflow_pnl_usdc"], -0.2)

    def test_should_return_none_on_empty_or_invalid(self):
        self.assertIsNone(parse_tail_json_text(""))
        self.assertIsNone(parse_tail_json_text("no json here"))
        self.assertIsNone(parse_tail_json_text("{\nnot json"))
        self.assertIsNone(parse_tail_json_text("[1, 2]"))


class ReportAggTests(unittest.TestCase):
    def test_should_sum_numeric_pnl_and_count_results(self):
        out = aggregate_run_objects(
            [
                {
                    "result": "done",
                    "opened": {"side": "UP", "market_slug": "m1", "open_tx": "a"},
                    "closed": {"close_status": "matched", "close_tx": "b"},
                    "realized_cashflow_pnl_usdc": 1.25,
                },
                {
                    "result": "incomplete_close_failed",
                    "opened": {"side": "DOWN", "market_slug": "m2"},
                    "closed": {"close_skipped": "zero_effective_shares"},
                    "realized_cashflow_pnl_usdc": None,
                },
                {
                    "result": "done",
                    "opened": {},
                    "closed": {},
                    "realized_cashflow_pnl_usdc": -0.5,
                },
            ]
        )
        self.assertEqual(out["runs_parsed"], 3)
        self.assertEqual(out["results"]["done"], 2)
        self.assertEqual(out["realized_pnl_count"], 2)
        self.assertEqual(out["realized_pnl_sum_usdc"], 0.75)
        self.assertEqual(out["close_status"]["matched"], 1)
        self.assertEqual(out["close_status"]["zero_effective_shares"], 1)
        self.assertEqual(out["close_status"]["none"], 1)

    def test_should_ignore_string_pnl_and_empty_input(self):
        out = aggregate_run_objects(
            [{"result": "no_entry_timeout", "realized_cashflow_pnl_usdc": "n/a"}]
        )
        self.assertIsNone(out["realized_pnl_sum_usdc"])
        self.assertEqual(out["realized_pnl_count"], 0)
        self.assertEqual(aggregate_run_objects([])["runs_parsed"], 0)


if __name__ == "__main__":
    unittest.main()
