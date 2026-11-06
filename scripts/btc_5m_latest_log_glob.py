#!/usr/bin/env python3
"""Latest-report log glob vs the narrower btc5m_report.py scan.

latest_report uses btc5m_*.log so a runner log is visible to Slack/ops.
report.py unions three tighter patterns and will skip names like
btc5m_runner.log. Keep both contracts locked so a glob edit cannot
silently drop the latest completion or double-count history.
"""

from __future__ import annotations

import fnmatch

LATEST_LOG_NAME_GLOB = "btc5m_*.log"

REPORT_NAME_GLOBS = (
    "btc5m_one_trade*.log",
    "btc5m_live_until_*.log",
    "btc5m_*_*.log",
)


def is_latest_log_name(name: str) -> bool:
    """True when basename matches latest_report's single glob."""
    return fnmatch.fnmatch(name, LATEST_LOG_NAME_GLOB)


def is_history_report_log_name(name: str) -> bool:
    """True when basename matches any of report.py's three globs."""
    return any(fnmatch.fnmatch(name, pat) for pat in REPORT_NAME_GLOBS)


def parse_latest_log_glob_source(source: str) -> dict[str, bool]:
    """Lock latest_report.py still scanning LATEST_LOG_NAME_GLOB."""
    return {
        "uses_latest_glob_const": "LATEST_LOG_NAME_GLOB" in source,
        "no_inline_star_glob": ' "btc5m_*.log"' not in source
        and " 'btc5m_*.log'" not in source,
    }


def parse_report_log_glob_source(source: str) -> dict[str, bool]:
    """Lock report.py still using the three narrower overlapping globs."""
    return {
        "one_trade_glob": "btc5m_one_trade*.log" in source,
        "live_until_glob": "btc5m_live_until_*.log" in source,
        "generic_glob": "btc5m_*_*.log" in source,
    }
