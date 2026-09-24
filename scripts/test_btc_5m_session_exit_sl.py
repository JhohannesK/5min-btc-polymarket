#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import subprocess
import time
from typing import Any, Optional
from pathlib import Path

import yaml
import requests

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import ApiCreds

from btc_5m_twap_fair import (
    ChainlinkTWAPTracker,
    FairValueCalculator,
    calculate_hold_ev
)
from btc_5m_entry_timing import (
    entry_timing_from_mapping,
)
from log_twap_settle import log_twap_settle
from btc_5m_state_tracker import StateTracker, TicketState
from btc_5m_maker_pilot import (
    MakerPilotConfig,
    MakerPilotEngine,
    SideBook,
    format_maker_pilot_report,
    summarize_maker_pilot,
)
from btc_5m_runner_parse import (
    apply_maker_pilot_cli_override,
    best_ask_notional as _best_ask_notional,
    best_bid_ask as _best_bid_ask,
    bucket_5m,
    check_kill_switch,
    estimate_session_fees,
    market_side_prices,
    min_spread as _min_spread,
    parse_json_objects,
    pick_timing_allowed_entry,
    should_skip_legacy_late_entry,
    spread_too_wide,
    timing_checks_for_allowed_sides,
)
from btc_5m_winmore_gates import (
    EntryDecision,
    WinmoreConfig,
    decision_log_fields,
    evaluate_side,
    parse_book_levels,
    select_entry,
    winmore_config_from_mapping,
)

UTC = dt.timezone.utc


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def ts_utc() -> str:
    return now_utc().isoformat().replace('+00:00', 'Z')


def fetch_event(slug: str) -> Optional[dict[str, Any]]:
    r = requests.get('https://gamma-api.polymarket.com/events', params={'slug': slug}, timeout=12)
    r.raise_for_status()
    arr = r.json()
    return arr[0] if arr else None


def resolve_active_current_5m_market() -> Optional[dict[str, Any]]:
    """Return active BTC 5m market for the current slot only."""
    now = int(time.time())
    cur = bucket_5m(now)
    slug = f'btc-updown-5m-{cur}'

    try:
        ev = fetch_event(slug)
    except Exception:
        return None
    if not ev:
        return None

    mkts = ev.get('markets') or []
    if not mkts:
        return None

    m = mkts[0]
    if m.get('closed') is True:
        return None
    if m.get('active') is False:
        return None

    end_iso = str(m.get('endDate') or m.get('endDateIso') or '')
    try:
        end_ts = dt.datetime.fromisoformat(end_iso.replace('Z', '+00:00')).timestamp()
    except Exception:
        return None

    sec_left = end_ts - time.time()
    if sec_left <= 5:
        return None

    mm = dict(m)
    mm['_event_slug'] = slug
    mm['_seconds_left'] = sec_left
    return mm


def fetch_clob_books(up_token: str, down_token: str, clob_base: str = 'https://clob.polymarket.com'):
    """Fetch both CLOB books once (asks+bids+depth)."""
    pub = ClobClient(host=clob_base, chain_id=POLYGON)
    return pub.get_order_book(str(up_token)), pub.get_order_book(str(down_token))


def side_book_from_clob(side: str, token_id: str, book) -> SideBook:
    bid, ask = _best_bid_ask(book)
    return SideBook(side=side, token_id=str(token_id), best_bid=bid, best_ask=ask)


def clob_side_prices(up_token: str, down_token: str, clob_base: str = 'https://clob.polymarket.com') -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return trigger prices from CLOB orderbooks: UP ask, DOWN ask, spread of picked side when available."""
    up_book, dn_book = fetch_clob_books(up_token, down_token, clob_base)
    up_bid, up_ask = _best_bid_ask(up_book)
    dn_bid, dn_ask = _best_bid_ask(dn_book)

    picked_spread = None
    # Side picked later by max ask; keep a generic sanity spread estimate
    if up_ask is not None and up_bid is not None:
        picked_spread = max(0.0, up_ask - up_bid)
    if dn_ask is not None and dn_bid is not None:
        s = max(0.0, dn_ask - dn_bid)
        picked_spread = s if picked_spread is None else min(picked_spread, s)

    return up_ask, dn_ask, picked_spread


def clob_side_books(
    up_token: str,
    down_token: str,
    clob_base: str = 'https://clob.polymarket.com',
) -> tuple[SideBook, SideBook]:
    up_raw, dn_raw = fetch_clob_books(up_token, down_token, clob_base)
    return (
        side_book_from_clob('UP', up_token, up_raw),
        side_book_from_clob('DOWN', down_token, dn_raw),
    )


def run_maker_pilot_tick(
    engine: MakerPilotEngine,
    report: dict[str, Any],
    *,
    now: float,
    fair_signal: str,
    up_book: SideBook,
    down_book: SideBook,
    stake_usd: float,
    execute: bool,
    rtds_ready: bool,
    bucket: int,
    slug: str,
    seconds_left: Optional[float],
) -> list[dict[str, Any]]:
    if not engine.config.enabled:
        return []
    events = engine.on_tick(
        now=now,
        fair_signal=fair_signal,
        up_book=up_book,
        down_book=down_book,
        stake_usd=stake_usd,
        execute=execute,
        rtds_ready=rtds_ready,
        creds_ready=False,
        bucket=bucket,
        market_slug=slug,
        seconds_left=seconds_left,
    )
    if events:
        report.setdefault('maker_pilot_events', []).extend(events)
        report['attempts'].append({
            'ts': ts_utc(),
            'slug': slug,
            'status': 'maker_pilot_tick',
            'events': [e.get('event') for e in events],
            'reasons': [e.get('reason') for e in events if e.get('reason')],
        })
    return events


def attach_maker_pilot_summary(report: dict[str, Any], engine: MakerPilotEngine) -> None:
    summary = summarize_maker_pilot(engine.events, engine.quotes)
    report['maker_pilot'] = summary
    report['maker_pilot_report'] = format_maker_pilot_report(summary)


def clob_best_bid(token_id: str, clob_base: str = 'https://clob.polymarket.com') -> Optional[float]:
    pub = ClobClient(host=clob_base, chain_id=POLYGON)
    book = pub.get_order_book(str(token_id))
    best_bid, _ = _best_bid_ask(book)
    return best_bid


def auth_clob_client(clob_base: str = 'https://clob.polymarket.com') -> Optional[ClobClient]:
    try:
        key = os.getenv('PM_PRIVATE_KEY') or ''
        funder = os.getenv('PM_FUNDER') or os.getenv('PM_ADDRESS') or None
        sig = int(os.getenv('PM_SIGNATURE_TYPE', '2'))
        v1 = os.getenv('PM_API_KEY') or ''
        v2 = os.getenv('PM_API_SECRET') or ''
        v3 = os.getenv('PM_API_PASSPHRASE') or ''
        if not key or not v1 or not v2 or not v3:
            return None
        c = ClobClient(host=clob_base, chain_id=POLYGON, key=key, signature_type=sig, funder=funder)
        creds = {
            f"api_{'key'}": v1,
            f"api_{'secret'}": v2,
            f"api_{'passphrase'}": v3,
        }
        c.set_api_creds(ApiCreds(**creds))
        return c
    except Exception:
        return None


def poll_order_status(client: Optional[ClobClient], order_id: str, wait_sec: float = 6.0, step_sec: float = 1.0) -> tuple[str, Optional[dict[str, Any]]]:
    if client is None or not order_id:
        return '', None
    deadline = time.time() + max(0.0, float(wait_sec))
    last = None
    while time.time() <= deadline:
        try:
            last = client.get_order(order_id)
            st = str((last or {}).get('status') or '').upper()
            if st and st not in ('LIVE', 'OPEN'):
                return st, last
        except Exception:
            pass
        time.sleep(max(0.2, float(step_sec)))
    try:
        last = client.get_order(order_id)
    except Exception:
        pass
    st = str((last or {}).get('status') or '').upper()
    return st, last


def cancel_token_orders(client: Optional[ClobClient], token_id: str) -> Optional[dict[str, Any]]:
    if client is None:
        return None
    try:
        return client.cancel_market_orders(asset_id=str(token_id))
    except Exception as e:
        return {'error': str(e)}


def run_open(
    repo: str,
    slug: str,
    side: str,
    stake: float,
    execute: bool,
    order_type: str = 'GTD',
) -> tuple[str, list[dict[str, Any]]]:
    cmd = [
        '.venv/bin/python',
        'src/live/pm_live_trade_runner.py',
        '--market-slug', slug,
        '--force-side', side,
        '--start-equity', '100',
        '--risk-frac', str(stake / 100.0),
        '--max-notional-usd', str(stake),
    ]
    if execute:
        cmd.append('--execute')
    env = os.environ.copy()
    # Set reasonable defaults for safety guards instead of disabling them
    # Only override if not already set in environment
    env.setdefault('PM_MAX_SPREAD', '0.05')
    env.setdefault('PM_MIN_TOP_ASK_NOTIONAL_USD', '10')
    # W2: honor prefer_post_only. Do not leave a sticky FAK env default in place.
    env['PM_ORDER_TYPE'] = str(order_type or 'GTD').upper()
    p = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=env)
    out = (p.stdout or '') + '\n' + (p.stderr or '')
    return out, parse_json_objects(out)


def run_close(
    repo: str,
    slug: str,
    token_id: str,
    shares: float,
    execute: bool,
    close_order_type: str = 'FAK',
    close_limit_price: float | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    cmd = [
        '.venv/bin/python',
        'src/live/pm_live_trade_runner.py',
        '--market-slug', slug,
        '--close-token-id', token_id,
        '--close-shares', f'{shares:.8f}',
    ]
    if close_limit_price is not None and close_limit_price > 0:
        cmd += ['--close-limit-price', f'{close_limit_price:.6f}']
    if execute:
        cmd.append('--execute')
    env = os.environ.copy()
    env['PM_CLOSE_ORDER_TYPE'] = str(close_order_type or 'FAK').upper()
    p = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=env)
    out = (p.stdout or '') + '\n' + (p.stderr or '')
    return out, parse_json_objects(out)


def get_side_price_from_slug(slug: str, side: str) -> Optional[float]:
    try:
        ev = fetch_event(slug)
        if not ev:
            return None
        mkts = ev.get('markets') or []
        if not mkts:
            return None
        up, dn, *_ = market_side_prices(mkts[0])
        return up if side == 'UP' else dn
    except Exception:
        return None


def load_profiles_from_yaml() -> dict[str, dict[str, Any]]:
    """Load profiles from config/btc_5m_profiles.yaml, with fallback to hardcoded defaults."""
    config_path = Path(__file__).parent.parent / 'config' / 'btc_5m_profiles.yaml'
    
    fallback_profiles = {
        'conservative': {
            'threshold': 0.70,
            'stake_usd': 5.0,
            'stop_loss_pct': 0.25,
            'exit_before_sec': 20,
            'min_entry_seconds_left': 60,
            'entry_timeout_min': 60,
            'poll_sec': 5.0,
            'use_twap_fair_value': True,
            'min_edge_bps': 5.0,
            'btc_daily_vol_pct': 3.5,
            'hold_to_redeem': True,
            'maker_pilot': MakerPilotConfig().as_public_dict(),
            'entry_timing': {},
            'daily_max_loss_usd': 50.0,
            'max_trades_per_day': 12,
            'winmore': winmore_config_from_mapping(None),
        },
        'aggressive': {
            'threshold': 0.70,
            'stake_usd': 5.0,
            'stop_loss_pct': 0.30,
            'exit_before_sec': 20,
            'min_entry_seconds_left': 60,
            'entry_timeout_min': 60,
            'poll_sec': 5.0,
            'use_twap_fair_value': True,
            'min_edge_bps': 3.0,
            'btc_daily_vol_pct': 3.5,
            'hold_to_redeem': True,
            'maker_pilot': MakerPilotConfig().as_public_dict(),
            'entry_timing': {},
            'daily_max_loss_usd': 50.0,
            'max_trades_per_day': 20,
            'winmore': winmore_config_from_mapping({
                'min_edge_pp': 0.003,
                'midband': {'taker_policy': 'raise_min_edge'},
                'taker_delay': {'buffer_pp': 0.010},
                'sizing': {'kelly_fraction': 0.30},
            }),
        },
    }
    
    if not config_path.exists():
        return fallback_profiles
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        profiles = {}
        for profile_name, profile_data in config.get('profiles', {}).items():
            signal = profile_data.get('signal', {})
            sizing = profile_data.get('sizing', {})
            stop_loss = profile_data.get('stop_loss', {})
            session_timing = config.get('shared_rules', {}).get('session_timing', {})
            twap_fair = profile_data.get('twap_fair_value', {})
            shared_mp = config.get('shared_rules', {}).get('maker_pilot', {}) or {}
            profile_mp = profile_data.get('maker_pilot', {}) or {}
            maker_pilot = {**shared_mp, **profile_mp}
            entry_timing = dict(session_timing.get('entry_timing') or {})
            entry_timing.update(twap_fair.get('entry_timing') or {})

            profiles[profile_name] = {
                'threshold': signal.get('threshold_price', 0.70),
                'stake_usd': sizing.get('stake_usd', 5.0),
                'stop_loss_pct': stop_loss.get('stop_loss_pct_from_entry', 0.25),
                'exit_before_sec': session_timing.get('exit_before_sec', 20),
                'min_entry_seconds_left': session_timing.get('min_entry_seconds_left', 60),
                'entry_timeout_min': 60,
                'poll_sec': 5.0,
                'use_twap_fair_value': twap_fair.get('enabled', True),
                'min_edge_bps': twap_fair.get('min_edge_bps', 5.0),
                'btc_daily_vol_pct': twap_fair.get('btc_daily_vol_pct', 3.5),
                'hold_to_redeem': twap_fair.get('hold_to_redeem', True),
                'maker_pilot': maker_pilot,
                'entry_timing': entry_timing,
                'daily_max_loss_usd': float(sizing.get('daily_max_loss_usd', 50.0)),
                'max_trades_per_day': int(sizing.get('max_trades_per_day', 20)),
                'winmore': winmore_config_from_mapping(profile_data.get('winmore')),
            }
        
        return profiles if profiles else fallback_profiles
    except Exception:
        return fallback_profiles


PROFILES = load_profiles_from_yaml()


def apply_profile(args: argparse.Namespace) -> argparse.Namespace:
    prof = PROFILES.get(args.profile or 'conservative', PROFILES['conservative'])
    if args.threshold is None:
        args.threshold = float(prof['threshold'])
    if args.stake_usd is None:
        args.stake_usd = float(prof['stake_usd'])
    if args.stop_loss_pct is None:
        args.stop_loss_pct = float(prof['stop_loss_pct'])
    if args.exit_before_sec is None:
        args.exit_before_sec = int(prof['exit_before_sec'])
    if args.min_entry_seconds_left is None:
        args.min_entry_seconds_left = int(prof['min_entry_seconds_left'])
    if args.entry_timeout_min is None:
        args.entry_timeout_min = int(prof['entry_timeout_min'])
    if args.poll_sec is None:
        args.poll_sec = float(prof['poll_sec'])
    if args.use_twap_fair_value is None:
        args.use_twap_fair_value = bool(prof.get('use_twap_fair_value', True))
    if args.min_edge_bps is None:
        args.min_edge_bps = float(prof.get('min_edge_bps', 5.0))
    if args.btc_daily_vol_pct is None:
        args.btc_daily_vol_pct = float(prof.get('btc_daily_vol_pct', 3.5))
    if args.hold_to_redeem is None:
        args.hold_to_redeem = bool(prof.get('hold_to_redeem', True))
    args.maker_pilot_cfg = apply_maker_pilot_cli_override(
        dict(prof.get('maker_pilot') or {}),
        getattr(args, 'maker_pilot', False),
    )
    args.entry_timing_cfg = entry_timing_from_mapping(prof.get('entry_timing'))
    args.daily_max_loss_usd = float(prof.get('daily_max_loss_usd', 50.0))
    args.max_trades_per_day = int(prof.get('max_trades_per_day', 20))
    wm = prof.get('winmore')
    args.winmore = wm if isinstance(wm, WinmoreConfig) else winmore_config_from_mapping(wm)
    return args


def default_repo_path() -> str:
    env_repo = os.environ.get('BTC5M_REPO')
    if env_repo:
        return env_repo
    return str(Path(__file__).resolve().parents[3] / 'pm-hl-conservative-plus-repo')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=default_repo_path())
    ap.add_argument('--profile', choices=['conservative', 'aggressive'], default='conservative')
    ap.add_argument('--threshold', type=float, default=None)
    ap.add_argument('--stake-usd', type=float, default=None)
    ap.add_argument('--stop-loss-pct', type=float, default=None, help='0.30 means -30%% from entry price')
    ap.add_argument('--exit-before-sec', type=int, default=None)
    ap.add_argument('--min-entry-seconds-left', type=int, default=None, help='Do not open if less seconds remain in current 5m slot')
    ap.add_argument('--entry-timeout-min', type=int, default=None)
    ap.add_argument('--poll-sec', type=float, default=None)
    ap.add_argument('--close-retry-max', type=int, default=18, help='Max close retries when position is not yet visible / not immediately closable')
    ap.add_argument('--close-retry-delay-sec', type=float, default=2.0, help='Delay between close retries')
    ap.add_argument('--use-twap-fair-value', type=bool, default=None, help='Use TWAP fair value for entry (default True)')
    ap.add_argument('--min-edge-bps', type=float, default=None, help='Minimum edge in bps to enter (default 5.0)')
    ap.add_argument('--btc-daily-vol-pct', type=float, default=None, help='BTC daily vol % for fair value calc (default 3.5)')
    ap.add_argument('--hold-to-redeem', type=bool, default=None, help='Hold to redeem unless bid >= hold-EV (default True)')
    ap.add_argument('--legacy-threshold-mode', action='store_true', help='Use legacy threshold-only mode (for debug/comparison)')
    ap.add_argument(
        '--maker-pilot',
        action='store_true',
        help='Enable W6 maker/post-only shadow pilot. Forces shadow; does not enable live execute.',
    )
    ap.add_argument('--execute', action='store_true')
    args = apply_profile(ap.parse_args())

    # Initialize TWAP tracker and fair value calculator
    use_fair_value = args.use_twap_fair_value and not args.legacy_threshold_mode
    if use_fair_value:
        # Allow fallback for dry-run; RTDS required for --execute
        twap_tracker = ChainlinkTWAPTracker()
        fair_calc = FairValueCalculator(twap_tracker)
    else:
        twap_tracker = None
        fair_calc = None
    
    # Initialize state tracker for one-ticket-per-bucket and daily limits
    state_tracker = StateTracker(
        state_dir=Path(__file__).parent.parent / 'runtime' / 'state',
        daily_loss_limit_usd=float(args.daily_max_loss_usd),
        max_trades_per_day=int(args.max_trades_per_day),
    )
    winmore_cfg: WinmoreConfig = args.winmore

    mp_cfg = MakerPilotConfig.from_mapping(getattr(args, 'maker_pilot_cfg', None))
    maker_engine = MakerPilotEngine(mp_cfg)
    maker_on = mp_cfg.enabled and use_fair_value

    report: dict[str, Any] = {
        'started_at': ts_utc(),
        'params': {
            'profile': args.profile,
            'threshold': args.threshold,
            'stake_usd': args.stake_usd,
            'stop_loss_pct': args.stop_loss_pct,
            'exit_before_sec': args.exit_before_sec,
            'min_entry_seconds_left': args.min_entry_seconds_left,
            'entry_timeout_min': args.entry_timeout_min,
            'poll_sec': args.poll_sec,
            'close_retry_max': args.close_retry_max,
            'close_retry_delay_sec': args.close_retry_delay_sec,
            'execute': args.execute,
            'use_twap_fair_value': use_fair_value,
            'min_edge_bps': args.min_edge_bps if use_fair_value else None,
            'btc_daily_vol_pct': args.btc_daily_vol_pct if use_fair_value else None,
            'hold_to_redeem': args.hold_to_redeem,
            'legacy_threshold_mode': args.legacy_threshold_mode,
            'maker_pilot': mp_cfg.as_public_dict(),
            'entry_timing': {
                'window_max_seconds_left': args.entry_timing_cfg.window_max_seconds_left,
                'window_min_seconds_left': args.entry_timing_cfg.window_min_seconds_left,
                'soft_skip_open_sec': args.entry_timing_cfg.soft_skip_open_sec,
                'hard_skip_last_sec': args.entry_timing_cfg.hard_skip_last_sec,
            },
            'winmore': {
                'min_edge_pp': winmore_cfg.min_edge_pp,
                'midband_taker_policy': winmore_cfg.midband_taker_policy,
                'midband': [winmore_cfg.midband_lower, winmore_cfg.midband_upper],
                'taker_delay_ms': winmore_cfg.taker_delay_ms,
                'taker_delay_buffer_pp': winmore_cfg.taker_delay_buffer_pp,
                'prefer_post_only': winmore_cfg.prefer_post_only,
                'depth_ticks': winmore_cfg.depth_ticks,
                'kelly_fraction': winmore_cfg.kelly_fraction,
            },
            'daily_max_loss_usd': args.daily_max_loss_usd,
        },
        'attempts': [],
        'maker_pilot_events': [],
    }

    deadline = time.time() + args.entry_timeout_min * 60
    opened = None
    # W5: first fill-attempt net edge per bucket; block a second clip if edge decayed.
    fill_attempts: dict[int, float] = {}
    
    # Daily summary at start
    daily_summary = state_tracker.get_daily_summary()
    report['daily_summary_start'] = daily_summary

    while time.time() < deadline:
        try:
            # Check kill switch
            kill_action = check_kill_switch()
            if kill_action:
                report['kill_switch_triggered'] = {
                    'ts': ts_utc(),
                    'action': kill_action,
                }
                report['result'] = f'kill_switch_{kill_action}'
                report['finished_at'] = ts_utc()
                attach_maker_pilot_summary(report, maker_engine)
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return
            
            m = resolve_active_current_5m_market()
            if not m:
                report['attempts'].append({'ts': ts_utc(), 'status': 'heartbeat_no_current_market'})
                time.sleep(args.poll_sec)
                continue

            g_up, g_dn, up_t, dn_t, slug, end_iso = market_side_prices(m)
            
            # Check one-ticket-per-bucket and daily limits
            current_bucket = bucket_5m(int(time.time()))
            can_open, reason = state_tracker.can_open_ticket(current_bucket)
            if not can_open:
                report['attempts'].append({
                    'ts': ts_utc(),
                    'slug': slug,
                    'status': 'skip_state_check_failed',
                    'reason': reason,
                    'bucket': current_bucket,
                })
                time.sleep(args.poll_sec)
                continue

            end_ts = None
            sec_left = None
            try:
                end_ts = dt.datetime.fromisoformat(end_iso.replace('Z', '+00:00')).timestamp()
                sec_left = max(0.0, end_ts - time.time())
            except Exception:
                pass

            if sec_left is None:
                report['attempts'].append({'ts': ts_utc(), 'slug': slug, 'status': 'heartbeat_bad_market_end'})
                time.sleep(args.poll_sec)
                continue

            # CLOB books: asks, bids, depth (fetched once per poll).
            try:
                up_raw, dn_raw = fetch_clob_books(up_t, dn_t)
            except Exception as e:
                report['attempts'].append({'ts': ts_utc(), 'slug': slug, 'status': 'skip_clob_unavailable', 'error': str(e)})
                time.sleep(args.poll_sec)
                continue

            up_bid, up_ask = _best_bid_ask(up_raw)
            dn_bid, dn_ask = _best_bid_ask(dn_raw)
            up_side_book = side_book_from_clob('UP', up_t, up_raw)
            dn_side_book = side_book_from_clob('DOWN', dn_t, dn_raw)
            min_spread = _min_spread(up_side_book, dn_side_book)

            # Legacy blunt late-entry skip. Fair-value path uses W4 timing instead.
            # Maker shadow still ticks so resting quotes cancel when the window is gone.
            if should_skip_legacy_late_entry(
                sec_left,
                args.min_entry_seconds_left,
                maker_on=maker_on,
                use_fair_value=use_fair_value,
            ):
                if maker_on:
                    run_maker_pilot_tick(
                        maker_engine,
                        report,
                        now=time.time(),
                        fair_signal='unavailable',
                        up_book=up_side_book,
                        down_book=dn_side_book,
                        stake_usd=args.stake_usd,
                        execute=args.execute,
                        rtds_ready=False,
                        bucket=current_bucket,
                        slug=slug,
                        seconds_left=sec_left,
                    )
                report['attempts'].append({
                    'ts': ts_utc(),
                    'slug': slug,
                    'status': 'skip_too_late_to_enter',
                    'seconds_left': sec_left,
                    'min_entry_seconds_left': args.min_entry_seconds_left,
                })
                time.sleep(args.poll_sec)
                continue
            report['attempts'].append({
                'ts': ts_utc(),
                'slug': slug,
                'status': 'heartbeat',
                'gamma_up': g_up,
                'gamma_down': g_dn,
                'clob_up_ask': up_ask,
                'clob_down_ask': dn_ask,
                'seconds_left': sec_left,
                'min_spread': min_spread,
            })

            # Enforce spread gate: skip if spread is too wide
            max_spread = 0.03
            if spread_too_wide(min_spread, max_spread):
                if maker_on:
                    wide_sig = 'unavailable'
                    if fair_calc is not None:
                        try:
                            fv_wide = fair_calc.calculate_fair_value(
                                slug, sec_left, args.btc_daily_vol_pct, allow_fallback=not args.execute
                            )
                            if fv_wide is not None:
                                wide_sig = fv_wide.edge_signal
                        except Exception:
                            pass
                    run_maker_pilot_tick(
                        maker_engine,
                        report,
                        now=time.time(),
                        fair_signal=wide_sig,
                        up_book=up_side_book,
                        down_book=dn_side_book,
                        stake_usd=args.stake_usd,
                        execute=args.execute,
                        rtds_ready=bool(args.execute),
                        bucket=current_bucket,
                        slug=slug,
                        seconds_left=sec_left,
                    )
                report['attempts'].append({
                    'ts': ts_utc(),
                    'slug': slug,
                    'status': 'skip_spread_too_wide',
                    'min_spread': min_spread,
                    'max_spread': max_spread,
                    'seconds_left': sec_left,
                })
                time.sleep(args.poll_sec)
                continue

            entry_stake = args.stake_usd
            entry_order_type = 'FAK'
            twap_entry_net_edge: Optional[float] = None

            # Entry logic: TWAP fair value or legacy threshold
            if use_fair_value and fair_calc is not None:
                # Check RTDS requirement for --execute mode
                if args.execute and twap_tracker:
                    try:
                        # Force RTDS for live trading
                        test_snapshot = twap_tracker.get_current_twap(allow_fallback=False)
                    except RuntimeError as e:
                        report['rtds_check_failed'] = str(e)
                        report['result'] = 'rtds_required_for_execute'
                        report['finished_at'] = ts_utc()
                        attach_maker_pilot_summary(report, maker_engine)
                        print(json.dumps(report, ensure_ascii=False, indent=2))
                        return
                
                # Calculate fair value based on TWAP
                # For --execute mode, never allow fallback; for dry-run, fallback is OK
                allow_fallback = not args.execute
                fair_value = fair_calc.calculate_fair_value(
                    slug, 
                    sec_left, 
                    args.btc_daily_vol_pct,
                    allow_fallback=allow_fallback
                )
                
                if fair_value is None:
                    if maker_on:
                        run_maker_pilot_tick(
                            maker_engine,
                            report,
                            now=time.time(),
                            fair_signal='unavailable',
                            up_book=up_side_book,
                            down_book=dn_side_book,
                            stake_usd=args.stake_usd,
                            execute=args.execute,
                            rtds_ready=bool(args.execute),
                            bucket=current_bucket,
                            slug=slug,
                            seconds_left=sec_left,
                        )
                    report['attempts'].append({
                        'ts': ts_utc(),
                        'slug': slug,
                        'status': 'skip_fair_value_unavailable',
                        'seconds_left': sec_left,
                    })
                    time.sleep(args.poll_sec)
                    continue

                remaining_budget = state_tracker.remaining_loss_budget_usd()
                prior_edge = fill_attempts.get(current_bucket)
                up_levels = parse_book_levels(getattr(up_raw, 'asks', None))
                dn_levels = parse_book_levels(getattr(dn_raw, 'asks', None))
                up_ask_notional = _best_ask_notional(up_raw)
                dn_ask_notional = _best_ask_notional(dn_raw)

                up_decision = evaluate_side(
                    'UP',
                    fair_value.p_up,
                    up_ask,
                    up_bid,
                    up_levels,
                    winmore_cfg,
                    args.stake_usd,
                    remaining_budget,
                    args.btc_daily_vol_pct,
                    prior_net_edge_pp=prior_edge,
                )
                dn_decision = evaluate_side(
                    'DOWN',
                    fair_value.p_down,
                    dn_ask,
                    dn_bid,
                    dn_levels,
                    winmore_cfg,
                    args.stake_usd,
                    remaining_budget,
                    args.btc_daily_vol_pct,
                    prior_net_edge_pp=prior_edge,
                )
                decision = select_entry(up_decision, dn_decision)

                report['attempts'].append({
                    'ts': ts_utc(),
                    'slug': slug,
                    'status': 'fair_value_winmore_gate',
                    'fair_p_up': fair_value.p_up,
                    'fair_p_down': fair_value.p_down,
                    'window_open_twap': fair_value.window_open_twap,
                    'current_twap': fair_value.current_twap,
                    'projected_final_twap': fair_value.projected_final_twap,
                    'locked_frac': fair_value.locked_frac,
                    'remaining_frac': fair_value.remaining_frac,
                    'fair_model': fair_value.model,
                    'edge_signal': fair_value.edge_signal,
                    'source': fair_value.source,
                    'source_ts': fair_value.source_ts,
                    'receipt_ts': fair_value.receipt_ts,
                    'decision_ts': fair_value.decision_ts,
                    'up_ask': up_ask,
                    'dn_ask': dn_ask,
                    'up_gate': decision_log_fields(up_decision),
                    'dn_gate': decision_log_fields(dn_decision),
                    'picked': decision_log_fields(decision),
                    'remaining_loss_budget_usd': remaining_budget,
                    'seconds_left': sec_left,
                })

                if maker_on:
                    run_maker_pilot_tick(
                        maker_engine,
                        report,
                        now=time.time(),
                        fair_signal=fair_value.edge_signal,
                        up_book=up_side_book,
                        down_book=dn_side_book,
                        stake_usd=args.stake_usd,
                        execute=args.execute,
                        rtds_ready=bool(args.execute),
                        bucket=current_bucket,
                        slug=slug,
                        seconds_left=sec_left,
                    )
                    time.sleep(args.poll_sec)
                    continue

                if not decision.allow:
                    report['attempts'].append({
                        'ts': ts_utc(),
                        'slug': slug,
                        'status': decision.reason,
                        'min_edge_pp': winmore_cfg.min_edge_pp,
                        'required_min_edge_pp': decision.required_min_edge_pp,
                        'up_net_edge_pp': up_decision.net_edge_pp,
                        'dn_net_edge_pp': dn_decision.net_edge_pp,
                        'seconds_left': sec_left,
                    })
                    time.sleep(args.poll_sec)
                    continue

                timing_checks = timing_checks_for_allowed_sides(
                    [up_decision, dn_decision],
                    seconds_left=sec_left,
                    fair_p_up=fair_value.p_up,
                    fair_p_down=fair_value.p_down,
                    up_ask_notional=up_ask_notional,
                    dn_ask_notional=dn_ask_notional,
                    timing_cfg=args.entry_timing_cfg,
                )
                for cand, timing, notional in timing_checks:
                    report['attempts'].append({
                        'ts': ts_utc(),
                        'slug': slug,
                        'status': 'entry_timing_check',
                        'side': cand.side,
                        'allow': timing.allow,
                        'reason': timing.reason,
                        'zone': timing.zone,
                        'seconds_left': sec_left,
                        'abs_fair_dev': timing.abs_fair_dev,
                        'top_ask_notional_usd': notional,
                    })

                picked = pick_timing_allowed_entry(timing_checks)
                if picked is None:
                    report['attempts'].append({
                        'ts': ts_utc(),
                        'slug': slug,
                        'status': 'skip_entry_timing',
                        'seconds_left': sec_left,
                    })
                    time.sleep(args.poll_sec)
                    continue

                decision, timing = picked
                side = str(decision.side)
                trigger_price = decision.entry_price
                entry_stake = decision.size_usd
                entry_order_type = decision.order_type
                twap_entry_net_edge = decision.net_edge_pp
                report['entry_edge_details'] = decision_log_fields(decision)
                report['entry_timing'] = {
                    'reason': timing.reason,
                    'zone': timing.zone,
                    'seconds_left': sec_left,
                }
                report['entry_fair_value'] = {
                    'p_up': fair_value.p_up,
                    'p_down': fair_value.p_down,
                    'window_open_twap': fair_value.window_open_twap,
                    'current_twap': fair_value.current_twap,
                    'projected_final_twap': fair_value.projected_final_twap,
                    'locked_frac': fair_value.locked_frac,
                    'remaining_frac': fair_value.remaining_frac,
                    'model': fair_value.model,
                    'edge_signal': fair_value.edge_signal,
                    'source': fair_value.source,
                    'source_ts': fair_value.source_ts,
                    'receipt_ts': fair_value.receipt_ts,
                    'decision_ts': fair_value.decision_ts,
                }
            else:
                # Legacy threshold-only mode
                candidates: list[tuple[str, float]] = []
                if up_ask is not None and float(up_ask) >= args.threshold:
                    candidates.append(('UP', float(up_ask)))
                if dn_ask is not None and float(dn_ask) >= args.threshold:
                    candidates.append(('DOWN', float(dn_ask)))

                if not candidates:
                    report['attempts'].append({
                        'ts': ts_utc(),
                        'slug': slug,
                        'status': 'skip_price_below_threshold',
                        'threshold': args.threshold,
                        'clob_up_ask': up_ask,
                        'clob_down_ask': dn_ask,
                        'seconds_left': sec_left,
                    })
                    time.sleep(args.poll_sec)
                    continue

                side, trigger_price = sorted(candidates, key=lambda x: x[1], reverse=True)[0]
                entry_stake = args.stake_usd
                entry_order_type = 'FAK'

            out, objs = run_open(
                args.repo,
                slug,
                side,
                entry_stake,
                args.execute,
                order_type=entry_order_type,
            )
            post = None
            runner = None
            for o in objs:
                if isinstance(o, dict) and 'order_post_result' in o:
                    runner = o
                    post = o.get('order_post_result') or {}
            if post and post.get('success') is True and str(post.get('status', '')).lower() == 'matched':
                token_id = str(runner.get('token_id') or (up_t if side == 'UP' else dn_t))
                shares = float(post.get('takingAmount') or 0)
                cost = float(post.get('makingAmount') or 0)
                entry_price = float(runner.get('entry_price') or trigger_price)
                opened = {
                    'opened_at': ts_utc(),
                    'market_slug': slug,
                    'market_end_iso': end_iso,
                    'side': side,
                    'token_id': token_id,
                    'entry_price': entry_price,
                    'shares': shares,
                    'cost_usdc': cost,
                    'open_order_id': post.get('orderID'),
                    'open_tx': (post.get('transactionsHashes') or [None])[0],
                    'bucket': current_bucket,
                }
                report['open_raw'] = out[-4000:]
                
                # Register ticket in state tracker
                ticket = TicketState(
                    bucket=current_bucket,
                    market_slug=slug,
                    opened_at=time.time(),
                    side=side,
                    entry_price=entry_price,
                    shares=shares,
                    cost_usdc=cost,
                    token_id=token_id,
                    status='open'
                )
                state_tracker.open_ticket(ticket)
                
                break
            else:
                report['last_open_try'] = out[-2000:]
                if twap_entry_net_edge is not None:
                    fill_attempts[current_bucket] = twap_entry_net_edge
        except Exception as e:
            report['attempts'].append({'ts': ts_utc(), 'status': 'error', 'error': str(e)})
        time.sleep(args.poll_sec)

    if not opened:
        report['finished_at'] = ts_utc()
        attach_maker_pilot_summary(report, maker_engine)
        if maker_on:
            report['result'] = 'maker_pilot_shadow_complete'
        else:
            report['result'] = 'no_entry_timeout'
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    report['opened'] = opened

    # monitor after open: stop-loss or time exit
    end_ts = None
    try:
        end_ts = dt.datetime.fromisoformat(opened['market_end_iso'].replace('Z', '+00:00')).timestamp()
    except Exception:
        end_ts = time.time() + 300

    # Exit monitoring: hold-to-redeem EV or legacy stop-loss
    if args.hold_to_redeem and use_fair_value and fair_calc is not None:
        # Hold-to-redeem mode: only exit if selling is better EV than holding
        report['exit_mode'] = 'hold_to_redeem_ev'
        close_reason = None
        held_to_redeem = False
        
        while True:
            now = time.time()
            if now >= (end_ts - args.exit_before_sec):
                close_reason = f'time_exit_{args.exit_before_sec}s_before_end'
                # If exiting very close to settlement, mark as held to redeem
                if args.exit_before_sec <= 20:
                    held_to_redeem = True
                break
            
            # Get current fair value and CLOB bid
            try:
                sec_left = max(0, end_ts - now)
                allow_fallback = not args.execute
                fair_value = fair_calc.calculate_fair_value(
                    slug, 
                    sec_left, 
                    args.btc_daily_vol_pct,
                    allow_fallback=allow_fallback
                )
                if fair_value is None:
                    time.sleep(args.poll_sec)
                    continue
                
                fair_p_win = fair_value.p_up if opened['side'] == 'UP' else fair_value.p_down
                current_bid = clob_best_bid(opened['token_id'])
                
                if current_bid is None:
                    time.sleep(args.poll_sec)
                    continue
                
                hold_ev = calculate_hold_ev(
                    fair_p_win,
                    current_bid,
                    shares=opened['shares']
                )
                
                report['last_hold_ev_check'] = {
                    'ts': ts_utc(),
                    'fair_p_win': fair_p_win,
                    'current_bid': current_bid,
                    'hold_ev': hold_ev['hold_ev'],
                    'sell_ev': hold_ev['sell_ev'],
                    'recommendation': hold_ev['recommendation'],
                }
                
                # Exit only if sell EV > hold EV
                if hold_ev['recommendation'] == 'sell':
                    close_reason = 'sell_ev_exceeds_hold_ev'
                    break
            except Exception as e:
                report['hold_ev_check_error'] = str(e)
            
            time.sleep(args.poll_sec)
    else:
        # Legacy stop-loss mode
        report['exit_mode'] = 'legacy_stop_loss'
        sl_price = opened['entry_price'] * (1.0 - args.stop_loss_pct)
        report['stop_loss_price'] = sl_price
        close_reason = None
        
        while True:
            now = time.time()
            if now >= (end_ts - args.exit_before_sec):
                close_reason = f'time_exit_{args.exit_before_sec}s_before_end'
                break

            # Use CLOB bid (sellable price) for stop-loss, not Gamma mid
            try:
                side_px = clob_best_bid(opened['token_id'])
            except Exception:
                side_px = get_side_price_from_slug(opened['market_slug'], opened['side'])
            
            report['last_side_price'] = side_px
            report['last_check_at'] = ts_utc()
            if side_px is not None and side_px <= sl_price:
                close_reason = f"stop_loss_{int(args.stop_loss_pct * 100)}pct"
                break
            time.sleep(args.poll_sec)

    close_debug: list[dict[str, Any]] = []
    close_obj: dict[str, Any] = {}
    out = ''
    fallback_used = None
    force_close_used = None
    client = auth_clob_client()

    for i in range(max(1, int(args.close_retry_max))):
        out, objs = run_close(
            args.repo,
            opened['market_slug'],
            opened['token_id'],
            opened['shares'],
            args.execute,
            close_order_type='FAK',
        )
        close_obj = objs[-1] if objs else {}
        post = close_obj.get('order_post_result') or {}
        status = str(post.get('status') or '').lower()
        skipped = str(close_obj.get('close_skipped') or '')
        close_debug.append({
            'ts': ts_utc(),
            'attempt': i + 1,
            'order_type': 'FAK',
            'status': status,
            'close_skipped': skipped,
        })
        if post.get('success') is True and status == 'matched':
            break

        # common transient path right after open: token balance not yet visible
        if skipped == 'zero_effective_shares':
            time.sleep(float(args.close_retry_delay_sec))
            continue

        # fallback: if FAK has no instant match, try a GTC limit close near current side price
        txt = ((out or '') + '\n' + json.dumps(close_obj, ensure_ascii=False)).lower()
        if 'no orders found to match with fak order' in txt:
            px = get_side_price_from_slug(opened['market_slug'], opened['side'])
            if px is None:
                px = report.get('last_side_price')
            if px is None:
                px = opened['entry_price']
            bb = None
            try:
                bb = clob_best_bid(opened['token_id'])
            except Exception:
                bb = None
            limit_px = max(0.01, min(0.99, float((bb - 0.01) if bb is not None else px)))
            fallback_used = {'type': 'GTC_LIMIT', 'price': limit_px}
            out2, objs2 = run_close(
                args.repo,
                opened['market_slug'],
                opened['token_id'],
                opened['shares'],
                args.execute,
                close_order_type='GTC',
                close_limit_price=limit_px,
            )
            close_obj2 = objs2[-1] if objs2 else {}
            post2 = close_obj2.get('order_post_result') or {}
            status2 = str(post2.get('status') or '').lower()
            close_debug.append({
                'ts': ts_utc(),
                'attempt': i + 1,
                'order_type': 'GTC',
                'status': status2,
                'close_skipped': str(close_obj2.get('close_skipped') or ''),
                'limit_price': limit_px,
            })
            close_obj = close_obj2
            out = out2
            if post2.get('success') is True and status2 == 'matched':
                break

            # If GTC is accepted but still live, force-close flow: poll status, cancel, repost aggressive.
            if post2.get('success') is True and status2 == 'live':
                oid2 = str(post2.get('orderID') or '')
                st_upd, ord_upd = poll_order_status(client, oid2, wait_sec=min(8.0, max(2.0, float(args.close_retry_delay_sec) * 2)), step_sec=1.0)
                close_debug.append({
                    'ts': ts_utc(),
                    'attempt': i + 1,
                    'order_type': 'GTC_POLL',
                    'status': st_upd.lower() if st_upd else '',
                    'order_id': oid2,
                })
                if st_upd == 'MATCHED':
                    post2['status'] = 'matched'
                    close_obj['order_post_result'] = post2
                    break

                cancel_info = cancel_token_orders(client, opened['token_id'])
                bb2 = None
                try:
                    bb2 = clob_best_bid(opened['token_id'])
                except Exception:
                    bb2 = None
                force_px = max(0.01, min(0.99, float((bb2 - 0.02) if bb2 is not None else 0.01)))
                force_close_used = {
                    'type': 'FORCE_GTC_LIMIT',
                    'price': force_px,
                    'cancel_info': cancel_info,
                }
                out3, objs3 = run_close(
                    args.repo,
                    opened['market_slug'],
                    opened['token_id'],
                    opened['shares'],
                    args.execute,
                    close_order_type='GTC',
                    close_limit_price=force_px,
                )
                close_obj3 = objs3[-1] if objs3 else {}
                post3 = close_obj3.get('order_post_result') or {}
                status3 = str(post3.get('status') or '').lower()
                close_debug.append({
                    'ts': ts_utc(),
                    'attempt': i + 1,
                    'order_type': 'FORCE_GTC',
                    'status': status3,
                    'close_skipped': str(close_obj3.get('close_skipped') or ''),
                    'limit_price': force_px,
                })
                close_obj = close_obj3
                out = out3
                if post3.get('success') is True and status3 == 'matched':
                    break

        time.sleep(float(args.close_retry_delay_sec))

    post = close_obj.get('order_post_result') or {}
    post_status = str(post.get('status') or '').lower()
    close_usdc = float(post.get('takingAmount') or 0)
    closed = {
        'close_reason': close_reason,
        'closed_at': ts_utc(),
        'close_success': bool(post.get('success') is True and (post_status == 'matched' or close_usdc > 0)),
        'close_status': post.get('status'),
        'close_order_id': post.get('orderID'),
        'close_tx': (post.get('transactionsHashes') or [None])[0],
        'close_shares': float(post.get('makingAmount') or 0),
        'close_usdc': close_usdc,
        'close_skipped': close_obj.get('close_skipped'),
    }
    report['close_debug'] = close_debug
    if fallback_used:
        report['close_fallback'] = fallback_used
    if force_close_used:
        report['close_force'] = force_close_used
    report['close_raw'] = out[-4000:]
    report['closed'] = closed

    pnl = None
    pnl_note = None
    if closed['close_usdc']:
        # Gross PnL (without fees)
        pnl = round(closed['close_usdc'] - opened['cost_usdc'], 6)
        pnl_note = "IMPORTANT: PnL excludes Polymarket taker fees (~2% on crypto markets at 70c entry). Actual net PnL is lower."
        
        # Estimate fees for informational purposes (Polymarket crypto: fee = shares * 0.07 * p * (1-p))
        entry_price = opened['entry_price']
        shares = opened['shares']
        fees = estimate_session_fees(shares, entry_price, closed['close_usdc'])
        net_pnl_estimate = round(pnl - fees['total_fee_usdc'], 6)
        
        report['fee_estimates'] = {
            'entry_fee_usdc': fees['entry_fee_usdc'],
            'close_fee_usdc': fees['close_fee_usdc'],
            'total_fee_usdc': fees['total_fee_usdc'],
            'note': 'Estimated using Polymarket crypto fee formula: shares * 0.07 * p * (1-p)',
        }
        report['net_pnl_estimate_usdc'] = net_pnl_estimate
    
    report['realized_cashflow_pnl_usdc'] = pnl
    report['pnl_note'] = pnl_note
    report['finished_at'] = ts_utc()
    
    # Report result accurately: done only if close succeeded, otherwise incomplete/failed
    if closed['close_success']:
        report['result'] = 'done'
        close_status = 'closed'
    elif closed['close_skipped']:
        report['result'] = 'incomplete_close_skipped'
        close_status = 'failed'
    else:
        report['result'] = 'incomplete_close_failed'
        close_status = 'failed'
    
    # Update state tracker with close
    pnl_for_tracker = pnl if pnl is not None else 0.0
    state_tracker.close_ticket(opened['bucket'], pnl_for_tracker, close_status)
    
    # Log TWAP settlement if position was held to redeem
    if args.hold_to_redeem and use_fair_value and fair_calc is not None:
        window_open_twap = fair_calc.get_window_open_twap(opened['market_slug'])
        if window_open_twap is not None and held_to_redeem:
            try:
                allow_fallback = not args.execute
                settle_result = log_twap_settle(
                    opened['market_slug'],
                    window_open_twap,
                    opened['side'],
                    allow_fallback=allow_fallback
                )
                report['twap_settlement'] = settle_result
            except Exception as e:
                report['twap_settlement_error'] = str(e)
    
    # Daily summary at end
    report['daily_summary_end'] = state_tracker.get_daily_summary()

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
