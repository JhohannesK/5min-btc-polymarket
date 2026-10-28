#!/usr/bin/env python3
"""Stop predicates for watch_btc_5m_threshold_and_enter.sh.

The live watcher currently greps runner stdout (and itself passes execute).
Keep the grep behavior explicit, and parse the printed report so a close
matched status cannot be mistaken for an open fill.
"""

from __future__ import annotations

import json
from typing import Any, Optional


def grep_opened_and_matched(text: str) -> bool:
    """Current shell stop: substring '"opened"' and pretty '"status": "matched"'."""
    return '"opened"' in text and '"status": "matched"' in text


def last_json_object(text: str) -> Optional[dict[str, Any]]:
    """Last top-level JSON object in runner stdout. Ignores leading log lines."""
    decoder = json.JSONDecoder()
    last: Optional[dict[str, Any]] = None
    idx = 0
    while idx < len(text):
        brace = text.find("{", idx)
        if brace < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, brace)
        except json.JSONDecodeError:
            idx = brace + 1
            continue
        if isinstance(obj, dict):
            last = obj
        idx = end
    return last


def opened_fill_reported(text: str) -> bool:
    """True when the runner report includes a filled opened position."""
    report = last_json_object(text)
    if not report:
        return False
    opened = report.get("opened")
    return (
        isinstance(opened, dict)
        and bool(opened.get("market_slug"))
        and bool(opened.get("side"))
    )
