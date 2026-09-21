# 5min BTC Polymarket Skill

Open-source OpenClaw skill for **BTC 5-minute Up/Down** markets on Polymarket.

Repository: https://github.com/Novals83/5min-btc-polymarket

## Strategy (TWAP Fair-Value Trading)
This skill uses Chainlink BTC/USD 60s TWAP (the series Polymarket pays for settlement) to estimate fair value and trade when the market is mispriced:

### Settlement Rule
Polymarket BTC 5m Up/Down markets resolve as:
- **Up wins** if Chainlink BTC/USD 60s TWAP at window end ≥ TWAP at window open
- **Down wins** otherwise

### Entry Logic
1. Pin the Chainlink 60s TWAP at window open
2. Track the live 60s TWAP path during the 5-minute window
3. Calculate fair P(Up) from the **projected final 60s TWAP** (incomplete path + remaining settle window). Spot momentum is ignored.
4. Default entry window T-240 to T-45 when |fair-0.5| and net edge are clear. Soft-skip the first ~20s of the bucket. Hard-skip the last ~15-20s unless a polarized ask is still <= hold-EV minus buffer and depth is OK.
5. **Trade the cheap side vs fair** when net edge > minimum threshold:
   - Net edge = (fair value - book price) - costs
   - Costs = crypto taker fee (7% × p × (1-p)) + half-spread
   - Default min edge: 5 bps (conservative) or 3 bps (aggressive)

### Exit Logic
- **Hold to redeem** (EV = fair_p × $1.00) unless selling at current bid yields higher EV after fees
- No legacy % stop-loss on mid prices
- No panic sells at $0.01
- Exit early only when bid ≥ hold-EV or time-based safety exit triggers

### Risk Controls
- **One ticket per 5m bucket** — enforced by state tracker
- **Daily loss limit** — hard stop at $50 loss (configurable)
- **Max trades per day** — 12 (conservative) or 20 (aggressive)
- **Kill switch** — presence of `runtime/.kill` stops the runner immediately. `action: flatten` or `action: hold` is **recorded** on the report (`kill_switch_flatten` / `kill_switch_hold`). This runner does **not** cancel CLOB orders or flatten/hold positions.
- **Spread/liquidity gates** — skip wide spreads or thin books
- **W1/W2/W5 win-more gates** — mid-band taker skip/raise, ~250ms delay buffer, depth+Kelly sizing, no second clip on decay
- **W6 maker pilot** — off by default; `--maker-pilot` is shadow-only and **replaces** the TWAP taker entry path (no live maker posts)

### Legacy Mode
Pass `--legacy-threshold-mode` to use old threshold-only entry logic (for comparison/debug only). Not recommended for live trading.

## Repository Structure
- `SKILL.md` — skill definition and operating rules
- `config/` — profiles and risk parameters
- `scripts/` — runners/wrappers/hot commands
- `examples/` — practical command examples

## Deploy / Run
### Prerequisites
- OpenClaw environment
- Polymarket execution stack available at:
  - `<your-workspace>/pm-hl-conservative-plus-repo`
- Python virtual env for runner scripts
- Valid API credentials configured outside this repository

### Quick Start
```bash
git clone https://github.com/Novals83/5min-btc-polymarket.git
cd 5min-btc-polymarket
```

Read:
- `SKILL.md`
- `config/btc_5m_profiles.yaml`

Run a conservative real test (example):
```bash
.venv/bin/python scripts/test_btc_5m_session_exit_sl.py --profile conservative --execute
```

Run aggressive profile:
```bash
.venv/bin/python scripts/test_btc_5m_session_exit_sl.py --profile aggressive --execute
```

Unified skill control (recommended):
```bash
scripts/btc5m_ctl.sh start --profile conservative
scripts/btc5m_ctl.sh status
scripts/btc5m_ctl.sh report --limit 20
scripts/btc5m_ctl.sh stop
```

Runtime isolation:
- skill runtime dir: `./runtime`
- auth/env source (default): `<your-workspace>/pm-hl-conservative-plus-repo/.env`
- overrides: `BTC5M_REPO`, `BTC5M_ENV_FILE`, `BTC5M_RUNNER`
- completion auto-report cron (topic 184): `btc5m-completion-autoreport-topic184`

Optional Docker isolation:
```bash
scripts/btc5m_docker.sh up
scripts/btc5m_docker.sh status
scripts/btc5m_docker.sh down
```

## Execution Checklist (Before Live Trade)
Use this pre-flight before any `--execute` order. The yaml `strategy_reference` block (T-120 / $70–$100 impulse / skew-follow) is **legacy** and is not used by the default TWAP path.

1. **Market validity**
   - Confirm the BTC 5m market is active (`btc-updown-5m-<bucket>`).
2. **W4 entry window**
   - Default window is **T-240 to T-45**. Soft-skip the first ~20s of the bucket. Hard-skip the last ~20s unless a polarized ask still clears hold-EV minus buffer and depth is OK. Do **not** target T-120.
3. **RTDS (required for `--execute`)**
   - `CHAINLINK_RTDS_ENDPOINT` + `CHAINLINK_RTDS_API_KEY` set. Logs must show `windowSeconds=60` and **no** `[TWAP_FALLBACK]`. See `BLOCKERS.md` and `RTDS_INTEGRATION.md`.
4. **Fair vs book (not impulse)**
   - Direction comes from W3 projected-final-TWAP vs the cheap side of the book. Spot momentum / $70–$100 impulse is ignored.
5. **W1–W5 gates**
   - Mid-band taker skip/raise (`[0.40, 0.60]`), delay buffer, depth-within-3-ticks + fractional Kelly vs remaining daily loss budget.
6. **Liquidity / spread**
   - Runner spread skip is **0.03** abs. Thin books fail the W5 depth cap.
7. **Sizing**
   - Profile stake, `daily_max_loss_usd` ($50 default), max trades/day (12 conservative / 20 aggressive).
8. **Exit**
   - Default is **hold-to-redeem EV**. Profile `stop_loss.enabled` is **false**. Early exit only when bid ≥ hold-EV after fees, or `exit_before_sec` time safety.
9. **Kill switch**
   - `cp runtime/.kill.example runtime/.kill` stops **this runner**. It does not flatten the CLOB book.
10. **Execution mode**
    - Dry-run first. `--execute` refuses to trade without RTDS. Do not enable `--execute` until W1–W5 paper numbers are re-validated.

## W6 maker / post-only shadow pilot

Shadow-first GTD + post-only quotes vs the CLOB book. **Never sends maker orders.** Live posting is stubbed even if yaml `live_execute: true` and `--execute` are set (`live_maker_path_stubbed_until_rtds_and_creds_ready`).

Enable for one session (forces `enabled=true`, `shadow=true`, `live_execute=false`):

```bash
.venv/bin/python scripts/test_btc_5m_session_exit_sl.py \
  --profile conservative \
  --maker-pilot \
  --entry-timeout-min 35
```

Offline sanity (includes W6):

```bash
python3 scripts/verify_twap_fair_dryrun.py
```

**Constraints**

- `--maker-pilot` **skips the TWAP taker `run_open()` path**. Result is `maker_pilot_shadow_complete`, not a fill.
- `btc5m_ctl.sh` does **not** pass `--maker-pilot`. Enable via the runner CLI or `profiles.*.maker_pilot.enabled: true` in `config/btc_5m_profiles.yaml`.
- Neutral TWAP `edge_signal` does not quote. Cancel on `fair_flip`, `spread_widen` (>0.03), `gtd_expired` (15s conservative / 12s aggressive), `bucket_roll`.
- Max 4 shadow quotes per bucket. Default join best bid (`quote_improve_ticks: 0`).
- `paper_window_required` is stored on config and summaries; it is **not** a runtime gate.
- `btc5m_report.py` / `btc5m_latest_report.py` ignore maker events. Grep logs instead:

```bash
grep 'MAKER_PILOT' runtime/btc5m_*.log
```

Events: `shadow_post`, `would_fill`, `would_cancel`, `post_skip`. All JSON after `[MAKER_PILOT]`.

## Risk Controls Template
Suggested baseline controls (adapt to your risk profile):

- **Per-trade risk cap**: 1%-15% of account equity (profile dependent)
- **Daily max loss**: hard stop at 10%-15%
- **Max trades/day**: fixed ceiling to avoid overtrading
- **Max notional/trade**: strict upper bound
- **Quote staleness guard**: skip if market data is stale
- **Spread guard**: skip when spread exceeds threshold
- **Liquidity guard**: skip when top ask/bid notional is too thin
- **Extreme skew hedge**: optional small opposite hedge in 95/5-type scenarios
- **Operational kill switch**: immediate stop on repeated API/DNS/execution failures

## Risk Notice
This repository is educational/operational infrastructure, not financial advice.
Use your own risk limits, daily loss caps, and capital controls.

## Contributing
- Fork the repository
- Create a feature branch
- Commit changes
- Open a PR to `main`

PRs are welcome.
