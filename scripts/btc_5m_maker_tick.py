#!/usr/bin/env python3
"""Runner wiring for W6 maker ticks.

Live CLOB posts stay locked off: run_maker_pilot_tick always passes
creds_ready=False even when execute and RTDS are up.
"""

from __future__ import annotations

import re

from btc_5m_maker_pilot import MakerPilotConfig, live_maker_allowed

# Must match test_btc_5m_session_exit_sl.run_maker_pilot_tick.
MAKER_TICK_CREDS_READY = False


def maker_tick_live_gate(
    config: MakerPilotConfig,
    execute: bool,
    rtds_ready: bool,
) -> tuple[bool, str]:
    """Live-maker decision using the runner's hardcoded creds flag."""
    return live_maker_allowed(
        config,
        execute,
        rtds_ready=rtds_ready,
        creds_ready=MAKER_TICK_CREDS_READY,
    )


def _run_maker_pilot_tick_source(source: str) -> str:
    match = re.search(
        r"def run_maker_pilot_tick\(.*?\n\ndef attach_maker_pilot_summary",
        source,
        re.S,
    )
    return match.group(0) if match else ""


def parse_maker_tick_source(source: str) -> dict[str, object]:
    """Lock creds_ready=False and the execute/RTDS passthrough."""
    body = _run_maker_pilot_tick_source(source)
    creds_assigns = re.findall(r"creds_ready\s*=\s*([^,\s]+)", body)
    return {
        "disabled_short_circuit": "if not engine.config.enabled:" in body,
        "creds_ready_false": "creds_ready=False" in body,
        "creds_ready_true_absent": "creds_ready=True" not in body,
        "creds_literal_values": creds_assigns,
        "forwards_execute": "execute=execute" in body,
        "forwards_rtds_ready": "rtds_ready=rtds_ready" in body,
        "never_sets_creds_from_env": "os.getenv" not in body,
    }
