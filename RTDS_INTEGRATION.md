# Chainlink RTDS Integration for BTC 5m TWAP

## Production Requirements (Research)

### Critical: 60s TWAP Series Only

**Since August 14, 2026**, Polymarket BTC 5m Up/Down markets settle on Chainlink BTC/USD **60s TWAP** only. Never mix 30s and 60s series.

**Ship-blocker:** `windowSeconds` must be logged on every TWAP operation to prevent silent series mix.

---

## RTDS Connection Details

### Endpoint
```
https://data.chain.link/streams/btc-usd-twap-60s-streams
```

### Topic
```
crypto_prices_twap_sixty
```

### Filter
```json
{
  "symbol": "btc/usd"
}
```

### Expected Response
```json
{
  "timestamp": 1789683600,
  "value": 76214.23,
  "windowSeconds": 60,
  "series": "btc-usd-twap-60s",
  "source": "chainlink"
}
```

---

## Implementation in `ChainlinkTWAPTracker`

### Constructor
```python
# Set via environment variables (preferred)
os.environ['CHAINLINK_RTDS_ENDPOINT'] = 'https://data.chain.link/streams/btc-usd-twap-60s-streams'
os.environ['CHAINLINK_RTDS_API_KEY'] = 'your-api-key'

tracker = ChainlinkTWAPTracker()

# Or pass directly
tracker = ChainlinkTWAPTracker(
    rtds_endpoint="https://data.chain.link/streams/btc-usd-twap-60s-streams",
    rtds_api_key="your-api-key"
)
```

### get_current_twap()

Production implementation (now in code):
```python
# For --execute mode, requires RTDS
snapshot = tracker.get_current_twap(allow_fallback=False)

# For dry-run, allows spot fallback
snapshot = tracker.get_current_twap(allow_fallback=True)
```

RTDS request flow:
```python
response = requests.post(
    self.rtds_endpoint,
    json={
        "topic": "crypto_prices_twap_sixty",
        "filter": {"symbol": "btc/usd"}
    },
    headers={
        "Authorization": f"Bearer {self.rtds_api_key}",
        "Content-Type": "application/json"
    }
)

data = response.json()

# SHIP-BLOCKER: Validate windowSeconds from feed (never invent it)
window_seconds = data.get("windowSeconds")
if window_seconds != 60:
    raise ValueError(f"Wrong TWAP window: {window_seconds}s, expected 60s")

# Use windowSeconds from feed, not hardcoded value
snapshot = TWAPSnapshot(
    timestamp=time.time(),
    twap_60s=data["value"],
    window_seconds=window_seconds,  # From feed, not invented
    source="chainlink_rtds",
    series_id=data.get("series", "btc-usd-twap-60s")
)
```

---

## windowSeconds Logging

### Required Logging Points

**1. Window-Open Pin** (`FairValueCalculator.pin_window_open`):
```
[TWAP_PIN] market=btc-updown-5m-1234567890 windowSeconds=60 series=btc-usd-twap-60s value=76214.23 source=chainlink_rtds
```

**2. Every Fair Value Calculation** (`FairValueCalculator.calculate_fair_value`):
```
[TWAP_CALC] market=btc-updown-5m-1234567890 windowSeconds=60 series=btc-usd-twap-60s open=76214.23 current=76250.45
```

**3. Settlement Check** (when position is held to redeem):
```
[TWAP_SETTLE] market=btc-updown-5m-1234567890 windowSeconds=60 series=btc-usd-twap-60s final=76280.10 open=76214.23 result=UP
```

### Error Cases (Ship-Blocker)

```
[TWAP_PIN_ERROR] WRONG WINDOW: got 30s, expected 60s
[TWAP_CALC_ERROR] WINDOW MISMATCH: current=60s, open=30s
```

Any window error should:
1. Log the error prominently
2. Refuse to open position
3. Refuse to calculate fair value
4. Alert operators

---

## Fee Math (Research Requirement)

Polymarket crypto taker fee formula:
```
fee = C × 0.07 × p × (1-p)
```

Where:
- `C` = cost in USDC (shares × price)
- `p` = price (0 to 1)
- Makers pay 0

### Fee Examples

| Price | Fee per 100 shares | Fee % of notional |
|-------|-------------------|-------------------|
| 0.50  | $1.75             | 3.5%              |
| 0.70  | $1.47             | 2.1%              |
| 0.90  | $0.63             | 0.7%              |
| 0.99  | $0.07             | 0.07%             |

Peak fee is at 50¢ (neutral odds).

### Edge Gate

Edge must clear:
```
net_edge > fee + half_spread + depth_to_size
```

Prefer:
1. **Maker-first** — zero fees
2. **Hold to redeem** — one-way fee, EV = fair_p × $1
3. **Taker exit** — only when bid ≥ hold-EV after fee

---

## Current Implementation Status

### ✅ Implemented
- windowSeconds field in TWAPSnapshot
- Logging on pin_window_open, calculate_fair_value, and settle
- 60s validation from feed (never invented)
- Fee math (shares × 0.07 × p × (1-p), C = shares)
- Depth-to-size cost in edge calculation
- RTDS connection with API key auth
- Spot fallback disabled for --execute mode
- [TWAP_SETTLE] logging helper (log_twap_settle.py)

### ⚠️ Blockers for --execute
- [ ] Set CHAINLINK_RTDS_ENDPOINT env var
- [ ] Set CHAINLINK_RTDS_API_KEY env var
- [ ] Verify RTDS connection returns windowSeconds=60
- [ ] Test with dry-run first (spot fallback OK)
- [ ] Monitor [RTDS] and [TWAP_*] logs

### Production Requirements
- RTDS endpoint and API key required for --execute
- Dry-run mode uses spot fallback (clearly marked)
- Never invent windowSeconds=60 on production path
- Always validate windowSeconds from RTDS response

---

## Testing

### Dry-Run Verification
```bash
$ python3 scripts/verify_twap_fair_dryrun.py
✓ Window: 60s (MUST be 60s)
✓ Series: btc-usd-twap-60s-fallback
[TWAP_PIN] market=btc-updown-5m-1234567890 windowSeconds=60 ...
```

### Live Verification (with RTDS)
```bash
# Set RTDS credentials
export RTDS_API_KEY="your-key"

# Run with RTDS endpoint
$ python3 scripts/test_btc_5m_session_exit_sl.py \
    --profile conservative \
    --rtds-endpoint "https://data.chain.link/streams/btc-usd-twap-60s-streams"

# Check logs for windowSeconds
$ grep "TWAP_PIN\|TWAP_CALC" runtime/btc5m_conservative_*.log
```

---

## Ship-Blocker Checklist

Before production:
- [ ] RTDS connection working
- [ ] windowSeconds == 60 validated on every operation
- [ ] Logging present: TWAP_PIN, TWAP_CALC, TWAP_SETTLE
- [ ] Error alerts configured for window mismatch
- [ ] Spot fallback disabled (or clearly marked in logs)
- [ ] Fee math verified: C × 0.07 × p × (1-p)
- [ ] Edge gate enforces fee + half-spread minimum
- [ ] No 30s/60s mix possible

**Never deploy without complete windowSeconds logging.**
