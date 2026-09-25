#!/usr/bin/env python3
import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from btc_5m_report_agg import aggregate_run_objects, parse_tail_json_text


def default_runtime_dir() -> str:
    return str(Path(__file__).resolve().parents[1] / "runtime")


def load_tail_json(path: str):
    try:
        txt = open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        return None
    return parse_tail_json_text(txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default=default_runtime_dir())
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    pats = [
        os.path.join(args.runtime_dir, "btc5m_one_trade*.log"),
        os.path.join(args.runtime_dir, "btc5m_live_until_*.log"),
        os.path.join(args.runtime_dir, "btc5m_*_*.log"),
    ]
    files = []
    for p in pats:
        files.extend(glob.glob(p))
    files = sorted(set(files), key=lambda p: os.path.getmtime(p), reverse=True)[: args.limit]

    parsed = []
    names = []
    for f in files:
        obj = load_tail_json(f)
        if not obj:
            continue
        parsed.append(obj)
        names.append(os.path.basename(f))

    agg = aggregate_run_objects(parsed)
    for row, name in zip(agg["runs"], names):
        row["file"] = name

    out = {
        "logs_scanned": len(files),
        "runs_parsed": agg["runs_parsed"],
        "results": agg["results"],
        "close_status": agg["close_status"],
        "realized_pnl_sum_usdc": agg["realized_pnl_sum_usdc"],
        "realized_pnl_count": agg["realized_pnl_count"],
        "runs": agg["runs"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
