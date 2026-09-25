#!/usr/bin/env python3
"""YAML profile → runtime args mapping for the BTC 5m runner.

No file I/O in the mapping functions. load_profiles_from_yaml stays
in the runner so the config path is unchanged.
"""

from __future__ import annotations

from typing import Any

from btc_5m_winmore_gates import winmore_config_from_mapping


def runtime_profile_from_yaml(
    profile_data: dict[str, Any],
    shared_rules: dict[str, Any],
) -> dict[str, Any]:
    signal = profile_data.get("signal", {})
    sizing = profile_data.get("sizing", {})
    stop_loss = profile_data.get("stop_loss", {})
    session_timing = shared_rules.get("session_timing", {})
    twap_fair = profile_data.get("twap_fair_value", {})
    shared_mp = shared_rules.get("maker_pilot", {}) or {}
    profile_mp = profile_data.get("maker_pilot", {}) or {}
    maker_pilot = {**shared_mp, **profile_mp}
    entry_timing = dict(session_timing.get("entry_timing") or {})
    entry_timing.update(twap_fair.get("entry_timing") or {})

    return {
        "threshold": signal.get("threshold_price", 0.70),
        "stake_usd": sizing.get("stake_usd", 5.0),
        "stop_loss_pct": stop_loss.get("stop_loss_pct_from_entry", 0.25),
        "exit_before_sec": session_timing.get("exit_before_sec", 20),
        "min_entry_seconds_left": session_timing.get("min_entry_seconds_left", 60),
        "entry_timeout_min": 60,
        "poll_sec": 5.0,
        "use_twap_fair_value": twap_fair.get("enabled", True),
        "min_edge_bps": twap_fair.get("min_edge_bps", 5.0),
        "btc_daily_vol_pct": twap_fair.get("btc_daily_vol_pct", 3.5),
        "hold_to_redeem": twap_fair.get("hold_to_redeem", True),
        "maker_pilot": maker_pilot,
        "entry_timing": entry_timing,
        "daily_max_loss_usd": float(sizing.get("daily_max_loss_usd", 50.0)),
        "max_trades_per_day": int(sizing.get("max_trades_per_day", 20)),
        "winmore": winmore_config_from_mapping(profile_data.get("winmore")),
    }


def profiles_from_yaml_config(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    shared = config.get("shared_rules", {}) or {}
    profiles: dict[str, dict[str, Any]] = {}
    for profile_name, profile_data in (config.get("profiles") or {}).items():
        if not isinstance(profile_data, dict):
            continue
        profiles[profile_name] = runtime_profile_from_yaml(profile_data, shared)
    return profiles
