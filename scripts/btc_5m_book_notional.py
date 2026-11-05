#!/usr/bin/env python3
"""Top-of-book ask notional and min spread from CLOB books.

Used by W5 depth/Kelly sizing. Empty or junk books must size to 0, not crash.
"""

from __future__ import annotations

from typing import Any, Optional

from btc_5m_maker_pilot import SideBook


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
