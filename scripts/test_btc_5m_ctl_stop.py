#!/usr/bin/env python3
"""ctl stop SIGTERM/SIGKILL ladder. No network. No live trading."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_ctl_stop import (
    STOP_TERM_WAIT_SEC,
    parse_ctl_stop_ladder,
    stop_after_term_tick,
    stop_first_action,
)

CTL_SH = Path(__file__).resolve().parent / "btc5m_ctl.sh"


class StopFirstActionTests(unittest.TestCase):
    def test_should_no_op_when_the_pidfile_is_already_stopped(self):
        self.assertEqual(stop_first_action(running=False), "already_stopped")

    def test_should_send_sigterm_first_when_a_pid_is_live(self):
        self.assertEqual(stop_first_action(running=True), "sigterm")


class StopAfterTermTests(unittest.TestCase):
    def test_should_stop_gracefully_as_soon_as_the_pid_exits(self):
        self.assertEqual(
            stop_after_term_tick(still_running=False, waited=0),
            "stopped_gracefully",
        )
        self.assertEqual(
            stop_after_term_tick(still_running=False, waited=9),
            "stopped_gracefully",
        )

    def test_should_keep_waiting_until_the_10s_term_budget(self):
        self.assertEqual(
            stop_after_term_tick(still_running=True, waited=0),
            "wait",
        )
        self.assertEqual(
            stop_after_term_tick(still_running=True, waited=9),
            "wait",
        )
        self.assertEqual(STOP_TERM_WAIT_SEC, 10)

    def test_should_sigkill_only_after_the_full_term_wait(self):
        self.assertEqual(
            stop_after_term_tick(still_running=True, waited=10),
            "sigkill",
        )
        self.assertEqual(
            stop_after_term_tick(still_running=True, waited=11),
            "sigkill",
        )


class CtlStopShellTests(unittest.TestCase):
    def test_should_keep_term_then_kill_in_the_shell_source(self):
        ladder = parse_ctl_stop_ladder(CTL_SH.read_text())
        self.assertEqual(
            ladder,
            {
                "term_before_kill": True,
                "max_wait_sec": 10,
                "removes_pidfile": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
