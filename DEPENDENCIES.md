# Dependencies and External Components

## External Repository: `pm-hl-conservative-plus-repo`

**Status**: Not included in this repository

**Purpose**: Contains the live trading engine (`src/live/pm_live_trade_runner.py`) that executes actual orders on Polymarket CLOB.

**Integration Point**: This skill calls the external runner as a subprocess for:
- Opening positions (`--force-side`, `--max-notional-usd`)
- Closing positions (`--close-token-id`, `--close-shares`)

**What This Skill Provides**:
- TWAP fair value calculation
- Entry/exit decision logic
- Risk controls (one-ticket-per-bucket, daily loss limits)
- State tracking
- Kill switch

**What External Runner Provides**:
- Actual CLOB order placement
- Fill confirmation
- Position management
- Low-level CLOB API interaction

## Chainlink TWAP Data

**Status**: Fallback implementation using spot prices

**CRITICAL (Research Requirements)**:
- **Canonical feed**: Chainlink BTC/USD **60s TWAP** only (since Aug 14, 2026)
- **RTDS endpoint**: `data.chain.link/streams/btc-usd-twap-60s-streams`
- **RTDS topic**: `crypto_prices_twap_sixty`
- **Filter**: `{"symbol":"btc/usd"}`
- **Ship-blocker**: Must log `windowSeconds` on every TWAP operation to prevent silent 30s/60s mix

**Current Implementation**:
- `ChainlinkTWAPTracker` in `scripts/btc_5m_twap_fair.py`
- Falls back to averaging spot prices from Binance/Coinbase (dry-run only)
- Marks source as `"spot_fallback_dry_run_only"` with `windowSeconds=60`
- Logs `[TWAP_PIN]` and `[TWAP_CALC]` with windowSeconds for verification
- RTDS enforced in `--execute` mode: spot fallback disabled for live trading

**Production Requirements**:
See `RTDS_INTEGRATION.md` for complete RTDS connection details, logging requirements, and ship-blocker checklist.

## Python Dependencies

Required packages:
- `pyyaml` - YAML config loading
- `requests` - API calls
- `py-clob-client` - Polymarket CLOB client

## Polymarket API Credentials

Required environment variables (set in external repo `.env` or environment):
- `PM_PRIVATE_KEY` - Private key for signing
- `PM_FUNDER` or `PM_ADDRESS` - Funder address
- `PM_SIGNATURE_TYPE` - Signature type (default 2)
- `PM_API_KEY` - CLOB API key
- `PM_API_SECRET` - CLOB API secret  
- `PM_API_PASSPHRASE` - CLOB API passphrase

## Risk Control Files

### State Tracking
- Location: `runtime/state/state_YYYY-MM-DD.json`
- Purpose: Track active tickets, daily PnL, trade counts
- Created automatically by `StateTracker`

### Kill Switch
- Location: `runtime/.kill`
- Purpose: Emergency stop for trading
- Actions:
  - `flatten`: Cancel orders and close all positions
  - `hold`: Cancel orders and hold positions to settlement
- Delete file to re-enable trading

## Verification Without Live Credentials

Dry-run mode works without Polymarket credentials:

```bash
# Dry-run fair value calculation
.venv/bin/python scripts/test_btc_5m_session_exit_sl.py --profile conservative

# Check TWAP tracker
python3 -c "from scripts.btc_5m_twap_fair import ChainlinkTWAPTracker; t=ChainlinkTWAPTracker(); print(t.get_current_twap())"

# Check state tracker
python3 -c "from scripts.btc_5m_state_tracker import StateTracker; from pathlib import Path; s=StateTracker(Path('./runtime/state')); print(s.get_daily_summary())"
```

All fair value calculations, edge estimation, and hold-EV logic run without credentials.
