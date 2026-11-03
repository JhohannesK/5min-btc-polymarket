#!/usr/bin/env python3
"""Start-outcome predicates for btc5m_ctl.sh.

already_running must be a no-op (exit 0) so a live session is not restarted.
failed_to_start must exit 1 after the grace sleep so a dead nohup is not
reported as started.
"""

from __future__ import annotations

import re
from typing import Literal

StartLabel = Literal["already_running", "started", "failed_to_start"]


def start_outcome(
    already_running: bool,
    process_alive_after_grace: bool,
) -> tuple[StartLabel, int]:
    """Mirror cmd_start after pidfile write + sleep 1."""
    if already_running:
        return "already_running", 0
    if process_alive_after_grace:
        return "started", 0
    return "failed_to_start", 1


def _cmd_start_source(source: str) -> str:
    match = re.search(r"cmd_start\(\) \{.*?\n\}\n\ncmd_status", source, re.S)
    return match.group(0) if match else ""


def parse_ctl_start_source(source: str) -> dict[str, object]:
    """Lock already_running no-op, grace sleep, and failed_to_start exit 1."""
    body = _cmd_start_source(source)
    meta_match = re.search(
        r'cat >"\$METAFILE" <<JSON\n(.*?)\nJSON',
        body,
        re.S,
    )
    meta = meta_match.group(1) if meta_match else ""
    return {
        "already_running_returns_0": (
            'echo "already_running pid=$(cat "$PIDFILE")"' in body
            and "return 0" in body.split("already_running", 1)[-1][:80]
        ),
        "already_running_before_nohup": body.find("already_running") < body.find(
            "nohup"
        ),
        "sleep_1_before_ps": bool(
            re.search(r"sleep 1\n\s+if ps -p \"\$pid\"", body)
        ),
        "failed_to_start_exits_1": (
            'echo "failed_to_start (check $log)"' in body and "exit 1" in body
        ),
        "started_echo": 'echo "started pid=$pid log=$log"' in body,
        "meta_omits_execute": "execute" not in meta.lower(),
    }
