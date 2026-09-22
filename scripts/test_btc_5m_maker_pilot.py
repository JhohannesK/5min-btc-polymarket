#!/usr/bin/env python3
"""
W6 unit tests: cancel-on-flip, spread widen, shadow logging.
No network. No execute / live posting.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from btc_5m_maker_pilot import (
    LIVE_STUB_REASON,
    MakerPilotConfig,
    MakerPilotEngine,
    ShadowQuote,
    SideBook,
    adverse_selection_usd,
    book_would_fill_buy,
    default_maker_pilot_mapping,
    evaluate_cancel_rules,
    favored_side_from_signal,
    format_maker_pilot_report,
    live_maker_allowed,
    post_only_buy_price,
    rebate_estimate_usd,
    summarize_maker_pilot,
    taker_fee_usd,
)


def _book(side: str, bid: float, ask: float, token_id: str = "tok") -> SideBook:
    return SideBook(side=side, token_id=token_id, best_bid=bid, best_ask=ask)


def _quote(**kwargs) -> ShadowQuote:
    base = dict(
        quote_id="sh-1",
        side="UP",
        token_id="tok",
        price=0.44,
        size_shares=10.0,
        order_type="GTD",
        post_only=True,
        posted_at=1_000.0,
        gtd_expire_at=1_015.0,
        quote_mid=0.445,
        quote_spread=0.01,
        fair_signal_at_post="up_favored",
        status="live",
    )
    base.update(kwargs)
    return ShadowQuote(**base)


class FavoredSideTests(unittest.TestCase):
    def test_should_map_up_and_down_signals(self):
        self.assertEqual(favored_side_from_signal("up_favored"), "UP")
        self.assertEqual(favored_side_from_signal("down_favored"), "DOWN")
        self.assertIsNone(favored_side_from_signal("neutral"))
        self.assertIsNone(favored_side_from_signal("unavailable"))

    def test_should_accept_short_aliases_and_ignore_blank(self):
        self.assertEqual(favored_side_from_signal("up"), "UP")
        self.assertEqual(favored_side_from_signal("DOWN"), "DOWN")
        self.assertIsNone(favored_side_from_signal(""))
        self.assertIsNone(favored_side_from_signal(None))  # type: ignore[arg-type]


class PostOnlyPriceTests(unittest.TestCase):
    def test_should_join_bid_without_crossing_ask(self):
        px = post_only_buy_price(0.44, 0.46, tick_size=0.01)
        self.assertEqual(px, 0.44)
        self.assertLess(px, 0.46)

    def test_should_return_none_when_book_is_missing(self):
        self.assertIsNone(post_only_buy_price(None, 0.46, tick_size=0.01))
        self.assertIsNone(post_only_buy_price(0.44, None, tick_size=0.01))

    def test_should_improve_into_spread_without_taking(self):
        px = post_only_buy_price(0.40, 0.45, tick_size=0.01, improve_ticks=2)
        self.assertEqual(px, 0.42)
        self.assertLess(px, 0.45)


class CancelOnFlipTests(unittest.TestCase):
    def test_should_cancel_when_twap_fair_flips_away_from_quote_side(self):
        quote = _quote(side="UP")
        book = _book("UP", 0.44, 0.45)
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        d = evaluate_cancel_rules(quote, book, "down_favored", now=1_005.0, config=cfg)
        self.assertTrue(d.cancel)
        self.assertEqual(d.reason, "fair_flip")

    def test_should_cancel_when_signal_goes_neutral(self):
        quote = _quote(side="DOWN")
        book = _book("DOWN", 0.52, 0.53)
        cfg = MakerPilotConfig(enabled=True)
        d = evaluate_cancel_rules(quote, book, "neutral", now=1_005.0, config=cfg)
        self.assertTrue(d.cancel)
        self.assertEqual(d.reason, "fair_flip")

    def test_should_not_cancel_when_side_still_favored_and_spread_tight(self):
        quote = _quote(side="UP")
        book = _book("UP", 0.44, 0.45)
        cfg = MakerPilotConfig(enabled=True, cancel_spread_widen_abs=0.03)
        d = evaluate_cancel_rules(quote, book, "up_favored", now=1_005.0, config=cfg)
        self.assertFalse(d.cancel)
        self.assertIsNone(d.reason)


class CancelOnSpreadWidenTests(unittest.TestCase):
    def test_should_cancel_when_spread_widens_past_threshold(self):
        quote = _quote(side="UP", quote_spread=0.01)
        book = _book("UP", 0.40, 0.48)  # 8c > 3c
        cfg = MakerPilotConfig(enabled=True, cancel_spread_widen_abs=0.03)
        d = evaluate_cancel_rules(quote, book, "up_favored", now=1_005.0, config=cfg)
        self.assertTrue(d.cancel)
        self.assertEqual(d.reason, "spread_widen")

    def test_should_not_cancel_when_spread_equals_threshold(self):
        quote = _quote(side="UP")
        book = _book("UP", 0.40, 0.43)  # 3c == threshold, not past
        cfg = MakerPilotConfig(enabled=True, cancel_spread_widen_abs=0.03)
        d = evaluate_cancel_rules(quote, book, "up_favored", now=1_005.0, config=cfg)
        self.assertFalse(d.cancel)

    def test_should_expire_gtd_before_spread_check_when_ttl_elapsed(self):
        quote = _quote(gtd_expire_at=1_010.0)
        book = _book("UP", 0.10, 0.90)
        cfg = MakerPilotConfig(enabled=True, cancel_spread_widen_abs=0.03)
        d = evaluate_cancel_rules(quote, book, "up_favored", now=1_010.0, config=cfg)
        self.assertTrue(d.cancel)
        self.assertEqual(d.reason, "gtd_expired")

    def test_should_ignore_cancel_rules_when_quote_is_already_closed(self):
        quote = _quote(status="cancelled")
        book = _book("UP", 0.10, 0.90)
        cfg = MakerPilotConfig(enabled=True, cancel_spread_widen_abs=0.03)
        d = evaluate_cancel_rules(quote, book, "down_favored", now=9_999.0, config=cfg)
        self.assertFalse(d.cancel)
        self.assertIsNone(d.reason)


class WouldFillTests(unittest.TestCase):
    def test_should_fill_when_ask_crosses_resting_buy(self):
        quote = _quote(price=0.44)
        book = _book("UP", 0.43, 0.44)
        self.assertTrue(book_would_fill_buy(quote, book))

    def test_should_not_fill_when_ask_still_above_bid(self):
        quote = _quote(price=0.44)
        book = _book("UP", 0.44, 0.46)
        self.assertFalse(book_would_fill_buy(quote, book))

    def test_should_not_fill_when_quote_is_not_live_or_ask_is_missing(self):
        live = _quote(price=0.44)
        dead = _quote(price=0.44, status="cancelled")
        self.assertFalse(book_would_fill_buy(dead, _book("UP", 0.43, 0.44)))
        self.assertFalse(book_would_fill_buy(live, _book("UP", 0.43, None)))


class RebateAndAdverseTests(unittest.TestCase):
    def test_should_estimate_rebate_as_taker_fee_saved_when_makers_pay_zero(self):
        rebate, saved = rebate_estimate_usd(100.0, 0.50, rebate_bps=0.0)
        self.assertAlmostEqual(saved, 1.75, places=6)
        self.assertAlmostEqual(rebate, 1.75, places=6)

    def test_should_add_rebate_bps_and_subtract_maker_fee(self):
        rebate, saved = rebate_estimate_usd(
            100.0, 0.50, rebate_bps=10.0, maker_fee_rate=0.001
        )
        self.assertAlmostEqual(saved, taker_fee_usd(100.0, 0.50), places=8)
        self.assertAlmostEqual(saved, 1.75, places=6)
        # 10 bps of 50 USDC notional = 0.05; maker fee 0.1% of 50 = 0.05
        self.assertAlmostEqual(rebate, 1.75 + 0.05 - 0.05, places=6)

    def test_should_treat_mid_drop_after_buy_quote_as_adverse(self):
        adv = adverse_selection_usd("UP", 10.0, mid_at_quote=0.50, mid_now=0.48)
        self.assertAlmostEqual(adv, 0.20, places=6)

    def test_should_treat_down_token_mid_drop_as_adverse_too(self):
        adv = adverse_selection_usd("DOWN", 10.0, mid_at_quote=0.60, mid_now=0.55)
        self.assertAlmostEqual(adv, 0.50, places=6)


class LiveGateTests(unittest.TestCase):
    def test_should_stay_shadow_when_enabled_defaults(self):
        cfg = MakerPilotConfig(enabled=True)
        ok, reason = live_maker_allowed(cfg, execute=False)
        self.assertFalse(ok)
        self.assertEqual(reason, "shadow_mode")

    def test_should_stub_live_even_when_all_gates_pass(self):
        cfg = MakerPilotConfig(enabled=True, shadow=False, live_execute=True)
        ok, reason = live_maker_allowed(
            cfg, execute=True, rtds_ready=True, creds_ready=True
        )
        self.assertFalse(ok)
        self.assertEqual(reason, LIVE_STUB_REASON)

    def test_should_require_execute_flag_before_live_stub(self):
        cfg = MakerPilotConfig(enabled=True, shadow=False, live_execute=True)
        ok, reason = live_maker_allowed(cfg, execute=False, rtds_ready=True, creds_ready=True)
        self.assertFalse(ok)
        self.assertEqual(reason, "requires_execute_flag")

    def test_default_config_is_disabled_shadow(self):
        cfg = MakerPilotConfig()
        self.assertFalse(cfg.enabled)
        self.assertTrue(cfg.shadow)
        self.assertFalse(cfg.live_execute)
        self.assertEqual(cfg.effective_mode(), "disabled")

    def test_should_report_live_requested_but_stubbed_when_all_live_flags_on(self):
        cfg = MakerPilotConfig(enabled=True, shadow=False, live_execute=True)
        self.assertEqual(cfg.effective_mode(), "live_requested_but_stubbed")
        public = cfg.as_public_dict()
        self.assertEqual(public["live_path"], "stubbed")
        self.assertEqual(public["mode"], "live_requested_but_stubbed")
        self.assertEqual(default_maker_pilot_mapping()["enabled"], False)


class ShadowEngineLoggingTests(unittest.TestCase):
    def test_should_log_shadow_post_and_cancel_on_fair_flip(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, gtd_ttl_sec=15.0)
        eng = MakerPilotEngine(cfg)
        up = _book("UP", 0.44, 0.46, token_id="up-tok")
        dn = _book("DOWN", 0.54, 0.56, token_id="dn-tok")
        buf = io.StringIO()
        with redirect_stdout(buf):
            posted = eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=up,
                down_book=dn,
                stake_usd=5.0,
                bucket=1,
                market_slug="btc-updown-5m-1",
            )
            flipped = eng.on_tick(
                now=1_002.0,
                fair_signal="down_favored",
                up_book=up,
                down_book=dn,
                stake_usd=5.0,
                bucket=1,
                market_slug="btc-updown-5m-1",
            )

        self.assertEqual(posted[0]["event"], "shadow_post")
        self.assertEqual(posted[0]["side"], "UP")
        self.assertFalse(posted[0]["live_submit"])
        self.assertEqual(posted[0]["order_type"], "GTD")
        self.assertTrue(posted[0]["post_only"])
        self.assertIn("rebate_estimate_usd", posted[0])

        cancel_ev = [e for e in flipped if e.get("event") == "would_cancel"]
        self.assertEqual(len(cancel_ev), 1)
        self.assertEqual(cancel_ev[0]["reason"], "fair_flip")
        self.assertIn("mid_move_pp", cancel_ev[0])
        self.assertIn("adverse_usd", cancel_ev[0])

        log = buf.getvalue()
        self.assertIn("[MAKER_PILOT]", log)
        self.assertIn('"event":"shadow_post"', log)
        self.assertIn('"event":"would_cancel"', log)
        self.assertIn("fair_flip", log)

        summary = summarize_maker_pilot(eng.events, eng.quotes)
        self.assertEqual(summary["quotes_posted"], 1)
        self.assertEqual(summary["cancels_fair_flip"], 1)
        self.assertEqual(summary["would_be_fills"], 0)
        self.assertEqual(summary["live_posts_attempted"], 0)
        self.assertEqual(summary["live_path"], "stubbed")
        self.assertTrue(summary["paper_window_required"])
        report = format_maker_pilot_report(summary)
        self.assertIn("fair_flip", report)
        self.assertIn("paper window", report)

    def test_should_log_would_be_fill_when_ask_crosses(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=9,
            )
            filled = eng.on_tick(
                now=1_001.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.42, 0.44),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=9,
            )
        fill_ev = [e for e in filled if e.get("event") == "would_fill"]
        self.assertEqual(len(fill_ev), 1)
        self.assertFalse(fill_ev[0]["live_fill"])
        self.assertIn("adverse_usd", fill_ev[0])
        summary = summarize_maker_pilot(eng.events, eng.quotes)
        self.assertEqual(summary["would_be_fills"], 1)

    def test_should_log_spread_widen_cancel(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, cancel_spread_widen_abs=0.03)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=4.0,
                bucket=3,
            )
            wide = eng.on_tick(
                now=1_001.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.40, 0.48),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=4.0,
                bucket=3,
            )
        cancel_ev = [e for e in wide if e.get("event") == "would_cancel"]
        self.assertEqual(cancel_ev[0]["reason"], "spread_widen")
        summary = summarize_maker_pilot(eng.events)
        self.assertEqual(summary["cancels_spread_widen"], 1)

    def test_disabled_engine_should_not_post(self):
        eng = MakerPilotEngine(MakerPilotConfig(enabled=False))
        with redirect_stdout(io.StringIO()) as buf:
            out = eng.on_tick(
                now=1.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
            )
        self.assertEqual(out, [])
        self.assertEqual(buf.getvalue(), "")

    def test_from_mapping_reads_yaml_shape(self):
        cfg = MakerPilotConfig.from_mapping({
            "enabled": False,
            "shadow": True,
            "live_execute": False,
            "order_type": "GTD",
            "cancel_spread_widen_abs": 0.04,
            "unknown_key": "ignore",
        })
        self.assertFalse(cfg.enabled)
        self.assertTrue(cfg.shadow)
        self.assertEqual(cfg.cancel_spread_widen_abs, 0.04)
        self.assertEqual(cfg.order_type, "GTD")

    def test_profiles_yaml_wires_maker_pilot_defaults(self):
        path = Path(__file__).parent.parent / "config" / "btc_5m_profiles.yaml"
        with open(path) as f:
            cfg = yaml.safe_load(f)
        shared = cfg["shared_rules"]["maker_pilot"]
        self.assertFalse(shared["enabled"])
        self.assertTrue(shared["shadow"])
        self.assertFalse(shared["live_execute"])
        self.assertTrue(shared["cancel_on_fair_flip"])
        self.assertEqual(shared["order_type"], "GTD")
        for name in ("conservative", "aggressive"):
            mp = cfg["profiles"][name]["maker_pilot"]
            self.assertFalse(mp["enabled"])
            self.assertTrue(mp["shadow"])
            self.assertFalse(mp["live_execute"])

    def test_shadow_post_event_is_json_structured(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        eng = MakerPilotEngine(cfg)
        buf = io.StringIO()
        with redirect_stdout(buf):
            eng.on_tick(
                now=50.0,
                fair_signal="down_favored",
                up_book=_book("UP", 0.40, 0.42, "u"),
                down_book=_book("DOWN", 0.58, 0.60, "d"),
                stake_usd=5.0,
            )
        line = buf.getvalue().strip().splitlines()[-1]
        self.assertTrue(line.startswith("[MAKER_PILOT] "))
        payload = json.loads(line.split(" ", 1)[1])
        self.assertEqual(payload["tag"], "MAKER_PILOT")
        self.assertEqual(payload["event"], "shadow_post")
        self.assertEqual(payload["side"], "DOWN")
        for key in (
            "price",
            "size_shares",
            "mid",
            "spread",
            "rebate_estimate_usd",
            "live_submit",
            "order_type",
        ):
            self.assertIn(key, payload)

    def test_should_prefer_fill_over_cancel_when_ask_crosses_on_a_wide_book(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, cancel_spread_widen_abs=0.03)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=4,
            )
            crossed_and_wide = eng.on_tick(
                now=1_001.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.30, 0.44),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=4,
            )
        events = [e.get("event") for e in crossed_and_wide]
        self.assertEqual(events, ["would_fill"])
        self.assertFalse(any(e.get("event") == "would_cancel" for e in crossed_and_wide))
        self.assertFalse(any(e.get("event") == "shadow_post" for e in crossed_and_wide))

    def test_should_not_requote_on_the_same_tick_as_a_fill(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, max_quotes_per_bucket=4)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=8,
            )
            filled = eng.on_tick(
                now=1_001.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.42, 0.44),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
                bucket=8,
            )
        self.assertEqual([e.get("event") for e in filled], ["would_fill"])
        self.assertIsNotNone(eng.active)
        self.assertEqual(eng.active.status, "would_fill")

    def test_should_skip_post_when_signal_is_neutral_or_stake_is_zero(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            neutral = eng.on_tick(
                now=1.0,
                fair_signal="neutral",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
            )
            zero = eng.on_tick(
                now=2.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=0.0,
            )
        self.assertEqual(neutral, [])
        self.assertEqual(zero, [])
        self.assertIsNone(eng.active)

    def test_should_not_gtd_expire_when_ttl_is_zero(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, gtd_ttl_sec=0.0)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            posted = eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
            )
            later = eng.on_tick(
                now=2_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
            )
        self.assertEqual(posted[0]["event"], "shadow_post")
        self.assertIsNone(posted[0]["gtd_expire_at"])
        self.assertEqual(posted[0]["live_reason"], "shadow_mode")
        self.assertEqual(later, [])
        self.assertEqual(eng.active.status, "live")

    def test_should_count_gtd_expiry_in_summary(self):
        cfg = MakerPilotConfig(enabled=True, shadow=True, gtd_ttl_sec=15.0)
        eng = MakerPilotEngine(cfg)
        with redirect_stdout(io.StringIO()):
            eng.on_tick(
                now=1_000.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
            )
            eng.on_tick(
                now=1_016.0,
                fair_signal="up_favored",
                up_book=_book("UP", 0.44, 0.46),
                down_book=_book("DOWN", 0.54, 0.56),
                stake_usd=5.0,
            )
        summary = summarize_maker_pilot(eng.events, eng.quotes)
        self.assertEqual(summary["cancels_gtd_expired"], 1)
        self.assertEqual(summary["cancel_reasons"]["gtd_expired"], 1)

    def test_side_book_clamps_inverted_spread_and_needs_both_quotes_for_mid(self):
        inverted = _book("UP", 0.50, 0.40)
        self.assertEqual(inverted.spread, 0.0)
        self.assertAlmostEqual(inverted.mid, 0.45)
        missing = SideBook(side="UP", best_bid=0.44)
        self.assertIsNone(missing.spread)
        self.assertIsNone(missing.mid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
