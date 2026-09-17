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
- **Kill switch** — `runtime/.kill` file triggers immediate halt with flatten or hold action
- **Spread/liquidity gates** — skip wide spreads or thin books

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
Use this quick pre-flight checklist before any real order:

1. **Market validity**
   - Confirm the BTC 5m market is active and not about to close unexpectedly.
2. **Time-to-close window**
   - Prefer entries around ~120 seconds left (with reasonable tolerance).
3. **Impulse confirmation**
   - Confirm the observed BTC move is meaningful (strategy reference: ~$70-$100).
4. **Skew confirmation**
   - Verify market skew supports the intended direction (do not fade strong momentum by default).
5. **Liquidity/spread checks**
   - Ensure spread and top-of-book notional pass your minimum thresholds.
6. **Sizing guardrails**
   - Validate stake, max notional, and daily loss limits before execution.
7. **Stop / exit controls**
   - Confirm stop-loss and `exit_before_sec` are configured.
8. **Execution mode**
   - Start in dry-run when changing parameters; switch to `--execute` only after validation.

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
