#!/usr/bin/env python3
"""
Lightweight consistency suite runner.

Runs static sanity checks, then pytest, then (optionally) replays a jsonl flight log
from /mnt/data/flight.jsonl or ./flight.jsonl and reports invariant violations.
Exits non-zero on any violation or failing tests.
"""

from __future__ import annotations
import json
import os
import sys
from pathlib import Path


def _static_checks() -> list[str]:
    errors: list[str] = []
    try:
        from projects.falklandV2.subsystems import webcore as core
    except Exception as e:  # pragma: no cover
        return [f"import webcore failed: {e}"]

    # Required weapons exist with configured envelopes
    for nm, rng in {
        "Sea Dart SAM": (2.0, 35.0),
        "MM38 Exocet": (8.0, 22.0),
    }.items():
        rec = core.WEAP_MAP.get(nm)
        if not isinstance(rec, dict):
            errors.append(f"missing weapon in catalog: {nm}")
            continue
        mn = float(rec.get("min_nm", -1)) if rec.get("min_nm") is not None else -1
        mx = float(rec.get("max_nm", -1)) if rec.get("max_nm") is not None else -1
        if (mn, mx) != rng:
            errors.append(f"range mismatch for {nm}: got ({mn},{mx}) expected {rng}")
    return errors


def _pytest_run() -> int:
    try:
        import pytest
    except Exception:  # pragma: no cover
        print("pytest not installed", file=sys.stderr)
        return 2
    # Run only our tests by default
    return pytest.main(["-q", "-k", "consistency or replay_flightlog"])  # type: ignore


def _find_log_path() -> str | None:
    p1 = Path("/mnt/data/flight.jsonl")
    p2 = Path("./flight.jsonl")
    return str(p1) if p1.exists() else (str(p2) if p2.exists() else None)


def _replay_log(path: str) -> tuple[int, dict[str, tuple[int, str | None]]]:
    from projects.falklandV2.subsystems import webcore as core
    wmap = core.WEAP_MAP
    counts = {"out_of_envelope_hits": 0, "tti_bad": 0, "linger_shots": 0, "ammo_regress": 0}
    first: dict[str, str | None] = {k: None for k in counts}
    seen_ammo: dict[str, int] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                ts = obj.get("ts") or obj.get("time")
                resp = obj.get("response") if isinstance(obj.get("response"), dict) else {}
                req = obj.get("request") if isinstance(obj.get("request"), dict) else {}
                text = resp.get("text") or ""
                if isinstance(text, str) and "TTI None" in text:
                    counts["tti_bad"] += 1
                    if first["tti_bad"] is None:
                        first["tti_bad"] = str(ts)
                if resp.get("event") in ("weapon.result.hit", "hit"):
                    name = (req.get("weapon") or resp.get("weapon") or "")
                    rng = req.get("rng") if isinstance(req.get("rng"), (int, float)) else resp.get("range_nm")
                    if name in wmap and isinstance(rng, (int, float)):
                        mn = float(wmap[name].get("min_nm", 0.0) or 0.0)
                        mx = float(wmap[name].get("max_nm", 0.0) or 0.0)
                        if rng < mn or rng > mx:
                            counts["out_of_envelope_hits"] += 1
                            if first["out_of_envelope_hits"] is None:
                                first["out_of_envelope_hits"] = str(ts)
                if isinstance(resp.get("audio"), dict):
                    shots = resp["audio"].get("shots_in_flight")
                    if isinstance(shots, list):
                        for s in shots:
                            if str(s.get("result") or "").upper() in ("HIT", "MISS"):
                                counts["linger_shots"] += 1
                                if first["linger_shots"] is None:
                                    first["linger_shots"] = str(ts)
                if isinstance(resp.get("weapons"), list):
                    for w in resp["weapons"]:
                        nm = w.get("name"); cur = int(w.get("ammo") or 0)
                        if nm and nm in seen_ammo and cur > seen_ammo[nm]:
                            counts["ammo_regress"] += 1
                            if first["ammo_regress"] is None:
                                first["ammo_regress"] = str(ts)
                        if nm:
                            seen_ammo[nm] = cur
    except FileNotFoundError:
        return 0, {k: (0, None) for k in counts}
    summary = {k: (counts[k], first[k]) for k in counts}
    return sum(counts.values()), summary


def main() -> int:
    errors = _static_checks()
    if errors:
        print("Static checks failed:", file=sys.stderr)
        for e in errors:
            print(f" - {e}")
        return 3

    rc = _pytest_run()
    if rc != 0:
        print(f"pytest failed with exit code {rc}")
        return rc if isinstance(rc, int) else 1

    log_path = _find_log_path()
    if log_path:
        total, summary = _replay_log(log_path)
        # Print compact summary
        for k, (cnt, first_ts) in summary.items():
            lbl = k.replace("_", " ")
            first = f" first_ts={first_ts}" if cnt else ""
            print(f"{lbl}: {cnt}{first}")
        if total > 0:
            return 4
    else:
        print("No flight.jsonl found; skip replay.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

