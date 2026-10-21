#!/usr/bin/env python3
"""Pure session-runner helpers for legacy threshold/SL and open-match gates.

Extracted from test_btc_5m_session_exit_sl.py so the money-path predicates
can be unit-tested without network or the live poll loop.
"""

from __future__ import annotations

from typing import Any, Optional


def stop_loss_trigger_price(entry_price: float, stop_loss_pct: float) -> float:
    return float(entry_price) * (1.0 - float(stop_loss_pct))


def stop_loss_hit(side_px: Optional[float], trigger: float) -> bool:
    return side_px is not None and float(side_px) <= float(trigger)


def pick_legacy_threshold_side(
    up_ask: Optional[float],
    dn_ask: Optional[float],
    threshold: float,
) -> Optional[tuple[str, float]]:
    candidates: list[tuple[str, float]] = []
    if up_ask is not None and float(up_ask) >= float(threshold):
        candidates.append(('UP', float(up_ask)))
    if dn_ask is not None and float(dn_ask) >= float(threshold):
        candidates.append(('DOWN', float(dn_ask)))
    if not candidates:
        return None
    return sorted(candidates, key=lambda x: x[1], reverse=True)[0]


def is_runner_open_matched(post: Any) -> bool:
    if not post or not isinstance(post, dict):
        return False
    return post.get('success') is True and str(post.get('status', '')).lower() == 'matched'


def shares_from_open_post(post: dict[str, Any]) -> float:
    return float(post.get('takingAmount') or 0)


def should_exit_for_hold_ev(recommendation: str) -> bool:
    return recommendation == 'sell'
