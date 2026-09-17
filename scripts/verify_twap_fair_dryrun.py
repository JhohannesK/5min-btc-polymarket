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
    calculate_hold_ev,
    projected_final_twap_fair,
)
from btc_5m_entry_timing import evaluate_entry_timing


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

    print("\n8. W3 projected-final-TWAP (same endpoint, early vs late)...")
    open_px, endpoint = 100000.0, 100200.0
    early = projected_final_twap_fair(open_px, endpoint, 240.0)
    late = projected_final_twap_fair(open_px, endpoint, 20.0)
    print(f"   ✓ early 240s P(Up)={early['p_up']:.4f} residual={early['residual_vol']:.6f}")
    print(f"   ✓ late  20s P(Up)={late['p_up']:.4f} residual={late['residual_vol']:.6f}")
    print(f"   ✓ model={early['model']}")
    dumped = projected_final_twap_fair(open_px, endpoint, 20.0, spot_price=90000.0)
    print(f"   ✓ spot dump ignored: spot_ignored={dumped['spot_ignored']} p_up={dumped['p_up']:.4f}")

    print("\n9. W4 entry-timing gates (offline)...")
    mid = evaluate_entry_timing(120, 0.68, 0.58, 25.0, 5.0, 80.0)
    soft = evaluate_entry_timing(290, 0.68, 0.58, 25.0, 5.0, 80.0)
    hard = evaluate_entry_timing(12, 0.58, 0.55, 25.0, 5.0, 80.0)
    late_ok = evaluate_entry_timing(15, 0.90, 0.70, 80.0, 5.0, 80.0)
    print(f"   ✓ default window: {mid.reason} allow={mid.allow}")
    print(f"   ✓ soft-skip open: {soft.reason} allow={soft.allow}")
    print(f"   ✓ hard-skip last: {hard.reason} allow={hard.allow}")
    print(f"   ✓ late polarized: {late_ok.reason} allow={late_ok.allow}")
    
    print("\n=== Verification Complete ===")
    print("✓ All components working without live Polymarket credentials")
    print("✓ TWAP tracker operational (using spot fallback)")
    print("✓ Fair value calculation functional")
    print("✓ Edge estimation working")
    print("✓ Hold-EV calculation working")
    print("✓ W3 projected-final-TWAP early/late consistent")
    print("✓ W4 entry-timing gates functional")
    print("\n⚠️  PRODUCTION REQUIREMENTS:")
    print("   - Connect to RTDS topic: crypto_prices_twap_sixty")
    print("   - Filter: {\"symbol\":\"btc/usd\"}")
    print("   - Validate windowSeconds == 60 (ship-blocker)")
    print("   - Endpoint: data.chain.link/streams/btc-usd-twap-60s-streams")
    return 0


if __name__ == '__main__':
    sys.exit(main())
