#!/usr/bin/env python3
"""Unit tests for RTDS/API source timestamp parse. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_feed_ts import parse_source_ts


class TestParseSourceTs(unittest.TestCase):
    def test_should_prefer_sourceTs_over_later_keys(self):
        got = parse_source_ts(
            {
                "sourceTs": 100.0,
                "source_ts": 200.0,
                "timestamp": 300.0,
                "ts": 400.0,
                "time": 500.0,
            }
        )
        self.assertEqual(got, 100.0)

    def test_should_fall_through_when_preferred_key_is_missing_or_invalid(self):
        self.assertEqual(parse_source_ts({"source_ts": "1700000001"}), 1700000001.0)
        self.assertEqual(
            parse_source_ts({"sourceTs": "not-a-ts", "timestamp": 77}),
            77.0,
        )
        self.assertEqual(parse_source_ts({"ts": 9, "time": 1}), 9.0)
        self.assertEqual(parse_source_ts({"time": "12.5"}), 12.5)

    def test_should_return_none_when_no_numeric_timestamp_exists(self):
        self.assertIsNone(parse_source_ts({}))
        self.assertIsNone(parse_source_ts({"sourceTs": None, "ts": "iso"}))
        self.assertIsNone(parse_source_ts({"timestamp": [], "time": {}}))

    def test_should_accept_zero_and_skip_none_without_inventing(self):
        self.assertEqual(parse_source_ts({"sourceTs": 0}), 0.0)
        self.assertEqual(parse_source_ts({"sourceTs": None, "source_ts": 1.5}), 1.5)
        self.assertIsNone(parse_source_ts({"value": 76123.4, "windowSeconds": 60}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
