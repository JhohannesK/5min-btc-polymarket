#!/usr/bin/env python3
"""ctl status running/stopped and extra lines. No network. No live trading."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_ctl_status import (
    parse_ctl_status_source,
    status_headline,
    status_latest_log_line,
    status_meta_line,
)

CTL_SH = Path(__file__).resolve().parent / "btc5m_ctl.sh"


class StatusHeadlineTests(unittest.TestCase):
    def test_should_print_running_pid_when_the_session_is_live(self):
        self.assertEqual(status_headline(True, "4242"), "running pid=4242")

    def test_should_print_stopped_when_not_running_even_if_a_pid_string_is_passed(self):
        self.assertEqual(status_headline(False, "4242"), "stopped")
        self.assertEqual(status_headline(True, None), "stopped")
        self.assertEqual(status_headline(False, None), "stopped")


class StatusExtraLineTests(unittest.TestCase):
    def test_should_print_meta_only_when_the_metafile_exists(self):
        self.assertEqual(
            status_meta_line(True, "/tmp/runtime/btc5m.meta.json"),
            "meta=/tmp/runtime/btc5m.meta.json",
        )
        self.assertIsNone(status_meta_line(False, "/tmp/runtime/btc5m.meta.json"))

    def test_should_print_latest_log_only_when_the_link_is_a_symlink(self):
        self.assertEqual(
            status_latest_log_line(True, "/tmp/runtime/btc5m_conservative_1.log"),
            "latest_log=/tmp/runtime/btc5m_conservative_1.log",
        )
        self.assertIsNone(
            status_latest_log_line(False, "/tmp/runtime/btc5m_conservative_1.log")
        )


class CtlStatusSourceTests(unittest.TestCase):
    def test_should_lock_status_as_read_only_running_or_stopped(self):
        flags = parse_ctl_status_source(CTL_SH.read_text(encoding="utf-8"))
        self.assertTrue(flags["uses_is_running"])
        self.assertTrue(flags["running_echo"])
        self.assertTrue(flags["stopped_echo"])
        self.assertTrue(flags["no_kill_in_status"])
        self.assertTrue(flags["no_nohup_in_status"])
        self.assertTrue(flags["meta_if_file"])
        self.assertTrue(flags["latest_if_symlink"])
        self.assertTrue(flags["latest_not_regular_file_test"])


if __name__ == "__main__":
    unittest.main()
