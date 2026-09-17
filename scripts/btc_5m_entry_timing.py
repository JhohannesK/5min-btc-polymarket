#!/usr/bin/env python3
"""
W4 entry-timing policy for the TWAP fair-value path.

Default window: T-240 ... T-45 when |fair-0.5| and net edge are clear.
Soft-skip the first ~20s of the 5m bucket.
Hard-skip the last ~15-20s unless a polarized ask is still <= hold-EV - buffer
and top-of-book depth is OK.

Numbers are paper defaults. Re-validate on a short paper window before prod lock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


TimingZone = Literal["soft_open", "before_window", "default_window", "after_window", "hard_last"]


@dataclass(frozen=True)
class EntryTimingConfig:
    """Profile-driven W4 knobs. Paper defaults, not locked prod."""

    enabled: bool = True
    bucket_seconds: float = 300.0
    window_max_seconds_left: float = 240.0  # T-240
    window_min_seconds_left: float = 45.0  # T-45
    soft_skip_open_sec: float = 20.0
    hard_skip_last_sec: float = 20.0
    min_abs_fair_dev: float = 0.02  # |fair-0.5| in the default window
    polarized_min_abs_fair_dev: float = 0.25
    late_hold_ev_buffer_pp: float = 0.02
    late_min_ask_notional_usd: float = 30.0
    redeem_value: float = 1.0


@dataclass
class TimingDecision:
    allow: bool
    reason: str
    zone: TimingZone
    seconds_left: float
    fair_p: float = 0.0
    ask: float = 0.0
    hold_ev_per_share: float = 0.0
    abs_fair_dev: float = 0.0
    net_edge_bps: float = 0.0
    top_ask_notional_usd: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


def entry_timing_from_mapping(mapping: Optional[dict[str, Any]]) -> EntryTimingConfig:
    """Load W4 knobs from profiles yaml (shared_rules.session_timing.entry_timing)."""
    m = mapping or {}
    return EntryTimingConfig(
        enabled=bool(m.get("enabled", True)),
        bucket_seconds=float(m.get("bucket_seconds", 300.0)),
        window_max_seconds_left=float(m.get("window_max_seconds_left", 240.0)),
        window_min_seconds_left=float(m.get("window_min_seconds_left", 45.0)),
        soft_skip_open_sec=float(m.get("soft_skip_open_sec", 20.0)),
        hard_skip_last_sec=float(m.get("hard_skip_last_sec", 20.0)),
        min_abs_fair_dev=float(m.get("min_abs_fair_dev", 0.02)),
        polarized_min_abs_fair_dev=float(m.get("polarized_min_abs_fair_dev", 0.25)),
        late_hold_ev_buffer_pp=float(m.get("late_hold_ev_buffer_pp", 0.02)),
        late_min_ask_notional_usd=float(m.get("late_min_ask_notional_usd", 30.0)),
        redeem_value=float(m.get("redeem_value", 1.0)),
    )


def classify_seconds_left(seconds_left: float, cfg: EntryTimingConfig) -> TimingZone:
    """Map seconds-to-settle onto the W4 timing zones."""
    if seconds_left > (cfg.bucket_seconds - cfg.soft_skip_open_sec):
        return "soft_open"
    if seconds_left > cfg.window_max_seconds_left:
        return "before_window"
    if seconds_left >= cfg.window_min_seconds_left:
        return "default_window"
    if seconds_left > cfg.hard_skip_last_sec:
        return "after_window"
    return "hard_last"


def _is_polarized(fair_p: float, cfg: EntryTimingConfig) -> bool:
    return abs(fair_p - 0.5) >= cfg.polarized_min_abs_fair_dev


def _late_exception_ok(
    fair_p: float,
    ask: float,
    top_ask_notional_usd: float,
    cfg: EntryTimingConfig,
) -> tuple[bool, str]:
    """
    Last ~15-20s exception: polarized ask <= hold-EV - buffer, and depth OK.
    hold-EV per share = fair_p * redeem_value.
    """
    hold_ev = fair_p * cfg.redeem_value
    cap = hold_ev - cfg.late_hold_ev_buffer_pp
    if not _is_polarized(fair_p, cfg):
        return False, "skip_hard_last_seconds_not_polarized"
    if top_ask_notional_usd + 1e-12 < cfg.late_min_ask_notional_usd:
        return False, "skip_hard_last_seconds_thin_depth"
    if ask > cap + 1e-12:
        return False, "skip_hard_last_seconds_ask_above_hold_ev_buffer"
    return True, "allow_late_polarized"


def evaluate_entry_timing(
    seconds_left: float,
    fair_p: float,
    ask: float,
    net_edge_bps: float,
    min_edge_bps: float,
    top_ask_notional_usd: float,
    cfg: Optional[EntryTimingConfig] = None,
) -> TimingDecision:
    """
    W4 gate. Default window requires |fair-0.5| and net edge clear.
    Soft-skip bucket open. Hard-skip last seconds unless late exception fires.
    """
    cfg = cfg or EntryTimingConfig()
    zone = classify_seconds_left(seconds_left, cfg)
    abs_dev = abs(fair_p - 0.5)
    hold_ev = fair_p * cfg.redeem_value
    base = dict(
        seconds_left=seconds_left,
        fair_p=fair_p,
        ask=ask,
        hold_ev_per_share=hold_ev,
        abs_fair_dev=abs_dev,
        net_edge_bps=net_edge_bps,
        top_ask_notional_usd=top_ask_notional_usd,
        zone=zone,
    )

    if not cfg.enabled:
        return TimingDecision(allow=True, reason="timing_disabled", **base)

    if zone == "soft_open":
        return TimingDecision(allow=False, reason="skip_soft_bucket_open", **base)
    if zone == "before_window":
        return TimingDecision(allow=False, reason="skip_before_entry_window", **base)
    if zone == "after_window":
        return TimingDecision(allow=False, reason="skip_past_default_window", **base)

    if zone == "hard_last":
        ok, reason = _late_exception_ok(fair_p, ask, top_ask_notional_usd, cfg)
        if not ok:
            return TimingDecision(allow=False, reason=reason, **base)
        if net_edge_bps < min_edge_bps:
            return TimingDecision(allow=False, reason="skip_hard_last_seconds_no_edge", **base)
        return TimingDecision(allow=True, reason=reason, **base)

    if zone == "default_window":
        if abs_dev < cfg.min_abs_fair_dev:
            return TimingDecision(allow=False, reason="skip_fair_too_close_to_half", **base)
        if net_edge_bps < min_edge_bps:
            return TimingDecision(allow=False, reason="skip_net_edge_not_clear", **base)
        return TimingDecision(allow=True, reason="allow_default_entry_window", **base)

    raise ValueError(f"unhandled timing zone: {zone}")
