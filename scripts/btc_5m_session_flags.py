#!/usr/bin/env python3
"""Pure session-result and live-guard helpers for the BTC 5m runner.

No network. Locks timeout result strings, RTDS settle fallback, and the
poll-vs-subprocess guard split so they cannot drift without a unit failure.
"""

from __future__ import annotations

from typing import Optional

# Poll loop skips takes above 3c. The subprocess open path still defaults to 5c.
POLL_MAX_SPREAD = 0.03
SUBPROCESS_MAX_SPREAD = 0.05
# W4 late exception requires $30 at the touch; subprocess default is $10.
W4_LATE_MIN_ASK_NOTIONAL_USD = 30.0
SUBPROCESS_MIN_TOP_ASK_NOTIONAL_USD = 10.0


def classify_no_entry_result(maker_on: bool) -> str:
    """Result string when the entry deadline expires with no fill."""
    if maker_on:
        return "maker_pilot_shadow_complete"
    return "no_entry_timeout"


def settle_allow_fallback(execute: bool) -> bool:
    """Live execute must not settle from spot fallback."""
    return not bool(execute)


def poll_spread_too_wide(
    min_spread: Optional[float],
    max_spread: float = POLL_MAX_SPREAD,
) -> bool:
    return min_spread is not None and min_spread > max_spread


def spread_poll_skips_open_allows(min_spread: float) -> bool:
    """True in the 3c-5c band where poll skips but subprocess still has room."""
    return poll_spread_too_wide(min_spread) and min_spread <= SUBPROCESS_MAX_SPREAD


def notional_w4_blocks_open_allows(top_ask_notional_usd: float) -> bool:
    """True when W4 late-depth fails but the subprocess $10 guard still passes."""
    return (
        top_ask_notional_usd + 1e-12 < W4_LATE_MIN_ASK_NOTIONAL_USD
        and top_ask_notional_usd + 1e-12 >= SUBPROCESS_MIN_TOP_ASK_NOTIONAL_USD
    )


def open_env_defaults() -> dict[str, str]:
    return {
        "PM_MAX_SPREAD": str(SUBPROCESS_MAX_SPREAD),
        "PM_MIN_TOP_ASK_NOTIONAL_USD": str(int(SUBPROCESS_MIN_TOP_ASK_NOTIONAL_USD)),
    }
