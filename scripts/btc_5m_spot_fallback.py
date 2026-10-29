#!/usr/bin/env python3
"""Dry-run spot TWAP averaging. No HTTP. Execute mode must not use this path."""

from __future__ import annotations

from typing import Callable, Optional, Sequence


SPOT_TWAP_SOURCES: tuple[tuple[str, str], ...] = (
    ("binance", "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"),
    ("coinbase", "https://api.coinbase.com/v2/prices/BTC-USD/spot"),
)


def spot_twap_from_sources(
    fetch_price: Callable[[str, str], Optional[float]],
    sources: Sequence[tuple[str, str]] = SPOT_TWAP_SOURCES,
) -> Optional[float]:
    """Average positive spot prices. Skip fetch errors and non-positive ticks."""
    prices: list[float] = []
    for name, url in sources:
        try:
            price = fetch_price(name, url)
        except Exception:
            continue
        if price is not None and price > 0:
            prices.append(price)
    if not prices:
        return None
    return sum(prices) / len(prices)
