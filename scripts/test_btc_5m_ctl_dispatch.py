#!/usr/bin/env python3
"""ctl verb routing and report limit parse. No network. No live trading."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_ctl_dispatch import (
    REPORT_DEFAULT_LIMIT,
    ctl_verb_exit,
    parse_ctl_dispatch_source,
    parse_flag_limit,
    parse_start_unknown,
)

CTL_SH = Path(__file__).resolve().parent / "btc5m_ctl.sh"


class CtlVerbExitTests(unittest.TestCase):
    def test_should_fail_closed_when_the_verb_is_missing_or_unknown(self):
        self.assertEqual(ctl_verb_exit(None), 2)
        self.assertEqual(ctl_verb_exit(""), 2)
        self.assertEqual(ctl_verb_exit("execute"), 2)
        self.assertEqual(ctl_verb_exit("Start"), 2)
        self.assertEqual(ctl_verb_exit("report-limit"), 2)

    def test_should_accept_the_five_ctl_verbs(self):
        for verb in ("start", "status", "stop", "report", "logs"):
            self.assertEqual(ctl_verb_exit(verb), 0, verb)


class ReportLimitParseTests(unittest.TestCase):
    def test_should_default_to_twenty_when_no_flags_are_passed(self):
        limit, status = parse_flag_limit([])
        self.assertEqual(status, "ok")
        self.assertEqual(limit, 20)
        self.assertEqual(REPORT_DEFAULT_LIMIT, 20)

    def test_should_take_the_limit_flag_value(self):
        limit, status = parse_flag_limit(["--limit", "5"])
        self.assertEqual(status, "ok")
        self.assertEqual(limit, 5)

    def test_should_reject_unknown_report_flags_and_a_dangling_limit(self):
        self.assertEqual(parse_flag_limit(["--execute"])[1], "unknown")
        self.assertEqual(parse_flag_limit(["--limit"])[1], "unknown")
        self.assertEqual(parse_flag_limit(["5"])[1], "unknown")
        self.assertEqual(parse_flag_limit(["--limit", "nope"])[1], "unknown")


class StartUnknownArgTests(unittest.TestCase):
    def test_should_accept_the_known_start_flags(self):
        self.assertEqual(parse_start_unknown([]), "ok")
        self.assertEqual(
            parse_start_unknown(
                ["--profile", "conservative", "--execute", "--stake-usd", "8"]
            ),
            "ok",
        )

    def test_should_reject_an_unknown_start_flag(self):
        self.assertEqual(parse_start_unknown(["--live"]), "unknown")
        self.assertEqual(parse_start_unknown(["--profile"]), "unknown")


class CtlDispatchShellTests(unittest.TestCase):
    def test_should_keep_fail_closed_verbs_and_report_limit_in_the_shell_source(self):
        parsed = parse_ctl_dispatch_source(CTL_SH.read_text())
        self.assertEqual(
            parsed,
            {
                "empty_cmd_exit_2": True,
                "unknown_verb_exit_2": True,
                "report_default_limit": 20,
                "report_unknown_arg_exit_2": True,
                "verbs": ["logs", "report", "start", "status", "stop"],
            },
        )


if __name__ == "__main__":
    unittest.main()
