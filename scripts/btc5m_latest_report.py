#!/usr/bin/env python3
import argparse
import glob
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from btc_5m_latest_report import (
    format_completed_report,
    parse_tail_json_text,
    report_skip_reason,
)


def default_runtime_dir() -> str:
    return str(pathlib.Path(__file__).resolve().parents[1] / "runtime")


def parse_tail_json(path: str):
    txt = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
    return parse_tail_json_text(txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default=default_runtime_dir())
    ap.add_argument("--mark", action="store_true")
    args = ap.parse_args()

    logs = sorted(
        glob.glob(os.path.join(args.runtime_dir, "btc5m_*.log")),
        key=os.path.getmtime,
        reverse=True,
    )
    latest = logs[0] if logs else ""
    marker = latest + ".reported" if latest else ""
    pidfile = os.path.join(args.runtime_dir, "btc5m.pid")
    pidfile_exists = os.path.exists(pidfile)
    pid_alive = False
    if pidfile_exists:
        try:
            pid = int(pathlib.Path(pidfile).read_text().strip())
            os.kill(pid, 0)
            pid_alive = True
        except Exception:
            pid_alive = False

    skip = report_skip_reason(
        has_logs=bool(logs),
        marker_exists=bool(marker) and os.path.exists(marker),
        pidfile_exists=pidfile_exists,
        pid_alive=pid_alive,
    )
    if skip:
        print(skip)
        return

    try:
        obj = parse_tail_json(latest)
    except Exception as e:
        print(f"NO_REPORT:parse_error:{e}")
        return

    if not isinstance(obj, dict):
        print("NO_REPORT:no_json")
        return

    print(format_completed_report(obj, latest))

    if args.mark:
        pathlib.Path(marker).write_text("reported\n", encoding="utf-8")


if __name__ == "__main__":
    main()
