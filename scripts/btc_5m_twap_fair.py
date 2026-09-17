#!/usr/bin/env python3
"""
BTC 5m TWAP fair value calculator.

Polymarket BTC 5m Up/Down markets settle on:
  Up wins iff Chainlink BTC/USD 60s TWAP at window end >= TWAP at window open.

This module:
1. Tracks Chainlink BTC/USD 60s TWAP (the same series Polymarket uses for settlement)
2. Pins window-open TWAP when a 5m market becomes active
3. Estimates fair P(TWAP_end >= TWAP_open) from live TWAP path + residual vol
"""

import time
import math
from typing import Optional
from dataclasses import dataclass

import requests


@dataclass
class TWAPSnapshot:
    """A point-in-time TWAP reading."""
    timestamp: float
    twap_60s: float
    source: str = "chainlink"


@dataclass
class FairValue:
    """Fair value estimate for a 5m Up/Down market."""
    p_up: float
    p_down: float
    window_open_twap: float
    current_twap: float
    seconds_left: float
    edge_signal: str
    confidence: float


class ChainlinkTWAPTracker:
    """
    Fetches Chainlink BTC/USD 60s TWAP.
    
    Note: In production, this should connect to on-chain Chainlink aggregator
    or a reliable oracle feed. For now, we'll use a fallback that estimates
    60s TWAP from spot prices.
    """
    
    def __init__(self, fallback_to_spot: bool = True):
        self.fallback_to_spot = fallback_to_spot
        self._cache: dict[str, TWAPSnapshot] = {}
        self._cache_ttl_sec = 5.0
    
    def get_current_twap(self) -> Optional[TWAPSnapshot]:
        """
        Fetch current Chainlink BTC/USD 60s TWAP.
        
        TODO: Replace with actual Chainlink on-chain or oracle API call.
        For now, falls back to estimating from spot.
        """
        cache_key = "current"
        now = time.time()
        
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if now - cached.timestamp < self._cache_ttl_sec:
                return cached
        
        try:
            # TODO: Connect to actual Chainlink BTC/USD 60s TWAP feed
            # For now, use a spot-based estimate as fallback
            if self.fallback_to_spot:
                twap_value = self._estimate_twap_from_spot()
                if twap_value is not None:
                    snapshot = TWAPSnapshot(
                        timestamp=now,
                        twap_60s=twap_value,
                        source="spot_estimate"
                    )
                    self._cache[cache_key] = snapshot
                    return snapshot
        except Exception:
            pass
        
        return None
    
    def _estimate_twap_from_spot(self) -> Optional[float]:
        """
        Estimate 60s TWAP from recent spot prices.
        
        This is a fallback; production should use actual Chainlink TWAP.
        Fetches spot from multiple sources and averages.
        """
        sources = [
            ("binance", "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"),
            ("coinbase", "https://api.coinbase.com/v2/prices/BTC-USD/spot"),
        ]
        
        prices = []
        for name, url in sources:
            try:
                r = requests.get(url, timeout=3)
                r.raise_for_status()
                data = r.json()
                
                if name == "binance":
                    price = float(data.get("price", 0))
                elif name == "coinbase":
                    price = float(data.get("data", {}).get("amount", 0))
                else:
                    continue
                
                if price > 0:
                    prices.append(price)
            except Exception:
                continue
        
        if not prices:
            return None
        
        return sum(prices) / len(prices)


class FairValueCalculator:
    """
    Calculates fair P(Up) and P(Down) for a 5m BTC Up/Down market.
    
    Fair value = P(TWAP_end >= TWAP_open) based on:
    - Window-open TWAP (pinned)
    - Current live TWAP
    - Residual time to settlement
    - Estimated BTC vol
    """
    
    def __init__(self, twap_tracker: ChainlinkTWAPTracker):
        self.twap_tracker = twap_tracker
        self._window_pins: dict[str, TWAPSnapshot] = {}
    
    def pin_window_open(self, market_slug: str) -> Optional[TWAPSnapshot]:
        """
        Pin the TWAP at window open for a market.
        
        Call this once when a new 5m market becomes active.
        Returns the pinned TWAP snapshot.
        """
        if market_slug in self._window_pins:
            return self._window_pins[market_slug]
        
        current = self.twap_tracker.get_current_twap()
        if current:
            self._window_pins[market_slug] = current
        
        return current
    
    def get_window_open_twap(self, market_slug: str) -> Optional[float]:
        """Get the pinned window-open TWAP for a market."""
        snapshot = self._window_pins.get(market_slug)
        return snapshot.twap_60s if snapshot else None
    
    def calculate_fair_value(
        self,
        market_slug: str,
        seconds_left: float,
        btc_daily_vol_pct: float = 3.5
    ) -> Optional[FairValue]:
        """
        Calculate fair P(Up) for a 5m market.
        
        Args:
            market_slug: Market identifier (e.g. btc-updown-5m-1234567890)
            seconds_left: Seconds remaining until settlement
            btc_daily_vol_pct: Estimated BTC daily volatility % (default 3.5%)
        
        Returns:
            FairValue with P(Up), P(Down), and edge signal
        """
        # Get pinned window-open TWAP
        open_twap = self.get_window_open_twap(market_slug)
        if open_twap is None:
            open_twap_snapshot = self.pin_window_open(market_slug)
            if open_twap_snapshot is None:
                return None
            open_twap = open_twap_snapshot.twap_60s
        
        # Get current TWAP
        current_snapshot = self.twap_tracker.get_current_twap()
        if current_snapshot is None:
            return None
        current_twap = current_snapshot.twap_60s
        
        # Calculate implied move and residual vol
        price_ratio = current_twap / open_twap
        log_move = math.log(price_ratio)
        
        # Residual vol scaled to remaining time
        # Daily vol -> 5min vol: sqrt(5min / 1440min) * daily_vol
        five_min_vol = btc_daily_vol_pct / 100.0 * math.sqrt(5.0 / (24.0 * 60.0))
        
        # Further scale by actual seconds left (may be less than full 5min)
        time_fraction = min(1.0, seconds_left / 300.0)
        residual_vol = five_min_vol * math.sqrt(time_fraction)
        
        # Estimate P(TWAP_end >= TWAP_open)
        # Simple model: current TWAP is a noisy signal of where end TWAP will be
        # P(Up) = P(log_return_end >= 0) 
        #       ≈ Φ((log_current_vs_open + 0) / residual_vol)
        # where Φ is standard normal CDF
        
        if residual_vol > 0:
            z_score = log_move / residual_vol
            p_up = self._normal_cdf(z_score)
        else:
            p_up = 1.0 if log_move >= 0 else 0.0
        
        p_down = 1.0 - p_up
        
        # Edge signal: which side is likely cheap vs naive 50/50
        move_bps = (price_ratio - 1.0) * 10000
        if abs(move_bps) < 5:
            edge_signal = "neutral"
        elif move_bps > 5:
            edge_signal = "up_favored"
        else:
            edge_signal = "down_favored"
        
        confidence = min(abs(p_up - 0.5) * 2.0, 1.0)
        
        return FairValue(
            p_up=p_up,
            p_down=p_down,
            window_open_twap=open_twap,
            current_twap=current_twap,
            seconds_left=seconds_left,
            edge_signal=edge_signal,
            confidence=confidence
        )
    
    @staticmethod
    def _normal_cdf(x: float) -> float:
        """Standard normal CDF approximation (accurate to ~1e-4)."""
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def estimate_trade_edge(
    fair_p: float,
    book_ask: float,
    book_bid: float,
    shares: float = 1.0,
    taker_fee_rate: float = 0.07
) -> dict:
    """
    Estimate edge of buying at book_ask vs fair value.
    
    Args:
        fair_p: Fair probability estimate (0 to 1)
        book_ask: CLOB best ask price
        book_bid: CLOB best bid price
        shares: Position size in shares
        taker_fee_rate: Polymarket crypto taker fee rate (default 0.07)
    
    Returns:
        Dict with edge_bps, cost_bps, net_edge_bps, and trade_signal
    """
    spread = max(0, book_ask - book_bid)
    half_spread_bps = (spread / 2.0) * 10000
    
    # Taker fee: shares × fee_rate × p × (1-p)
    taker_fee = shares * taker_fee_rate * book_ask * (1 - book_ask)
    taker_fee_bps = (taker_fee / shares) * 10000 if shares > 0 else 0
    
    # Edge: fair value - book price
    edge = fair_p - book_ask
    edge_bps = edge * 10000
    
    # Total cost: half-spread + taker fee
    cost_bps = half_spread_bps + taker_fee_bps
    
    # Net edge after costs
    net_edge_bps = edge_bps - cost_bps
    
    # Trade signal
    if net_edge_bps > 5:
        signal = "buy"
    elif net_edge_bps < -5:
        signal = "avoid"
    else:
        signal = "marginal"
    
    return {
        "fair_p": fair_p,
        "book_ask": book_ask,
        "book_bid": book_bid,
        "spread_bps": spread * 10000,
        "half_spread_bps": half_spread_bps,
        "taker_fee_bps": taker_fee_bps,
        "cost_bps": cost_bps,
        "edge_bps": edge_bps,
        "net_edge_bps": net_edge_bps,
        "signal": signal
    }


def calculate_hold_ev(
    fair_p_win: float,
    current_bid: float,
    shares: float = 1.0,
    taker_fee_rate: float = 0.07,
    redeem_value: float = 1.0
) -> dict:
    """
    Calculate EV of holding to redeem vs selling at current bid.
    
    Args:
        fair_p_win: Fair probability this token wins
        current_bid: CLOB best bid price
        shares: Position size in shares
        taker_fee_rate: Polymarket crypto taker fee rate
        redeem_value: Value per share if token wins (usually 1.0)
    
    Returns:
        Dict with hold_ev, sell_ev, and recommendation
    """
    # EV of holding to redeem
    hold_ev_per_share = fair_p_win * redeem_value
    hold_ev = hold_ev_per_share * shares
    
    # EV of selling at bid (after fees)
    sell_fee = shares * taker_fee_rate * current_bid * (1 - current_bid)
    sell_proceeds = (current_bid * shares) - sell_fee
    sell_ev = sell_proceeds
    
    # Compare
    ev_diff = hold_ev - sell_ev
    
    if ev_diff > 0.01:
        recommendation = "hold"
    elif ev_diff < -0.01:
        recommendation = "sell"
    else:
        recommendation = "marginal"
    
    return {
        "fair_p_win": fair_p_win,
        "current_bid": current_bid,
        "hold_ev_per_share": hold_ev_per_share,
        "hold_ev": hold_ev,
        "sell_ev": sell_ev,
        "sell_fee": sell_fee,
        "ev_diff": ev_diff,
        "recommendation": recommendation
    }
