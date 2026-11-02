#!/usr/bin/env python3
"""Verb routing for btc5m_ctl.sh.

Empty or unknown verbs must fail closed so a typo cannot start, stop, or
report on the live runner. report's limit flag defaults to 20.
"""

from __future__ import annotations

import re
from typing import Optional


CTL_VERBS = frozenset({"start", "status", "stop", "report", "logs"})
REPORT_DEFAULT_LIMIT = 20


def ctl_verb_exit(cmd: Optional[str]) -> int:
    """2 when the verb is missing or unknown; 0 when it is a known verb."""
    if not cmd:
        return 2
    if cmd in CTL_VERBS:
        return 0
    return 2


def parse_flag_limit(
    argv: list[str],
    default: int = REPORT_DEFAULT_LIMIT,
) -> tuple[Optional[int], str]:
    """Mirror cmd_report argv. ('ok', limit) or ('unknown', None)."""
    limit: object = default
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--limit":
            if i + 1 >= len(argv):
                return None, "unknown"
            limit = argv[i + 1]
            i += 2
            continue
        return None, "unknown"
    try:
        return int(limit), "ok"
    except (TypeError, ValueError):
        return None, "unknown"


def parse_start_unknown(argv: list[str]) -> str:
    """cmd_start rejects anything outside the known flags."""
    known = {
        "--profile",
        "--entry-timeout-min",
        "--stake-usd",
        "--threshold",
        "--poll-sec",
        "--close-retry-max",
        "--close-retry-delay-sec",
        "--execute",
    }
    takes_value = known - {"--execute"}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token not in known:
            return "unknown"
        if token in takes_value:
            if i + 1 >= len(argv):
                return "unknown"
            i += 2
            continue
        i += 1
    return "ok"


def parse_ctl_dispatch_source(source: str) -> dict[str, object]:
    """Lock the shell's verb table, default report limit, and fail-closed exits."""
    default_limit = None
    match = re.search(r'cmd_report\(\) \{\s*local limit="(\d+)"', source)
    if match:
        default_limit = int(match.group(1))
    return {
        "empty_cmd_exit_2": '[[ -z "$cmd" ]] && { usage; exit 2; }' in source,
        "unknown_verb_exit_2": bool(re.search(r"\*\) usage; exit 2 ;;", source)),
        "report_default_limit": default_limit,
        "report_unknown_arg_exit_2": (
            "Unknown arg:" in source and "cmd_report" in source and "exit 2" in source
        ),
        "verbs": sorted(
            verb
            for verb in CTL_VERBS
            if re.search(rf"{verb}\) cmd_{verb}", source)
        ),
    }
