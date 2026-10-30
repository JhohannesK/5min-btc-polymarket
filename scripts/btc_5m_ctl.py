#!/usr/bin/env python3
"""Control-script predicates for btc5m_ctl.sh.

The shell pidfile / start argv / logs path decide whether a live runner
is treated as already running and whether the live execute flag is
passed through. Keep those checks explicit so a stale pid cannot block
a restart and a missing flag cannot silently go live.
"""

from __future__ import annotations

from typing import Callable, Optional


def pidfile_is_running(pid_text: str, process_exists: Callable[[int], bool]) -> bool:
    """True when the pidfile contains a live pid. Empty / garbage is stopped."""
    raw = (pid_text or "").strip()
    if not raw:
        return False
    try:
        pid = int(raw)
    except ValueError:
        return False
    if pid <= 0:
        return False
    try:
        return bool(process_exists(pid))
    except Exception:
        return False


def start_runner_argv(
    *,
    venv_py: str,
    runner: str,
    profile: str,
    entry_timeout_min: str,
    poll_sec: str,
    close_retry_max: str,
    close_retry_delay_sec: str,
    stake_usd: Optional[str] = None,
    threshold: Optional[str] = None,
    execute: bool = False,
) -> list[str]:
    """Argv built by cmd_start. Optional stake/threshold/execute stay off by default."""
    cmd = [
        venv_py,
        runner,
        "--profile",
        profile,
        "--entry-timeout-min",
        entry_timeout_min,
        "--poll-sec",
        poll_sec,
        "--close-retry-max",
        close_retry_max,
        "--close-retry-delay-sec",
        close_retry_delay_sec,
    ]
    if stake_usd:
        cmd.extend(["--stake-usd", stake_usd])
    if threshold:
        cmd.extend(["--threshold", threshold])
    if execute:
        cmd.append("--execute")
    return cmd


def logs_target(latest_is_symlink: bool, latest_path: Optional[str]) -> Optional[str]:
    """cmd_logs prints no_logs unless latest.log is a symlink to a path."""
    if latest_is_symlink and latest_path:
        return latest_path
    return None
