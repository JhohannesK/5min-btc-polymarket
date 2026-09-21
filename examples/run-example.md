# Example commands

Dry-run (safe validation):

```bash
.venv/bin/python scripts/test_btc_5m_session_exit_sl.py --profile conservative
```

W6 maker/post-only **shadow** (no taker orders, no live maker posts):

```bash
.venv/bin/python scripts/test_btc_5m_session_exit_sl.py \
  --profile conservative \
  --maker-pilot \
  --entry-timeout-min 35
```

`btc5m_ctl.sh` does not pass `--maker-pilot`. Use the runner CLI above, or set `profiles.conservative.maker_pilot.enabled: true` in yaml (still shadow unless you also flip `live_execute`, which remains stubbed).

Real execution (conservative). Requires RTDS; see `BLOCKERS.md`:

```bash
.venv/bin/python scripts/test_btc_5m_session_exit_sl.py --profile conservative --execute
```

Real execution (aggressive):

```bash
.venv/bin/python scripts/test_btc_5m_session_exit_sl.py --profile aggressive --execute
```

Do not combine `--maker-pilot` with an expectation of fills. Maker-on sessions end with `maker_pilot_shadow_complete`.
