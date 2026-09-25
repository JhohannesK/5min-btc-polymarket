#!/usr/bin/env python3
"""Pure entry helpers for the BTC 5m runner.

Legacy threshold pick, open-fill parse, and conservative CLOB spread.
No network.
"""

from __future__ import annotations

from typing import Any, Optional


def pick_legacy_threshold_side(
    up_ask: Optional[float],
    dn_ask: Optional[float],
    threshold: float,
) -> Optional[tuple[str, float]]:
    """Highest ask at or above threshold. None when both sides miss."""
    candidates: list[tuple[str, float]] = []
    if up_ask is not None and float(up_ask) >= threshold:
        candidates.append(("UP", float(up_ask)))
    if dn_ask is not None and float(dn_ask) >= threshold:
        candidates.append(("DOWN", float(dn_ask)))
    if not candidates:
        return None
    return sorted(candidates, key=lambda x: x[1], reverse=True)[0]


def clob_picked_spread(
    up_bid: Optional[float],
    up_ask: Optional[float],
    dn_bid: Optional[float],
    dn_ask: Optional[float],
) -> Optional[float]:
    """Min of defined UP/DOWN spreads. Missing side is ignored, not zero."""
    picked_spread = None
    if up_ask is not None and up_bid is not None:
        picked_spread = max(0.0, up_ask - up_bid)
    if dn_ask is not None and dn_bid is not None:
        s = max(0.0, dn_ask - dn_bid)
        picked_spread = s if picked_spread is None else min(picked_spread, s)
    return picked_spread


def extract_open_post(objs: list[Any]) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    runner = None
    post = None
    for o in objs:
        if isinstance(o, dict) and "order_post_result" in o:
            runner = o
            post = o.get("order_post_result") or {}
    return runner, post


def open_fill_matched(post: Optional[dict[str, Any]]) -> bool:
    return bool(
        post
        and post.get("success") is True
        and str(post.get("status", "")).lower() == "matched"
    )


def opened_from_fill(
    runner: dict[str, Any],
    post: dict[str, Any],
    *,
    side: str,
    up_token: str,
    down_token: str,
    trigger_price: float,
    slug: str,
    end_iso: str,
    bucket: int,
    opened_at: str,
) -> dict[str, Any]:
    token_id = str(runner.get("token_id") or (up_token if side == "UP" else down_token))
    shares = float(post.get("takingAmount") or 0)
    cost = float(post.get("makingAmount") or 0)
    entry_price = float(runner.get("entry_price") or trigger_price)
    return {
        "opened_at": opened_at,
        "market_slug": slug,
        "market_end_iso": end_iso,
        "side": side,
        "token_id": token_id,
        "entry_price": entry_price,
        "shares": shares,
        "cost_usdc": cost,
        "open_order_id": post.get("orderID"),
        "open_tx": (post.get("transactionsHashes") or [None])[0],
        "bucket": bucket,
    }
