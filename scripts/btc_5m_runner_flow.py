#!/usr/bin/env python3
"""Pure runner-flow helpers: market slot, CLI, close retry, mode flags.

Kept out of the live runner so unit tests can lock the session state machine
without network or subprocess.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from typing import Any, Callable, Literal, Optional

from btc_5m_entry_timing import entry_timing_from_mapping
from btc_5m_winmore_gates import WinmoreConfig, winmore_config_from_mapping

FakCloseAction = Literal["done", "retry", "retry_zero_shares", "fallback_gtc"]
GtcCloseAction = Literal["done", "retry", "poll_then_force"]
GtcPollAction = Literal["done", "force_close"]
AfterWinmoreAction = Literal["maker_tick_continue", "deny", "time_then_open"]


def select_active_5m_market(
    event: Optional[dict[str, Any]],
    now_ts: float,
    slug: str,
    min_seconds_left: float = 5.0,
) -> Optional[dict[str, Any]]:
    """Accept only the current, still-open 5m market with enough time left."""
    if not event:
        return None
    mkts = event.get("markets") or []
    if not mkts:
        return None

    m = mkts[0]
    if m.get("closed") is True:
        return None
    if m.get("active") is False:
        return None

    end_iso = str(m.get("endDate") or m.get("endDateIso") or "")
    try:
        end_ts = dt.datetime.fromisoformat(end_iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

    sec_left = end_ts - now_ts
    if sec_left <= min_seconds_left:
        return None

    mm = dict(m)
    mm["_event_slug"] = slug
    mm["_seconds_left"] = sec_left
    return mm


def use_fair_value_mode(use_twap_fair_value: bool, legacy_threshold_mode: bool) -> bool:
    return bool(use_twap_fair_value) and not bool(legacy_threshold_mode)


def maker_pilot_is_on(maker_enabled: bool, use_fair_value: bool) -> bool:
    return bool(maker_enabled) and bool(use_fair_value)


def abort_execute_without_rtds(execute: bool, rtds_ok: bool) -> bool:
    return bool(execute) and not bool(rtds_ok)


def after_winmore_gate_action(maker_on: bool, decision_allow: bool) -> AfterWinmoreAction:
    """Maker shadow ticks then continues; it must never fall into taker open."""
    if maker_on:
        return "maker_tick_continue"
    if not decision_allow:
        return "deny"
    return "time_then_open"


def record_failed_open_edge(
    fill_attempts: dict[int, float],
    bucket: int,
    twap_entry_net_edge: Optional[float],
) -> None:
    if twap_entry_net_edge is not None:
        fill_attempts[bucket] = twap_entry_net_edge


def should_log_twap_settle(
    hold_to_redeem: bool,
    use_fair_value: bool,
    held_to_redeem: bool,
    window_open_twap: Optional[float],
) -> bool:
    return (
        bool(hold_to_redeem)
        and bool(use_fair_value)
        and bool(held_to_redeem)
        and window_open_twap is not None
    )


def clob_creds_ready(env: dict[str, str]) -> bool:
    key = env.get("PM_PRIVATE_KEY") or ""
    v1 = env.get("PM_API_KEY") or ""
    v2 = env.get("PM_API_SECRET") or ""
    v3 = env.get("PM_API_PASSPHRASE") or ""
    return bool(key and v1 and v2 and v3)


def open_risk_frac(stake: float) -> float:
    return float(stake) / 100.0


def build_open_cmd(
    slug: str,
    side: str,
    stake: float,
    execute: bool,
    order_type: str = "GTD",
) -> list[str]:
    cmd = [
        ".venv/bin/python",
        "src/live/pm_live_trade_runner.py",
        "--market-slug",
        slug,
        "--force-side",
        side,
        "--start-equity",
        "100",
        "--risk-frac",
        str(open_risk_frac(stake)),
        "--max-notional-usd",
        str(stake),
    ]
    if execute:
        cmd.append("--execute")
    return cmd


def apply_open_env(env: dict[str, str], order_type: str) -> dict[str, str]:
    out = dict(env)
    out.setdefault("PM_MAX_SPREAD", "0.05")
    out.setdefault("PM_MIN_TOP_ASK_NOTIONAL_USD", "10")
    out["PM_ORDER_TYPE"] = str(order_type or "GTD").upper()
    return out


def build_close_cmd(
    slug: str,
    token_id: str,
    shares: float,
    execute: bool,
    close_limit_price: Optional[float] = None,
) -> list[str]:
    cmd = [
        ".venv/bin/python",
        "src/live/pm_live_trade_runner.py",
        "--market-slug",
        slug,
        "--close-token-id",
        token_id,
        "--close-shares",
        f"{shares:.8f}",
    ]
    if close_limit_price is not None and close_limit_price > 0:
        cmd += ["--close-limit-price", f"{close_limit_price:.6f}"]
    if execute:
        cmd.append("--execute")
    return cmd


def apply_close_env(env: dict[str, str], close_order_type: str) -> dict[str, str]:
    out = dict(env)
    out["PM_CLOSE_ORDER_TYPE"] = str(close_order_type or "FAK").upper()
    return out


def classify_fak_close(
    post: dict[str, Any],
    close_obj: dict[str, Any],
    out_text: str,
) -> FakCloseAction:
    status = str(post.get("status") or "").lower()
    if post.get("success") is True and status == "matched":
        return "done"
    skipped = str(close_obj.get("close_skipped") or "")
    if skipped == "zero_effective_shares":
        return "retry_zero_shares"
    txt = ((out_text or "") + "\n" + json.dumps(close_obj, ensure_ascii=False)).lower()
    if "no orders found to match with fak order" in txt:
        return "fallback_gtc"
    return "retry"


def classify_gtc_close(post: dict[str, Any]) -> GtcCloseAction:
    status = str(post.get("status") or "").lower()
    if post.get("success") is True and status == "matched":
        return "done"
    if post.get("success") is True and status == "live":
        return "poll_then_force"
    return "retry"


def classify_gtc_poll(status: str) -> GtcPollAction:
    if str(status or "").upper() == "MATCHED":
        return "done"
    return "force_close"


def poll_order_status(
    client: Any,
    order_id: str,
    wait_sec: float = 6.0,
    step_sec: float = 1.0,
    *,
    now_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[str, Optional[dict[str, Any]]]:
    if client is None or not order_id:
        return "", None
    deadline = now_fn() + max(0.0, float(wait_sec))
    last = None
    while now_fn() <= deadline:
        try:
            last = client.get_order(order_id)
            st = str((last or {}).get("status") or "").upper()
            if st and st not in ("LIVE", "OPEN"):
                return st, last
        except Exception:
            pass
        sleep_fn(max(0.2, float(step_sec)))
    try:
        last = client.get_order(order_id)
    except Exception:
        pass
    st = str((last or {}).get("status") or "").upper()
    return st, last


def apply_profile_overrides(
    args: argparse.Namespace,
    profiles: dict[str, dict[str, Any]],
) -> argparse.Namespace:
    prof = profiles.get(args.profile or "conservative", profiles["conservative"])
    if args.threshold is None:
        args.threshold = float(prof["threshold"])
    if args.stake_usd is None:
        args.stake_usd = float(prof["stake_usd"])
    if args.stop_loss_pct is None:
        args.stop_loss_pct = float(prof["stop_loss_pct"])
    if args.exit_before_sec is None:
        args.exit_before_sec = int(prof["exit_before_sec"])
    if args.min_entry_seconds_left is None:
        args.min_entry_seconds_left = int(prof["min_entry_seconds_left"])
    if args.entry_timeout_min is None:
        args.entry_timeout_min = int(prof["entry_timeout_min"])
    if args.poll_sec is None:
        args.poll_sec = float(prof["poll_sec"])
    if args.use_twap_fair_value is None:
        args.use_twap_fair_value = bool(prof.get("use_twap_fair_value", True))
    if args.min_edge_bps is None:
        args.min_edge_bps = float(prof.get("min_edge_bps", 5.0))
    if args.btc_daily_vol_pct is None:
        args.btc_daily_vol_pct = float(prof.get("btc_daily_vol_pct", 3.5))
    if args.hold_to_redeem is None:
        args.hold_to_redeem = bool(prof.get("hold_to_redeem", True))
    args.maker_pilot_cfg = dict(prof.get("maker_pilot") or {})
    if getattr(args, "maker_pilot", False):
        args.maker_pilot_cfg["enabled"] = True
        args.maker_pilot_cfg["shadow"] = True
        args.maker_pilot_cfg["live_execute"] = False
    args.entry_timing_cfg = entry_timing_from_mapping(prof.get("entry_timing"))
    args.daily_max_loss_usd = float(prof.get("daily_max_loss_usd", 50.0))
    args.max_trades_per_day = int(prof.get("max_trades_per_day", 20))
    wm = prof.get("winmore")
    args.winmore = wm if isinstance(wm, WinmoreConfig) else winmore_config_from_mapping(wm)
    return args
