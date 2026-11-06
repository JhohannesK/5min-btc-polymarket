#!/usr/bin/env python3
"""latest_report glob is wider than report.py. No filesystem, no network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_latest_log_glob import (
    LATEST_LOG_NAME_GLOB,
    is_history_report_log_name,
    is_latest_log_name,
    parse_latest_log_glob_source,
    parse_report_log_glob_source,
)

SCRIPTS = Path(__file__).resolve().parent


class LatestLogNameTests(unittest.TestCase):
    def test_should_match_runner_and_timestamped_logs(self):
        self.assertEqual(LATEST_LOG_NAME_GLOB, "btc5m_*.log")
        self.assertTrue(is_latest_log_name("btc5m_runner.log"))
        self.assertTrue(is_latest_log_name("btc5m_one_trade.log"))
        self.assertTrue(is_latest_log_name("btc5m_conservative_20261102T100000Z.log"))

    def test_should_skip_pidfile_marker_and_unprefixed_logs(self):
        self.assertFalse(is_latest_log_name("btc5m.pid"))
        self.assertFalse(is_latest_log_name("btc5m_conservative_1.log.reported"))
        self.assertFalse(is_latest_log_name("latest.log"))
        self.assertFalse(is_latest_log_name("one_trade.log"))


class LatestVsReportMismatchTests(unittest.TestCase):
    def test_should_include_runner_log_that_report_py_skips(self):
        name = "btc5m_runner.log"
        self.assertTrue(is_latest_log_name(name))
        self.assertFalse(is_history_report_log_name(name))

    def test_should_still_share_timestamped_and_one_trade_names(self):
        for name in (
            "btc5m_one_trade.log",
            "btc5m_one_trade_extra.log",
            "btc5m_live_until_20261021.log",
            "btc5m_conservative_20261102T100000Z.log",
        ):
            self.assertTrue(is_latest_log_name(name), name)
            self.assertTrue(is_history_report_log_name(name), name)

    def test_should_keep_wide_glob_wired_in_latest_report_py(self):
        parsed = parse_latest_log_glob_source(
            (SCRIPTS / "btc5m_latest_report.py").read_text()
        )
        self.assertEqual(
            parsed,
            {"uses_latest_glob_const": True, "no_inline_star_glob": True},
        )

    def test_should_keep_narrow_globs_in_report_py(self):
        parsed = parse_report_log_glob_source(
            (SCRIPTS / "btc5m_report.py").read_text()
        )
        self.assertEqual(
            parsed,
            {
                "one_trade_glob": True,
                "live_until_glob": True,
                "generic_glob": True,
            },
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
