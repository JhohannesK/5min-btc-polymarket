#!/usr/bin/env python3
"""Log-file selection for btc5m_report.py.

Overlapping globs must not double-count a run. The limit slice is newest
mtime first so a small limit cannot hide the latest PnL.
"""

from __future__ import annotations

import fnmatch
import re


REPORT_NAME_GLOBS = (
    "btc5m_one_trade*.log",
    "btc5m_live_until_*.log",
    "btc5m_*_*.log",
)


def is_report_log_name(name: str) -> bool:
    """True when basename matches the report globs. Pidfiles and markers skip."""
    return any(fnmatch.fnmatch(name, pat) for pat in REPORT_NAME_GLOBS)


def select_report_logs(entries: list[tuple[str, float]], limit: int) -> list[str]:
    """Dedup paths, newest mtime first, then slice to limit. limit<=0 is empty."""
    newest: dict[str, float] = {}
    for path, mtime in entries:
        prev = newest.get(path)
        if prev is None or mtime > prev:
            newest[path] = mtime
    ordered = sorted(newest.items(), key=lambda item: (-item[1], item[0]))
    if limit <= 0:
        return []
    return [path for path, _mtime in ordered[:limit]]


def parse_report_log_source(source: str) -> dict[str, object]:
    """Lock the three overlapping globs and the mtime+limit slice in report.py."""
    return {
        "one_trade_glob": "btc5m_one_trade*.log" in source,
        "live_until_glob": "btc5m_live_until_*.log" in source,
        "generic_glob": "btc5m_*_*.log" in source,
        "dedupes": "sorted(set(files)" in source,
        "slices_limit": bool(re.search(r"\[: args\.limit\]", source)),
    }
