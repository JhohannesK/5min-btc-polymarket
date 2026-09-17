#!/usr/bin/env python3
"""
Helper to log TWAP settlement when position is held to expiry.

Call this after market resolves to log final TWAP and result.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from btc_5m_twap_fair import ChainlinkTWAPTracker


def log_twap_settle(market_slug: str, window_open_twap: float, side: str, allow_fallback: bool = True):
    """
    Log TWAP settlement for a market.
    
    Args:
        market_slug: Market identifier
        window_open_twap: Pinned TWAP at window open
        side: Position side ('UP' or 'DOWN')
        allow_fallback: If False, raises if RTDS not configured (for --execute mode)
    """
    tracker = ChainlinkTWAPTracker()
    
    try:
        final_snapshot = tracker.get_current_twap(allow_fallback=allow_fallback)
        if final_snapshot is None:
            print(f"[TWAP_SETTLE_ERROR] Could not fetch final TWAP for {market_slug}")
            return
        
        final_twap = final_snapshot.twap_60s
        window_seconds = final_snapshot.window_seconds
        series_id = final_snapshot.series_id
        
        # Determine result: Up wins if final >= open
        up_wins = final_twap >= window_open_twap
        result = "UP" if up_wins else "DOWN"
        position_wins = (side == result)
        
        print(f"[TWAP_SETTLE] market={market_slug} "
              f"windowSeconds={window_seconds} "
              f"series={series_id} "
              f"open={window_open_twap:.2f} "
              f"final={final_twap:.2f} "
              f"result={result} "
              f"position_side={side} "
              f"position_wins={position_wins}")
        
        return {
            'market_slug': market_slug,
            'window_seconds': window_seconds,
            'series_id': series_id,
            'open_twap': window_open_twap,
            'final_twap': final_twap,
            'result': result,
            'position_side': side,
            'position_wins': position_wins
        }
    except Exception as e:
        print(f"[TWAP_SETTLE_ERROR] {market_slug}: {e}")
        return None


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("Usage: log_twap_settle.py <market_slug> <window_open_twap> <side>")
        print("Example: log_twap_settle.py btc-updown-5m-1234567890 76214.23 UP")
        sys.exit(1)
    
    market_slug = sys.argv[1]
    window_open_twap = float(sys.argv[2])
    side = sys.argv[3].upper()
    
    result = log_twap_settle(market_slug, window_open_twap, side)
    if result:
        print(f"\nSettlement: {result['result']} (position {side} {'wins' if result['position_wins'] else 'loses'})")
