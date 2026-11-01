#!/usr/bin/env python3
"""Stop ladder for btc5m_ctl.sh.

cmd_stop sends SIGTERM, waits up to 10s, then SIGKILL. A live runner
that ignores TERM would keep trading unless KILL is reached. Keep the
ladder explicit so a shorter wait or a skipped KILL cannot leave a
live process attached to the pidfile.
"""

from __future__ import annotations

from typing import Literal

STOP_TERM_WAIT_SEC = 10

StopAction = Literal[
    "already_stopped",
    "sigterm",
    "wait",
    "stopped_gracefully",
    "sigkill",
]


def stop_first_action(*, running: bool) -> StopAction:
    """already_stopped when the pidfile is stale; otherwise SIGTERM."""
    if not running:
        return "already_stopped"
    return "sigterm"


def stop_after_term_tick(
    *,
    still_running: bool,
    waited: int,
    max_wait: int = STOP_TERM_WAIT_SEC,
) -> StopAction:
    """After each 1s wait: drop the pidfile, keep waiting, or SIGKILL."""
    if not still_running:
        return "stopped_gracefully"
    if waited < max_wait:
        return "wait"
    return "sigkill"


def parse_ctl_stop_ladder(script: str) -> dict[str, object]:
    """Lock the shell source: TERM first, 10s wait, then kill -9."""
    term_at = script.find("sending SIGTERM")
    kill_at = script.find("kill -9")
    wait_at = script.find("max_wait=10")
    if term_at < 0:
        raise ValueError("ctl stop is missing SIGTERM")
    if kill_at < 0:
        raise ValueError("ctl stop is missing SIGKILL")
    if wait_at < 0:
        raise ValueError("ctl stop is missing the 10s TERM wait")
    return {
        "term_before_kill": term_at < kill_at,
        "max_wait_sec": 10,
        "removes_pidfile": 'rm -f "$PIDFILE"' in script,
    }
