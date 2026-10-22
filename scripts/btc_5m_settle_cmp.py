#!/usr/bin/env python3
"""Pure TWAP settle comparison. No network."""


def settle_winner(final_twap: float, window_open_twap: float) -> str:
    """Polymarket BTC 5m: Up wins iff final 60s TWAP >= window-open TWAP."""
    return "UP" if final_twap >= window_open_twap else "DOWN"


def position_won(side: str, winner: str) -> bool:
    return str(side).strip().upper() == winner
