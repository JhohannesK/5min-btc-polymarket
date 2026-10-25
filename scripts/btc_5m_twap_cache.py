#!/usr/bin/env python3
"""TWAP snapshot cache policy.

Spot fallback is dry-run only. A cached fallback snapshot must not be
reused when RTDS is required (--execute / allow_fallback=False).
"""

from __future__ import annotations


def twap_cache_key() -> str:
    return "current"


def cache_entry_fresh(cached_ts: float, now: float, ttl_sec: float) -> bool:
    return now - cached_ts < ttl_sec


def cached_twap_allowed(source: str, allow_fallback: bool) -> bool:
    """Reject a dry-run spot snapshot when the caller forbids fallback."""
    if not allow_fallback and str(source).startswith("spot_fallback"):
        return False
    return True
