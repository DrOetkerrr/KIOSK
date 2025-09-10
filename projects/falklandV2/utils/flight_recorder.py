#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Falklands V3 — Flight Recorder (NDJSON)
"""

import sys, json, os, uuid, datetime, argparse
from pathlib import Path
from typing import Optional

# Resolve kiosk home:
# 1) $KIOSK_HOME if set
# 2) ~/Documents/kiosk if it exists
# 3) fallback ~/kiosk
def _kiosk_home() -> Path:
    home = Path.home()
    env = os.environ.get("KIOSK_HOME")
    if env:
        return Path(env).expanduser().resolve()
    docs = home / "Documents" / "kiosk"
    if docs.exists():
        return docs
    return home / "kiosk"

KIOSK_HOME = _kiosk_home()
LOG_DIR = (KIOSK_HOME / "logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "flight_recorder.ndjson"

class FlightRecorder:
    def __init__(self, log_path: Optional[Path] = None):
        self.log_file: Path = Path(log_path) if log_path else LOG_FILE
        # Ensure the directory exists even if a custom path is passed
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = os.environ.get("KIOSK_SESSION_ID", str(uuid.uuid4()))

    def log(self, event: str, data: dict):
        rec = {
            "ts": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "session_id": self.session_id,
            "event": event,
            "data": data or {},
        }
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def stage(self, event: str, data: Optional[dict] = None):
        class _Ctx:
            def __enter__(inner_self):
                FlightRecorder.log(self, event, data or {})
                return inner_self
            def __exit__(inner_self, *exc):
                FlightRecorder.log(self, event + ".done", {"ok": exc[0] is None})
                return False
        return _Ctx()

def _cli(argv):
    parser = argparse.ArgumentParser(description="Flight recorder utility")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("heartbeat", help="write a heartbeat event")
    p_start = sub.add_parser("start", help="write a start event")
    p_start.add_argument("--seed")
    p_start.add_argument("--mode")

    args = parser.parse_args(argv[1:])
    rec = FlightRecorder()

    if args.cmd == "heartbeat":
        rec.log("canary.heartbeat", {"ok": True})
    elif args.cmd == "start":
        rec.log("start", {"seed": args.seed, "mode": args.mode})

    print(f"Log written to {rec.log_file}")
    return 0

if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))