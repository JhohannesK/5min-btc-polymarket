#!/usr/bin/env python3
"""Pure close-state helpers for the BTC 5m runner.

No network. Locks failed-close PnL writes, wide-spread maker signals,
GTC fallback price order, and TWAP settle comparison so they cannot
drift without a unit failure.
"""

from __future__ import annotations

from typing import Optional


def pnl_for_state_tracker(realized_pnl: Optional[float]) -> float:
    """Missing close PnL is written as 0.0, not skipped."""
    return float(realized_pnl) if realized_pnl is not None else 0.0


def maker_fair_signal_when_spread_blocked(edge_signal: Optional[str]) -> str:
    """Taker is skipped on a wide book; maker still uses the live fair signal."""
    if edge_signal:
        return edge_signal
    return "unavailable"


def resolve_gtc_fallback_reference_px(
    gamma_px: Optional[float],
    last_side_px: Optional[float],
    entry_price: float,
) -> float:
    """Gamma outcome price, then last monitor print, then entry."""
    if gamma_px is not None:
        return float(gamma_px)
    if last_side_px is not None:
        return float(last_side_px)
    return float(entry_price)


def maker_tick_rtds_ready(path: str, execute: bool) -> bool:
    """Late-entry maker ticks never claim RTDS; other paths follow the execute flag."""
    if path == "too_late":
        return False
    return bool(execute)


def twap_settle_result(final_twap: float, window_open_twap: float) -> str:
    """Exact TWAP tie is UP. Comparison is numeric, not side-case aware."""
    return "UP" if float(final_twap) >= float(window_open_twap) else "DOWN"


def twap_position_wins(side: str, result: str) -> bool:
    """Current contract: case-sensitive. side='up' loses to result='UP'."""
    return side == result
