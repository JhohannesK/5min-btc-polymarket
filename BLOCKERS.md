# --execute Mode Blockers

## Status: RTDS Connection Required

**Cannot run `--execute` mode without live Chainlink RTDS connection.**

---

## Required Before --execute

### 1. Chainlink RTDS Credentials ⚠️ BLOCKER

**Environment Variables:**
```bash
export CHAINLINK_RTDS_ENDPOINT="https://data.chain.link/streams/btc-usd-twap-60s-streams"
export CHAINLINK_RTDS_API_KEY="your-api-key-here"
```

**How to Get:**
- Contact Chainlink for RTDS access
- Request access to `crypto_prices_twap_sixty` topic
- Filter: `{"symbol":"btc/usd"}`

**Verification:**
```bash
# Should connect to RTDS and show windowSeconds=60
python3 scripts/verify_twap_fair_dryrun.py

# Look for:
[RTDS] Connected successfully: windowSeconds=60

# NOT:
[TWAP_FALLBACK] Using spot estimate - ONLY FOR DRY-RUN
```

### 2. External Live Runner ⚠️ BLOCKER

**Repository:** `pm-hl-conservative-plus-repo` (not in this tree)

**Required:**
- `src/live/pm_live_trade_runner.py` must be present
- Polymarket API credentials configured in `.env`

**Environment Variables:**
```bash
export BTC5M_REPO="/path/to/pm-hl-conservative-plus-repo"
```

Or place at default location:
```
../../pm-hl-conservative-plus-repo/
```

### 3. Polymarket API Credentials ⚠️ BLOCKER

**Required in external repo `.env`:**
```bash
PM_PRIVATE_KEY=...
PM_FUNDER=...
PM_API_KEY=...
PM_API_SECRET=...
PM_API_PASSPHRASE=...
```

---

## What Works Without Blockers

### ✅ Dry-Run Mode (No Credentials)

```bash
# Fair value calculation with spot fallback
python3 scripts/test_btc_5m_session_exit_sl.py --profile conservative

# TWAP verification
python3 scripts/verify_twap_fair_dryrun.py

# State tracker
python3 -c "from scripts.btc_5m_state_tracker import StateTracker; from pathlib import Path; s=StateTracker(Path('./runtime/state')); print(s.get_daily_summary())"
```

**Output will show:**
```
[TWAP_FALLBACK] Using spot estimate - ONLY FOR DRY-RUN
series=spot-estimate-not-rtds
```

This is **intentional** for dry-run. Spot fallback is disabled for `--execute`.

---

## Enforced Protections

### RTDS Check on --execute

When you run with `--execute`, the code will:

1. Check for RTDS credentials
2. Attempt RTDS connection
3. If RTDS not available: **REFUSE TO TRADE**

```python
if args.execute:
    try:
        test_snapshot = twap_tracker.get_current_twap(allow_fallback=False)
    except RuntimeError as e:
        report['result'] = 'rtds_required_for_execute'
        # Exit without trading
```

**Error message:**
```
RTDS connection required for --execute mode.
Set CHAINLINK_RTDS_ENDPOINT and CHAINLINK_RTDS_API_KEY env vars.
Never use spot fallback in production.
```

### No Hardcoded windowSeconds

Production path:
- ✅ Validates `windowSeconds` from RTDS response
- ✅ Errors if `windowSeconds != 60`
- ❌ Never invents `windowSeconds=60`

Spot fallback (dry-run only):
- Uses `windowSeconds=60` assumption
- Clearly marked: `source=spot_fallback_dry_run_only`
- Series ID: `spot-estimate-not-rtds`
- Disabled for `--execute`

---

## Testing Checklist

### Before --execute
- [ ] CHAINLINK_RTDS_ENDPOINT set
- [ ] CHAINLINK_RTDS_API_KEY set
- [ ] RTDS connection verified: `[RTDS] Connected successfully`
- [ ] windowSeconds=60 confirmed in RTDS response
- [ ] External repo at BTC5M_REPO path
- [ ] Polymarket credentials in external repo `.env`
- [ ] Dry-run test passes
- [ ] State tracker working

### During First --execute Test
- [ ] Start with small stake (--stake-usd 1)
- [ ] Monitor logs for [RTDS], [TWAP_PIN], [TWAP_CALC]
- [ ] Verify no [TWAP_FALLBACK] messages
- [ ] Check windowSeconds logged on every operation
- [ ] Verify series_id = btc-usd-twap-60s (not spot-estimate)

---

## Known Limitations

### Spot Fallback Accuracy
Spot estimate averages Binance + Coinbase but is NOT the canonical Chainlink 60s TWAP. It approximates for dry-run testing only.

**Do not trade real money on spot fallback.**

### External Runner
This skill provides:
- Fair value calculation
- Entry/exit decisions  
- Risk controls

External runner provides:
- Actual CLOB order execution
- Fill confirmation
- Position tracking

Any bugs in external runner are out of scope for this repo.

### W6 maker_pilot does not unlock --execute
`--maker-pilot` (or yaml `enabled: true`) is shadow-only. It skips TWAP taker entry. The live maker path always returns `live_maker_path_stubbed_until_rtds_and_creds_ready`. Clearing RTDS/CLOB blockers does not enable maker posts.

---

## Quick Start for Dry-Run (No Blockers)

```bash
# 1. Verify TWAP fair value calculation works
python3 scripts/verify_twap_fair_dryrun.py

# 2. Run dry-run strategy test
python3 scripts/test_btc_5m_session_exit_sl.py --profile conservative

# 3. Check state tracker
python3 -c "from scripts.btc_5m_state_tracker import StateTracker; from pathlib import Path; print(StateTracker(Path('./runtime/state')).get_daily_summary())"

# All should work without any credentials
```

Expected output includes:
- `[TWAP_FALLBACK] Using spot estimate - ONLY FOR DRY-RUN`
- `source=spot_fallback_dry_run_only`
- `series=spot-estimate-not-rtds`

This is correct for dry-run.

---

## Quick Start for --execute (Requires All Blockers Cleared)

```bash
# 1. Set RTDS credentials
export CHAINLINK_RTDS_ENDPOINT="https://data.chain.link/streams/btc-usd-twap-60s-streams"
export CHAINLINK_RTDS_API_KEY="your-key"

# 2. Verify RTDS connection
python3 scripts/verify_twap_fair_dryrun.py
# Should show: [RTDS] Connected successfully

# 3. Set external repo path
export BTC5M_REPO="/path/to/pm-hl-conservative-plus-repo"

# 4. Run with --execute (small stake first)
./scripts/btc5m_ctl.sh start --profile conservative --stake-usd 1 --execute

# 5. Monitor logs
tail -f runtime/btc5m_conservative_*.log | grep "TWAP_\|RTDS"
```

Expected in logs:
- `[RTDS] Connected successfully`
- `[TWAP_PIN] windowSeconds=60 series=btc-usd-twap-60s`
- `[TWAP_CALC] windowSeconds=60`
- NO `[TWAP_FALLBACK]` messages
