#!/usr/bin/env python3
"""CLOB env auth fail-closed. No network. No ClobClient. No live trading."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_clob_auth import clob_auth_from_env, parse_clob_auth_source

RUNNER = Path(__file__).resolve().parent / "test_btc_5m_session_exit_sl.py"

FULL_ENV = {
    "PM_PRIVATE_KEY": "0xabc",
    "PM_API_KEY": "k",
    "PM_API_SECRET": "s",
    "PM_API_PASSPHRASE": "p",
    "PM_FUNDER": "0xfunder",
    "PM_ADDRESS": "0xaddr",
    "PM_SIGNATURE_TYPE": "1",
}


class ClobAuthFromEnvTests(unittest.TestCase):
    def test_should_build_a_plan_when_all_secrets_are_present(self):
        plan = clob_auth_from_env(FULL_ENV)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.key, "0xabc")
        self.assertEqual(plan.signature_type, 1)
        self.assertEqual(plan.funder, "0xfunder")
        self.assertEqual(plan.api_key, "k")
        self.assertEqual(plan.api_secret, "s")
        self.assertEqual(plan.api_passphrase, "p")

    def test_should_default_signature_type_to_two_and_funder_to_address(self):
        env = {
            "PM_PRIVATE_KEY": "0xabc",
            "PM_API_KEY": "k",
            "PM_API_SECRET": "s",
            "PM_API_PASSPHRASE": "p",
            "PM_ADDRESS": "0xaddr",
        }
        plan = clob_auth_from_env(env)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.signature_type, 2)
        self.assertEqual(plan.funder, "0xaddr")

    def test_should_return_none_when_any_secret_is_missing_or_blank(self):
        for blank in ("PM_PRIVATE_KEY", "PM_API_KEY", "PM_API_SECRET", "PM_API_PASSPHRASE"):
            env = dict(FULL_ENV)
            env[blank] = ""
            self.assertIsNone(clob_auth_from_env(env), blank)
            del env[blank]
            self.assertIsNone(clob_auth_from_env(env), blank)

    def test_should_return_none_when_signature_type_is_not_an_int(self):
        env = dict(FULL_ENV)
        env["PM_SIGNATURE_TYPE"] = "nope"
        self.assertIsNone(clob_auth_from_env(env))
        env["PM_SIGNATURE_TYPE"] = "2.0"
        self.assertIsNone(clob_auth_from_env(env))

    def test_should_accept_signature_type_zero(self):
        env = dict(FULL_ENV)
        env["PM_SIGNATURE_TYPE"] = "0"
        plan = clob_auth_from_env(env)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.signature_type, 0)


class ClobAuthSourceTests(unittest.TestCase):
    def test_should_lock_missing_field_and_exception_fail_closed(self):
        flags = parse_clob_auth_source(RUNNER.read_text(encoding="utf-8"))
        self.assertTrue(flags["missing_any_returns_none"])
        self.assertTrue(flags["exception_returns_none"])
        self.assertTrue(flags["funder_falls_back_to_address"])
        self.assertTrue(flags["signature_type_defaults_2"])


if __name__ == "__main__":
    unittest.main()
