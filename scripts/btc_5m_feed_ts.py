#!/usr/bin/env python3
"""Extract feed-produced timestamps from an RTDS/API payload. No network."""

from typing import Any, Optional


def parse_source_ts(data: dict[str, Any]) -> Optional[float]:
    """Extract feed-produced timestamp. Never invent one."""
    for key in ("sourceTs", "source_ts", "timestamp", "ts", "time"):
        raw = data.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None
