#!/usr/bin/env python3
"""Tail-JSON parse and PnL rollup for btc5m_report / latest_report.

No filesystem in the pure helpers.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Optional


def parse_tail_json_text(txt: str) -> Optional[dict[str, Any]]:
    i = txt.rfind("\n{")
    if i == -1 and txt.startswith("{"):
        i = 0
    if i == -1:
        return None
    blob = txt[i + 1 :] if txt[i : i + 1] == "\n" else txt[i:]
    try:
        obj = json.loads(blob)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def aggregate_run_objects(objs: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    total_pnl = 0.0
    pnl_count = 0
    close_status: Counter[str] = Counter()
    results: Counter[Any] = Counter()

    for obj in objs:
        r = obj.get("result")
        results[r] += 1
        op = obj.get("opened") or {}
        cl = obj.get("closed") or {}
        pnl = obj.get("realized_cashflow_pnl_usdc")
        if isinstance(pnl, (int, float)):
            total_pnl += float(pnl)
            pnl_count += 1
        close_status[str(cl.get("close_status") or cl.get("close_skipped") or "none")] += 1
        rows.append(
            {
                "result": r,
                "side": op.get("side"),
                "market": op.get("market_slug"),
                "open_tx": op.get("open_tx"),
                "close_tx": cl.get("close_tx"),
                "close_status": cl.get("close_status"),
                "close_skipped": cl.get("close_skipped"),
                "pnl": pnl,
            }
        )

    return {
        "runs_parsed": len(rows),
        "results": dict(results),
        "close_status": dict(close_status),
        "realized_pnl_sum_usdc": round(total_pnl, 6) if pnl_count else None,
        "realized_pnl_count": pnl_count,
        "runs": rows,
    }
