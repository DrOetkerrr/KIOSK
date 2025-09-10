#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Falklands V3 — Canary (pre-flight checks)
- Validates rules JSON (attack_types, global_rules, cap_config)
- Smoke-tests the Engine for N ticks
- Runs a stability suite (start cell, grid sizes, movement, border warn)
- Logs a canary.heartbeat via Flight Recorder
"""

from __future__ import annotations
import argparse, importlib, json, sys
from pathlib import Path
from typing import Any, Optional

# --- Make project imports predictable ----------------------------------------
HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]           # .../falklandV2
sys.path.insert(0, str(PROJECT_ROOT))    # so "utils.*" and "core.*" resolve

# Rule Book import (try both spellings)
try:
    from utils.rule_book import validate_rules  # preferred
except Exception:
    try:
        from utils.rulebook import validate_rules  # fallback
    except Exception as e:
        sys.stderr.write(f"ERROR: cannot import Rule Book validator: {e}\n")
        sys.exit(2)

# Flight Recorder import
try:
    from utils.flight_recorder import FlightRecorder
except Exception as e:
    sys.stderr.write(f"ERROR: cannot import Flight Recorder: {e}\n")
    sys.exit(2)

REC = FlightRecorder()

# --- Helpers -----------------------------------------------------------------
def _load_json(path: Path) -> Any:
    try:
        txt = path.read_text(encoding="utf-8")
        return json.loads(txt)
    except FileNotFoundError:
        sys.exit(f"ERROR: rules file not found: {path}")
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: invalid JSON at {path}:{e.lineno}:{e.colno} — {e.msg}")
    except Exception as e:
        sys.exit(f"ERROR: cannot read {path}: {e}")

def _digest_rules(obj: dict) -> dict:
    at = obj.get("attack_types", [])
    g  = obj.get("global_rules", {})
    c  = obj.get("cap_config")
    return {
        "attack_types": len(at) if isinstance(at, list) else 0,
        "min_hit_floor": g.get("min_hit_floor"),
        "cap": bool(c),
    }

def _import_engine(module_path: str):
    """Return (module, Engine instance) or (module, None) on failure to construct."""
    mod = importlib.import_module(module_path)
    Eng = getattr(mod, "Engine", None)
    if Eng is None:
        raise RuntimeError(f"module '{module_path}' has no class 'Engine'")
    eng = Eng()
    return mod, eng

def _smoke_engine(mod, eng, ticks: int) -> dict:
    info: dict = {"engine_imported": bool(eng), "tick_ok": False,
                  "contacts": None, "hud": None, "warning": None}
    try:
        for _ in range(max(1, ticks)):
            eng.tick(1.0)
        info["tick_ok"] = True
        if hasattr(eng, "hud_line"):
            try: info["hud"] = eng.hud_line()
            except Exception: pass
        if hasattr(eng, "contacts"):
            try: info["contacts"] = len(getattr(eng, "contacts"))
            except Exception: pass
    except Exception as e:
        info["warning"] = f"engine run failed: {e}"
    return info

def _stability_suite(mod, eng) -> list[str]:
    """
    Baseline checks against DesignSpecs.md:
      - WORLD_N==40, BOARD_N==26
      - start cell K13
      - 18 kts for 60s to the east moves ~0.30 nm, ~0.00 nm north/south
      - border warning triggers when 60s lookahead would leave the captain board
    """
    errs: list[str] = []

    # S1: World/board constants
    WORLD_N = getattr(mod, "WORLD_N", None)
    BOARD_N = getattr(mod, "BOARD_N", None)
    if WORLD_N != 40: errs.append(f"WORLD_N expected 40, got {WORLD_N}")
    if BOARD_N != 26: errs.append(f"BOARD_N expected 26, got {BOARD_N}")

    # S2: Start cell K13
    cell = eng.ship.board_cell() if hasattr(eng, "ship") else None
    if not cell or cell[0] != "K" or cell[1] != 13:
        errs.append(f"start cell expected K13, got {cell}")

    # S3: Movement physics — 18 kts for 60s east ≈ 0.3 nm dx, ~0 dy
    x0, y0 = eng.ship.x, eng.ship.y
    if hasattr(eng, "set_course"): eng.set_course(90)
    if hasattr(eng, "set_speed"):  eng.set_speed(18)
    eng.tick(60.0)
    dx = eng.ship.x - x0
    dy = eng.ship.y - y0
    if not (0.29 <= dx <= 0.31):
        errs.append(f"dx expected ~0.30 nm after 60s @ 18kts east, got {dx:.3f}")
    if abs(dy) > 1e-3:
        errs.append(f"dy expected ~0.00 nm, got {dy:.3f}")

    # S4: Border warning — place ship 0.25 nm south of the TOP edge, heading north.
    # Board top edge is at integer y = BOARD_MIN_Y. Moving north reduces y.
    # One-minute lookahead at 18 kts is 0.30 nm, so if current y < BOARD_MIN_Y + 0.30, next step exits.
    if hasattr(mod, "project_edge_warning") and hasattr(mod, "BOARD_MIN_Y"):
        eng.ship.course_deg = 0.0   # north
        eng.ship.speed_kts  = 18.0
        top_edge_y = getattr(mod, "BOARD_MIN_Y")  # integer top edge (e.g., 7)
        eng.ship.y = float(top_edge_y) + 0.25     # 0.25 nm inside → next minute exits
        warn = mod.project_edge_warning(
            eng.ship.x, eng.ship.y, eng.ship.course_deg, eng.ship.speed_kts, dt_s=60.0
        )
        if not warn:
            errs.append("border warning did not trigger at 0.25 nm from top edge with 60s lookahead")
    else:
        errs.append("project_edge_warning/BOARD_MIN_Y not available for border test")

    return errs

# --- Command -----------------------------------------------------------------
def cmd_preflight(args: argparse.Namespace) -> int:
    rules_path = Path(args.rules).expanduser().resolve()

    # 1) Rules: load + validate
    obj = _load_json(rules_path)
    errors = validate_rules(obj)
    if errors:
        for e in errors:
            sys.stderr.write(f"ERROR: {e}\n")
        REC.log("canary.preflight", {"ok": False, "rules_path": str(rules_path), "errors": errors[:6]})
        sys.stderr.write("RESULT: PRE-FLIGHT FAILED\n")
        return 1

    digest = _digest_rules(obj)

    # 2) Engine import + smoke test
    engine_report: Optional[dict] = None
    stability_errors: list[str] = ["engine module not provided"]
    try:
        mod, eng = _import_engine(args.engine_module)
        engine_report = _smoke_engine(mod, eng, args.ticks)
        stability_errors = _stability_suite(mod, eng)
    except Exception as e:
        engine_report = {"engine_imported": False, "tick_ok": False, "warning": f"import failed: {e}",
                         "contacts": None, "hud": None}

    # 3) Log heartbeat
    payload = {
        "ok": True,
        "rules_path": str(rules_path),
        "rules_digest": digest,
        "stability": {"ok": len(stability_errors) == 0, "errors": stability_errors[:6]}
    }
    if engine_report:
        payload["engine"] = engine_report
    REC.log("canary.heartbeat", payload)

    # 4) Console summary
    print("RESULT: PRE-FLIGHT OK")
    print(f"rules: {rules_path.name} | attack_types={digest['attack_types']} | cap={'yes' if digest['cap'] else 'no'} | min_hit_floor={digest['min_hit_floor']}")
    if engine_report:
        print("engine:", "OK" if (engine_report.get("engine_imported") and engine_report.get("tick_ok")) else f"warn — {engine_report.get('warning')}")
    print("stability:", "OK" if not stability_errors else "FAIL — " + "; ".join(stability_errors))
    return 0

def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="canary", description="Pre-flight checks for Falklands V3")
    sub = p.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("preflight", help="Validate rules, smoke-test engine, run stability suite")
    pf.add_argument("--rules", required=True, help="Path to rules JSON (e.g., rules/falklands_rules.json)")
    pf.add_argument("--engine-module", default="core.engine", help="Python module path to Engine (default: core.engine)")
    pf.add_argument("--ticks", type=int, default=10, help="Engine smoke-test ticks (default 10)")
    args = p.parse_args(argv[1:])
    return cmd_preflight(args)

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))