#!/usr/bin/env python3
"""btc5m_ctl start/pidfile/logs predicates. No network. No live trading."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_ctl import logs_target, pidfile_is_running, start_runner_argv


class PidfileRunningTests(unittest.TestCase):
    def test_should_treat_missing_or_blank_pidfile_as_stopped(self):
        self.assertFalse(pidfile_is_running("", lambda _pid: True))
        self.assertFalse(pidfile_is_running("   \n", lambda _pid: True))

    def test_should_treat_garbage_and_non_positive_pids_as_stopped(self):
        self.assertFalse(pidfile_is_running("not-a-pid", lambda _pid: True))
        self.assertFalse(pidfile_is_running("0", lambda _pid: True))
        self.assertFalse(pidfile_is_running("-9", lambda _pid: True))

    def test_should_follow_the_live_process_check(self):
        self.assertTrue(pidfile_is_running(" 4321 \n", lambda pid: pid == 4321))
        self.assertFalse(pidfile_is_running("4321", lambda _pid: False))

    def test_should_treat_process_lookup_errors_as_stopped(self):
        def boom(_pid: int) -> bool:
            raise OSError("no such process")

        self.assertFalse(pidfile_is_running("99", boom))


class StartArgvTests(unittest.TestCase):
    def _base(self, **kwargs):
        return start_runner_argv(
            venv_py="/repo/.venv/bin/python",
            runner="/skill/scripts/test_btc_5m_session_exit_sl.py",
            profile="conservative",
            entry_timeout_min="35",
            poll_sec="2",
            close_retry_max="30",
            close_retry_delay_sec="2",
            **kwargs,
        )

    def test_should_omit_execute_and_optional_size_flags_by_default(self):
        cmd = self._base()
        self.assertNotIn("--execute", cmd)
        self.assertNotIn("--stake-usd", cmd)
        self.assertNotIn("--threshold", cmd)
        self.assertEqual(cmd[2], "--profile")
        self.assertEqual(cmd[3], "conservative")

    def test_should_append_execute_only_when_requested(self):
        cmd = self._base(execute=True, stake_usd="8", threshold="0.75")
        self.assertEqual(cmd[-5:], ["--stake-usd", "8", "--threshold", "0.75", "--execute"])
        dry = self._base(stake_usd="8", threshold="0.75", execute=False)
        self.assertNotIn("--execute", dry)
        self.assertEqual(dry[-4:], ["--stake-usd", "8", "--threshold", "0.75"])


class LogsTargetTests(unittest.TestCase):
    def test_should_return_none_when_latest_log_is_missing(self):
        self.assertIsNone(logs_target(False, "/runtime/btc5m_conservative_1.log"))
        self.assertIsNone(logs_target(True, None))
        self.assertIsNone(logs_target(True, ""))

    def test_should_return_the_symlink_target_when_present(self):
        path = "/runtime/btc5m_conservative_20261030T100000Z.log"
        self.assertEqual(logs_target(True, path), path)


if __name__ == "__main__":
    unittest.main()
