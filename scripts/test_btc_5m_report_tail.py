#!/usr/bin/env python3
"""Unit tests for runner-log tail JSON parse. No network.

Run: python3 scripts/test_btc_5m_report_tail.py
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_report_tail import load_tail_json


class TestLoadTailJson(unittest.TestCase):
    def test_load_tail_json_returns_last_complete_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'run.log')
            first = {'result': 'win', 'realized_cashflow_pnl_usdc': 1.0}
            second = {'result': 'loss', 'realized_cashflow_pnl_usdc': -2.5}
            Path(path).write_text(
                json.dumps(first) + '\n' + json.dumps(second) + '\n',
                encoding='utf-8',
            )
            self.assertEqual(load_tail_json(path), second)

    def test_load_tail_json_parses_a_file_that_is_one_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'one.log')
            obj = {'result': 'win'}
            Path(path).write_text(json.dumps(obj), encoding='utf-8')
            self.assertEqual(load_tail_json(path), obj)

    def test_load_tail_json_returns_none_on_invalid_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'bad.log')
            Path(path).write_text('{"result":"win"}\n{\n', encoding='utf-8')
            self.assertIsNone(load_tail_json(path))

    def test_load_tail_json_returns_none_for_missing_file(self):
        self.assertIsNone(load_tail_json('/tmp/btc5m-missing-report-tail.log'))

    def test_load_tail_json_returns_none_when_no_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'plain.log')
            Path(path).write_text('not json at all\n', encoding='utf-8')
            self.assertIsNone(load_tail_json(path))


if __name__ == '__main__':
    unittest.main()
