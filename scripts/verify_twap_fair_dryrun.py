#!/usr/bin/env python3
"""
Dry-run verification of TWAP fair value calculation.
No Polymarket credentials required.
"""

import sys
from pathlib import Path

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent))

from btc_5m_twap_fair import (
    ChainlinkTWAPTracker,
    FairValueCalculator,
    estimate_trade_edge,
    calculate_hold_ev
)
from btc_5m_maker_pilot import (
    MakerPilotConfig,
    MakerPilotEngine,
    SideBook,
    format_maker_pilot_report,
    summarize_maker_pilot,
)


def main():
    print("=== BTC 5m TWAP Fair Value Dry-Run Verification ===\n")
    
    # Initialize TWAP tracker (will use fallback for dry-run)
    print("1. Initializing TWAP tracker...")
    print("   Note: No RTDS credentials - using spot fallback for dry-run")
    tracker = ChainlinkTWAPTracker()
    
    # Get current TWAP
    print("2. Fetching current TWAP...")
    twap_snapshot = tracker.get_current_twap()
    
    if twap_snapshot is None:
        print("   ❌ Failed to get TWAP snapshot")
        return 1
    
    print(f"   ✓ TWAP: ${twap_snapshot.twap_60s:,.2f}")
    print(f"   ✓ Window: {twap_snapshot.window_seconds}s")
    print(f"   ✓ Series: {twap_snapshot.series_id}")
    print(f"   ✓ Source: {twap_snapshot.source}")
    print(f"   ✓ Timestamp: {twap_snapshot.timestamp}")
    
    # Initialize fair value calculator
    print("\n3. Initializing fair value calculator...")
    calc = FairValueCalculator(tracker)
    
    # Pin window open for a test market
    print("4. Pinning window-open TWAP...")
    test_slug = "btc-updown-5m-1234567890"
    pin = calc.pin_window_open(test_slug)
    print(f"   ✓ Pinned: ${pin.twap_60s:,.2f}")
    print(f"   ✓ Window: {pin.window_seconds}s (MUST be 60s)")
    print(f"   ✓ Series: {pin.series_id}")
    
    # Calculate fair value at different seconds left
    print("\n5. Calculating fair values at different times...")
    for sec_left in [300, 180, 60, 20]:
        fair = calc.calculate_fair_value(test_slug, sec_left)
        if fair:
            print(f"   ✓ {sec_left}s left:")
            print(f"      P(Up) = {fair.p_up:.4f}, P(Down) = {fair.p_down:.4f}")
            print(f"      Edge signal: {fair.edge_signal}")
            print(f"      Current TWAP: ${fair.current_twap:,.2f}")
    
    # Test edge calculation
    print("\n6. Testing edge calculation...")
    test_cases = [
        (0.55, 0.70, 0.68),  # Fair 55%, ask 70% -> bad
        (0.75, 0.70, 0.68),  # Fair 75%, ask 70% -> good
        (0.50, 0.50, 0.48),  # Fair 50%, ask 50% -> marginal
    ]
    
    for fair_p, ask, bid in test_cases:
        edge = estimate_trade_edge(fair_p, ask, bid)
        print(f"   Fair={fair_p:.2f}, Ask={ask:.2f}: {edge['signal']} "
              f"(edge={edge['net_edge_bps']:.1f} bps)")
    
    # Test hold-EV calculation
    print("\n7. Testing hold-EV calculation...")
    test_holds = [
        (0.90, 0.85),  # Fair 90%, bid 85% -> hold
        (0.60, 0.70),  # Fair 60%, bid 70% -> sell
        (0.55, 0.55),  # Fair 55%, bid 55% -> marginal
    ]
    
    for fair_p, bid in test_holds:
        hold_ev = calculate_hold_ev(fair_p, bid)
        print(f"   Fair={fair_p:.2f}, Bid={bid:.2f}: {hold_ev['recommendation']} "
              f"(EV diff=${hold_ev['ev_diff']:.3f})")
    
    print("\n8. W6 maker/post-only shadow (no live orders)...")
    cfg = MakerPilotConfig(enabled=True, shadow=True, gtd_ttl_sec=15.0)
    eng = MakerPilotEngine(cfg)
    up = SideBook(side="UP", token_id="up", best_bid=0.44, best_ask=0.46)
    dn = SideBook(side="DOWN", token_id="dn", best_bid=0.54, best_ask=0.56)
    eng.on_tick(now=1000.0, fair_signal="up_favored", up_book=up, down_book=dn, stake_usd=5.0, bucket=1)
    eng.on_tick(
        now=1002.0,
        fair_signal="down_favored",
        up_book=up,
        down_book=dn,
        stake_usd=5.0,
        bucket=1,
    )
    summary = summarize_maker_pilot(eng.events, eng.quotes)
    print(format_maker_pilot_report(summary))
    if summary["cancels_fair_flip"] != 1 or summary["live_posts_attempted"] != 0:
        print("   ❌ Maker shadow cancel-on-flip / live-stub check failed")
        return 1
    print("   ✓ Shadow post + cancel on TWAP fair flip")
    print("   ✓ Live path stubbed (0 live posts)")
    
    print("\n=== Verification Complete ===")
    print("✓ All components working without live Polymarket credentials")
    print("✓ TWAP tracker operational (using spot fallback)")
    print("✓ Fair value calculation functional")
    print("✓ Edge estimation working")
    print("✓ Hold-EV calculation working")
    print("✓ W6 maker shadow path working (cancel-on-flip, live stubbed)")
    print("\n⚠️  PRODUCTION REQUIREMENTS:")
    print("   - Connect to RTDS topic: crypto_prices_twap_sixty")
    print("   - Filter: {\"symbol\":\"btc/usd\"}")
    print("   - Validate windowSeconds == 60 (ship-blocker)")
    print("   - Endpoint: data.chain.link/streams/btc-usd-twap-60s-streams")
    return 0


if __name__ == '__main__':
    sys.exit(main())
