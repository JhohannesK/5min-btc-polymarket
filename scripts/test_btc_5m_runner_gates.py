#!/usr/bin/env python3
"""Complementary runner-gate tests: market identity, book parse, kill switch,
fee report, and winmore x W4 composition.

No network. Does not import the live CLOB runner.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_entry_timing import (
    EntryTimingConfig,
    classify_seconds_left,
    evaluate_entry_timing,
)
from btc_5m_maker_pilot import SideBook
from btc_5m_runner_parse import (
    apply_maker_pilot_cli_override,
    best_ask_notional,
    best_bid_ask,
    bucket_5m,
    check_kill_switch,
    estimate_session_fees,
    market_side_prices,
    min_spread,
    parse_json_field,
    parse_json_objects,
    pick_timing_allowed_entry,
    should_skip_legacy_late_entry,
    spread_too_wide,
    timing_checks_for_allowed_sides,
)
from btc_5m_winmore_gates import (
    BookLevel,
    EntryDecision,
    WinmoreConfig,
    evaluate_side,
    fee_pp,
    fractional_kelly_usd,
)


class _Lvl:
    def __init__(self, price: float, size: float = 0.0):
        self.price = price
        self.size = size


class ParseAndIdentityTests(unittest.TestCase):
    def test_should_swap_up_down_when_up_is_second_outcome(self):
        up_p, dn_p, up_t, dn_t, slug, end = market_side_prices(
            {
                "outcomes": ["Down", "Up"],
                "outcomePrices": ["0.40", "0.60"],
                "clobTokenIds": ["tok-down", "tok-up"],
                "slug": "btc-updown-5m-1",
                "endDate": "2026-09-24T10:00:00Z",
            }
        )
        self.assertEqual(up_p, 0.60)
        self.assertEqual(dn_p, 0.40)
        self.assertEqual(up_t, "tok-up")
        self.assertEqual(dn_t, "tok-down")
        self.assertEqual(slug, "btc-updown-5m-1")
        self.assertEqual(end, "2026-09-24T10:00:00Z")

    def test_should_treat_yes_label_as_up_when_second(self):
        up_p, dn_p, up_t, dn_t, _, _ = market_side_prices(
            {
                "outcomes": ["No", "Yes"],
                "outcomePrices": [0.35, 0.65],
                "clobTokenIds": ["no-tok", "yes-tok"],
            }
        )
        self.assertEqual(up_p, 0.65)
        self.assertEqual(dn_p, 0.35)
        self.assertEqual(up_t, "yes-tok")
        self.assertEqual(dn_t, "no-tok")

    def test_should_keep_default_order_when_up_is_first(self):
        up_p, dn_p, up_t, dn_t, _, _ = market_side_prices(
            {
                "outcomes": ["Up", "Down"],
                "outcomePrices": ["0.55", "0.45"],
                "clobTokenIds": ["u", "d"],
            }
        )
        self.assertEqual(up_p, 0.55)
        self.assertEqual(dn_p, 0.45)
        self.assertEqual(up_t, "u")
        self.assertEqual(dn_t, "d")

    def test_should_parse_json_string_fields(self):
        up_p, _, up_t, _, _, _ = market_side_prices(
            {
                "outcomes": '["Up","Down"]',
                "outcomePrices": '["0.51","0.49"]',
                "clobTokenIds": '["a","b"]',
            }
        )
        self.assertEqual(up_p, 0.51)
        self.assertEqual(up_t, "a")

    def test_should_raise_when_prices_or_tokens_missing(self):
        with self.assertRaises(RuntimeError):
            market_side_prices({"outcomes": ["Up", "Down"], "outcomePrices": [0.5]})
        with self.assertRaises(RuntimeError):
            market_side_prices(
                {"outcomes": ["Up", "Down"], "outcomePrices": [0.5, 0.5], "clobTokenIds": ["only-one"]}
            )

    def test_should_extract_multiple_json_objects_from_noisy_stdout(self):
        text = 'noise {"a": 1} trailing {"b": 2} done'
        got = parse_json_objects(text)
        self.assertEqual(got, [{"a": 1}, {"b": 2}])

    def test_should_skip_invalid_inner_json_without_raising(self):
        got = parse_json_objects("{not json} {\"ok\": true}")
        self.assertEqual(got, [{"ok": True}])

    def test_should_return_raw_string_when_json_field_is_garbage(self):
        self.assertEqual(parse_json_field("not-json"), "not-json")
        self.assertEqual(parse_json_field({"already": 1}), {"already": 1})

    def test_should_floor_bucket_to_300s(self):
        self.assertEqual(bucket_5m(1_700_000_123), 1_700_000_123 - (1_700_000_123 % 300))
        self.assertEqual(bucket_5m(300), 300)
        self.assertEqual(bucket_5m(599), 300)


class BookExtremesTests(unittest.TestCase):
    def test_should_pick_highest_bid_and_lowest_ask(self):
        book = SimpleNamespace(
            bids=[_Lvl(0.40), _Lvl(0.45), _Lvl(0.41)],
            asks=[_Lvl(0.55), _Lvl(0.52), _Lvl(0.60)],
        )
        bid, ask = best_bid_ask(book)
        self.assertEqual(bid, 0.45)
        self.assertEqual(ask, 0.52)

    def test_should_return_none_for_empty_book(self):
        self.assertEqual(best_bid_ask(SimpleNamespace(bids=[], asks=[])), (None, None))

    def test_should_use_only_top_of_book_ask_notional(self):
        book = SimpleNamespace(
            asks=[_Lvl(0.55, 10.0), _Lvl(0.52, 4.0), _Lvl(0.60, 99.0)]
        )
        self.assertAlmostEqual(best_ask_notional(book), 0.52 * 4.0)

    def test_should_ignore_non_positive_asks_in_notional(self):
        book = SimpleNamespace(asks=[_Lvl(0.0, 10.0), _Lvl(-0.1, 5.0)])
        self.assertEqual(best_ask_notional(book), 0.0)

    def test_should_take_min_spread_across_sides(self):
        up = SideBook(side="UP", best_bid=0.40, best_ask=0.50)  # 10c
        dn = SideBook(side="DOWN", best_bid=0.48, best_ask=0.50)  # 2c
        self.assertAlmostEqual(min_spread(up, dn), 0.02)

    def test_should_ignore_side_with_missing_book(self):
        up = SideBook(side="UP", best_bid=None, best_ask=0.50)
        dn = SideBook(side="DOWN", best_bid=0.40, best_ask=0.44)
        self.assertAlmostEqual(min_spread(up, dn), 0.04)
        both_missing = SideBook(side="UP")
        self.assertIsNone(min_spread(both_missing, SideBook(side="DOWN")))


class KillSwitchAndCliTests(unittest.TestCase):
    def test_should_return_none_when_kill_file_missing(self):
        missing = Path(tempfile.gettempdir()) / "btc5m-missing-kill-file"
        if missing.exists():
            missing.unlink()
        self.assertIsNone(check_kill_switch(missing))

    def test_should_read_hold_and_flatten_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".kill"
            path.write_text("reason: test\naction: hold\n")
            self.assertEqual(check_kill_switch(path), "hold")
            path.write_text("action: flatten\n")
            self.assertEqual(check_kill_switch(path), "flatten")

    def test_should_default_unknown_action_to_flatten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".kill"
            path.write_text("action: yolo\n")
            self.assertEqual(check_kill_switch(path), "flatten")

    def test_should_flatten_when_file_exists_without_action_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".kill"
            path.write_text("halt trading\n")
            self.assertEqual(check_kill_switch(path), "flatten")

    def test_should_force_shadow_when_maker_pilot_flag_set(self):
        cfg = apply_maker_pilot_cli_override(
            {"enabled": False, "shadow": False, "live_execute": True},
            True,
        )
        self.assertTrue(cfg["enabled"])
        self.assertTrue(cfg["shadow"])
        self.assertFalse(cfg["live_execute"])

    def test_should_leave_mapping_alone_when_flag_off(self):
        src = {"enabled": False, "shadow": True, "live_execute": False}
        self.assertEqual(apply_maker_pilot_cli_override(src, False), src)


class LateEntryAndSpreadPredicateTests(unittest.TestCase):
    def test_should_skip_legacy_and_maker_late_but_not_twap_only(self):
        self.assertTrue(
            should_skip_legacy_late_entry(30.0, 60.0, maker_on=False, use_fair_value=False)
        )
        self.assertTrue(
            should_skip_legacy_late_entry(30.0, 60.0, maker_on=True, use_fair_value=True)
        )
        self.assertFalse(
            should_skip_legacy_late_entry(30.0, 60.0, maker_on=False, use_fair_value=True)
        )
        self.assertFalse(
            should_skip_legacy_late_entry(90.0, 60.0, maker_on=True, use_fair_value=True)
        )

    def test_should_flag_spread_strictly_above_max(self):
        self.assertTrue(spread_too_wide(0.031, 0.03))
        self.assertFalse(spread_too_wide(0.03, 0.03))
        self.assertFalse(spread_too_wide(None, 0.03))


class SessionFeeTests(unittest.TestCase):
    def test_should_match_taker_formula_on_entry_and_close(self):
        shares = 10.0
        entry = 0.70
        close_usdc = 8.0  # 0.80 exit
        fees = estimate_session_fees(shares, entry, close_usdc)
        self.assertAlmostEqual(fees["entry_fee_usdc"], shares * 0.07 * 0.70 * 0.30, places=6)
        self.assertAlmostEqual(fees["close_fee_usdc"], shares * 0.07 * 0.80 * 0.20, places=6)
        self.assertAlmostEqual(
            fees["total_fee_usdc"],
            fees["entry_fee_usdc"] + fees["close_fee_usdc"],
            places=6,
        )

    def test_should_zero_close_fee_when_no_close_notional(self):
        fees = estimate_session_fees(10.0, 0.70, 0.0)
        self.assertGreater(fees["entry_fee_usdc"], 0.0)
        self.assertEqual(fees["close_fee_usdc"], 0.0)


class WinmoreBoundaryTests(unittest.TestCase):
    def test_should_clamp_fee_pp_probability_outside_unit_interval(self):
        self.assertAlmostEqual(fee_pp(1.5, 0.0, 0.0), fee_pp(1.0, 0.0, 0.0))
        self.assertAlmostEqual(fee_pp(-0.5, 0.0, 0.0), fee_pp(0.0, 0.0, 0.0))
        self.assertGreaterEqual(fee_pp(2.0, 0.0, 0.0), 0.0)

    def test_should_cap_fractional_kelly_f_star_at_one(self):
        # fair 1.50 vs 0.40 entry would be f* = 1.10/0.60 > 1 without the clamp
        capped = fractional_kelly_usd(1.50, 0.40, 0.25, 100.0)
        self.assertAlmostEqual(capped, 0.25 * 1.0 * 100.0, places=8)

    def test_should_treat_missing_bid_as_zero_half_spread(self):
        cfg = WinmoreConfig(
            midband_enabled=False,
            min_edge_pp=0.001,
            taker_delay_buffer_vol_scale=False,
            taker_delay_buffer_pp=0.001,
            kelly_fraction=0.5,
        )
        levels = [BookLevel(0.70, 100.0)]
        no_bid = evaluate_side(
            "UP", 0.85, 0.70, None, levels, cfg, 5.0, 50.0, 3.5
        )
        with_bid = evaluate_side(
            "UP", 0.85, 0.70, 0.68, levels, cfg, 5.0, 50.0, 3.5
        )
        self.assertEqual(no_bid.details["half_spread"], 0.0)
        self.assertAlmostEqual(with_bid.details["half_spread"], 0.01, places=8)
        self.assertLess(no_bid.fee_pp, with_bid.fee_pp)
        self.assertGreater(no_bid.net_edge_pp, with_bid.net_edge_pp)


class TimingBoundaryAndCompositionTests(unittest.TestCase):
    def test_should_classify_seconds_left_on_inclusive_boundaries(self):
        cfg = EntryTimingConfig()
        cases = (
            (281.0, "soft_open"),
            (280.0, "before_window"),
            (241.0, "before_window"),
            (240.0, "default_window"),
            (45.0, "default_window"),
            (44.0, "after_window"),
            (21.0, "after_window"),
            (20.0, "hard_last"),
        )
        for seconds_left, zone in cases:
            self.assertEqual(
                classify_seconds_left(seconds_left, cfg),
                zone,
                msg=f"{seconds_left}s -> {zone}",
            )

    def test_should_allow_late_exception_when_ask_equals_hold_ev_cap(self):
        cfg = EntryTimingConfig()
        fair_p = 0.90
        cap = fair_p * cfg.redeem_value - cfg.late_hold_ev_buffer_pp
        d = evaluate_entry_timing(
            seconds_left=15.0,
            fair_p=fair_p,
            ask=cap,
            net_edge_bps=80.0,
            min_edge_bps=5.0,
            top_ask_notional_usd=80.0,
            cfg=cfg,
        )
        self.assertTrue(d.allow, d.reason)
        self.assertEqual(d.reason, "allow_late_polarized")

    def test_should_pick_only_timing_allowed_side(self):
        up = EntryDecision(
            allow=True,
            reason="enter",
            side="UP",
            entry_price=0.58,
            net_edge_pp=0.04,
            required_min_edge_pp=0.01,
        )
        down = EntryDecision(
            allow=True,
            reason="enter",
            side="DOWN",
            entry_price=0.70,
            net_edge_pp=0.02,
            required_min_edge_pp=0.01,
        )
        # UP fair too close to half (0.505) denies W4; DOWN polarized-enough allows
        checks = timing_checks_for_allowed_sides(
            [up, down],
            seconds_left=120.0,
            fair_p_up=0.505,
            fair_p_down=0.72,
            up_ask_notional=80.0,
            dn_ask_notional=80.0,
            timing_cfg=EntryTimingConfig(),
        )
        picked = pick_timing_allowed_entry(checks)
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked[0].side, "DOWN")
        self.assertTrue(picked[1].allow)

    def test_should_prefer_higher_net_edge_when_both_pass_timing(self):
        up = EntryDecision(
            allow=True,
            reason="enter",
            side="UP",
            entry_price=0.58,
            net_edge_pp=0.03,
            required_min_edge_pp=0.01,
        )
        down = EntryDecision(
            allow=True,
            reason="enter",
            side="DOWN",
            entry_price=0.60,
            net_edge_pp=0.06,
            required_min_edge_pp=0.01,
        )
        checks = timing_checks_for_allowed_sides(
            [up, down],
            seconds_left=120.0,
            fair_p_up=0.70,
            fair_p_down=0.72,
            up_ask_notional=80.0,
            dn_ask_notional=80.0,
        )
        picked = pick_timing_allowed_entry(checks)
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked[0].side, "DOWN")

    def test_should_return_none_when_no_side_clears_timing(self):
        denied = EntryDecision(allow=False, reason="skip_no_ask", side="UP")
        self.assertIsNone(pick_timing_allowed_entry([(denied, evaluate_entry_timing(
            seconds_left=120.0,
            fair_p=0.70,
            ask=0.58,
            net_edge_bps=25.0,
            min_edge_bps=5.0,
            top_ask_notional_usd=80.0,
        ), 80.0)]))
        # allowed winmore but both in soft-open
        ok = EntryDecision(
            allow=True,
            reason="enter",
            side="UP",
            entry_price=0.58,
            net_edge_pp=0.04,
            required_min_edge_pp=0.01,
        )
        checks = timing_checks_for_allowed_sides(
            [ok],
            seconds_left=290.0,
            fair_p_up=0.70,
            fair_p_down=0.30,
            up_ask_notional=80.0,
            dn_ask_notional=80.0,
        )
        self.assertIsNone(pick_timing_allowed_entry(checks))


if __name__ == "__main__":
    unittest.main()
