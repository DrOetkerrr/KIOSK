"""
Central fire-control gatekeeping.

One entry point:
  fire(engine, ship_file, weapon_key, mode, sfx_push, cooldowns, events, seq_ref, eng_log)
Returns:
  ok==True  -> { ok, target_id?, target_cell?, range_nm?, shots: 1 }
  ok==False -> { ok, error, reason, ... }

Notes
- All envelopes / cooldown / travel / hit% are in data/weapon_profiles.json
- Sounds come from data/audio.json; we default to event 'fire' to match your file
- 'corvus_chaff' is defensive: no lock needed, no target, just consumes ammo + cooldown + SFX
"""

from __future__ import annotations
import json, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from subsystems import contacts as cons

# -------------------- helpers --------------------

def _read_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

def _ship_xy(engine) -> Tuple[float, float]:
    return engine._ship_xy()

def _locked_target(engine):
    lid = engine.state.get("radar", {}).get("locked_contact_id")
    if not lid:
        return None
    return next((c for c in engine.pool.contacts if c.id == lid), None)

def _range_nm(engine, tgt) -> float:
    sx, sy = _ship_xy(engine)
    return cons.dist_nm_xy(tgt.x, tgt.y, sx, sy, engine.pool.grid)

def _cell_of(tgt) -> str:
    return cons.format_cell(int(round(tgt.x)), int(round(tgt.y)))

# -------------------- profile + checks --------------------

def _load_profile(profiles: Dict[str, Any], key: str) -> Dict[str, Any]:
    p = profiles.get(key, {})
    # defaults match your audio.json and keep tests snappy
    return {
        "min_nm": float(p.get("min_nm", 0.0)),
        "max_nm": float(p.get("max_nm", 999.0)),
        "allowed_kinds": list(p.get("allowed_kinds", ["Ship", "Aircraft", "Missile", "Any"])),
        "cooldown_s": float(p.get("cooldown_s", 5.0)),
        "travel_base_s": float(p.get("travel_base_s", 0.0)),
        "travel_per_nm_s": float(p.get("travel_per_nm_s", 2.0)),
        "travel_min_s": float(p.get("travel_min_s", 0.0)),
        "hit_p": float(p.get("hit_p", 0.25)),
        "sfx_key": str(p.get("sfx_key", key)),   # weapon key in audio.json
        "fire_event": str(p.get("fire_event", "fire")),  # event name in audio.json
        "requires_lock": bool(p.get("requires_lock", True)),
        "target_required": bool(p.get("target_required", True)),  # chaff sets this False
    }

def _kind_of_target(tgt) -> str:
    t = (getattr(tgt, "type", "") or "").lower()
    if any(k in t for k in ["skyhawk", "mirage", "aircraft", "helicopter", "pucará", "dagger"]):
        return "Aircraft"
    if any(k in t for k in ["ship", "frigate", "destroyer", "carrier", "cruiser", "tanker", "lcu", "landing"]):
        return "Ship"
    if "missile" in t:
        return "Missile"
    return "Unknown"

def _range_ok(p: Dict[str, Any], rnm: float) -> bool:
    return (rnm >= p["min_nm"]) and (rnm <= p["max_nm"])

def _allowed(p: Dict[str, Any], kind: str) -> bool:
    return (kind in p["allowed_kinds"]) or ("Any" in p["allowed_kinds"])

def _travel_seconds(p: Dict[str, Any], rnm: float) -> float:
    base = p["travel_base_s"]; per = p["travel_per_nm_s"]; mn = p["travel_min_s"]
    return max(mn, base + per * float(rnm))

# -------------------- PUBLIC: fire -------------------------------------------

def fire(
    engine,
    ship_file: Path,
    weapon_key: str,
    mode: Optional[str],
    sfx_push,                      # callable(key:str, event:str) -> None
    cooldowns: Dict[str, float],   # weapon_key -> unix_ts_ready
    events: List[Dict[str, Any]],  # not modified here
    seq_ref: Dict[str, int],       # may be used for logging context
    eng_log: List[str],            # append human logs here
    *,
    profiles_file: Optional[Path] = None,
    audio_file: Optional[Path] = None,
) -> Dict[str, Any]:

    now = time.time()

    # load config
    if profiles_file is None:
        profiles_file = Path(engine.root_dir if hasattr(engine, "root_dir") else ".") / "data" / "weapon_profiles.json"

    profiles = _read_json(profiles_file)
    p = _load_profile(profiles, weapon_key)

    # ship & weapon state
    ship = _read_json(ship_file)
    wdb = ship.get("weapons", {})
    wcfg = wdb.get(weapon_key)
    if not wcfg:
        return {"ok": False, "error": f"weapon '{weapon_key}' not installed", "reason": "not_installed"}

    # cooldown gate
    ready_at = cooldowns.get(weapon_key, 0.0)
    if ready_at > now:
        left = max(0, int(round(ready_at - now)))
        return {"ok": False, "error": f"cooldown {left}s", "reason": "cooldown", "cooldown_s": left}

    # Defensive case: corvus_chaff (or any profile with no target required)
    if not p["target_required"]:
        ammo  = int(wcfg.get("ammo", 0))
        salvo = max(1, int(wcfg.get("salvo", 1)))
        if ammo < salvo:
            return {"ok": False, "error": "no ammo", "reason": "ammo"}
        wcfg["ammo"] = ammo - salvo
        wdb[weapon_key] = wcfg
        ship["weapons"] = wdb
        _write_json(ship_file, ship)
        cooldowns[weapon_key] = now + float(p["cooldown_s"])
        # fire SFX
        sfx_push(str(p["sfx_key"]), str(p["fire_event"]))
        eng_log.append(f"[{time.strftime('%H:%M:%S')}] DEPLOY {weapon_key.upper()}")
        return {"ok": True, "shots": 1}

    # Offensive cases: require lock (by default)
    tgt = _locked_target(engine)
    if p["requires_lock"] and tgt is None:
        return {"ok": False, "error": "no locked target", "reason": "no_lock"}
    if tgt is None:
        return {"ok": False, "error": "no target", "reason": "no_target"}

    if getattr(tgt, "allegiance", "") != "Hostile":
        return {"ok": False, "error": "locked target not hostile", "reason": "not_hostile"}

    kind = _kind_of_target(tgt)
    rng  = _range_nm(engine, tgt)
    if not _allowed(p, kind):
        return {"ok": False, "error": f"{weapon_key} cannot engage {kind}", "reason": "wrong_target"}
    if not _range_ok(p, rng):
        return {"ok": False, "error": f"range {rng:.1f}nm out of envelope", "reason": "range"}

    # ammo gate
    ammo  = int(wcfg.get("ammo", 0))
    salvo = max(1, int(wcfg.get("salvo", 1)))
    if ammo < salvo:
        return {"ok": False, "error": "no ammo", "reason": "ammo"}

    # consume
    wcfg["ammo"] = ammo - salvo
    wdb[weapon_key] = wcfg
    ship["weapons"] = wdb
    _write_json(ship_file, ship)

    # cooldown set
    cooldowns[weapon_key] = now + float(p["cooldown_s"])

    # fire SFX
    sfx_push(str(p["sfx_key"]), str(p["fire_event"]))

    # log
    cell = _cell_of(tgt)
    eng_log.append(f"[{time.strftime('%H:%M:%S')}] LAUNCH {weapon_key.upper()} → #{tgt.id} {tgt.type} at {cell}, {rng:.1f}nm")

    return {
        "ok": True,
        "target_id": int(tgt.id),
        "target_cell": cell,
        "range_nm": float(rng),
        "shots": 1
    }