#!/usr/bin/env python3
"""Predicates for the live-ops wrappers around the BTC 5m runner.

btc5m_hot.sh, btc5m_docker.sh, and watch_btc_5m_threshold_and_enter.sh
decide whether a start can go live and which compose verbs are legal.
Keep those checks explicit so a typo profile cannot start, docker cannot
invent a verb, and the watcher cannot drop the live execute flag.
"""

from __future__ import annotations

from typing import Optional


HOT_PROFILES = ("conservative", "aggressive")
DOCKER_COMMANDS = ("up", "down", "status", "run")


def hot_profile_allowed(profile: str) -> bool:
    """btc5m_hot.sh exits 2 unless the profile is conservative or aggressive."""
    return profile in HOT_PROFILES


def hot_start_argv(ctl: str, profile: str) -> list[str]:
    """hot.sh execs ctl start with a profile only. It never forwards execute."""
    return [ctl, "start", "--profile", profile]


def docker_command_allowed(command: Optional[str]) -> bool:
    """btc5m_docker.sh accepts up/down/status/run and rejects everything else."""
    return command in DOCKER_COMMANDS


def watch_runner_argv(
    *,
    threshold: str,
    stake: str,
    entry_timeout_min: str = "8",
    poll_sec: str = "2",
) -> list[str]:
    """Watcher always starts conservative and always passes execute."""
    return [
        "--profile",
        "conservative",
        "--threshold",
        threshold,
        "--stake-usd",
        stake,
        "--entry-timeout-min",
        entry_timeout_min,
        "--poll-sec",
        poll_sec,
        "--execute",
    ]


def watch_end_ts(start_ts: int, max_min: int) -> int:
    """Watcher timeout is start + max_min * 60 seconds."""
    return start_ts + max_min * 60
