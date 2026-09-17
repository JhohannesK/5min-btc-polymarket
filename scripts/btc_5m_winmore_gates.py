#!/usr/bin/env python3
"""
W1/W2/W5 win-more gates for the TWAP fair-value entry path.

W1  Mid-band taker ban / fee-aware gate (probability points, not bps-only)
W2  Taker-delay (~250ms / itode-style) adverse-selection buffer; prefer GTD/post-only
W5  Size to depth within N ticks; fractional Kelly x remaining daily loss budget;
    no second clip after edge decay
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


TAKER_ORDER_TYPES = frozenset({"FAK", "FOK", "MARKET"})
POST_ONLY_ORDER_TYPES = frozenset({"GTD", "GTC", "POST_ONLY"})

MidbandPolicy = Literal["skip", "raise_min_edge"]


@dataclass(frozen=True)
class WinmoreConfig:
    """Profile-driven W1/W2/W5 knobs. Numbers are paper defaults, not locked prod."""

    # W1 fee-aware / mid-band
    midband_enabled: bool = True
    midband_lower: float = 0.40
    midband_upper: float = 0.60
    # skip = block mid-band takes; raise_min_edge = allow if net edge clears extra bar
    midband_taker_policy: MidbandPolicy = "skip"
    midband_extra_min_edge_pp: float = 0.02  # +2.0c when raising
    min_edge_pp: float = 0.005  # 0.5c base bar (pp, not bps)
    taker_fee_rate: float = 0.07  # crypto taker: 0.07 * p * (1-p)

    # W2 delay buffer (~250ms matching delay). Units: probability points (0.01 = 1c).
    # Vol-scaled around btc_daily_vol_pct / 3.5, then clamped to [min, max].
    taker_delay_ms: int = 250
    taker_delay_buffer_pp: float = 0.005  # 0.5c at 3.5% daily vol
    taker_delay_buffer_vol_scale: bool = True
    taker_delay_vol_ref_pct: float = 3.5
    taker_delay_buffer_min_pp: float = 0.005  # 0.5c floor
    taker_delay_buffer_max_pp: float = 0.010  # 1.0c cap
    prefer_post_only: bool = True
    entry_order_type_post_only: str = "GTD"
    entry_order_type_taker: str = "FAK"

    # W5 depth + Kelly
    depth_ticks: int = 3
    tick_size: float = 0.01
    kelly_fraction: float = 0.25
    edge_decay_ratio: float = 0.50
    min_stake_usd: float = 1.0


@dataclass
class BookLevel:
    price: float
    size: float  # shares


@dataclass
class DepthSlice:
    shares: float
    notional_usd: float
    vwap: float
    depth_cost_pp: float  # vwap - touch, in probability points
    levels_used: int


@dataclass
class EntryDecision:
    allow: bool
    reason: str
    side: Optional[str] = None
    entry_price: float = 0.0
    fair_p: float = 0.0
    gross_edge_pp: float = 0.0
    fee_pp: float = 0.0
    net_edge_pp: float = 0.0
    required_min_edge_pp: float = 0.0
    delay_buffer_pp: float = 0.0
    size_usd: float = 0.0
    size_shares: float = 0.0
    depth_shares: float = 0.0
    depth_notional_usd: float = 0.0
    kelly_cap_usd: float = 0.0
    order_type: str = "GTD"
    in_midband: bool = False
    details: dict[str, Any] = field(default_factory=dict)


def is_midband(price: float, lower: float = 0.40, upper: float = 0.60) -> bool:
    """W1: entry price in [lower, upper] inclusive."""
    return lower <= price <= upper


def fee_pp(
    p: float,
    half_spread: float,
    depth_cost: float = 0.0,
    taker_fee_rate: float = 0.07,
) -> float:
    """
    W1 gate in probability points (not bps):
      fee_pp = 0.07 * p * (1-p) + half_spread + depth_cost
    All terms are in price/probability units (0.01 = 1c = 1pp).
    """
    p = min(max(p, 0.0), 1.0)
    return (taker_fee_rate * p * (1.0 - p)) + max(0.0, half_spread) + max(0.0, depth_cost)


def taker_delay_buffer_pp(cfg: WinmoreConfig, btc_daily_vol_pct: float) -> float:
    """
    W2: extra required edge for ~250ms / itode-style taker delay.

    Default 0.5c at 3.5% daily vol; vol-scale then clamp to [0.5c, 1.0c].
    Delay itself is documented on the config (taker_delay_ms); the buffer is
    an adverse-selection haircut, not a latency model.
    """
    base = cfg.taker_delay_buffer_pp
    if cfg.taker_delay_buffer_vol_scale and cfg.taker_delay_vol_ref_pct > 0:
        base = cfg.taker_delay_buffer_pp * (btc_daily_vol_pct / cfg.taker_delay_vol_ref_pct)
    lo = cfg.taker_delay_buffer_min_pp
    hi = cfg.taker_delay_buffer_max_pp
    if hi < lo:
        lo, hi = hi, lo
    return min(max(base, lo), hi)


def choose_entry_order_type(cfg: WinmoreConfig) -> str:
    """
    W2: prefer GTD/post-only over FAK/FOK snipes.

    FAK/FOK cross the spread into a delayed book (~250ms). GTD rests as a
    limit and avoids the snipe. Default stays post-only; dry-run is unchanged.
    """
    if cfg.prefer_post_only:
        return str(cfg.entry_order_type_post_only or "GTD").upper()
    return str(cfg.entry_order_type_taker or "FAK").upper()


def parse_book_levels(raw_levels: Any) -> list[BookLevel]:
    """Accept CLOB objects, dicts, or (price, size) tuples."""
    out: list[BookLevel] = []
    if not raw_levels:
        return out
    for lvl in raw_levels:
        if isinstance(lvl, BookLevel):
            price, size = lvl.price, lvl.size
        elif isinstance(lvl, dict):
            price = float(lvl.get("price") or 0)
            size = float(lvl.get("size") or 0)
        elif isinstance(lvl, (tuple, list)) and len(lvl) >= 2:
            price, size = float(lvl[0]), float(lvl[1])
        else:
            price = float(getattr(lvl, "price", 0) or 0)
            size = float(getattr(lvl, "size", 0) or 0)
        if price > 0 and size > 0:
            out.append(BookLevel(price=price, size=size))
    return out


def depth_within_n_ticks(
    levels: list[BookLevel],
    touch: float,
    n_ticks: int,
    tick_size: float = 0.01,
    side: Literal["ask", "bid"] = "ask",
) -> DepthSlice:
    """
    W5: available size within N ticks of the touch.

    Asks: include prices in [touch, touch + N*tick].
    Bids: include prices in [touch - N*tick, touch].
    depth_cost_pp = vwap - touch for asks (walk cost); touch - vwap for bids.
    """
    if touch <= 0 or n_ticks < 0 or tick_size <= 0:
        return DepthSlice(0.0, 0.0, touch, 0.0, 0)

    band = n_ticks * tick_size
    parsed = list(levels)
    if side == "ask":
        parsed.sort(key=lambda x: x.price)
        lo, hi = touch, touch + band
        picked = [lvl for lvl in parsed if lo - 1e-12 <= lvl.price <= hi + 1e-12]
    elif side == "bid":
        parsed.sort(key=lambda x: x.price, reverse=True)
        lo, hi = touch - band, touch
        picked = [lvl for lvl in parsed if lo - 1e-12 <= lvl.price <= hi + 1e-12]
    else:
        raise ValueError(f"unknown book side: {side}")

    shares = sum(lvl.size for lvl in picked)
    notional = sum(lvl.size * lvl.price for lvl in picked)
    if shares <= 0:
        return DepthSlice(0.0, 0.0, touch, 0.0, 0)
    vwap = notional / shares
    if side == "ask":
        depth_cost = max(0.0, vwap - touch)
    else:
        depth_cost = max(0.0, touch - vwap)
    return DepthSlice(
        shares=shares,
        notional_usd=notional,
        vwap=vwap,
        depth_cost_pp=depth_cost,
        levels_used=len(picked),
    )


def fractional_kelly_usd(
    fair_p: float,
    entry_p: float,
    kelly_fraction: float,
    remaining_loss_budget_usd: float,
) -> float:
    """
    W5: cap stake with fractional Kelly x remaining daily loss budget.

    Binary contract bought at entry_p paying 1 if win:
      f* = (fair_p - entry_p) / (1 - entry_p)
    Size = kelly_fraction * f* * remaining_loss_budget.
    Losing the stake is what consumes the daily loss budget.
    """
    if remaining_loss_budget_usd <= 0 or kelly_fraction <= 0:
        return 0.0
    if entry_p <= 0.0 or entry_p >= 1.0:
        return 0.0
    edge = fair_p - entry_p
    if edge <= 0:
        return 0.0
    f_star = edge / (1.0 - entry_p)
    f_star = min(max(f_star, 0.0), 1.0)
    return kelly_fraction * f_star * remaining_loss_budget_usd


def required_min_edge_pp(
    cfg: WinmoreConfig,
    entry_price: float,
    btc_daily_vol_pct: float,
    _order_type: str,
) -> tuple[float, str]:
    """
    Required net edge in probability points after W1 mid-band + W2 delay buffer.

    Returns (required_pp, deny_reason_or_empty).
    deny_reason is set when mid-band taker policy is skip.
    """
    delay = taker_delay_buffer_pp(cfg, btc_daily_vol_pct)
    required = cfg.min_edge_pp + delay
    in_band = cfg.midband_enabled and is_midband(
        entry_price, cfg.midband_lower, cfg.midband_upper
    )
    if not in_band:
        return required, ""

    policy = cfg.midband_taker_policy
    if policy == "skip":
        # Ban mid-band takes. Fees peak at 50c and adverse selection is worst in-band.
        return required, "skip_midband_taker_ban"
    if policy == "raise_min_edge":
        required += cfg.midband_extra_min_edge_pp
        return required, ""
    raise ValueError(f"unknown midband_taker_policy: {policy}")


def should_block_second_clip(
    first_net_edge_pp: float,
    current_net_edge_pp: float,
    required_min_edge_pp_value: float,
    decay_ratio: float = 0.50,
) -> bool:
    """
    W5: after a first fill attempt, do not fire a second clip if edge decayed.

    Decayed = current < required min, or current < first_edge * decay_ratio.
    """
    if current_net_edge_pp < required_min_edge_pp_value:
        return True
    if first_net_edge_pp > 0 and current_net_edge_pp < first_net_edge_pp * decay_ratio:
        return True
    return False


def cap_size_usd(
    requested_usd: float,
    entry_price: float,
    depth: DepthSlice,
    kelly_cap_usd: float,
    min_stake_usd: float,
) -> tuple[float, float, str]:
    """
    W5: size <= depth within N ticks, and <= Kelly cap.
    Returns (size_usd, size_shares, deny_reason_or_empty).
    Does not round up past Kelly/depth.
    """
    if requested_usd <= 0 or entry_price <= 0:
        return 0.0, 0.0, "skip_invalid_size_inputs"
    if depth.shares <= 0 or depth.notional_usd <= 0:
        return 0.0, 0.0, "skip_no_depth_within_ticks"
    if kelly_cap_usd <= 0:
        return 0.0, 0.0, "skip_kelly_cap_zero"

    sized = min(requested_usd, depth.notional_usd, kelly_cap_usd)
    if sized + 1e-12 < min_stake_usd:
        return 0.0, 0.0, "skip_size_below_min_stake"
    shares = sized / entry_price
    if shares > depth.shares:
        shares = depth.shares
        sized = shares * entry_price
        if sized + 1e-12 < min_stake_usd:
            return 0.0, 0.0, "skip_size_below_min_stake"
    return sized, shares, ""


def evaluate_side(
    side: str,
    fair_p: float,
    ask: Optional[float],
    bid: Optional[float],
    ask_levels: list[BookLevel],
    cfg: WinmoreConfig,
    stake_usd: float,
    remaining_loss_budget_usd: float,
    btc_daily_vol_pct: float,
    prior_net_edge_pp: Optional[float] = None,
) -> EntryDecision:
    """Evaluate one side against W1+W2+W5. Pure; no I/O."""
    order_type = choose_entry_order_type(cfg)
    if ask is None or ask <= 0:
        return EntryDecision(allow=False, reason="skip_no_ask", side=side, order_type=order_type)

    spread = 0.0
    if bid is not None and bid > 0:
        spread = max(0.0, ask - bid)
    half_spread = spread / 2.0

    depth = depth_within_n_ticks(
        ask_levels, ask, cfg.depth_ticks, cfg.tick_size, side="ask"
    )
    fee = fee_pp(ask, half_spread, depth.depth_cost_pp, cfg.taker_fee_rate)
    gross = fair_p - ask
    net = gross - fee
    in_band = cfg.midband_enabled and is_midband(ask, cfg.midband_lower, cfg.midband_upper)
    delay = taker_delay_buffer_pp(cfg, btc_daily_vol_pct)
    required, midband_deny = required_min_edge_pp(cfg, ask, btc_daily_vol_pct, order_type)

    details = {
        "half_spread": half_spread,
        "depth_cost_pp": depth.depth_cost_pp,
        "taker_fee_pp": cfg.taker_fee_rate * ask * (1.0 - ask),
        "taker_delay_ms": cfg.taker_delay_ms,
        "depth_levels_used": depth.levels_used,
        "fee_formula": "0.07*p*(1-p) + half_spread + depth_cost",
    }

    base = EntryDecision(
        allow=False,
        reason="",
        side=side,
        entry_price=ask,
        fair_p=fair_p,
        gross_edge_pp=gross,
        fee_pp=fee,
        net_edge_pp=net,
        required_min_edge_pp=required,
        delay_buffer_pp=delay,
        depth_shares=depth.shares,
        depth_notional_usd=depth.notional_usd,
        order_type=order_type,
        in_midband=in_band,
        details=details,
    )

    if midband_deny:
        base.reason = midband_deny
        return base

    if net < required:
        base.reason = "skip_net_edge_below_fee_aware_min"
        return base

    if prior_net_edge_pp is not None and should_block_second_clip(
        prior_net_edge_pp, net, required, cfg.edge_decay_ratio
    ):
        base.reason = "skip_second_clip_edge_decayed"
        return base

    kelly_cap = fractional_kelly_usd(
        fair_p, ask, cfg.kelly_fraction, remaining_loss_budget_usd
    )
    base.kelly_cap_usd = kelly_cap
    size_usd, size_shares, size_deny = cap_size_usd(
        stake_usd, ask, depth, kelly_cap, cfg.min_stake_usd
    )
    base.size_usd = size_usd
    base.size_shares = size_shares
    if size_deny:
        base.reason = size_deny
        return base

    base.allow = True
    base.reason = "enter"
    return base


def select_entry(
    up: EntryDecision,
    down: EntryDecision,
) -> EntryDecision:
    """Pick the allowed side with higher net edge; otherwise return the richer deny."""
    allowed = [d for d in (up, down) if d.allow]
    if allowed:
        return sorted(allowed, key=lambda d: d.net_edge_pp, reverse=True)[0]
    # Prefer the deny that got closest to clearing the bar for logs.
    return sorted((up, down), key=lambda d: d.net_edge_pp, reverse=True)[0]


def winmore_config_from_mapping(data: Optional[dict[str, Any]]) -> WinmoreConfig:
    """Build WinmoreConfig from a yaml mapping (profiles.*.winmore)."""
    d = data or {}
    midband = d.get("midband") or {}
    delay = d.get("taker_delay") or {}
    sizing = d.get("sizing") or {}
    policy = str(midband.get("taker_policy", "skip"))
    if policy not in ("skip", "raise_min_edge"):
        raise ValueError(f"unknown midband.taker_policy: {policy}")
    return WinmoreConfig(
        midband_enabled=bool(midband.get("enabled", True)),
        midband_lower=float(midband.get("lower", 0.40)),
        midband_upper=float(midband.get("upper", 0.60)),
        midband_taker_policy=policy,  # type: ignore[arg-type]
        midband_extra_min_edge_pp=float(midband.get("extra_min_edge_pp", 0.02)),
        min_edge_pp=float(d.get("min_edge_pp", 0.005)),
        taker_fee_rate=float(d.get("taker_fee_rate", 0.07)),
        taker_delay_ms=int(delay.get("delay_ms", 250)),
        taker_delay_buffer_pp=float(delay.get("buffer_pp", 0.005)),
        taker_delay_buffer_vol_scale=bool(delay.get("vol_scale", True)),
        taker_delay_vol_ref_pct=float(delay.get("vol_ref_pct", 3.5)),
        taker_delay_buffer_min_pp=float(delay.get("buffer_min_pp", 0.005)),
        taker_delay_buffer_max_pp=float(delay.get("buffer_max_pp", 0.010)),
        prefer_post_only=bool(delay.get("prefer_post_only", True)),
        entry_order_type_post_only=str(delay.get("order_type_post_only", "GTD")),
        entry_order_type_taker=str(delay.get("order_type_taker", "FAK")),
        depth_ticks=int(sizing.get("depth_ticks", 3)),
        tick_size=float(sizing.get("tick_size", 0.01)),
        kelly_fraction=float(sizing.get("kelly_fraction", 0.25)),
        edge_decay_ratio=float(sizing.get("edge_decay_ratio", 0.50)),
        min_stake_usd=float(sizing.get("min_stake_usd", 1.0)),
    )


def decision_log_fields(d: EntryDecision) -> dict[str, Any]:
    """Compact JSON fields for runner attempt logs."""
    return {
        "allow": d.allow,
        "reason": d.reason,
        "side": d.side,
        "entry_price": d.entry_price,
        "fair_p": d.fair_p,
        "gross_edge_pp": d.gross_edge_pp,
        "fee_pp": d.fee_pp,
        "net_edge_pp": d.net_edge_pp,
        "required_min_edge_pp": d.required_min_edge_pp,
        "delay_buffer_pp": d.delay_buffer_pp,
        "size_usd": d.size_usd,
        "size_shares": d.size_shares,
        "depth_shares": d.depth_shares,
        "depth_notional_usd": d.depth_notional_usd,
        "kelly_cap_usd": d.kelly_cap_usd,
        "order_type": d.order_type,
        "in_midband": d.in_midband,
        "details": d.details,
    }
