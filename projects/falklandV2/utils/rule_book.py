#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rule Book — loader + validator for Falklands V3 rules JSON

What it does
- Loads a rules JSON file (UTF-8)
- Hard-fails on NaN/Infinity (including string "NaN")
- Validates core schema pieces used by the engine:
  * attack_types[] (ids, names, base_hit, ranges, defences[], damage_profile)
  * global_rules (min_hit_floor, critical_system_selection, system_effects)
  * optional cap_config (Hermes CAP mission rules)
- Prints clear, path-based errors; exits non-zero on failure
- CLI:
    python3 utils/rule_book.py validate rules/falklands_rules.json
    python3 utils/rule_book.py summary  rules/falklands_rules.json
"""

from __future__ import annotations
import json, math, sys
from pathlib import Path
from typing import Any, Dict, List, Tuple, Iterable

# ---------- generic helpers ----------

def _err(msg: str) -> None:
    sys.stderr.write(msg + "\n")

def _ok(msg: str) -> None:
    sys.stdout.write(msg + "\n")

def _type_name(x: Any) -> str:
    return type(x).__name__

def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)

def _load_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(f"ERROR: file not found: {path}")
    except Exception as e:
        raise SystemExit(f"ERROR: cannot read file: {path} ({e})")
    try:
        # Python's json allows NaN/Infinity in input; we accept parse, then detect and reject.
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"ERROR: invalid JSON at {path}:{e.lineno}:{e.colno} — {e.msg}")

def _walk(data: Any, path: str = "$") -> Iterable[Tuple[str, Any]]:
    """Depth-first walk yielding (json_pointer, value)."""
    yield (path, data)
    if isinstance(data, dict):
        for k, v in data.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(data, list):
        for i, v in enumerate(data):
            yield from _walk(v, f"{path}[{i}]")

# ---------- NaN / Infinity guard ----------

def _find_nonfinite(data: Any) -> List[str]:
    """Return json-pointer paths where value is NaN/Infinity or string 'NaN'/'Infinity'."""
    bad: List[str] = []
    for p, v in _walk(data):
        if _is_number(v) and (math.isnan(v) or math.isinf(v)):  # float('nan'), inf
            bad.append(f"{p} = {v}")
        if isinstance(v, str) and v.strip().lower() in {"nan", "+inf", "-inf", "inf", "infinity"}:
            bad.append(f"{p} = '{v}' (string)")
    return bad

# ---------- schema checks ----------

def _require(d: Dict[str, Any], key: str, path: str, typ: type | Tuple[type, ...]) -> Any:
    if key not in d:
        raise ValueError(f"{path}.{key} missing")
    val = d[key]
    if not isinstance(val, typ):
        raise ValueError(f"{path}.{key} expected {getattr(typ,'__name__',typ)} got {_type_name(val)}")
    return val

def _pct(name: str, val: Any, path: str) -> None:
    if not _is_number(val): raise ValueError(f"{path}.{name} expected number 0..1")
    if not (0.0 <= float(val) <= 1.0): raise ValueError(f"{path}.{name} out of range 0..1: {val}")

def _range_nm(name: str, seq: Any, path: str) -> None:
    if not isinstance(seq, list) or len(seq) != 2:
        raise ValueError(f"{path}.{name} must be [min,max]")
    a, b = seq
    if not (_is_number(a) and _is_number(b)): raise ValueError(f"{path}.{name} must be numeric")
    if a < 0 or b < 0 or a > b: raise ValueError(f"{path}.{name} invalid bounds: {a},{b}")

def _damage_profile(d: Any, path: str) -> None:
    if not isinstance(d, dict): raise ValueError(f"{path}.damage_profile must be object")
    needed = {"Light","Moderate","Severe","Critical"}
    missing = needed - set(d.keys())
    if missing: raise ValueError(f"{path}.damage_profile missing keys: {sorted(missing)}")
    total = 0.0
    for k in needed:
        v = d[k]
        if not _is_number(v): raise ValueError(f"{path}.damage_profile['{k}'] must be number")
        if v < 0 or v > 1: raise ValueError(f"{path}.damage_profile['{k}'] out of 0..1")
        total += float(v)
    # allow slight float slop
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"{path}.damage_profile probabilities must sum to 1.0 (got {total:.6f})")

def _defences(lst: Any, path: str) -> None:
    if lst is None: return
    if not isinstance(lst, list): raise ValueError(f"{path}.defences must be array or null")
    for i, d in enumerate(lst):
        if not isinstance(d, dict): raise ValueError(f"{path}.defences[{i}] must be object")
        _require(d, "id", f"{path}.defences[{i}]", str)
        _require(d, "label", f"{path}.defences[{i}]", str)
        delta = _require(d, "delta", f"{path}.defences[{i}]", (int, float))
        if not (-1.0 <= float(delta) <= 1.0):
            raise ValueError(f"{path}.defences[{i}].delta out of -1..1: {delta}")
        if "range_le_nm" in d:
            v = d["range_le_nm"]
            if not _is_number(v) or v < 0:
                raise ValueError(f"{path}.defences[{i}].range_le_nm must be >=0 nm")

def _check_attack_types(rules: Dict[str, Any], path: str) -> None:
    at = _require(rules, "attack_types", path, list)
    if not at:
        raise ValueError(f"{path}.attack_types must not be empty")
    seen: set[str] = set()
    for i, a in enumerate(at):
        p = f"{path}.attack_types[{i}]"
        if not isinstance(a, dict): raise ValueError(f"{p} must be object")
        id_ = _require(a, "id", p, str)
        if id_ in seen: raise ValueError(f"{p}.id duplicate: {id_}")
        seen.add(id_)
        _require(a, "name", p, str)
        base = _require(a, "base_hit", p, (int, float))
        _pct("base_hit", base, p)
        _range_nm("range_hint_nm", _require(a, "range_hint_nm", p, list), p)
        _defences(a.get("defences"), p)
        _damage_profile(_require(a, "damage_profile", p, dict), p)
        # optional decoy prereq
        if "prerequisite_decoy" in a:
            d = a["prerequisite_decoy"]
            if not isinstance(d, dict): raise ValueError(f"{p}.prerequisite_decoy must be object")
            _require(d, "id", f"{p}.prerequisite_decoy", str)
            _require(d, "label", f"{p}.prerequisite_decoy", str)
            _pct("Pc_default", _require(d, "Pc_default", f"{p}.prerequisite_decoy", (int, float)), f"{p}.prerequisite_decoy")
            _pct("Pc_with_active_cloud", _require(d, "Pc_with_active_cloud", f"{p}.prerequisite_decoy", (int, float)), f"{p}.prerequisite_decoy")

def _check_global_rules(rules: Dict[str, Any], path: str) -> None:
    g = _require(rules, "global_rules", path, dict)
    _pct("min_hit_floor", _require(g, "min_hit_floor", f"{path}.global_rules", (int, float)), f"{path}.global_rules")
    # critical_system_selection
    css = _require(g, "critical_system_selection", f"{path}.global_rules", dict)
    mode = _require(css, "mode", f"{path}.global_rules.critical_system_selection", str)
    if mode not in {"uniform", "weighted"}:
        raise ValueError(f"{path}.global_rules.critical_system_selection.mode must be 'uniform' or 'weighted'")
    systems = _require(css, "systems", f"{path}.global_rules.critical_system_selection", list)
    if not systems: raise ValueError(f"{path}.global_rules.critical_system_selection.systems must not be empty")
    for i, s in enumerate(systems):
        ps = f"{path}.global_rules.critical_system_selection.systems[{i}]"
        if not isinstance(s, dict): raise ValueError(f"{ps} must be object")
        _require(s, "id", ps, str)
        _require(s, "label", ps, str)
        w = _require(s, "weight", ps, (int, float))
        if float(w) <= 0: raise ValueError(f"{ps}.weight must be > 0")
    # system_effects
    se = _require(g, "system_effects", f"{path}.global_rules", dict)
    if not isinstance(se, dict) or not se:
        raise ValueError(f"{path}.global_rules.system_effects must be a non-empty object")

def _check_cap_config(rules: Dict[str, Any], path: str) -> None:
    if "cap_config" not in rules:  # optional, but encouraged
        return
    c = _require(rules, "cap_config", path, dict)
    # required numeric/time-ish fields
    required_nums = [
        "cruise_speed_kts","deck_cycle_per_pair_s","max_ready_pairs","airframe_pool_total",
        "default_onstation_min","bingo_rtb_buffer_min","scramble_cooldown_min","station_radius_nm"
    ]
    for k in required_nums:
        v = _require(c, k, f"{path}.cap_config", (int, float))
        if float(v) < 0:
            raise ValueError(f"{path}.cap_config.{k} must be >= 0")
    _require(c, "aircraft_type", f"{path}.cap_config", str)
    # effects
    eff = _require(c, "effects", f"{path}.cap_config", dict)
    swm = _require(eff, "spawn_weight_multiplier", f"{path}.cap_config.effects", dict)
    ipr = _require(eff, "intercept_prob_pre_release", f"{path}.cap_config.effects", dict)
    for typ, mult in swm.items():
        if not _is_number(mult) or not (0.0 <= float(mult) <= 1.0):
            raise ValueError(f"{path}.cap_config.effects.spawn_weight_multiplier['{typ}'] must be 0..1")
    for typ, p in ipr.items():
        if not _is_number(p) or not (0.0 <= float(p) <= 1.0):
            raise ValueError(f"{path}.cap_config.effects.intercept_prob_pre_release['{typ}'] must be 0..1")
    _pct("defence_bonus_if_not_intercepted", _require(eff, "defence_bonus_if_not_intercepted", f"{path}.cap_config.effects", (int, float)), f"{path}.cap_config.effects")

def validate_rules(obj: Any) -> List[str]:
    """Return list of error strings; empty list means VALID."""
    errors: List[str] = []
    # 1) non-finite scan
    bad = _find_nonfinite(obj)
    if bad:
        errors.append("Non-finite values (NaN/Infinity or their string forms) found at:\n  - " + "\n  - ".join(bad))
    # 2) top-level type
    if not isinstance(obj, dict):
        errors.append(f"Top-level must be an object, got {_type_name(obj)}")
        return errors
    # 3) pieces
    try:
        _check_attack_types(obj, "$")
    except ValueError as e:
        errors.append(str(e))
    try:
        _check_global_rules(obj, "$")
    except ValueError as e:
        errors.append(str(e))
    try:
        _check_cap_config(obj, "$")
    except ValueError as e:
        errors.append(str(e))
    return errors

# ---------- CLI ----------

def cmd_validate(path: Path) -> int:
    obj = _load_json(path)
    errs = validate_rules(obj)
    if errs:
        _err("\n".join(f"ERROR: {e}" for e in errs))
        _err("RESULT: INVALID")
        return 1
    _ok("RESULT: VALID")
    # quick digest for confidence
    at = obj.get("attack_types", [])
    _ok(f"attack_types: {len(at)}")
    if "cap_config" in obj:
        _ok("cap_config: present")
    else:
        _ok("cap_config: (absent)")
    return 0

def cmd_summary(path: Path) -> int:
    obj = _load_json(path)
    errs = validate_rules(obj)
    if errs:
        _err("(Summary computed, but rules are INVALID)")
    # print a short human digest
    at = obj.get("attack_types", [])
    _ok(f"ATTACK TYPES ({len(at)}): " + ", ".join(a.get("id","?") for a in at if isinstance(a, dict)))
    g = obj.get("global_rules", {})
    _ok(f"min_hit_floor: {g.get('min_hit_floor','?')}")
    css = (g.get("critical_system_selection") or {})
    _ok(f"critical_system_selection.mode: {css.get('mode','?')}, systems: {len(css.get('systems',[]))}")
    if "cap_config" in obj:
        c = obj["cap_config"]
        _ok(f"CAP: {c.get('aircraft_type','?')} @ {c.get('cruise_speed_kts','?')} kts, radius {c.get('station_radius_nm','?')} nm")
    return 0

def main(argv: List[str]) -> int:
    if len(argv) < 3 or argv[1] not in {"validate","summary"}:
        _err("usage: rule_book.py {validate|summary} <rules.json>")
        return 2
    cmd, path = argv[1], Path(argv[2]).expanduser()
    if cmd == "validate": return cmd_validate(path)
    if cmd == "summary":  return cmd_summary(path)
    return 2

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))