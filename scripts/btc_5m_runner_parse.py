#!/usr/bin/env python3
"""Pure runner helpers: market parse, book extremes, kill switch, fee report.

Extracted so unit tests can cover identity/book/fee paths without importing
the live CLOB runner (network, py_clob_client).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from btc_5m_entry_timing import EntryTimingConfig, TimingDecision, evaluate_entry_timing
from btc_5m_maker_pilot import SideBook
from btc_5m_winmore_gates import EntryDecision

DEFAULT_KILL_FILE = Path(__file__).resolve().parent.parent / "runtime" / ".kill"
DEFAULT_MAX_SPREAD = 0.03
TAKER_FEE_RATE = 0.07


def parse_json_objects(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cur: list[str] = []
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        if depth > 0:
            cur.append(ch)
        if ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                s = "".join(cur)
                cur = []
                try:
                    out.append(json.loads(s))
                except Exception:
                    pass
    return out


def bucket_5m(ts: int) -> int:
    return ts - (ts % 300)


def parse_json_field(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def market_side_prices(market: dict[str, Any]) -> tuple[float, float, str, str, str, str]:
    outcomes = parse_json_field(market.get("outcomes")) or []
    prices = parse_json_field(market.get("outcomePrices")) or []
    token_ids = parse_json_field(market.get("clobTokenIds")) or []
    if len(prices) < 2 or len(token_ids) < 2:
        raise RuntimeError("missing outcomePrices/clobTokenIds")

    up_i, down_i = 0, 1
    labs = [str(x).lower() for x in outcomes[:2]] if isinstance(outcomes, list) else []
    if len(labs) >= 2 and ("up" in labs[1] or "yes" in labs[1]):
        up_i, down_i = 1, 0

    up_p = float(prices[up_i])
    dn_p = float(prices[down_i])
    up_t = str(token_ids[up_i])
    dn_t = str(token_ids[down_i])
    return (
        up_p,
        dn_p,
        up_t,
        dn_t,
        str(market.get("slug") or market.get("_event_slug") or ""),
        str(market.get("endDate") or market.get("endDateIso") or ""),
    )


def best_bid_ask(book: Any) -> tuple[Optional[float], Optional[float]]:
    bids = getattr(book, "bids", []) or []
    asks = getattr(book, "asks", []) or []
    best_bid = None
    best_ask = None
    for b in bids:
        p = float(getattr(b, "price", 0) or 0)
        if best_bid is None or p > best_bid:
            best_bid = p
    for a in asks:
        p = float(getattr(a, "price", 0) or 0)
        if best_ask is None or p < best_ask:
            best_ask = p
    return best_bid, best_ask


def best_ask_notional(book: Any) -> float:
    """Top-of-book ask notional in USD (price * size). 0 if the book is empty."""
    asks = getattr(book, "asks", []) or []
    best_p = None
    best_sz = 0.0
    for a in asks:
        p = float(getattr(a, "price", 0) or 0)
        if p <= 0:
            continue
        sz = float(getattr(a, "size", 0) or 0)
        if best_p is None or p < best_p:
            best_p = p
            best_sz = sz
    if best_p is None:
        return 0.0
    return best_p * best_sz


def min_spread(up_book: SideBook, dn_book: SideBook) -> Optional[float]:
    spreads = [s for s in (up_book.spread, dn_book.spread) if s is not None]
    if not spreads:
        return None
    return min(spreads)


def apply_maker_pilot_cli_override(
    cfg: dict[str, Any], maker_pilot_flag: bool
) -> dict[str, Any]:
    """CLI maker_pilot flag forces enabled+shadow and never live_execute."""
    out = dict(cfg)
    if maker_pilot_flag:
        out["enabled"] = True
        out["shadow"] = True
        out["live_execute"] = False
    return out


def should_skip_legacy_late_entry(
    sec_left: float,
    min_entry_seconds_left: float,
    *,
    maker_on: bool,
    use_fair_value: bool,
) -> bool:
    """Blunt late-entry skip. TWAP path without maker uses W4 instead."""
    return sec_left < min_entry_seconds_left and (maker_on or not use_fair_value)


def spread_too_wide(
    observed: Optional[float], max_spread: float = DEFAULT_MAX_SPREAD
) -> bool:
    return observed is not None and observed > max_spread


def check_kill_switch(kill_file: Optional[Path] = None) -> Optional[str]:
    """
    Check for kill switch file.

    Returns:
        Action if kill switch is active: 'flatten' or 'hold', None otherwise
    """
    path = Path(kill_file) if kill_file is not None else DEFAULT_KILL_FILE
    if not path.exists():
        return None

    try:
        with open(path, "r") as f:
            content = f.read()

        for line in content.split("\n"):
            if line.startswith("action:"):
                action = line.split(":", 1)[1].strip()
                return action if action in ["flatten", "hold"] else "flatten"
    except Exception:
        pass

    return "flatten"


def estimate_session_fees(
    shares: float,
    entry_price: float,
    close_usdc: float,
    taker_fee_rate: float = TAKER_FEE_RATE,
) -> dict[str, float]:
    """Polymarket crypto: fee = shares * 0.07 * p * (1-p) on each fill."""
    entry_fee = round(shares * taker_fee_rate * entry_price * (1.0 - entry_price), 6)
    if close_usdc > 0 and shares > 0:
        close_price = close_usdc / shares
        close_fee = round(
            shares * taker_fee_rate * close_price * (1.0 - close_price), 6
        )
    else:
        close_fee = 0.0
    return {
        "entry_fee_usdc": entry_fee,
        "close_fee_usdc": close_fee,
        "total_fee_usdc": round(entry_fee + close_fee, 6),
    }


def timing_checks_for_allowed_sides(
    candidates: list[EntryDecision],
    *,
    seconds_left: float,
    fair_p_up: float,
    fair_p_down: float,
    up_ask_notional: float,
    dn_ask_notional: float,
    timing_cfg: Optional[EntryTimingConfig] = None,
) -> list[tuple[EntryDecision, TimingDecision, float]]:
    """W4 check per winmore-allowed side. Runner logs each row."""
    checks: list[tuple[EntryDecision, TimingDecision, float]] = []
    for cand in candidates:
        if not cand.allow or not cand.side:
            continue
        fair_p_side = fair_p_up if cand.side == "UP" else fair_p_down
        notional = up_ask_notional if cand.side == "UP" else dn_ask_notional
        timing = evaluate_entry_timing(
            seconds_left=seconds_left,
            fair_p=fair_p_side,
            ask=cand.entry_price,
            net_edge_bps=cand.net_edge_pp * 10000.0,
            min_edge_bps=cand.required_min_edge_pp * 10000.0,
            top_ask_notional_usd=notional,
            cfg=timing_cfg,
        )
        checks.append((cand, timing, notional))
    return checks


def pick_timing_allowed_entry(
    checks: list[tuple[EntryDecision, TimingDecision, float]],
) -> Optional[tuple[EntryDecision, TimingDecision]]:
    timed = [
        (cand, timing)
        for cand, timing, _ in checks
        if cand.allow and timing.allow
    ]
    if not timed:
        return None
    return sorted(timed, key=lambda x: x[0].net_edge_pp, reverse=True)[0]


# Runner aliases keep existing call sites one-line.
_best_bid_ask = best_bid_ask
_best_ask_notional = best_ask_notional
_min_spread = min_spread
