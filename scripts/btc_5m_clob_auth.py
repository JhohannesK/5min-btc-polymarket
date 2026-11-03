#!/usr/bin/env python3
"""CLOB auth plan from env. Missing any secret must fail closed (None).

auth_clob_client swallows exceptions and returns None, so close/cancel/poll
never run. This module mirrors the env gate without constructing ClobClient.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class ClobAuthPlan:
    key: str
    signature_type: int
    funder: Optional[str]
    api_key: str
    api_secret: str
    api_passphrase: str


def clob_auth_from_env(env: Mapping[str, str]) -> Optional[ClobAuthPlan]:
    """Mirror auth_clob_client's env parse. Any gap or int() failure is None."""
    try:
        key = env.get("PM_PRIVATE_KEY") or ""
        funder = env.get("PM_FUNDER") or env.get("PM_ADDRESS") or None
        sig = int(env.get("PM_SIGNATURE_TYPE", "2"))
        api_key = env.get("PM_API_KEY") or ""
        api_secret = env.get("PM_API_SECRET") or ""
        api_passphrase = env.get("PM_API_PASSPHRASE") or ""
        if not key or not api_key or not api_secret or not api_passphrase:
            return None
        return ClobAuthPlan(
            key=key,
            signature_type=sig,
            funder=funder,
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        )
    except Exception:
        return None


def _auth_fn_source(source: str) -> str:
    match = re.search(r"def auth_clob_client\b.*?(?=\ndef )", source, re.S)
    return match.group(0) if match else ""


def parse_clob_auth_source(source: str) -> dict[str, bool]:
    """Lock missing-any-field and exception swallow in auth_clob_client."""
    body = _auth_fn_source(source)
    return {
        "missing_any_returns_none": (
            "if not key or not v1 or not v2 or not v3:" in body
            and "return None" in body
        ),
        "exception_returns_none": "except Exception:" in body
        and "return None" in body.split("except Exception:", 1)[-1][:80],
        "funder_falls_back_to_address": (
            "os.getenv('PM_FUNDER') or os.getenv('PM_ADDRESS')" in body
        ),
        "signature_type_defaults_2": "PM_SIGNATURE_TYPE" in body
        and "'2'" in body,
    }
