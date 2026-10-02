#!/usr/bin/env python3
"""Pure helpers for the latest-run Slack/ops report gate.

btc5m_latest_report.py owns filesystem + pid probing; this module is
deterministic so skip/parse/format cannot silently change.
"""

from __future__ import annotations

import json
from typing import Any, Optional


def parse_tail_json_text(txt: str) -> Any:
    """Last JSON object in a log blob. Raises json.JSONDecodeError on a bad tail."""
    i = txt.rfind("\n{")
    if i == -1 and txt.startswith("{"):
        i = 0
    if i == -1:
        return None
    blob = txt[i + 1 :] if txt[i : i + 1] == "\n" else txt[i:]
    return json.loads(blob)


def report_skip_reason(
    *,
    has_logs: bool,
    marker_exists: bool,
    pidfile_exists: bool,
    pid_alive: bool,
) -> Optional[str]:
    """Why the latest log must not be posted. None means format and print."""
    if not has_logs:
        return "NO_REPORT:no_logs"
    if marker_exists:
        return "NO_REPORT:already_reported"
    if pidfile_exists and pid_alive:
        return "NO_REPORT:running"
    return None


def format_completed_report(obj: dict[str, Any], latest_log: str) -> str:
    opened = obj.get("opened") or {}
    closed = obj.get("closed") or {}
    close_debug = obj.get("close_debug") or []

    lines = [
        "BTC5m run completed",
        f"log: {latest_log}",
        f"result: {obj.get('result')}",
    ]

    if opened:
        lines.append(
            f"open: {opened.get('side')} {opened.get('market_slug')} "
            f"cost={opened.get('cost_usdc')} tx={opened.get('open_tx')}"
        )
    else:
        lines.append("open: none")

    if closed:
        lines.append(
            f"close: success={closed.get('close_success')} "
            f"status={closed.get('close_status')} "
            f"skipped={closed.get('close_skipped')} tx={closed.get('close_tx')}"
        )
    else:
        lines.append("close: none")

    lines.append(f"realized_cashflow_pnl_usdc: {obj.get('realized_cashflow_pnl_usdc')}")
    lines.append(f"close_debug_attempts: {len(close_debug)}")
    if obj.get("close_fallback"):
        lines.append(f"close_fallback: {obj.get('close_fallback')}")
    return "\n".join(lines)
