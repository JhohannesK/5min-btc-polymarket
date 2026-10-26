#!/usr/bin/env python3
"""
Deprecated threshold wrapper must keep routing to the canonical runner.

Wrong script path, dropped execute flag, or cwd not equal to repo would
dry-run when the operator asked for live, or run against the wrong checkout.
No network. Does not spawn the runner.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import run_btc_5m_threshold_test as wrapper


class ThresholdWrapperTests(unittest.TestCase):
    def test_should_point_canonical_script_at_the_sibling_runner(self):
        script = Path(wrapper.canonical_script())
        self.assertEqual(script.name, "test_btc_5m_session_exit_sl.py")
        self.assertEqual(script.parent, Path(wrapper.__file__).resolve().parent)
        self.assertTrue(script.is_file())

    def test_should_prefer_BTC5M_REPO_over_the_hardcoded_default(self):
        with patch.dict(os.environ, {"BTC5M_REPO": "/tmp/override-repo"}):
            self.assertEqual(wrapper.default_repo(), "/tmp/override-repo")

    def test_should_fall_back_to_the_legacy_sibling_repo_path(self):
        env = {k: v for k, v in os.environ.items() if k != "BTC5M_REPO"}
        with patch.dict(os.environ, env, clear=True):
            path = wrapper.default_repo()
        self.assertTrue(path.endswith("pm-hl-conservative-plus-repo"))

    def test_should_forward_profile_threshold_stake_and_timeouts(self):
        cmd = wrapper.canonical_cmd(
            profile="conservative",
            threshold=0.70,
            stake_usd=5.0,
            entry_timeout_min=8,
            poll_sec=2.0,
            execute=False,
        )
        self.assertEqual(cmd[0], ".venv/bin/python")
        self.assertEqual(Path(cmd[1]).name, "test_btc_5m_session_exit_sl.py")
        self.assertEqual(
            cmd[2:],
            [
                "--profile",
                "conservative",
                "--threshold",
                "0.7",
                "--stake-usd",
                "5.0",
                "--entry-timeout-min",
                "8",
                "--poll-sec",
                "2.0",
            ],
        )
        self.assertNotIn("--execute", cmd)

    def test_should_append_execute_only_when_requested(self):
        dry = wrapper.canonical_cmd(
            profile="conservative",
            threshold=0.8,
            stake_usd=10.0,
            entry_timeout_min=8,
            poll_sec=2.0,
            execute=False,
        )
        live = wrapper.canonical_cmd(
            profile="conservative",
            threshold=0.8,
            stake_usd=10.0,
            entry_timeout_min=8,
            poll_sec=2.0,
            execute=True,
        )
        self.assertEqual(live, dry + ["--execute"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
