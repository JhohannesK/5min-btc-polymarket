#!/usr/bin/env python3
"""Status-line predicates for btc5m_ctl.sh.

cmd_status must not start or stop a session. A live pid prints running;
otherwise stopped. Meta and latest_log are extra lines, not the headline.
"""

from __future__ import annotations

import re
from typing import Optional


def status_headline(running: bool, pid: Optional[str] = None) -> str:
    """Mirror the first echo in cmd_status."""
    if running and pid:
        return f"running pid={pid}"
    return "stopped"


def status_meta_line(metafile_exists: bool, metafile: str) -> Optional[str]:
    if not metafile_exists:
        return None
    return f"meta={metafile}"


def status_latest_log_line(latest_is_symlink: bool, target: str) -> Optional[str]:
    """Only a symlink latest.log is printed; a regular file is ignored."""
    if not latest_is_symlink:
        return None
    return f"latest_log={target}"


def _cmd_status_source(source: str) -> str:
    match = re.search(r"cmd_status\(\) \{.*?\n\}\n\ncmd_stop", source, re.S)
    return match.group(0) if match else ""


def parse_ctl_status_source(source: str) -> dict[str, bool]:
    """Lock running vs stopped, meta file, and symlink-only latest_log."""
    body = _cmd_status_source(source)
    return {
        "uses_is_running": "if is_running; then" in body,
        "running_echo": 'echo "running pid=$pid"' in body,
        "stopped_echo": 'echo "stopped"' in body,
        "no_kill_in_status": "kill" not in body,
        "no_nohup_in_status": "nohup" not in body,
        "meta_if_file": 'if [[ -f "$METAFILE" ]]; then' in body and 'echo "meta=$METAFILE"' in body,
        "latest_if_symlink": (
            'if [[ -L "$LATEST_LINK" ]]; then' in body
            and 'echo "latest_log=$(readlink "$LATEST_LINK")"' in body
        ),
        "latest_not_regular_file_test": '-f "$LATEST_LINK"' not in body,
    }
