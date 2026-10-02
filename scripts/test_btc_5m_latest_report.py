#!/usr/bin/env python3
"""Unit tests for latest-run report skip/parse/format. No filesystem, no network."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_latest_report import (
    format_completed_report,
    parse_tail_json_text,
    report_skip_reason,
)


class TestParseTailJsonText(unittest.TestCase):
    def test_should_return_none_when_log_has_no_json_object(self):
        self.assertIsNone(parse_tail_json_text("started\nno blob here\n"))

    def test_should_parse_a_file_that_is_only_one_object(self):
        obj = parse_tail_json_text('{"result": "flat", "opened": {}}')
        self.assertEqual(obj["result"], "flat")

    def test_should_take_the_last_object_after_log_noise(self):
        txt = (
            "boot\n"
            '{"result": "stale"}\n'
            "more noise\n"
            '{"result": "closed", "realized_cashflow_pnl_usdc": -1.25}\n'
        )
        obj = parse_tail_json_text(txt)
        self.assertEqual(obj["result"], "closed")
        self.assertEqual(obj["realized_cashflow_pnl_usdc"], -1.25)

    def test_should_raise_on_truncated_tail_json(self):
        with self.assertRaises(json.JSONDecodeError):
            parse_tail_json_text("ok\n{\"result\": ")


class TestReportSkipReason(unittest.TestCase):
    def test_should_skip_when_runtime_has_no_logs(self):
        self.assertEqual(
            report_skip_reason(
                has_logs=False,
                marker_exists=False,
                pidfile_exists=False,
                pid_alive=False,
            ),
            "NO_REPORT:no_logs",
        )

    def test_should_skip_when_latest_log_already_reported(self):
        self.assertEqual(
            report_skip_reason(
                has_logs=True,
                marker_exists=True,
                pidfile_exists=True,
                pid_alive=True,
            ),
            "NO_REPORT:already_reported",
        )

    def test_should_skip_while_bot_pid_is_alive(self):
        self.assertEqual(
            report_skip_reason(
                has_logs=True,
                marker_exists=False,
                pidfile_exists=True,
                pid_alive=True,
            ),
            "NO_REPORT:running",
        )

    def test_should_proceed_when_pidfile_is_stale(self):
        self.assertIsNone(
            report_skip_reason(
                has_logs=True,
                marker_exists=False,
                pidfile_exists=True,
                pid_alive=False,
            )
        )

    def test_should_proceed_when_pidfile_is_missing(self):
        self.assertIsNone(
            report_skip_reason(
                has_logs=True,
                marker_exists=False,
                pidfile_exists=False,
                pid_alive=False,
            )
        )


class TestFormatCompletedReport(unittest.TestCase):
    def test_should_render_open_close_and_fallback(self):
        text = format_completed_report(
            {
                "result": "closed",
                "opened": {
                    "side": "UP",
                    "market_slug": "btc-updown-5m-1",
                    "cost_usdc": 4.2,
                    "open_tx": "0xabc",
                },
                "closed": {
                    "close_success": True,
                    "close_status": "MATCHED",
                    "close_skipped": None,
                    "close_tx": "0xdef",
                },
                "close_debug": [{}, {}],
                "realized_cashflow_pnl_usdc": 0.15,
                "close_fallback": "force_gtc",
            },
            "/tmp/btc5m_1.log",
        )
        self.assertIn("result: closed", text)
        self.assertIn("open: UP btc-updown-5m-1 cost=4.2 tx=0xabc", text)
        self.assertIn("close: success=True status=MATCHED skipped=None tx=0xdef", text)
        self.assertIn("realized_cashflow_pnl_usdc: 0.15", text)
        self.assertIn("close_debug_attempts: 2", text)
        self.assertIn("close_fallback: force_gtc", text)

    def test_should_say_none_when_session_never_opened(self):
        text = format_completed_report({"result": "no_entry"}, "runtime/btc5m.log")
        self.assertIn("open: none", text)
        self.assertIn("close: none", text)
        self.assertNotIn("close_fallback:", text)


if __name__ == "__main__":
    unittest.main()
