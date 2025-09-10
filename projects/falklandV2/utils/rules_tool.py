#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rules Tool — quick ways to work with your rules JSON.

Usage examples:
  python3 utils/rules_tool.py validate rules/falklands_rules.json
  python3 utils/rules_tool.py keys rules/falklands_rules.json
  python3 utils/rules_tool.py get rules/falklands_rules.json cap_config.station_radius_nm
  python3 utils/rules_tool.py show radar rules/falklands_rules.json
"""
import sys, json, argparse
from pathlib import Path

# try to use your validator (if present)
def _validate_rules(obj):
    try:
        from utils.rule_book import validate_rules  # your module
    except Exception:
        try:
            from utils.rulebook import validate_rules  # alt spelling
        except Exception:
            # no validator? best-effort sanity checks
            def validate_rules(x):
                errs = []
                if not isinstance(x, dict): errs.append("$. type must be object")
                for k in ("attack_types", "global_rules", "cap_config"):
                    if k not in x: errs.append(f"$.{k} missing")
                return errs
    return validate_rules(obj)

def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: invalid JSON at {path}:{e.lineno}:{e.colno} — {e.msg}")
    except FileNotFoundError:
        sys.exit(f"ERROR: file not found: {path}")

def _get(obj, dotted: str):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, list):
            try: idx = int(part)
            except ValueError: return None
            if idx < 0 or idx >= len(cur): return None
            cur = cur[idx]
        elif isinstance(cur, dict):
            if part not in cur: return None
            cur = cur[part]
        else:
            return None
    return cur

def cmd_validate(args):
    obj = _load(Path(args.path))
    errs = _validate_rules(obj)
    if errs:
        for e in errs: print(f"ERROR: {e}")
        print("RESULT: INVALID")
        return 1
    print("RESULT: OK")
    return 0

def cmd_keys(args):
    obj = _load(Path(args.path))
    print(sorted(obj.keys()))
    return 0

def cmd_get(args):
    obj = _load(Path(args.path))
    val = _get(obj, args.key)
    if val is None:
        print(f"NOT FOUND: {args.key}")
        return 1
    print(json.dumps(val, indent=2))
    return 0

def cmd_show(args):
    obj = _load(Path(args.path))
    if args.topic == "radar":
        rad = obj.get("radar", {})
        # fallbacks if not defined in JSON
        print("Radar config")
        print("  scan_interval_s:", rad.get("scan_interval_s", 180))
        print("  no_spawn_nm:", rad.get("no_spawn_nm", [15.0, 20.0]))
        print("  surprise_nm:", rad.get("surprise_nm", 10.0))
        print("  offboard_max_nm:", rad.get("offboard_max_nm", 40.0))
        return 0
    if args.topic == "cap":
        cap = obj.get("cap_config", {})
        print("CAP config")
        for k in ("aircraft_type","cruise_speed_kts","deck_cycle_per_pair_s",
                  "max_ready_pairs","airframe_pool_total","default_onstation_min",
                  "bingo_rtb_buffer_min","scramble_cooldown_min","station_radius_nm"):
            if k in cap: print(f"  {k}: {cap[k]}")
        return 0
    if args.topic == "contacts":
        ats = obj.get("attack_types") or obj.get("contacts") or []
        print(f"{len(ats)} contact types")
        # print top 10 by weight if present
        def w(x): return x.get("Weight") or x.get("weight") or 1
        for row in sorted(ats, key=w, reverse=True)[:10]:
            name = row.get("Name") or row.get("name")
            alleg = row.get("Allegiance") or row.get("allegiance")
            speed = row.get("Speed (kts)") or row.get("speed_kts")
            weight = w(row)
            print(f"  - {name} ({alleg}) speed={speed} w={weight}")
        return 0
    print("Unknown topic. Use: radar | cap | contacts")
    return 1

def main(argv):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate"); v.add_argument("path")
    k = sub.add_parser("keys");     k.add_argument("path")
    g = sub.add_parser("get");      g.add_argument("path"); g.add_argument("key")
    s = sub.add_parser("show");     s.add_argument("topic", choices=["radar","cap","contacts"]); s.add_argument("path")
    args = p.parse_args(argv[1:])
    return {"validate":cmd_validate,"keys":cmd_keys,"get":cmd_get,"show":cmd_show}[args.cmd](args)

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))