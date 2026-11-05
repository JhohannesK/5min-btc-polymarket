#!/usr/bin/env python3
"""GTC poll/cancel fail-closed without CLOB creds. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_order_guard import cancel_token_orders, poll_order_status


class BoomClient:
    def get_order(self, order_id: str):
        raise AssertionError(f"should not poll {order_id}")

    def cancel_market_orders(self, asset_id: str):
        raise AssertionError(f"should not cancel {asset_id}")


class MatchedClient:
    def __init__(self) -> None:
        self.calls = 0

    def get_order(self, order_id: str):
        self.calls += 1
        return {"status": "matched", "orderID": order_id}


class CancelOkClient:
    def cancel_market_orders(self, asset_id: str):
        return {"canceled": [asset_id]}


class CancelBoomClient:
    def cancel_market_orders(self, asset_id: str):
        raise RuntimeError("clob down")


class PollOrderStatusTests(unittest.TestCase):
    def test_should_return_empty_when_client_is_missing(self):
        st, last = poll_order_status(None, "ord-1", wait_sec=1.0)
        self.assertEqual(st, "")
        self.assertIsNone(last)

    def test_should_skip_get_order_when_order_id_is_blank(self):
        st, last = poll_order_status(BoomClient(), "", wait_sec=1.0)
        self.assertEqual(st, "")
        self.assertIsNone(last)

    def test_should_return_matched_without_waiting(self):
        client = MatchedClient()
        st, last = poll_order_status(client, "ord-9", wait_sec=0.0, step_sec=0.2)
        self.assertEqual(st, "MATCHED")
        self.assertEqual(last, {"status": "matched", "orderID": "ord-9"})
        self.assertEqual(client.calls, 1)


class CancelTokenOrdersTests(unittest.TestCase):
    def test_should_return_none_when_client_is_missing(self):
        self.assertIsNone(cancel_token_orders(None, "tok-1"))

    def test_should_return_cancel_payload_when_clob_accepts(self):
        self.assertEqual(
            cancel_token_orders(CancelOkClient(), "tok-2"),
            {"canceled": ["tok-2"]},
        )

    def test_should_return_error_dict_when_cancel_raises(self):
        out = cancel_token_orders(CancelBoomClient(), "tok-3")
        self.assertEqual(out, {"error": "clob down"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
