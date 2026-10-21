#!/usr/bin/env python3
"""Parse the last JSON object from a runner log. No network."""

from __future__ import annotations

import json


def load_tail_json(path: str):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as handle:
            txt = handle.read()
    except Exception:
        return None
    i = txt.rfind('\n{')
    if i == -1 and txt.startswith('{'):
        i = 0
    if i == -1:
        return None
    blob = txt[i + 1 :] if txt[i : i + 1] == '\n' else txt[i:]
    try:
        return json.loads(blob)
    except Exception:
        return None
