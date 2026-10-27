#!/usr/bin/env python3
"""RTDS JSON -> TWAPSnapshot mapping. No network."""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_twap_fair import parse_source_ts, rtds_snapshot_from_payload


NOW = 1_700_000_000.5


class RtdsPayloadSnapshotTests(unittest.TestCase):
    def test_should_accept_int_60_window_and_numeric_value(self):
        snap = rtds_snapshot_from_payload(
            {"windowSeconds": 60, "value": "101234.5", "series": "btc-usd-twap-60s"},
            NOW,
        )
        self.assertEqual(snap.window_seconds, 60)
        self.assertAlmostEqual(snap.twap_60s, 101234.5)
        self.assertEqual(snap.source, "chainlink_rtds")
        self.assertEqual(snap.series_id, "btc-usd-twap-60s")
        self.assertEqual(snap.timestamp, NOW)
        self.assertEqual(snap.receipt_ts, NOW)

    def test_should_reject_30s_window_instead_of_inventing_60(self):
        with self.assertRaisesRegex(ValueError, r"wrong window: 30s"):
            rtds_snapshot_from_payload({"windowSeconds": 30, "value": 100000}, NOW)

    def test_should_reject_string_60_window_as_not_the_int_60_feed(self):
        with self.assertRaisesRegex(ValueError, r"wrong window: 60s"):
            rtds_snapshot_from_payload({"windowSeconds": "60", "value": 100000}, NOW)

    def test_should_reject_missing_window_seconds(self):
        with self.assertRaisesRegex(ValueError, r"wrong window: Nones"):
            rtds_snapshot_from_payload({"value": 100000}, NOW)

    def test_should_raise_when_value_is_missing(self):
        with self.assertRaises((KeyError, TypeError, ValueError)):
            rtds_snapshot_from_payload({"windowSeconds": 60}, NOW)

    def test_should_default_series_when_absent(self):
        snap = rtds_snapshot_from_payload({"windowSeconds": 60, "value": 1.0}, NOW)
        self.assertEqual(snap.series_id, "btc-usd-twap-60s")

    def test_should_use_sourceTs_when_present(self):
        snap = rtds_snapshot_from_payload(
            {"windowSeconds": 60, "value": 1.0, "sourceTs": 1_699_999_000},
            NOW,
        )
        self.assertEqual(snap.source_ts, 1_699_999_000.0)
        self.assertNotEqual(snap.source_ts, snap.receipt_ts)

    def test_should_fall_back_to_receipt_ts_when_source_ts_cannot_be_parsed(self):
        payload = {"windowSeconds": 60, "value": 1.0, "sourceTs": "not-a-ts"}
        self.assertIsNone(parse_source_ts(payload))
        snap = rtds_snapshot_from_payload(payload, NOW)
        self.assertEqual(snap.source_ts, NOW)
        self.assertEqual(snap.receipt_ts, NOW)


if __name__ == "__main__":
    unittest.main(verbosity=2)
