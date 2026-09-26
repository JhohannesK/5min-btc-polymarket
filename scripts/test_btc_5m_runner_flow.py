#!/usr/bin/env python3
"""Unit tests for runner session flow: slot gate, CLI, close retry, modes.

No network. No subprocess. Complementary to draft PRs that own parse/exit/entry.
"""

from __future__ import annotations

import argparse
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_runner_flow import (
    abort_execute_without_rtds,
    after_winmore_gate_action,
    apply_close_env,
    apply_open_env,
    apply_profile_overrides,
    build_close_cmd,
    build_open_cmd,
    classify_fak_close,
    classify_gtc_close,
    classify_gtc_poll,
    clob_creds_ready,
    maker_pilot_is_on,
    open_risk_frac,
    poll_order_status,
    record_failed_open_edge,
    select_active_5m_market,
    should_log_twap_settle,
    use_fair_value_mode,
)


def _market(**over) -> dict:
    base = {
        "closed": False,
        "active": True,
        "endDate": "2026-09-26T10:05:00+00:00",
        "slug": "btc-updown-5m-1",
    }
    base.update(over)
    return base


class SelectActiveMarketTests(unittest.TestCase):
    def test_should_accept_open_market_with_time_left(self):
        # 2026-09-26T10:04:00Z vs end 10:05:00Z
        now = 1_790_417_040.0
        m = select_active_5m_market(
            {"markets": [_market(endDate="2026-09-26T10:05:00+00:00")]},
            now_ts=now,
            slug="btc-updown-5m-1",
        )
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m["_seconds_left"], 60.0, places=3)
        self.assertEqual(m["_event_slug"], "btc-updown-5m-1")

    def test_should_reject_closed_inactive_empty_or_bad_end(self):
        now = 1_790_417_040.0
        self.assertIsNone(select_active_5m_market(None, now, "s"))
        self.assertIsNone(select_active_5m_market({"markets": []}, now, "s"))
        self.assertIsNone(
            select_active_5m_market({"markets": [_market(closed=True)]}, now, "s")
        )
        self.assertIsNone(
            select_active_5m_market({"markets": [_market(active=False)]}, now, "s")
        )
        self.assertIsNone(
            select_active_5m_market({"markets": [_market(endDate="not-a-date")]}, now, "s")
        )

    def test_should_reject_when_seconds_left_at_or_below_5(self):
        end = "2026-09-26T10:05:00+00:00"
        now_eq = 1_790_417_095.0
        self.assertIsNone(
            select_active_5m_market({"markets": [_market(endDate=end)]}, now_eq, "s")
        )
        now_late = 1_790_417_096.0
        self.assertIsNone(
            select_active_5m_market({"markets": [_market(endDate=end)]}, now_late, "s")
        )

    def test_should_read_endDateIso_fallback(self):
        now = 1_790_417_040.0
        m = select_active_5m_market(
            {
                "markets": [
                    {
                        "closed": False,
                        "active": True,
                        "endDateIso": "2026-09-26T10:05:00Z",
                    }
                ]
            },
            now,
            "slug-x",
        )
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m["_seconds_left"], 60.0, places=3)


class ModeFlagTests(unittest.TestCase):
    def test_fair_value_off_when_legacy_threshold(self):
        self.assertTrue(use_fair_value_mode(True, False))
        self.assertFalse(use_fair_value_mode(True, True))
        self.assertFalse(use_fair_value_mode(False, False))

    def test_maker_on_requires_enabled_and_fair_value(self):
        self.assertTrue(maker_pilot_is_on(True, True))
        self.assertFalse(maker_pilot_is_on(True, False))
        self.assertFalse(maker_pilot_is_on(False, True))

    def test_execute_without_rtds_must_abort(self):
        self.assertTrue(abort_execute_without_rtds(True, False))
        self.assertFalse(abort_execute_without_rtds(True, True))
        self.assertFalse(abort_execute_without_rtds(False, False))

    def test_maker_shadow_never_falls_into_taker_open(self):
        self.assertEqual(after_winmore_gate_action(True, True), "maker_tick_continue")
        self.assertEqual(after_winmore_gate_action(True, False), "maker_tick_continue")
        self.assertEqual(after_winmore_gate_action(False, False), "deny")
        self.assertEqual(after_winmore_gate_action(False, True), "time_then_open")

    def test_twap_settle_only_when_held_to_redeem(self):
        self.assertTrue(should_log_twap_settle(True, True, True, 100.0))
        self.assertFalse(should_log_twap_settle(True, True, False, 100.0))
        self.assertFalse(should_log_twap_settle(True, True, True, None))
        self.assertFalse(should_log_twap_settle(False, True, True, 100.0))

    def test_failed_open_records_prior_edge_for_w5(self):
        attempts: dict[int, float] = {}
        record_failed_open_edge(attempts, 12, None)
        self.assertEqual(attempts, {})
        record_failed_open_edge(attempts, 12, 0.012)
        self.assertEqual(attempts[12], 0.012)
        record_failed_open_edge(attempts, 12, 0.004)
        self.assertEqual(attempts[12], 0.004)


class OpenCloseCliTests(unittest.TestCase):
    def test_open_risk_frac_is_stake_over_100(self):
        self.assertEqual(open_risk_frac(5.0), 0.05)
        self.assertEqual(open_risk_frac(25.0), 0.25)

    def test_open_cmd_omits_execute_until_flag_and_sizes_ticket(self):
        dry = build_open_cmd("btc-updown-5m-1", "UP", 5.0, False, order_type="GTD")
        self.assertNotIn("--execute", dry)
        self.assertEqual(dry[dry.index("--risk-frac") + 1], "0.05")
        self.assertEqual(dry[dry.index("--max-notional-usd") + 1], "5.0")
        self.assertEqual(dry[dry.index("--force-side") + 1], "UP")
        live = build_open_cmd("btc-updown-5m-1", "DOWN", 5.0, True)
        self.assertIn("--execute", live)

    def test_open_env_sets_order_type_but_does_not_clobber_spread_defaults(self):
        env = apply_open_env({"PM_MAX_SPREAD": "0.02", "OTHER": "x"}, "fak")
        self.assertEqual(env["PM_ORDER_TYPE"], "FAK")
        self.assertEqual(env["PM_MAX_SPREAD"], "0.02")
        self.assertEqual(env["PM_MIN_TOP_ASK_NOTIONAL_USD"], "10")
        self.assertEqual(env["OTHER"], "x")

    def test_close_cmd_skips_non_positive_limit_and_sets_close_env(self):
        no_px = build_close_cmd("s", "tok", 12.5, False, close_limit_price=None)
        self.assertNotIn("--close-limit-price", no_px)
        zero = build_close_cmd("s", "tok", 12.5, False, close_limit_price=0.0)
        self.assertNotIn("--close-limit-price", zero)
        with_px = build_close_cmd("s", "tok", 12.5, True, close_limit_price=0.43)
        self.assertEqual(with_px[with_px.index("--close-limit-price") + 1], "0.430000")
        self.assertIn("--execute", with_px)
        env = apply_close_env({}, "gtc")
        self.assertEqual(env["PM_CLOSE_ORDER_TYPE"], "GTC")


class CloseRetryTests(unittest.TestCase):
    def test_fak_matched_is_done(self):
        self.assertEqual(
            classify_fak_close({"success": True, "status": "MATCHED"}, {}, ""),
            "done",
        )

    def test_zero_effective_shares_retries(self):
        self.assertEqual(
            classify_fak_close(
                {"success": False},
                {"close_skipped": "zero_effective_shares"},
                "",
            ),
            "retry_zero_shares",
        )

    def test_fak_no_match_text_triggers_gtc_fallback(self):
        self.assertEqual(
            classify_fak_close(
                {"success": False, "status": "live"},
                {},
                "No orders found to match with FAK order",
            ),
            "fallback_gtc",
        )
        self.assertEqual(
            classify_fak_close(
                {"success": False},
                {"note": "no orders found to match with fak order"},
                "",
            ),
            "fallback_gtc",
        )

    def test_other_fak_failures_retry(self):
        self.assertEqual(
            classify_fak_close({"success": False, "status": "delayed"}, {}, "book busy"),
            "retry",
        )

    def test_gtc_live_polls_then_forces_unless_matched(self):
        self.assertEqual(classify_gtc_close({"success": True, "status": "matched"}), "done")
        self.assertEqual(classify_gtc_close({"success": True, "status": "LIVE"}), "poll_then_force")
        self.assertEqual(classify_gtc_close({"success": False, "status": "live"}), "retry")
        self.assertEqual(classify_gtc_poll("MATCHED"), "done")
        self.assertEqual(classify_gtc_poll("matched"), "done")
        self.assertEqual(classify_gtc_poll("LIVE"), "force_close")
        self.assertEqual(classify_gtc_poll(""), "force_close")


class PollOrderStatusTests(unittest.TestCase):
    def test_should_return_empty_without_client_or_order_id(self):
        self.assertEqual(poll_order_status(None, "oid", now_fn=lambda: 0, sleep_fn=lambda _s: None), ("", None))
        self.assertEqual(poll_order_status(object(), "", now_fn=lambda: 0, sleep_fn=lambda _s: None), ("", None))

    def test_should_return_terminal_status_without_waiting_out(self):
        class Client:
            def get_order(self, oid):
                return {"status": "MATCHED", "orderID": oid}

        sleeps: list[float] = []
        st, last = poll_order_status(
            Client(),
            "abc",
            wait_sec=6.0,
            step_sec=1.0,
            now_fn=lambda: 10.0,
            sleep_fn=sleeps.append,
        )
        self.assertEqual(st, "MATCHED")
        self.assertEqual(last["orderID"], "abc")
        self.assertEqual(sleeps, [])

    def test_should_keep_polling_live_then_return_last(self):
        ticks = {"n": 0, "now": 0.0}

        class Client:
            def get_order(self, oid):
                ticks["n"] += 1
                if ticks["n"] < 3:
                    return {"status": "LIVE"}
                return {"status": "OPEN"}

        def now():
            return ticks["now"]

        def sleep(sec):
            ticks["now"] += sec

        st, last = poll_order_status(
            Client(),
            "oid",
            wait_sec=1.0,
            step_sec=0.5,
            now_fn=now,
            sleep_fn=sleep,
        )
        self.assertEqual(st, "OPEN")
        self.assertEqual(last["status"], "OPEN")
        self.assertGreaterEqual(ticks["n"], 2)


class CredsAndProfileTests(unittest.TestCase):
    def test_creds_require_all_four_fields(self):
        full = {
            "PM_PRIVATE_KEY": "k",
            "PM_API_KEY": "a",
            "PM_API_SECRET": "s",
            "PM_API_PASSPHRASE": "p",
        }
        self.assertTrue(clob_creds_ready(full))
        missing = dict(full)
        missing["PM_API_SECRET"] = ""
        self.assertFalse(clob_creds_ready(missing))
        self.assertFalse(clob_creds_ready({}))

    def test_profile_fills_nones_but_cli_wins_and_maker_flag_forces_shadow(self):
        profiles = {
            "conservative": {
                "threshold": 0.70,
                "stake_usd": 5.0,
                "stop_loss_pct": 0.25,
                "exit_before_sec": 20,
                "min_entry_seconds_left": 60,
                "entry_timeout_min": 60,
                "poll_sec": 5.0,
                "use_twap_fair_value": True,
                "min_edge_bps": 5.0,
                "btc_daily_vol_pct": 3.5,
                "hold_to_redeem": True,
                "maker_pilot": {"enabled": False, "shadow": True, "live_execute": False},
                "entry_timing": {},
                "daily_max_loss_usd": 50.0,
                "max_trades_per_day": 12,
                "winmore": None,
            }
        }
        args = argparse.Namespace(
            profile="conservative",
            threshold=0.81,
            stake_usd=None,
            stop_loss_pct=None,
            exit_before_sec=None,
            min_entry_seconds_left=None,
            entry_timeout_min=None,
            poll_sec=None,
            use_twap_fair_value=None,
            min_edge_bps=None,
            btc_daily_vol_pct=None,
            hold_to_redeem=None,
            maker_pilot=True,
        )
        apply_profile_overrides(args, profiles)
        self.assertEqual(args.threshold, 0.81)
        self.assertEqual(args.stake_usd, 5.0)
        self.assertTrue(args.maker_pilot_cfg["enabled"])
        self.assertTrue(args.maker_pilot_cfg["shadow"])
        self.assertFalse(args.maker_pilot_cfg["live_execute"])
        self.assertEqual(args.max_trades_per_day, 12)


if __name__ == "__main__":
    unittest.main()
