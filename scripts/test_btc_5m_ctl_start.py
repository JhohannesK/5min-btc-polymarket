#!/usr/bin/env python3
"""ctl start already_running / failed_to_start. No network. No live trading."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_ctl_start import parse_ctl_start_source, start_outcome

CTL_SH = Path(__file__).resolve().parent / "btc5m_ctl.sh"


class StartOutcomeTests(unittest.TestCase):
    def test_should_no_op_when_a_live_pid_is_already_running(self):
        label, code = start_outcome(True, False)
        self.assertEqual(label, "already_running")
        self.assertEqual(code, 0)
        label, code = start_outcome(True, True)
        self.assertEqual(label, "already_running")
        self.assertEqual(code, 0)

    def test_should_report_started_when_the_pid_is_alive_after_grace(self):
        label, code = start_outcome(False, True)
        self.assertEqual(label, "started")
        self.assertEqual(code, 0)

    def test_should_fail_when_the_pid_dies_during_the_grace_sleep(self):
        label, code = start_outcome(False, False)
        self.assertEqual(label, "failed_to_start")
        self.assertEqual(code, 1)


class CtlStartSourceTests(unittest.TestCase):
    def test_should_lock_already_running_grace_sleep_and_failed_exit(self):
        flags = parse_ctl_start_source(CTL_SH.read_text(encoding="utf-8"))
        self.assertTrue(flags["already_running_returns_0"])
        self.assertTrue(flags["already_running_before_nohup"])
        self.assertTrue(flags["sleep_1_before_ps"])
        self.assertTrue(flags["failed_to_start_exits_1"])
        self.assertTrue(flags["started_echo"])
        self.assertTrue(flags["meta_omits_execute"])


if __name__ == "__main__":
    unittest.main()
