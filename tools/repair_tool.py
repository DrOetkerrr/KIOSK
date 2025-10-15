#!/usr/bin/env python3
"""
Freeze investigation & repair helper (“repair tool”).

Usage:
    python tools/repair_tool.py --timestamp 2025-10-14T14:42:32 --window 180 --auto-fix

The tool inspects flight recorder logs around the provided timestamp (default: tail of the latest
flight log) and prints aggregate statistics.  Known issues are auto-diagnosed.  When run with
--auto-fix it applies corrective actions (currently: normalising the ship state to avoid 0/0 radar
seeding loops).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
STATE_FILE = ROOT / "falklands_state.json"
DEFAULT_STATE = {
    "systems": {"radar": {"online": True, "degraded": False}},
    "ship": {
        "col": 17.0,
        "row": 19.0,
        "heading": 0.0,
        "speed": 0.0,
        "max_speed": 32.0,
    },
    "ship_position": {"col_f": 17.0, "row_f": 19.0},
    "ship_course_deg": 0.0,
    "ship_speed_kn": 0.0,
}


@dataclass
class LogEvent:
    ts: datetime
    route: str
    event: Optional[str]
    raw: Dict[str, object]


def parse_iso8601(value: str) -> Optional[datetime]:
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except Exception:
        return None


def iter_log_events(path: Path) -> Iterator[LogEvent]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ts_raw = obj.get("ts") or obj.get("time")
            if not isinstance(ts_raw, str):
                continue
            ts = parse_iso8601(ts_raw)
            if ts is None:
                continue
            route = str(obj.get("route") or "")
            resp = obj.get("response")
            event = resp.get("event") if isinstance(resp, dict) else None  # type: ignore[arg-type]
            yield LogEvent(ts=ts, route=route, event=event, raw=obj)


def find_latest_flight_log() -> Optional[Path]:
    if not LOG_DIR.exists():
        return None
    candidates = sorted(LOG_DIR.glob("flight_*.jsonl"))
    if candidates:
        return candidates[-1]
    fallback = ROOT / "flight.jsonl"
    return fallback if fallback.exists() else None


def filter_window(events: Iterable[LogEvent], *, center: Optional[datetime], window: timedelta) -> List[LogEvent]:
    events = list(events)
    if not events:
        return []
    if center is None:
        # Tail-based window
        end = events[-1].ts
        start = end - window
    else:
        end = center
        start = end - window
    return [ev for ev in events if start <= ev.ts <= end]


def analyze(events: List[LogEvent]) -> Dict[str, object]:
    counts = Counter(ev.event or "<?>"
                     for ev in events)
    force_spawn_zero = [
        ev for ev in events
        if ev.event == "radar.force_spawn"
        and ev.raw.get("response", {}).get("target_world_xy") == [0.0, 0.0]
    ]
    summary: Dict[str, object] = {
        "total": len(events),
        "event_counts": counts.most_common(15),
        "force_spawn_zero": len(force_spawn_zero),
    }
    if force_spawn_zero:
        summary["force_spawn_zero_first"] = force_spawn_zero[0].ts.isoformat()
    return summary


def apply_repairs(detected: Dict[str, object], *, dry_run: bool = False) -> List[str]:
    actions: List[str] = []
    if detected.get("force_spawn_zero", 0):
        actions.append("Resetting falklands_state.json to default ship position.")
        if not dry_run:
            STATE_FILE.write_text(json.dumps(DEFAULT_STATE, indent=2) + "\n", encoding="utf-8")
    return actions


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Investigate freezes via flight recorder.")
    parser.add_argument("--log", type=Path, help="Flight log to inspect (defaults to latest)")
    parser.add_argument("--timestamp", type=str, help="ISO timestamp of freeze (UTC).")
    parser.add_argument("--window", type=int, default=180, help="Window (seconds) to analyze before timestamp.")
    parser.add_argument("--auto-fix", action="store_true", help="Attempt automated repairs when known issues detected.")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without writing changes.")
    args = parser.parse_args(argv)

    log_path = args.log or find_latest_flight_log()
    if not log_path or not log_path.exists():
        print("No flight log found. Use --log to specify a file.", file=sys.stderr)
        return 1

    center_ts: Optional[datetime] = None
    if args.timestamp:
        center_ts = parse_iso8601(args.timestamp)
        if center_ts is None:
            print(f"Invalid timestamp: {args.timestamp}", file=sys.stderr)
            return 1

    print(f"[repair-tool] Using log: {log_path}")
    events = list(iter_log_events(log_path))
    if not events:
        print("[repair-tool] Log contains no parsable events.")
        return 0

    window_events = filter_window(events, center=center_ts, window=timedelta(seconds=args.window))
    print(f"[repair-tool] Analyzing {len(window_events)} events within window {args.window}s.")

    analysis = analyze(window_events)
    print("\n[repair-tool] Event counts (top 15):")
    for event, count in analysis["event_counts"]:
        print(f"  {event:25s} {count:6d}")

    if analysis.get("force_spawn_zero"):
        first_ts = analysis.get("force_spawn_zero_first")
        print(f"\n[repair-tool] Detected {analysis['force_spawn_zero']} friendly spawns at (0,0). First occurrence: {first_ts}")
        print("  Likely cause: stale ship origin in state file causing radar seeding loop.")

    if args.auto_fix:
        actions = apply_repairs(analysis, dry_run=args.dry_run)
        if actions:
            print("\n[repair-tool] Applied fixes:")
            for act in actions:
                print(f"  - {act}{' (dry-run)' if args.dry_run else ''}")
        else:
            print("\n[repair-tool] No known corrective actions triggered.")

    print("\n[repair-tool] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
