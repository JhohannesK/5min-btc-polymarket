---
name: btc-5m-live
description: Run and monitor BTC 5-minute Up/Down trading on Polymarket using Chainlink TWAP fair-value estimation. Trades the cheap side vs fair when edge clears costs. Holds to redeem unless selling yields higher EV.
---

# BTC 5m Live

## Paths
- Main trading repo: `<your-workspace>/pm-hl-conservative-plus-repo` (or set `BTC5M_REPO`)
- Core runner: `src/live/pm_live_trade_runner.py`
- Canonical skill runner: `scripts/test_btc_5m_session_exit_sl.py`
- TWAP fair value module: `scripts/btc_5m_twap_fair.py`
- Entry timing (W4): `scripts/btc_5m_entry_timing.py`
- W1/W2/W5 win-more gates: `scripts/btc_5m_winmore_gates.py`
- Maker/post-only shadow pilot (W6): `scripts/btc_5m_maker_pilot.py`
- State tracker: `scripts/btc_5m_state_tracker.py`
- Skill control entrypoint: `scripts/btc5m_ctl.sh`
- Compatibility wrapper (deprecated): `scripts/run_btc_5m_threshold_test.py`

## Strategy Alignment
Use this skill when the operator wants to execute a BTC 5m TWAP fair-value strategy:
- **Settlement-aligned**: Uses Chainlink BTC/USD 60s TWAP (the series Polymarket pays).
- **Entry**: Pins window-open TWAP. W3 projected-final-TWAP: P(Up) from the incomplete 60s path + remaining settle window (not spot momentum). Default W4 window T-240 to T-45. Trades the cheap side when net edge > costs.
- **Exit**: Holds to redeem unless bid ≥ hold-EV after fees. No legacy % stops.
- **Risk**: One ticket per bucket, hard daily loss limit, kill switch.

## Operational Rules
- Default is dry-run unless `--execute` is set. Do not enable `--execute` until W1-W5 paper numbers are re-validated.
- W3 projected-final-TWAP fair; W4 entry window T-240 to T-45 (soft-skip open, hard-skip last ~20s unless polarized hold-EV exception).
- TWAP entries go through win-more gates: mid-band taker ban / fee_pp, 250ms delay buffer + GTD/post-only, depth+Kelly sizing, no second clip on decay.
- W6 maker_pilot is shadow-first and off by default (`enabled: false`, `shadow: true`, `live_execute: false`). Live maker posting is stubbed and still requires the execute flag; it does not unlock live trading.
- Use controlled stake sizing (`--stake-usd`, profile caps, then W5 depth/Kelly cap).
- If both UP and DOWN satisfy threshold logic, choose the stronger side.
- Keep stop-loss and timing guards enabled in profile config.

## One-shot real test
From trading repo root:

```bash
.venv/bin/python scripts/test_btc_5m_session_exit_sl.py --profile conservative --execute
```

Aggressive profile:

```bash
.venv/bin/python scripts/test_btc_5m_session_exit_sl.py --profile aggressive --execute
```

Override profile params manually (example):

```bash
.venv/bin/python scripts/test_btc_5m_session_exit_sl.py --profile conservative --stake-usd 5 --entry-timeout-min 90 --execute
```

## Strategy Profiles
- File: `config/btc_5m_profiles.yaml`
- Presets: `conservative`, `aggressive`
- Includes entry/exit timing, quote staleness checks, spread/liquidity guards, hedge triggers, and risk caps.

## Hot Commands (chat-friendly)
Examples:
- `btc5m conservative start`
- `btc5m aggressive start`

Handlers:
- `scripts/btc5m_hot.sh [conservative|aggressive]`
- `scripts/btc5m_ctl.sh start --profile [conservative|aggressive]`
- `scripts/btc5m_ctl.sh status|stop|report|logs`
- completion summary utility: `scripts/btc5m_latest_report.py --mark`

Output:
- isolated skill runtime logs: `skills/btc-5m-live/runtime/btc5m_<profile>_<UTCSTAMP>.log`

## Notes
- Canonical runner resolves current BTC 5m market slug (`btc-updown-5m-<bucket>`).
- Real order placement is delegated to `pm_live_trade_runner.py` with `--force-side` and `--max-notional-usd`.
- Keep BTC5m automation scoped to this skill contour (`btc5m_ctl.sh` + `skills/btc-5m-live/runtime`) to avoid cross-skill interference.
- Keep all GitHub-facing docs and metadata in English.
