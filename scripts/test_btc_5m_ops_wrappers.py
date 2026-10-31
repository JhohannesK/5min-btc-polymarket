#!/usr/bin/env python3
"""Ops wrapper predicates. No network. No live trading."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_ops_wrappers import (
    docker_command_allowed,
    hot_profile_allowed,
    hot_start_argv,
    watch_end_ts,
    watch_runner_argv,
)


class HotWrapperTests(unittest.TestCase):
    def test_should_allow_only_the_two_named_profiles(self):
        self.assertTrue(hot_profile_allowed("conservative"))
        self.assertTrue(hot_profile_allowed("aggressive"))
        self.assertFalse(hot_profile_allowed("Conservative"))
        self.assertFalse(hot_profile_allowed("shadow"))
        self.assertFalse(hot_profile_allowed(""))

    def test_should_start_without_the_live_execute_flag(self):
        cmd = hot_start_argv("/skill/scripts/btc5m_ctl.sh", "aggressive")
        self.assertEqual(
            cmd,
            ["/skill/scripts/btc5m_ctl.sh", "start", "--profile", "aggressive"],
        )
        self.assertNotIn("--execute", cmd)


class DockerWrapperTests(unittest.TestCase):
    def test_should_accept_only_the_compose_verbs(self):
        self.assertTrue(docker_command_allowed("up"))
        self.assertTrue(docker_command_allowed("down"))
        self.assertTrue(docker_command_allowed("status"))
        self.assertTrue(docker_command_allowed("run"))
        self.assertFalse(docker_command_allowed("start"))
        self.assertFalse(docker_command_allowed("execute"))
        self.assertFalse(docker_command_allowed(None))
        self.assertFalse(docker_command_allowed(""))


class WatchLiveTests(unittest.TestCase):
    def test_should_always_pass_execute_on_the_conservative_profile(self):
        cmd = watch_runner_argv(threshold="0.75", stake="4")
        self.assertEqual(cmd[0:4], ["--profile", "conservative", "--threshold", "0.75"])
        self.assertEqual(cmd[4:6], ["--stake-usd", "4"])
        self.assertEqual(cmd[-1], "--execute")
        self.assertEqual(cmd.count("--execute"), 1)

    def test_should_end_the_watch_window_after_max_minutes(self):
        self.assertEqual(watch_end_ts(1_000, 180), 1_000 + 180 * 60)
        self.assertEqual(watch_end_ts(1_000, 0), 1_000)


if __name__ == "__main__":
    unittest.main()
