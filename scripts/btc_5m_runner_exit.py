#!/usr/bin/env python3
"""Pure exit / close / PnL helpers for the BTC 5m runner.

No network. Extracted so SL, close-limit, and result classification
cannot drift without a unit test.
"""

from __future__ import annotations

from typing import Any, Optional


PNL_NOTE = (
    "IMPORTANT: PnL excludes Polymarket taker fees (~2% on crypto markets "
    "at 70c entry). Actual net PnL is lower."
)

FEE_NOTE = (
    "Estimated using Polymarket crypto fee formula: shares * 0.07 * p * (1-p)"
)


def stop_loss_price(entry_price: float, stop_loss_pct: float) -> float:
    return float(entry_price) * (1.0 - float(stop_loss_pct))


def stop_loss_triggered(side_px: Optional[float], sl_price: float) -> bool:
    return side_px is not None and side_px <= sl_price


def mark_held_to_redeem_on_time_exit(exit_before_sec: int) -> bool:
    return int(exit_before_sec) <= 20


def should_exit_on_hold_ev(recommendation: str) -> bool:
    return recommendation == "sell"


def clamp_limit_price(px: float) -> float:
    return max(0.01, min(0.99, float(px)))


def gtc_fallback_limit_price(best_bid: Optional[float], fallback_px: float) -> float:
    return clamp_limit_price((best_bid - 0.01) if best_bid is not None else fallback_px)


def force_close_limit_price(best_bid: Optional[float]) -> float:
    return clamp_limit_price((best_bid - 0.02) if best_bid is not None else 0.01)


def close_succeeded(success: Any, status: str, close_usdc: float) -> bool:
    return bool(success is True and (status == "matched" or close_usdc > 0))


def classify_close_result(close_success: bool, close_skipped: Any) -> tuple[str, str]:
    if close_success:
        return "done", "closed"
    if close_skipped:
        return "incomplete_close_skipped", "failed"
    return "incomplete_close_failed", "failed"


def crypto_taker_fee_usdc(shares: float, price: float) -> float:
    return round(float(shares) * 0.07 * float(price) * (1.0 - float(price)), 6)


def session_pnl_bundle(opened: dict[str, Any], closed: dict[str, Any]) -> dict[str, Any]:
    """Gross cashflow PnL plus fee estimates. None when close_usdc is missing/0."""
    close_usdc = closed["close_usdc"]
    if not close_usdc:
        return {
            "realized_cashflow_pnl_usdc": None,
            "pnl_note": None,
            "fee_estimates": None,
            "net_pnl_estimate_usdc": None,
        }

    pnl = round(float(close_usdc) - float(opened["cost_usdc"]), 6)
    shares = float(opened["shares"])
    entry_price = float(opened["entry_price"])
    entry_fee = crypto_taker_fee_usdc(shares, entry_price)
    if float(close_usdc) > 0:
        close_price = float(close_usdc) / shares if shares > 0 else 0.0
        close_fee = crypto_taker_fee_usdc(shares, close_price)
    else:
        close_fee = 0.0
    total_fee = entry_fee + close_fee
    return {
        "realized_cashflow_pnl_usdc": pnl,
        "pnl_note": PNL_NOTE,
        "fee_estimates": {
            "entry_fee_usdc": entry_fee,
            "close_fee_usdc": close_fee,
            "total_fee_usdc": total_fee,
            "note": FEE_NOTE,
        },
        "net_pnl_estimate_usdc": round(pnl - total_fee, 6),
    }
