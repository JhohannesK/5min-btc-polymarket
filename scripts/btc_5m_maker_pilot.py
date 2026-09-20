#!/usr/bin/env python3
"""
W6 post-only / maker pilot.

Shadow-first: simulate GTD + post-only quotes against the CLOB book without
sending live maker orders. Live posting stays stubbed behind an explicit
config flag that still requires the execute CLI flag, and is off by default.

Cancel rules:
  - TWAP fair flip (quoted side is no longer favored)
  - spread widen past threshold
  - GTD TTL expiry

Metrics:
  - rebate estimate (Polymarket crypto makers pay 0; estimate = taker fee saved
    plus optional rebate_bps)
  - adverse selection / mid-move after the quote
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Optional


MAKER_PILOT_TAG = "MAKER_PILOT"

# Polymarket crypto: makers pay 0. Taker fee is shares * 0.07 * p * (1-p).
DEFAULT_TAKER_FEE_RATE = 0.07
DEFAULT_MAKER_FEE_RATE = 0.0

LIVE_STUB_REASON = "live_maker_path_stubbed_until_rtds_and_creds_ready"


@dataclass
class MakerPilotConfig:
    """Yaml-backed maker pilot knobs. Live posting is never on by default."""

    enabled: bool = False
    shadow: bool = True
    live_execute: bool = False
    order_type: str = "GTD"
    post_only: bool = True
    gtd_ttl_sec: float = 15.0
    cancel_on_fair_flip: bool = True
    cancel_spread_widen_abs: float = 0.03
    tick_size: float = 0.01
    quote_improve_ticks: int = 0
    rebate_bps: float = 0.0
    taker_fee_rate: float = DEFAULT_TAKER_FEE_RATE
    maker_fee_rate: float = DEFAULT_MAKER_FEE_RATE
    max_quotes_per_bucket: int = 4
    paper_window_required: bool = True

    @classmethod
    def from_mapping(cls, raw: Optional[dict[str, Any]]) -> "MakerPilotConfig":
        if not raw:
            return cls()
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def as_public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["live_path"] = "stubbed"
        d["mode"] = self.effective_mode()
        return d

    def effective_mode(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.shadow or not self.live_execute:
            return "shadow"
        return "live_requested_but_stubbed"


@dataclass
class SideBook:
    side: str
    token_id: str = ""
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    bid_size: float = 0.0
    ask_size: float = 0.0

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return max(0.0, float(self.best_ask) - float(self.best_bid))

    @property
    def mid(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (float(self.best_bid) + float(self.best_ask)) / 2.0


@dataclass
class ShadowQuote:
    quote_id: str
    side: str
    token_id: str
    price: float
    size_shares: float
    order_type: str
    post_only: bool
    posted_at: float
    gtd_expire_at: Optional[float]
    quote_mid: float
    quote_spread: float
    fair_signal_at_post: str
    status: str = "live"
    cancel_reason: Optional[str] = None
    filled_at: Optional[float] = None
    closed_at: Optional[float] = None
    mid_at_close: Optional[float] = None
    mid_move_pp: Optional[float] = None
    adverse_usd: Optional[float] = None
    rebate_estimate_usd: Optional[float] = None
    taker_fee_saved_usd: Optional[float] = None


@dataclass
class CancelDecision:
    cancel: bool
    reason: Optional[str] = None


def favored_side_from_signal(edge_signal: str) -> Optional[str]:
    """Map TWAP FairValue.edge_signal to UP/DOWN. Neutral/unknown -> None."""
    sig = str(edge_signal or "").strip().lower()
    if sig in ("up_favored", "up"):
        return "UP"
    if sig in ("down_favored", "down"):
        return "DOWN"
    return None


def post_only_buy_price(
    best_bid: Optional[float],
    best_ask: Optional[float],
    tick_size: float = 0.01,
    improve_ticks: int = 0,
) -> Optional[float]:
    """
    Resting buy price that does not cross the ask (post-only).
    Default: join the bid. improve_ticks steps into the spread without taking.
    """
    if best_bid is None or best_ask is None:
        return None
    tick = float(tick_size) if tick_size > 0 else 0.01
    bid = float(best_bid)
    ask = float(best_ask)
    if ask <= 0 or bid < 0:
        return None

    px = bid + max(0, int(improve_ticks)) * tick
    cap = ask - tick
    if cap <= 0:
        return None
    px = min(px, cap)
    ticks = round(px / tick)
    px = round(ticks * tick, 8)
    px = max(tick, min(px, 1.0 - tick))
    if px >= ask:
        return None
    return px


def taker_fee_usd(shares: float, price: float, fee_rate: float = DEFAULT_TAKER_FEE_RATE) -> float:
    p = min(max(float(price), 0.0), 1.0)
    return float(shares) * float(fee_rate) * p * (1.0 - p)


def rebate_estimate_usd(
    shares: float,
    price: float,
    rebate_bps: float = 0.0,
    taker_fee_rate: float = DEFAULT_TAKER_FEE_RATE,
    maker_fee_rate: float = DEFAULT_MAKER_FEE_RATE,
) -> tuple[float, float]:
    """
    Return (rebate_estimate_usd, taker_fee_saved_usd).

    Crypto makers pay 0, so the economic rebate of quoting is the taker fee
    avoided, plus any configured rebate_bps, minus maker_fee_rate.
    """
    saved = taker_fee_usd(shares, price, taker_fee_rate)
    rebate_from_bps = float(shares) * float(price) * (float(rebate_bps) / 10000.0)
    maker_fee = float(shares) * float(price) * float(maker_fee_rate)
    return rebate_from_bps + saved - maker_fee, saved


def adverse_selection_usd(side: str, shares: float, mid_at_quote: float, mid_now: float) -> float:
    """
    Buy quote: mid drop after posting is adverse (positive).
    (We only post buys of the cheap/favored token.)
    """
    move = float(mid_now) - float(mid_at_quote)
    if str(side).upper() in ("UP", "DOWN", "BUY"):
        return float(shares) * (-move)
    return float(shares) * move


def evaluate_cancel_rules(
    quote: ShadowQuote,
    book: SideBook,
    fair_signal: str,
    now: float,
    config: MakerPilotConfig,
) -> CancelDecision:
    """Cancel on TWAP fair flip, spread widen, or GTD expiry."""
    if quote.status != "live":
        return CancelDecision(False, None)

    if quote.gtd_expire_at is not None and now >= quote.gtd_expire_at:
        return CancelDecision(True, "gtd_expired")

    if config.cancel_on_fair_flip:
        favored = favored_side_from_signal(fair_signal)
        if favored != quote.side:
            return CancelDecision(True, "fair_flip")

    spread = book.spread
    if spread is not None and spread > float(config.cancel_spread_widen_abs):
        return CancelDecision(True, "spread_widen")

    return CancelDecision(False, None)


def book_would_fill_buy(quote: ShadowQuote, book: SideBook) -> bool:
    """Resting buy would fill if the ask touches or crosses our price."""
    if quote.status != "live" or book.best_ask is None:
        return False
    return float(book.best_ask) <= float(quote.price)


def live_maker_allowed(
    config: MakerPilotConfig,
    execute: bool,
    rtds_ready: bool = False,
    creds_ready: bool = False,
) -> tuple[bool, str]:
    """
    Live maker posts require enabled + not shadow + live_execute + execute
    flag + RTDS + creds. This PR always stubs the live path even if those
    are set, so --execute is never unlocked by W6.
    """
    if not config.enabled:
        return False, "maker_pilot_disabled"
    if config.shadow:
        return False, "shadow_mode"
    if not config.live_execute:
        return False, "live_execute_flag_off"
    if not execute:
        return False, "requires_execute_flag"
    if not rtds_ready:
        return False, "rtds_not_ready"
    if not creds_ready:
        return False, "creds_not_ready"
    return False, LIVE_STUB_REASON


def emit_maker_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event)
    payload.setdefault("tag", MAKER_PILOT_TAG)
    print(f"[{MAKER_PILOT_TAG}] {json.dumps(payload, separators=(',', ':'), ensure_ascii=False)}")
    return payload


class MakerPilotEngine:
    """Stateful shadow quoter. Never sends live maker orders in this PR."""

    def __init__(self, config: Optional[MakerPilotConfig] = None):
        self.config = config or MakerPilotConfig()
        self.active: Optional[ShadowQuote] = None
        self.quotes: list[ShadowQuote] = []
        self.events: list[dict[str, Any]] = []
        self._seq = 0
        self._bucket: Optional[int] = None
        self._quotes_this_bucket = 0

    def on_tick(
        self,
        now: float,
        fair_signal: str,
        up_book: SideBook,
        down_book: SideBook,
        stake_usd: float,
        execute: bool = False,
        rtds_ready: bool = False,
        creds_ready: bool = False,
        bucket: Optional[int] = None,
        market_slug: str = "",
        seconds_left: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        produced: list[dict[str, Any]] = []
        if not self.config.enabled:
            return produced

        if bucket is not None and self._bucket is not None and bucket != self._bucket:
            if self.active and self.active.status == "live":
                book = up_book if self.active.side == "UP" else down_book
                produced.append(self._close_quote(self.active, book, now, "bucket_roll"))
            self._quotes_this_bucket = 0
        if bucket is not None:
            self._bucket = bucket

        closed_this_tick = False
        if self.active and self.active.status == "live":
            book = up_book if self.active.side == "UP" else down_book
            managed = self._manage_live_quote(self.active, book, fair_signal, now)
            produced.extend(managed)
            closed_this_tick = any(
                e.get("event") in ("would_fill", "would_cancel") for e in managed
            )

        # Do not immediately re-quote on the same tick as a fill/cancel.
        if not closed_this_tick and (self.active is None or self.active.status != "live"):
            posted = self._maybe_post(
                now=now,
                fair_signal=fair_signal,
                up_book=up_book,
                down_book=down_book,
                stake_usd=stake_usd,
                execute=execute,
                rtds_ready=rtds_ready,
                creds_ready=creds_ready,
                market_slug=market_slug,
                seconds_left=seconds_left,
                bucket=bucket,
            )
            if posted is not None:
                produced.append(posted)

        self.events.extend(produced)
        return produced

    def _maybe_post(
        self,
        now: float,
        fair_signal: str,
        up_book: SideBook,
        down_book: SideBook,
        stake_usd: float,
        execute: bool,
        rtds_ready: bool,
        creds_ready: bool,
        market_slug: str,
        seconds_left: Optional[float],
        bucket: Optional[int],
    ) -> Optional[dict[str, Any]]:
        if self._quotes_this_bucket >= int(self.config.max_quotes_per_bucket):
            return None

        side = favored_side_from_signal(fair_signal)
        if side is None:
            return None

        book = up_book if side == "UP" else down_book
        price = post_only_buy_price(
            book.best_bid,
            book.best_ask,
            tick_size=self.config.tick_size,
            improve_ticks=self.config.quote_improve_ticks,
        )
        if price is None:
            return emit_maker_event({
                "event": "post_skip",
                "reason": "no_post_only_price",
                "side": side,
                "best_bid": book.best_bid,
                "best_ask": book.best_ask,
                "market_slug": market_slug,
            })

        if book.mid is None or book.spread is None:
            return None

        shares = float(stake_usd) / price if price > 0 else 0.0
        if shares <= 0:
            return None

        _, live_reason = live_maker_allowed(
            self.config, execute, rtds_ready=rtds_ready, creds_ready=creds_ready
        )
        mode = self.config.effective_mode()

        self._seq += 1
        qid = f"sh-{self._seq}"
        ttl = float(self.config.gtd_ttl_sec)
        quote = ShadowQuote(
            quote_id=qid,
            side=side,
            token_id=book.token_id,
            price=price,
            size_shares=shares,
            order_type=self.config.order_type,
            post_only=self.config.post_only,
            posted_at=now,
            gtd_expire_at=(now + ttl) if ttl > 0 else None,
            quote_mid=book.mid,
            quote_spread=book.spread,
            fair_signal_at_post=fair_signal,
            status="live",
        )
        rebate, saved = rebate_estimate_usd(
            shares,
            price,
            rebate_bps=self.config.rebate_bps,
            taker_fee_rate=self.config.taker_fee_rate,
            maker_fee_rate=self.config.maker_fee_rate,
        )
        quote.rebate_estimate_usd = rebate
        quote.taker_fee_saved_usd = saved

        self.active = quote
        self.quotes.append(quote)
        self._quotes_this_bucket += 1

        return emit_maker_event({
            "event": "shadow_post",
            "quote_id": qid,
            "mode": mode,
            "side": side,
            "token_id": book.token_id,
            "price": price,
            "size_shares": round(shares, 8),
            "order_type": self.config.order_type,
            "post_only": self.config.post_only,
            "gtd_ttl_sec": self.config.gtd_ttl_sec,
            "gtd_expire_at": quote.gtd_expire_at,
            "best_bid": book.best_bid,
            "best_ask": book.best_ask,
            "mid": book.mid,
            "spread": book.spread,
            "fair_signal": fair_signal,
            "rebate_estimate_usd": round(rebate, 8),
            "taker_fee_saved_usd": round(saved, 8),
            "live_submit": False,
            "live_reason": live_reason,
            "market_slug": market_slug,
            "seconds_left": seconds_left,
            "bucket": bucket,
        })

    def _manage_live_quote(
        self,
        quote: ShadowQuote,
        book: SideBook,
        fair_signal: str,
        now: float,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []

        if book_would_fill_buy(quote, book):
            out.append(self._fill_quote(quote, book, now, fair_signal))
            return out

        decision = evaluate_cancel_rules(quote, book, fair_signal, now, self.config)
        if decision.cancel:
            out.append(self._close_quote(quote, book, now, decision.reason or "cancel"))
        return out

    def _fill_quote(
        self,
        quote: ShadowQuote,
        book: SideBook,
        now: float,
        fair_signal: str,
    ) -> dict[str, Any]:
        quote.status = "would_fill"
        quote.filled_at = now
        quote.closed_at = now
        mid_now = book.mid if book.mid is not None else quote.quote_mid
        quote.mid_at_close = mid_now
        quote.mid_move_pp = mid_now - quote.quote_mid
        quote.adverse_usd = adverse_selection_usd(
            quote.side, quote.size_shares, quote.quote_mid, mid_now
        )
        return emit_maker_event({
            "event": "would_fill",
            "quote_id": quote.quote_id,
            "side": quote.side,
            "price": quote.price,
            "size_shares": quote.size_shares,
            "best_bid": book.best_bid,
            "best_ask": book.best_ask,
            "mid_at_quote": quote.quote_mid,
            "mid_at_fill": mid_now,
            "mid_move_pp": quote.mid_move_pp,
            "adverse_usd": quote.adverse_usd,
            "rebate_estimate_usd": quote.rebate_estimate_usd,
            "taker_fee_saved_usd": quote.taker_fee_saved_usd,
            "fair_signal_at_post": quote.fair_signal_at_post,
            "fair_signal_at_fill": fair_signal,
            "live_fill": False,
        })

    def _close_quote(
        self,
        quote: ShadowQuote,
        book: SideBook,
        now: float,
        reason: str,
    ) -> dict[str, Any]:
        quote.status = "cancelled"
        quote.cancel_reason = reason
        quote.closed_at = now
        mid_now = book.mid if book.mid is not None else quote.quote_mid
        quote.mid_at_close = mid_now
        quote.mid_move_pp = mid_now - quote.quote_mid
        quote.adverse_usd = adverse_selection_usd(
            quote.side, quote.size_shares, quote.quote_mid, mid_now
        )
        return emit_maker_event({
            "event": "would_cancel",
            "quote_id": quote.quote_id,
            "reason": reason,
            "side": quote.side,
            "price": quote.price,
            "size_shares": quote.size_shares,
            "best_bid": book.best_bid,
            "best_ask": book.best_ask,
            "spread": book.spread,
            "mid_at_quote": quote.quote_mid,
            "mid_at_cancel": mid_now,
            "mid_move_pp": quote.mid_move_pp,
            "adverse_usd": quote.adverse_usd,
            "rebate_estimate_usd": quote.rebate_estimate_usd,
            "taker_fee_saved_usd": quote.taker_fee_saved_usd,
            "fair_signal_at_post": quote.fair_signal_at_post,
            "live_cancel": False,
        })


def summarize_maker_pilot(events: list[dict[str, Any]], quotes: Optional[list[ShadowQuote]] = None) -> dict[str, Any]:
    """Aggregate shadow fills/cancels and rebate vs adverse-selection."""
    posts = [e for e in events if e.get("event") == "shadow_post"]
    fills = [e for e in events if e.get("event") == "would_fill"]
    cancels = [e for e in events if e.get("event") == "would_cancel"]

    def _sum(rows: list[dict[str, Any]], key: str) -> float:
        return float(sum(float(r[key]) for r in rows if r.get(key) is not None))

    closed = fills + cancels
    rebate = _sum(posts, "rebate_estimate_usd")
    if quotes:
        rebate = float(sum(q.rebate_estimate_usd or 0.0 for q in quotes))
    adverse = _sum(closed, "adverse_usd")
    mid_moves = [float(e["mid_move_pp"]) for e in closed if e.get("mid_move_pp") is not None]
    reasons: dict[str, int] = {}
    for c in cancels:
        r = str(c.get("reason") or "unknown")
        reasons[r] = reasons.get(r, 0) + 1

    return {
        "mode": "shadow",
        "live_path": "stubbed",
        "quotes_posted": len(posts),
        "would_be_fills": len(fills),
        "would_be_cancels": len(cancels),
        "cancels_fair_flip": reasons.get("fair_flip", 0),
        "cancels_spread_widen": reasons.get("spread_widen", 0),
        "cancels_gtd_expired": reasons.get("gtd_expired", 0),
        "cancel_reasons": reasons,
        "rebate_estimate_usd": round(rebate, 8),
        "adverse_selection_usd": round(adverse, 8),
        "net_rebate_minus_adverse_usd": round(rebate - adverse, 8),
        "mean_mid_move_pp": round(sum(mid_moves) / len(mid_moves), 8) if mid_moves else None,
        "live_posts_attempted": 0,
        "paper_window_required": True,
    }


def format_maker_pilot_report(summary: dict[str, Any]) -> str:
    lines = [
        "W6 maker/post-only shadow report",
        f"  mode={summary.get('mode')} live_path={summary.get('live_path')}",
        f"  posted={summary.get('quotes_posted')} would_fill={summary.get('would_be_fills')} "
        f"would_cancel={summary.get('would_be_cancels')}",
        f"  cancel fair_flip={summary.get('cancels_fair_flip')} "
        f"spread_widen={summary.get('cancels_spread_widen')} "
        f"gtd_expired={summary.get('cancels_gtd_expired')}",
        f"  rebate_est_usd={summary.get('rebate_estimate_usd')} "
        f"adverse_usd={summary.get('adverse_selection_usd')} "
        f"net={summary.get('net_rebate_minus_adverse_usd')}",
        f"  mean_mid_move_pp={summary.get('mean_mid_move_pp')}",
        "  paper window required before any live pilot",
    ]
    return "\n".join(lines)


def default_maker_pilot_mapping() -> dict[str, Any]:
    return asdict(MakerPilotConfig())
