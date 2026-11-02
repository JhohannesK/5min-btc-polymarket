#!/usr/bin/env python3
"""btc5m_report glob overlap, mtime order, and limit slice. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_report_logs import (
    is_report_log_name,
    parse_report_log_source,
    select_report_logs,
)

REPORT_PY = Path(__file__).resolve().parent / "btc5m_report.py"


class ReportLogNameTests(unittest.TestCase):
    def test_should_match_the_three_runtime_globs(self):
        self.assertTrue(is_report_log_name("btc5m_one_trade.log"))
        self.assertTrue(is_report_log_name("btc5m_one_trade_extra.log"))
        self.assertTrue(is_report_log_name("btc5m_live_until_20261021.log"))
        self.assertTrue(is_report_log_name("btc5m_conservative_20261102T100000Z.log"))

    def test_should_skip_pidfiles_markers_and_latest_link(self):
        self.assertFalse(is_report_log_name("btc5m.pid"))
        self.assertFalse(is_report_log_name("btc5m.meta.json"))
        self.assertFalse(is_report_log_name("latest.log"))
        self.assertFalse(is_report_log_name("btc5m_conservative_1.log.reported"))


class SelectReportLogsTests(unittest.TestCase):
    def test_should_dedupe_overlapping_globs_and_keep_newest_first(self):
        newer = "/runtime/btc5m_conservative_2.log"
        older = "/runtime/btc5m_one_trade.log"
        selected = select_report_logs(
            [
                (older, 100.0),
                (newer, 200.0),
                (older, 100.0),
            ],
            limit=20,
        )
        self.assertEqual(selected, [newer, older])

    def test_should_slice_to_limit_after_mtime_sort(self):
        selected = select_report_logs(
            [
                ("/runtime/a.log", 1.0),
                ("/runtime/b.log", 3.0),
                ("/runtime/c.log", 2.0),
            ],
            limit=2,
        )
        self.assertEqual(selected, ["/runtime/b.log", "/runtime/c.log"])

    def test_should_return_empty_when_limit_is_not_positive(self):
        entries = [("/runtime/a.log", 1.0)]
        self.assertEqual(select_report_logs(entries, 0), [])
        self.assertEqual(select_report_logs(entries, -1), [])
        self.assertEqual(select_report_logs([], 20), [])


class ReportLogSourceTests(unittest.TestCase):
    def test_should_keep_overlapping_globs_and_limit_slice_in_report_py(self):
        parsed = parse_report_log_source(REPORT_PY.read_text())
        self.assertEqual(
            parsed,
            {
                "one_trade_glob": True,
                "live_until_glob": True,
                "generic_glob": True,
                "dedupes": True,
                "slices_limit": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
