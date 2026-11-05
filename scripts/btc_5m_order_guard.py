#!/usr/bin/env python3
"""CLOB GTC poll/cancel fail-closed when auth is missing.

poll/cancel run after a live GTC close is accepted but still LIVE.
A None client (auth_clob_client env parse miss) must not throw or hit the API.
"""

from __future__ import annotations

import time
from typing import Any, Optional


def poll_order_status(
    client: Optional[Any],
    order_id: str,
    wait_sec: float = 6.0,
    step_sec: float = 1.0,
) -> tuple[str, Optional[dict[str, Any]]]:
    if client is None or not order_id:
        return "", None
    deadline = time.time() + max(0.0, float(wait_sec))
    last = None
    while time.time() <= deadline:
        try:
            last = client.get_order(order_id)
            st = str((last or {}).get("status") or "").upper()
            if st and st not in ("LIVE", "OPEN"):
                return st, last
        except Exception:
            pass
        time.sleep(max(0.2, float(step_sec)))
    try:
        last = client.get_order(order_id)
    except Exception:
        pass
    st = str((last or {}).get("status") or "").upper()
    return st, last


def cancel_token_orders(client: Optional[Any], token_id: str) -> Optional[dict[str, Any]]:
    if client is None:
        return None
    try:
        return client.cancel_market_orders(asset_id=str(token_id))
    except Exception as e:
        return {"error": str(e)}
