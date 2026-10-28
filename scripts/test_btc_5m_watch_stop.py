#!/usr/bin/env python3
"""Live watcher stop predicates. No network. No live trading flag."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_watch_stop import (
    grep_opened_and_matched,
    last_json_object,
    opened_fill_reported,
)


OPENED = {
    "opened_at": "2026-10-28T10:00:00Z",
    "market_slug": "btc-updown-5m-1",
    "side": "UP",
    "entry_price": 0.62,
    "shares": 8.0,
}


class WatchStopPredicateTests(unittest.TestCase):
    def test_should_stop_on_pretty_report_with_opened_fill(self):
        text = json.dumps(
            {
                "result": "closed",
                "opened": OPENED,
                "closed": {"order_post_result": {"status": "matched"}},
            },
            indent=2,
        )
        self.assertTrue(grep_opened_and_matched(text))
        self.assertTrue(opened_fill_reported(text))

    def test_should_not_stop_on_timeout_without_opened_object(self):
        text = json.dumps(
            {"result": "no_entry_timeout", "attempts": [{"status": "heartbeat_no_current_market"}]},
            indent=2,
        )
        self.assertFalse(grep_opened_and_matched(text))
        self.assertFalse(opened_fill_reported(text))
        self.assertIsNotNone(last_json_object(text))

    def test_should_not_treat_null_opened_as_a_fill(self):
        text = json.dumps(
            {
                "opened": None,
                "closed": {"order_post_result": {"status": "matched"}},
            },
            indent=2,
        )
        self.assertTrue(grep_opened_and_matched(text), "shell grep still matches the key")
        self.assertFalse(opened_fill_reported(text))

    def test_should_ignore_close_matched_when_opened_was_never_filled(self):
        text = json.dumps(
            {
                "result": "no_entry_timeout",
                "opened": {},
                "closed": {"order_post_result": {"status": "matched"}},
            },
            indent=2,
        )
        self.assertTrue(grep_opened_and_matched(text), "shell grep matches close status")
        self.assertFalse(opened_fill_reported(text))

    def test_should_detect_compact_opened_fill_the_shell_grep_misses(self):
        text = json.dumps({"opened": OPENED, "closed": {"status": "matched"}}, separators=(",", ":"))
        self.assertIn('"opened"', text)
        self.assertNotIn('"status": "matched"', text)
        self.assertFalse(grep_opened_and_matched(text))
        self.assertTrue(opened_fill_reported(text))

    def test_should_read_the_last_json_object_after_log_lines(self):
        text = (
            "[RTDS] Connected successfully: windowSeconds=60\n"
            + json.dumps({"opened": OPENED})
        )
        self.assertTrue(opened_fill_reported(text))
        self.assertEqual(last_json_object(text)["opened"]["side"], "UP")


if __name__ == "__main__":
    unittest.main()
