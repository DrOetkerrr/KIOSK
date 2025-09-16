#!/usr/bin/env python3
"""
Session Debrief — analyze logs/flight.jsonl and produce a concise report.

Usage examples:
  python tools/debrief_session.py                  # analyze last 30 minutes
  python tools/debrief_session.py --last 90m       # analyze last 90 minutes
  python tools/debrief_session.py --tail 800       # analyze last 800 lines
  python tools/debrief_session.py --since 2025-09-11T18:00:00Z
  python tools/debrief_session.py --file custom.jsonl --last 45m

Output: writes logs/debrief_YYYYmmdd_HHMMSS.md and prints its path.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, json, os, statistics, sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
DEFAULT_FLIGHT = LOGS / "flight.jsonl"


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Session Debrief for Falklands V2 logs")
    p.add_argument("--file", default=str(DEFAULT_FLIGHT), help="Path to flight JSONL (default: logs/flight.jsonl)")
    p.add_argument("--last", default="30m", help="Time window back from now, e.g., 30m, 2h (default 30m)")
    p.add_argument("--since", default=None, help="ISO8601 start time (UTC), e.g., 2025-09-11T18:00:00Z")
    p.add_argument("--tail", type=int, default=None, help="Analyze only the last N lines (overrides --last/--since)")
    p.add_argument("--session", default=None, help="Filter by session_id (export KIOSK_SESSION_ID before run)")
    # Expect argv to be sys.argv; drop program name
    return p.parse_args(argv[1:])


def parse_duration(s: str) -> dt.timedelta:
    s = (s or "").strip().lower()
    if not s:
        return dt.timedelta(minutes=30)
    try:
        if s.endswith("m"):
            return dt.timedelta(minutes=int(s[:-1]))
        if s.endswith("h"):
            return dt.timedelta(hours=int(s[:-1]))
        if s.endswith("s"):
            return dt.timedelta(seconds=int(s[:-1]))
        # plain minutes
        return dt.timedelta(minutes=int(s))
    except Exception:
        return dt.timedelta(minutes=30)


def parse_ts(s: str) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def load_lines(path: Path, tail: Optional[int] = None) -> Iterable[str]:
    if not path.exists():
        return []
    if tail is None or tail <= 0:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()
    # Efficient tail: read last blocks
    out: List[str] = []
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell(); block = 8192
        buf = b""
        while len(out) <= tail and size > 0:
            step = min(block, size)
            size -= step
            f.seek(size, os.SEEK_SET)
            buf = f.read(step) + buf
            out = buf.splitlines()
    return [l.decode("utf-8", errors="ignore") for l in out[-tail:]]


def summarize(lines: Iterable[str], since: Optional[dt.datetime], session: Optional[str]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    records: List[Dict[str, Any]] = []
    for ln in lines:
        try:
            j = json.loads(ln)
        except Exception:
            continue
        ts = parse_ts(str(j.get("ts", "")) or "")
        if since and ts and ts < since:
            continue
        if session and str(j.get("session_id") or "") != session:
            continue
        records.append(j)

    total = len(records)
    if total == 0:
        return {"total": 0}, []

    # Metrics
    status_codes: List[int] = []
    durations: List[float] = []
    per_route = collections.Counter()
    per_err = collections.Counter()
    errors: List[Dict[str, Any]] = []
    radio: List[Dict[str, Any]] = []
    actions = collections.Counter()

    for r in records:
        route = str(r.get("route", ""))
        per_route[route] += 1
        st = r.get("status")
        if isinstance(st, int):
            status_codes.append(st)
        dur = r.get("duration_ms")
        if isinstance(dur, (int, float)):
            durations.append(float(dur))
        # errors = HTTP >= 400 or response.ok == False
        resp = r.get("response") or {}
        ok = None
        if isinstance(resp, dict) and "ok" in resp:
            try:
                ok = bool(resp.get("ok"))
            except Exception:
                ok = None
        if (isinstance(st, int) and st >= 400) or (ok is False):
            per_err[route] += 1
            errors.append(r)
        if route == "/radio.officer":
            if isinstance(resp, dict):
                radio.append({"ts": r.get("ts"), "role": resp.get("role"), "text": resp.get("text")})
        # action counters
        if route.startswith("/api/command"):
            req = r.get("request") or {}
            cmd = str(req.get("cmd") or "").strip().lower()
            if cmd.startswith("/radar lock"):
                actions["radar_lock"] += 1
            elif cmd.startswith("/radar unlock"):
                actions["radar_unlock"] += 1
            elif cmd.startswith("/radar scan"):
                actions["radar_scan"] += 1
            elif cmd.startswith("/nav set"):
                actions["nav_set"] += 1
        elif route == "/api/nav/set":
            actions["nav_set"] += 1
        elif route.startswith("/nav/"):
            # e.g., /nav/hermes/close_in | stand_off
            actions["nav_ops"] += 1
        elif route.startswith("/weapons/fire"):
            actions["weapons_fire"] += 1
        elif route.startswith("/weapons/arm"):
            actions["weapons_arm"] += 1
        elif route.startswith("/cap/"):
            if route == "/cap/request":
                actions["cap_request"] += 1
            elif route == "/cap/launch_to":
                actions["cap_launch_to"] += 1
            else:
                actions["cap_ops"] += 1
        elif route.startswith("/radar/force_spawn_near"):
            actions["radar_spawn_near"] += 1
        elif route.startswith("/radar/force_spawn"):
            actions["radar_spawn"] += 1
        elif route.startswith("/radar/reload_catalog"):
            actions["radar_catalog_reload"] += 1
        elif route == "/diag/selftest":
            actions["diag_selftest"] += 1
        elif route == "/diag/reset":
            actions["diag_reset"] += 1
        elif route == "/session.start":
            actions["session_start"] += 1
        elif route == "/session.end":
            actions["session_end"] += 1

    dur_stats = {
        "min": (min(durations) if durations else None),
        "avg": (statistics.fmean(durations) if durations else None),
        "p95": (statistics.quantiles(durations, n=20)[-1] if len(durations) >= 20 else (max(durations) if durations else None)),
        "max": (max(durations) if durations else None),
    }
    err_total = sum(per_err.values())
    err_rate = (err_total / total) if total else 0.0

    summary = {
        "total": total,
        "time_range": {
            "start": records[0].get("ts"),
            "end": records[-1].get("ts"),
        },
        "durations_ms": dur_stats,
        "routes": per_route.most_common(10),
        "errors": per_err.most_common(10),
        "error_rate": err_rate,
        "radio_last": radio[-6:],
        "actions": dict(actions),
        "errors_last": errors[-8:],
    }
    return summary, records


def write_report(summary: Dict[str, Any]) -> Path:
    now = dt.datetime.utcnow()
    out = LOGS / f"debrief_{now.strftime('%Y%m%d_%H%M%S')}.md"
    def _fmt(v: Any) -> str:
        if isinstance(v, float):
            return f"{v:.1f}"
        return str(v)
    with out.open("w", encoding="utf-8") as f:
        f.write(f"# Session Debrief — {now.strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n")
        f.write(f"Total events: {summary.get('total',0)}\n\n")
        tr = summary.get("time_range", {})
        f.write(f"Window: {tr.get('start','?')} → {tr.get('end','?')}\n\n")
        # Durations
        ds = summary.get("durations_ms", {})
        f.write("## Latency (ms)\n")
        f.write(f"min={_fmt(ds.get('min'))} avg={_fmt(ds.get('avg'))} p95={_fmt(ds.get('p95'))} max={_fmt(ds.get('max'))}\n\n")
        # Routes
        f.write("## Top Routes\n")
        for path, count in (summary.get("routes") or []):
            f.write(f"- {path}: {count}\n")
        f.write("\n")
        # Errors
        f.write(f"## Errors (rate={summary.get('error_rate',0):.1%})\n")
        for path, count in (summary.get("errors") or []):
            f.write(f"- {path}: {count}\n")
        f.write("\n")
        # Actions
        f.write("## Actions\n")
        for k, v in (summary.get("actions") or {}).items():
            f.write(f"- {k}: {v}\n")
        f.write("\n")
        # Radio (last few)
        f.write("## Radio (last few)\n")
        for r in (summary.get("radio_last") or []):
            f.write(f"- [{r.get('role','?')}] {r.get('text','')}\n")
        f.write("\n")
        # Recent error lines (last few)
        f.write("## Recent Errors\n")
        for e in (summary.get("errors_last") or []):
            route = e.get("route"); status = e.get("status")
            msg = e.get("response", {}).get("error") if isinstance(e.get("response"), dict) else None
            f.write(f"- {route} HTTP {status} {('- ' + msg) if msg else ''}\n")
        f.write("\n")
    return out


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    path = Path(args.file).expanduser().resolve()
    if not path.exists():
        print(f"No log file found: {path}")
        return 2
    if args.tail:
        lines = load_lines(path, tail=args.tail)
        since = None
    else:
        since = parse_ts(args.since) if args.since else (dt.datetime.now(dt.timezone.utc) - parse_duration(args.last))
        # Read full file and filter by time; file is usually small (< few MB)
        lines = load_lines(path)
    summary, _ = summarize(lines, since, args.session)
    if summary.get("total", 0) == 0:
        print("No matching records for the selected window.")
        return 0
    out = write_report(summary)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
