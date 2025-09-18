from __future__ import annotations

"""
Core helpers extracted from webdash to reduce file size there.

Notes
- Functions that need live state import the webdash module lazily
  (from .. import webdash as wd) to avoid circular imports.
- Paths are relative to the falklandV2 package; log dir is at repo /logs.
"""

import os
import json
import time
import logging
import hashlib
import random
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

try:
    import requests  # used by TTS
except Exception:  # pragma: no cover
    requests = None  # type: ignore

# ---- Roots and paths ----
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[3]
BASE_DIR = HERE.parents[1]  # .../falklandV2

# Logs
LOG_DIR = REPO_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
FLIGHT_PATH = LOG_DIR / "flight.jsonl"
FLIGHT_MAX_BYTES = int(os.environ.get("FLIGHT_MAX_BYTES", str(25 * 1024 * 1024)))  # 25 MB

# Data/state
DATA_DIR = BASE_DIR / "data"
STATE_DIR = BASE_DIR / "state"
AMMO_PATH = STATE_DIR / "ammo.json"
ARMING_PATH = STATE_DIR / "arming.json"
WEAP_CATALOG_PATH = DATA_DIR / "weapons_catalog.json"
CONTACTS_PATH = DATA_DIR / "contacts.json"
CREW_PATH = DATA_DIR / "crew.json"
ALARM_CFG_PATH = DATA_DIR / "alarms.json"
HEALTH_PATH = STATE_DIR / "health.json"
VOICE_EVENTS_PATH = DATA_DIR / "voice_events.json"
EVENT_TEMPLATES_PATH = DATA_DIR / "event_console.json"
SKIRMISHES_PATH = STATE_DIR / "skirmishes.json"
ROADMAP_PATH = STATE_DIR / "roadmap.json"
TTS_DIR = STATE_DIR / "tts"; TTS_DIR.mkdir(parents=True, exist_ok=True)
VOICES_DIR = STATE_DIR / "voices"; VOICES_DIR.mkdir(parents=True, exist_ok=True)
ENG_SYS_PATH = STATE_DIR / "eng_systems.json"


# ---- JSON helpers ----
def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        return default


def _save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_health() -> Dict[str, Any]:
    try:
        obj = _load_json(HEALTH_PATH, {})
        if not isinstance(obj, dict):
            obj = {}
    except Exception:
        obj = {}
    # Spec: Sheffield max lives = 4
    if 'max_lives' not in obj: obj['max_lives'] = 4
    if 'lives' not in obj: obj['lives'] = obj['max_lives']
    # Spec: Hermes max lives = 8
    if 'hermes_max_lives' not in obj: obj['hermes_max_lives'] = 8
    if 'hermes_lives' not in obj: obj['hermes_lives'] = obj['hermes_max_lives']
    try:
        _save_json(HEALTH_PATH, obj)
    except Exception:
        pass
    return obj


def _save_health(obj: Dict[str, Any]) -> None:
    try:
        _save_json(HEALTH_PATH, obj)
    except Exception:
        pass


def reset_health_state() -> None:
    base = {
        'max_lives': 4,
        'lives': 4,
        'hermes_max_lives': 8,
        'hermes_lives': 8,
    }
    _save_health(base)


def reset_eng_state() -> None:
    base = _eng_defaults_from_validation()
    try:
        _save_json(ENG_SYS_PATH, base)
    except Exception:
        pass


def reset_damage_state() -> None:
    """Restore ship health + engineering systems to their factory defaults."""
    reset_health_state()
    reset_eng_state()


# ---- Engineering systems (assign/repair) ----
def _eng_defaults_from_validation() -> Dict[str, Any]:
    """Seed ENG systems from templates/Validation.json if present."""
    try:
        val = _load_json(BASE_DIR / 'templates' / 'Validation.json', {})
        systems = val.get('engineering_systems') if isinstance(val, dict) else []
        items = []
        for s in (systems or []):
            if not isinstance(s, dict):
                continue
            items.append({
                'id': s.get('id'),
                'status': 'OK',
                'timer_s': 0,
                'team_assigned': False,
                'last_damaged_ts': 0.0,
            })
        teams_total = int((val.get('repair_rules') or {}).get('teams_total', 4))
        return {'teams_total': teams_total, 'teams_free': teams_total, 'systems': items}
    except Exception:
        return {'teams_total': 4, 'teams_free': 4, 'systems': []}


def load_eng_sys() -> Dict[str, Any]:
    obj = _load_json(ENG_SYS_PATH, None)
    if not isinstance(obj, dict):
        obj = _eng_defaults_from_validation()
        try:
            _save_json(ENG_SYS_PATH, obj)
        except Exception:
            pass
    return obj


def save_eng_sys(obj: Dict[str, Any]) -> None:
    _save_json(ENG_SYS_PATH, obj)


# ---- Sound mapping helpers ----
def _sound_key_for_weapon(name: str) -> str:
    """Map weapon display names to stable sound IDs used by frontends.
    Falls back to a generic launch sound key.
    """
    key = str(name or "").strip().lower()
    # Common aliases
    table = {
        'mm38 exocet': 'exocet_mm38',
        'exocet mm38': 'exocet_mm38',
        'sea dart': 'seacat',  # legacy key retained in SOUND_MAP
        '4.5in mk.8': 'gun_4_5in',
        '4.5 in mk.8': 'gun_4_5in',
        '20mm oerlikon': 'oerlikon_20mm',
        '20 mm oerlikon': 'oerlikon_20mm',
        '20mm gam-bo1 (twin)': 'gam_bo1_20mm',
        '20 mm gam-bo1 (twin)': 'gam_bo1_20mm',
        'corvus chaff': 'corvus_chaff',
    }
    return table.get(key, 'weapon_launch')


# ---- Flight recorder ----
def _truncate(val: Any, max_len: int = 400) -> Any:
    if isinstance(val, str) and len(val) > max_len:
        return val[:max_len] + "…"
    return val


def _maybe_rotate_flight_log() -> None:
    try:
        if FLIGHT_PATH.exists():
            sz = FLIGHT_PATH.stat().st_size
            if FLIGHT_MAX_BYTES > 0 and sz >= FLIGHT_MAX_BYTES:
                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                backup = FLIGHT_PATH.with_name(f"flight_{ts}.jsonl")
                try:
                    FLIGHT_PATH.rename(backup)
                except Exception:
                    FLIGHT_PATH.write_text("", encoding="utf-8")
    except Exception:
        pass


def record_flight(ev: Dict[str, Any]) -> None:
    try:
        # Lazy import to avoid cycles
        try:
            from .. import webdash as wd  # type: ignore
        except Exception:  # pragma: no cover
            wd = None  # type: ignore
        base = {"ts": datetime.now(timezone.utc).isoformat(), "hud": None}
        try:
            if wd is not None and hasattr(wd, "ENG") and hasattr(wd.ENG, "hud_line"):
                base["hud"] = wd.ENG.hud_line()  # type: ignore[attr-defined]
        except Exception:
            base["hud"] = None
        sess = os.environ.get('KIOSK_SESSION_ID')
        if sess:
            base["session_id"] = sess
        rec = {**base, **ev}
        if isinstance(rec.get("response"), dict):
            rec["response"] = {k: _truncate(v) for k, v in rec["response"].items()}
        _maybe_rotate_flight_log()
        with FLIGHT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def record_radio(kind: str, text: str) -> None:
    try:
        payload = {
            "route": "/radio.msg",
            "method": "INT",
            "status": 200,
            "duration_ms": 0,
            "request": {},
            "response": {"event": "radio.msg", "kind": str(kind or "ENSIGN"), "text": str(text or "")},
        }
        record_flight(payload)
    except Exception:
        pass


# ---- Audio and alarms ----
AUDIO_STATE: Dict[str, Any] = {
    "last_launch": None,
    "last_result": None,
    "radio": None,
    "alarm": None,
    "cap_launch": None,
    "enemy_bomb": None,
    "shots_in_flight": []
}


def trigger_alarm(sound: str = "red-alert.wav", *, message: str | None = None, role: str | None = None, loop: bool = False) -> None:
    try:
        from .. import webdash as wd  # type: ignore
        with wd.STATE_LOCK:
            AUDIO_STATE['alarm'] = {"file": str(sound), "loop": False, "ts": time.time()}
        if message:
            wd.record_officer(role or 'Captain', message)
        try:
            record_flight({"route": "/alarm.trigger", "method": "INT", "status": 200, "duration_ms": 0,
                           "request": {"sound": sound, "loop": loop, "role": role, "message": message}, "response": {"ok": True}})
        except Exception:
            pass
    except Exception:
        pass


def clear_alarm() -> None:
    try:
        from .. import webdash as wd  # type: ignore
        with wd.STATE_LOCK:
            AUDIO_STATE['alarm'] = {"stop": True, "ts": time.time()}
        record_flight({"route": "/alarm.clear", "method": "INT", "status": 200, "duration_ms": 0,
                       "request": {}, "response": {"ok": True}})
    except Exception:
        pass


def stamp_cap_launch(sound_file: str = "SHAR.wav", volume: float = 0.10, fade_s: float = 2.0) -> None:
    try:
        from .. import webdash as wd  # type: ignore
        with wd.STATE_LOCK:
            AUDIO_STATE['cap_launch'] = {"file": str(sound_file), "vol": float(max(0.0, min(1.0, volume))), "fade_s": float(max(0.0, fade_s)), "ts": time.time()}
    except Exception:
        pass


# ---- Grid conversion (world 40×40 → board A..Z × 1..26) ----
WORLD_N = 40
BOARD_N = 26
BOARD_MIN = (WORLD_N - BOARD_N) / 2.0


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _idx_to_letters(idx: int) -> str:
    s = ""; n = max(1, int(idx))
    while n > 0:
        n -= 1
        s = chr(ord('A') + (n % 26)) + s
        n //= 26
    return s


def world_to_board(row: float, col: float) -> tuple[int, int]:
    def mapv(v: float) -> int:
        t = (v - BOARD_MIN)
        return int(round(clamp(1.0 + t, 1.0, float(BOARD_N))))
    return mapv(row), mapv(col)


def board_to_cell(row_i: int, col_i: int) -> str:
    return f"{_idx_to_letters(int(col_i))}{int(row_i)}"


def cell_for_world(row: float, col: float) -> str:
    r_i, c_i = world_to_board(row, col)
    return board_to_cell(r_i, c_i)


def ship_cell_from_state(state: Dict[str, Any]) -> str:
    ship = (state or {}).get('ship', {}) if isinstance(state, dict) else {}
    try:
        col = float(ship.get('col', 0.0))
        row = float(ship.get('row', 0.0))
    except Exception:
        col, row = 0.0, 0.0
    if (col > float(WORLD_N)) or (row > float(WORLD_N)):
        try:
            legacy_span = 100.0
            col = (float(col) / legacy_span) * float(WORLD_N)
            row = (float(row) / legacy_span) * float(WORLD_N)
        except Exception:
            pass
    return cell_for_world(row, col)


def radar_xy_from_state(state: Dict[str, Any]) -> tuple[float, float]:
    try:
        from .. import webdash as wd  # type: ignore
        x, y = wd.get_own_xy(state)
        xf, yf = float(x), float(y)
    except Exception:
        xf, yf = 0.0, 0.0
    if xf > float(WORLD_N) or yf > float(WORLD_N):
        try:
            legacy_span = 100.0
            xf = (xf / legacy_span) * float(WORLD_N)
            yf = (yf / legacy_span) * float(WORLD_N)
        except Exception:
            pass
    return (xf, yf)


def cell_to_world(cell: str) -> tuple[float, float]:
    s = str(cell or "").strip().upper()
    if not s:
        return (0.0, 0.0)
    i = 0
    while i < len(s) and s[i].isalpha():
        i += 1
    col_letters = s[:i] or "A"
    row_str = s[i:] or "1"
    col_i = 0
    for ch in col_letters:
        col_i = col_i * 26 + (ord(ch) - ord('A') + 1)
    try:
        row_i = int(row_str)
    except Exception:
        row_i = 1
    col_i = max(1, min(BOARD_N, col_i))
    row_i = max(1, min(BOARD_N, row_i))
    x = float(BOARD_MIN) + float(col_i - 1)
    y = float(BOARD_MIN) + float(row_i - 1)
    return (x, y)


# ---- Weapons data and helpers ----
WEAP_CATALOG = _load_json(WEAP_CATALOG_PATH, [])
if not isinstance(WEAP_CATALOG, list):
    WEAP_CATALOG = []
WEAP_MAP = {str(it.get("name","")): it for it in WEAP_CATALOG if isinstance(it, dict)}

def _load_targets_class_map():
    obj = _load_json(CONTACTS_PATH, [])
    items = obj.get('items') if isinstance(obj, dict) else obj
    mapping = {}
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict):
                name = str(it.get('name',''))
                klass = str(it.get('class') or it.get('type') or '').title()
                if name:
                    mapping[name] = klass
    return mapping

TARGET_CLASS_BY_NAME = _load_targets_class_map()

WEAP_DEFAULT_AMMO = {
    "MM38 Exocet": 4,
    "4.5 inch Mk.8 gun": 550,
    "Sea Dart SAM": 26,
    "20mm Oerlikon": 5000,
    "20mm GAM-BO1 (twin)": 1850,
    "Corvus chaff": 15,
}

WEAP_DEFAULT_ARMING = {
    "MM38 Exocet": "Safe",
    "4.5 inch Mk.8 gun": "Safe",
    "Sea Dart SAM": "Safe",
    "20mm Oerlikon": "Safe",
    "20mm GAM-BO1 (twin)": "Safe",
    "Corvus chaff": "Safe",
}


def _normalize_weapon_name(name: str) -> str:
    s = (name or "").strip().lower()
    aliases = {
        "seacat": "Sea Dart SAM",
        "gws-24 seacat sam": "Sea Dart SAM",
        "gws.24 seacat": "Sea Dart SAM",
        "sea dart": "Sea Dart SAM",
        "sea dart sam": "Sea Dart SAM",
        "4.5in": "4.5 inch Mk.8 gun",
        "4.5 inch": "4.5 inch Mk.8 gun",
        "gun_4_5in": "4.5 inch Mk.8 gun",
        "mk.8": "4.5 inch Mk.8 gun",
        "oerlikon": "20mm Oerlikon",
        "oerlikon_20mm": "20mm Oerlikon",
        "20 mm oerlikon": "20mm Oerlikon",
        "gam-bo1": "20mm GAM-BO1 (twin)",
        "gam_bo1_20mm": "20mm GAM-BO1 (twin)",
        "exocet": "MM38 Exocet",
        "mm38": "MM38 Exocet",
        "mm38 exocet": "MM38 Exocet",
        "corvus": "Corvus chaff",
        "chaff": "Corvus chaff",
    }
    return aliases.get(s, name)


def _coerce_arming(v) -> str:
    try:
        if isinstance(v, str):
            return "Armed" if v.strip().lower().startswith("armed") else "Safe"
        if isinstance(v, bool):
            return "Armed" if v else "Safe"
        if isinstance(v, (int, float)):
            return "Armed" if float(v) != 0.0 else "Safe"
    except Exception:
        pass
    return "Safe"


def _ammo_defaults_from_ship() -> Dict[str, int]:
    try:
        ship = _load_json(DATA_DIR / 'ship.json', {})
        w = ship.get('weapons', {}) if isinstance(ship, dict) else {}
        def gi(obj, key, field, default=0):
            try:
                return int(((obj or {}).get(key) or {}).get(field, default))
            except Exception:
                return int(default)
        return {
            "4.5 inch Mk.8 gun": gi(w, 'gun_4_5in', 'ammo_he', 550),
            "Sea Dart SAM": gi(w, 'seacat', 'rounds', 26),
            "20mm Oerlikon": gi(w, 'oerlikon_20mm', 'rounds', 5000),
            "20mm GAM-BO1 (twin)": gi(w, 'gam_bo1_20mm', 'rounds', 1850),
            "MM38 Exocet": gi(w, 'exocet_mm38', 'rounds', 4),
            "Corvus chaff": gi(w, 'corvus_chaff', 'salvoes', 15),
        }
    except Exception:
        return {}


def load_ammo() -> Dict[str,int]:
    raw = _load_json(AMMO_PATH, {})
    normalized: Dict[str, int] = {}
    try:
        if isinstance(raw, dict) and isinstance(raw.get("weapons"), dict):
            for k, v in raw.get("weapons", {}).items():
                nm = _normalize_weapon_name(str(k))
                amt = 0
                try:
                    if isinstance(v, dict):
                        if 'rounds' in v: amt = int(v.get('rounds') or 0)
                        elif 'ammo' in v: amt = int(v.get('ammo') or 0)
                        elif 'salvoes' in v: amt = int(v.get('salvoes') or 0)
                        else: amt = int(next(iter(v.values()))) if v else 0
                    else:
                        amt = int(v)
                except Exception:
                    amt = 0
                if nm:
                    normalized[nm] = max(0, int(amt))
        elif isinstance(raw, dict):
            for k, v in raw.items():
                nm = _normalize_weapon_name(str(k))
                try:
                    normalized[nm] = max(0, int(v))
                except Exception:
                    continue
    except Exception:
        normalized = {}
    base = {**WEAP_DEFAULT_AMMO, **_ammo_defaults_from_ship()}
    merged = dict(base)
    for k, v in normalized.items():
        try:
            vi = int(v)
        except Exception:
            continue
        if vi > 0: merged[k] = vi
        else:
            if base.get(k, 0) <= 0: merged[k] = 0
    try:
        if isinstance(raw, dict):
            flat_like = all(not isinstance(v, dict) for v in raw.values()) and 'weapons' not in raw
        else:
            flat_like = False
        if (not flat_like) or any(_normalize_weapon_name(k) != k for k in merged.keys()):
            save_ammo(merged)
    except Exception:
        pass
    return merged


def save_ammo(d: Dict[str,int]) -> None:
    _save_json(AMMO_PATH, d)


def load_arming() -> Dict[str,str]:
    raw = _load_json(ARMING_PATH, {})
    normalized: Dict[str, str] = {}
    dirty = False
    now = time.time()
    try:
        source = raw.get("weapons", {}) if (isinstance(raw, dict) and isinstance(raw.get("weapons"), dict)) else (raw if isinstance(raw, dict) else {})
        for k, v in (source or {}).items():
            nm = _normalize_weapon_name(str(k))
            if isinstance(v, dict):
                armed = bool(v.get('armed', False))
                until = float(v.get('arming_until', 0) or 0)
                if armed:
                    normalized[nm] = 'Armed'
                elif until > now:
                    normalized[nm] = 'Arming'
                elif until > 0 and until <= now:
                    normalized[nm] = 'Armed'; v['armed'] = True; v['arming_until'] = 0; dirty = True
                else:
                    normalized[nm] = 'Safe'
            else:
                normalized[nm] = _coerce_arming(v)
    except Exception:
        normalized = {}
    merged = {**WEAP_DEFAULT_ARMING, **normalized}
    try:
        if dirty and isinstance(raw, dict):
            _save_json(ARMING_PATH, raw)
    except Exception:
        pass
    return merged


def save_arming(d: Dict[str,str]) -> None:
    _save_json(ARMING_PATH, d)


def _primary_class(primary: Dict[str,Any] | None) -> str | None:
    if not primary or not isinstance(primary, dict):
        return None
    name = str(primary.get('name',''))
    return TARGET_CLASS_BY_NAME.get(name)


def compute_in_range(weapon_name: str, primary: Dict[str,Any] | None) -> bool:
    if not primary: return False
    w = WEAP_MAP.get(weapon_name)
    if not w: return False
    try:
        rng = float(primary.get('range_nm'))
    except Exception:
        return False
    klass = _primary_class(primary)
    supports = [str(x) for x in (w.get('supports') or [])]
    if not klass or (supports and klass not in supports):
        return False
    try:
        mn = float(w.get('min_nm', 0.0)); mx = float(w.get('max_nm', 0.0))
    except Exception:
        return False
    return (mn <= rng <= mx)


def _ownfleet_snapshot(state: Dict[str, Any]) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    ship = (state or {}).get('ship', {})
    try:
        cell = ship_cell_from_state(state)
    except Exception:
        cell = 'K13'
    ship_cfg = _load_json(DATA_DIR / 'ship.json', {})
    own_name = ship_cfg.get('name', 'Own Ship')
    own_class = ship_cfg.get('class', 'DD')
    lives = int((state or {}).get('lives', 1) or 1)
    max_lives = int((state or {}).get('max_lives', 1) or 1)
    health_pct = int(round(100.0 * (lives / max(1, max_lives))))
    out.append({'id':'own','name':own_name,'class':own_class,'cell':cell,'speed':ship.get('speed',0),'heading':ship.get('heading',0),'status':{'health_pct':health_pct}})
    # Escorts from convoy.json: compute cell as own_cell plus offset_cells; lock speed/course to leader
    try:
        convoy = _load_json(DATA_DIR / 'convoy.json', {})
        escorts = convoy.get('escorts', []) if isinstance(convoy, dict) else []
        # Parse own cell into indices
        i = 0
        while i < len(cell) and cell[i].isalpha():
            i += 1
        col_letters = cell[:i] or 'A'
        row_str = cell[i:] or '1'
        col_i = 0
        for ch in col_letters:
            col_i = col_i*26 + (ord(ch)-ord('A')+1)
        try:
            row_i = int(row_str)
        except Exception:
            row_i = 1
        for e in escorts[:3]:
            try:
                dx, dy = (e.get('offset_cells') or [0,0])
                ec = board_to_cell(int(clamp(row_i + int(dy), 1, BOARD_N)), int(clamp(col_i + int(dx), 1, BOARD_N)))
                out.append({
                    'id': e.get('id','esc'),
                    'name': e.get('name','Escort'),
                    'class': e.get('class','FF'),
                    'cell': ec,
                    'speed': ship.get('speed', 0),
                    'heading': ship.get('heading', 0),
                    'status': {'health_pct': 100}
                })
            except Exception:
                continue
    except Exception:
        pass
    return out


def load_alarm_cfg() -> Dict[str, Any]:
    obj = _load_json(ALARM_CFG_PATH, {})
    return obj if isinstance(obj, dict) else {}


# ---- Voice helpers ----
VOICE_EVENTS_DEFAULT: Dict[str, Dict[str, Any]] = {
    "pilot.intercept.launch": {"role": "Pilot", "intent": "Acknowledge Hermes for intercept", "hint": "Hermes, intercept bogey, vector to {cell}."},
    "pilot.fox2": {"role":"Pilot","intent":"Missile fired","hint":"Fox Two!"},
    "pilot.splash": {"role":"Pilot","intent":"Kill confirm","hint":"Splash one bandit."},
    # Invariant guard: consistency suite — voice event for re-vectoring
    "pilot.vector": {"role":"Pilot","intent":"Vector to new target","hint":"Vectoring to {cell}."},
    "radar.scan.start": {"role":"Radar","intent":"Acknowledge scanning","hint":"Captain, scanning radar."},
    "radar.scan.complete": {"role":"Radar","intent":"Scan complete","hint":"Captain, radar scan complete: {contacts} contact(s); hostiles {hostiles}; friendlies {friendlies}."},
    "hostile.attack.warn": {"role":"Fire Control","intent":"Inbound threat warning","hint":"Incoming {weapon} at {range_nm} nm."},
    "engineering.damage": {"role":"Engineering","intent":"Damage acknowledged","hint":"Captain, hit on {system}. Damage control responding."},
    "weapons.launch": {"role":"Weapons","intent":"Weapon fired","hint":"{weapon} away."},
}


def _load_voice_events() -> Dict[str, Dict[str, Any]]:
    try:
        if VOICE_EVENTS_PATH.exists():
            data = _load_json(VOICE_EVENTS_PATH, [])
            events: Dict[str, Dict[str, Any]] = {}
            if isinstance(data, list):
                for it in data:
                    try:
                        ev = str(it.get('event') or '').strip()
                        if not ev: continue
                        events[ev] = {'role': (it.get('role') or 'Ensign'), 'intent': (it.get('intent') or ''), 'hint': (it.get('hint') or '')}
                    except Exception:
                        continue
            return events or dict(VOICE_EVENTS_DEFAULT)
    except Exception:
        pass
    return dict(VOICE_EVENTS_DEFAULT)


VOICE_EVENTS: Dict[str, Dict[str, Any]] = _load_voice_events()


def _load_event_templates() -> Dict[str, str]:
    try:
        raw = _load_json(EVENT_TEMPLATES_PATH, [])
        if isinstance(raw, dict):
            raw = raw.get('events')
        templates: Dict[str, str] = {}
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                eid = str(item.get('id') or '').strip()
                if not eid:
                    continue
                templates[eid] = str(item.get('text') or '')
        return templates
    except Exception:
        return {}


EVENT_TEMPLATES: Dict[str, str] = _load_event_templates()


def _crew_voice(role: str) -> str:
    """Return the configured voice for a crew role.
    Preserve case when provider is explicit (e.g., 'piper:en_GB-alan-medium').
    // Invariant guard: consistency suite — fix lowercasing that broke Piper model lookups
    """
    try:
        from .. import webdash as wd  # type: ignore
        crew = wd.CREW
        r = (crew.get('roles') or {}).get(role)
        v = (r or {}).get('voice')
        defaults = crew.get('defaults') or {}
        default_voice = defaults.get('voice')
        raw = str(v or default_voice or os.environ.get('OPENAI_TTS_VOICE', 'alloy'))
        s = (raw or '').strip()
        if ':' in s:
            # Keep exact case for model id; only provider name is case-insensitive
            parts = s.split(':', 1)
            return f"{parts[0].strip().lower()}:{parts[1].strip()}"
        # OpenAI-style short names — normalize to lowercase
        s_low = s.lower()
        if s_low == 'ash':
            s_low = 'alloy'
        return s_low
    except Exception:
        return os.environ.get('OPENAI_TTS_VOICE', 'alloy')


def voice_emit(event_id: str, ctx: Dict[str, Any] | None = None, *, fallback: str | None = None, role: str | None = None) -> None:
    try:
        from .. import webdash as wd  # type: ignore
        ev = VOICE_EVENTS.get(str(event_id)) or {}
        r = role or ev.get('role') or 'Ensign'
        templ = ev.get('hint') or fallback or ''
        if not templ: return
        try:
            txt = templ.format_map({k: ("—" if v is None else v) for k, v in (ctx or {}).items()})
        except Exception:
            txt = templ
        if txt:
            wd.record_officer(str(r), txt)
    except Exception:
        pass


def format_event_text(event_id: str, ctx: Dict[str, Any] | None = None) -> str:
    template = EVENT_TEMPLATES.get(str(event_id))
    if not template:
        return str(event_id).replace('.', ' ')

    class _Safe(dict):
        def __missing__(self, key):  # type: ignore[override]
            return '—'

    # Invariant guard: consistency suite — normalize None values (e.g., TTI) to '—'
    safe_ctx = {k: ('—' if v is None else v) for k, v in (ctx or {}).items()}
    try:
        return template.format_map(_Safe(**safe_ctx))
    except Exception:
        return template


# Invariant guard: consistency suite — shared resolve helper & envelope check
def _envelope_ok(weapon: str, target_name: str | None, range_nm: float) -> bool:
    """Return True if the weapon-target-range combination is inside the envelope.
    Uses the same compute_in_range() function shared with fire gates.
    """
    try:
        primary = {"name": (target_name or "Target"), "range_nm": float(range_nm)}
        return bool(compute_in_range(str(weapon), primary))
    except Exception:
        return False


def _resolve_shot_once(*, weapon: str, target_id: int | None, target_name: str | None,
                       target_class: str | None, range_nm: float, shot_id: str,
                       pk: float) -> str:
    """Resolve a single shot and update AUDIO_STATE (remove from in-flight, append to archive).

    Returns a result label: 'hit', 'miss', or 'no_effect' for out-of-envelope.
    """
    now_ts = time.time()
    # Determine envelope first
    allowed = _envelope_ok(weapon, target_name, range_nm)
    # Decide outcome
    if not allowed:
        outcome = 'no_effect'
    else:
        try:
            hit = (random.random() < float(pk))
        except Exception:
            hit = False
        outcome = 'hit' if hit else 'miss'

    # Remove from in-flight and append to archive immediately
    try:
        shots_state = AUDIO_STATE.get('shots_in_flight')
        if not isinstance(shots_state, list):
            shots_state = []
        new_list = []
        archive = AUDIO_STATE.get('shots_archive')
        if not isinstance(archive, list):
            archive = []
        fired_ts = now_ts
        due_ts = now_ts
        for rec in shots_state:
            if rec.get('id') == shot_id:
                try:
                    fired_ts = float(rec.get('fired_ts', fired_ts) or fired_ts)
                except Exception:
                    pass
                try:
                    due_ts = float(rec.get('due_ts', due_ts) or due_ts)
                except Exception:
                    pass
                # Skip adding back to in-flight list (remove immediately)
                continue
            new_list.append(rec)
        AUDIO_STATE['shots_in_flight'] = new_list
        archive.append({
            'id': shot_id,
            'weapon': weapon,
            'target_id': target_id,
            'target_name': target_name,
            'target_class': target_class,
            'range_nm': float(range_nm),
            'pk': float(pk),
            'fired_ts': fired_ts,
            'due_ts': due_ts,
            'outcome': outcome,
            'result_ts': now_ts,
        })
        AUDIO_STATE['shots_archive'] = archive
        AUDIO_STATE['last_result'] = {'event': outcome, 'ts': now_ts}
    except Exception:
        pass
    return outcome


def _tts_synthesize(text: str, role: str) -> str | None:
    txt = (text or '').strip()
    if not txt:
        return None
    voice_spec = _crew_voice(role).strip()
    provider_env = os.environ.get('TTS_PROVIDER', '').strip().lower()
    provider_default = provider_env or 'openai'
    forced_piper = (provider_env == 'piper')
    if ':' in voice_spec:
        provider, voice_id = voice_spec.split(':', 1)
        provider = provider.strip().lower(); voice_id = voice_id.strip()
    else:
        provider, voice_id = provider_default, voice_spec
    # Force Piper globally if requested, regardless of crew.json
    if forced_piper and provider != 'piper':
        provider = 'piper'
        if not voice_id or voice_id.lower() in ('', 'alloy', 'ash', 'verse', 'openai'):
            voice_id = os.environ.get('TTS_PIPER_DEFAULT_MODEL', 'en_GB-alan-medium')
    def _hash_name(ext: str) -> tuple[str, Path]:
        h = hashlib.sha1(f"{provider}|{voice_id}|{txt}".encode('utf-8')).hexdigest()[:20]
        fname = f"{h}.{ext}"; return fname, (TTS_DIR / fname)
    if provider == 'macos':  # pragma: no cover
        try:
            fname, aiff = _hash_name('aiff')
            if (TTS_DIR / (fname[:-5] + 'm4a')).exists():
                return f"/data/tts/{fname[:-5] + 'm4a'}"
            if not aiff.exists():
                import subprocess
                subprocess.run(["say", "-v", voice_id or 'Daniel', "-o", str(aiff), txt], check=True, timeout=20)
            m4a = TTS_DIR / (fname[:-5] + 'm4a')
            try:
                import subprocess
                subprocess.run(["afconvert", str(aiff), str(m4a), "-f", "mp4f", "-d", "aac"], check=True, timeout=20)
                return f"/data/tts/{m4a.name}"
            except Exception:
                return f"/data/tts/{fname}"
        except Exception as e:
            logging.warning("macOS TTS error: %s", e)
            return None
    if provider == 'piper':  # pragma: no cover
        try:
            import shutil
            piper_bin = os.environ.get('TTS_PIPER_BIN', 'piper')
            if not shutil.which(piper_bin):
                logging.error("Piper forced but binary not found: %s (set TTS_PIPER_BIN)", piper_bin)
                return None if forced_piper else None
            model_dir = Path(os.environ.get('TTS_PIPER_MODEL_DIR', str(VOICES_DIR)))
            model_path = Path(voice_id)
            if not model_path.exists():
                model_path = model_dir / (voice_id + ('' if voice_id.endswith('.onnx') else '.onnx'))
            if not model_path.exists():
                logging.error("Piper forced but model not found: %s (set TTS_PIPER_MODEL_DIR or TTS_PIPER_DEFAULT_MODEL)", model_path)
                return None
            fname_wav, wav = _hash_name('wav')
            if not wav.exists():
                import subprocess
                cmd = [piper_bin, "--model", str(model_path), "--output_file", str(wav), "--text", txt]
                ls = os.environ.get('TTS_PIPER_LENGTH'); ns = os.environ.get('TTS_PIPER_NOISE'); nw = os.environ.get('TTS_PIPER_NOISEW')
                if ls: cmd += ["--length_scale", str(ls)]
                if ns: cmd += ["--noise_scale", str(ns)]
                if nw: cmd += ["--noise_w", str(nw)]
                subprocess.run(cmd, check=True, timeout=30)
            m4a = TTS_DIR / (fname_wav[:-3] + 'm4a')
            try:
                import subprocess
                subprocess.run(["afconvert", str(wav), str(m4a), "-f", "mp4f", "-d", "aac"], check=True, timeout=20)
                return f"/data/tts/{m4a.name}"
            except Exception:
                mp3 = TTS_DIR / (fname_wav[:-3] + 'mp3')
                try:
                    import subprocess
                    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), str(mp3)], check=True, timeout=20)
                    return f"/data/tts/{mp3.name}"
                except Exception:
                    return f"/data/tts/{wav.name}"
        except Exception as e:
            logging.error("Piper TTS error: %s", e); return None if forced_piper else None
    # Default: OpenAI TTS
    if forced_piper:
        # Do not silently fall back when Piper is forced
        logging.error("TTS_PROVIDER=piper set; skipping OpenAI fallback for role=%s", role)
        return None
    key = os.environ.get('OPENAI_API_KEY')
    if not key or requests is None:
        return None
    model = os.environ.get('OPENAI_TTS_MODEL', 'gpt-4o-mini-tts')
    voice = voice_id or os.environ.get('OPENAI_TTS_VOICE', 'alloy')
    h = hashlib.sha1(f"{model}|{voice}|{txt}".encode('utf-8')).hexdigest()[:20]
    fname = f"{h}.mp3"; fpath = TTS_DIR / fname
    if fpath.exists():
        return f"/data/tts/{fname}"
    url = "https://api.openai.com/v1/audio/speech"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"model": model, "input": txt, "voice": voice, "format": "mp3"}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code == 200:
            fpath.write_bytes(r.content)
            return f"/data/tts/{fname}"
        else:
            logging.warning("OpenAI TTS failed %s: %s", r.status_code, r.text[:200])
            return None
    except Exception as e:
        logging.warning("OpenAI TTS error: %s", e)
        return None


# ---- Debug contacts storage ----
DEBUG_CONTACTS: list[Dict[str, Any]] = []
DEBUG_NEXT_ID: int = 1


def _make_debug_contact(cell: str | None = None,
                        name: str | None = None,
                        typ: str | None = None,
                        range_nm: float | None = None,
                        course: int | None = None,
                        speed: int | None = None) -> dict:
    global DEBUG_NEXT_ID
    cid = int(DEBUG_NEXT_ID)
    DEBUG_NEXT_ID += 1
    c = {
        "id": cid,
        "cell": cell or "K13",
        "name": name or "Contact",
        "type": typ or "Unknown",
        "range_nm": float(range_nm if range_nm is not None else 10.0),
        "course": int(course if course is not None else 90),
        "speed": int(speed if speed is not None else 300),
    }
    return c


# ---- Spawners reused by webdash ----
def spawn_initial_friendlies(wd) -> None:
    try:
        st = wd.ENG.public_state() if hasattr(wd.ENG, "public_state") else {}
        own_x, own_y = radar_xy_from_state(st)
        if getattr(wd.RADAR, 'contacts', None):
            return
        for b in (45.0, 135.0, 315.0):
            try:
                r = random.uniform(6.0, 12.0)
                wd.RADAR.force_spawn(own_x, own_y, 'Friendly', bearing_deg=b, range_nm=r)
            except Exception:
                continue
    except Exception:
        pass


def spawn_hostile_by_name(wd, own_x: float, own_y: float, *, name: str, range_nm: float, bearing_deg: float):
    try:
        from ..radar import Contact, WORLD_N  # late import to avoid cycles
    except Exception:
        return None
    try:
        rad = math.radians(float(bearing_deg))
        dx = math.sin(rad) * float(range_nm)
        dy = -math.cos(rad) * float(range_nm)
        x = max(0.0, min(float(WORLD_N), own_x + dx))
        y = max(0.0, min(float(WORLD_N), own_y + dy))
        # Speed from contacts catalog if available
        try:
            data = _load_json(CONTACTS_PATH, [])
            items = data.get('items') if isinstance(data, dict) else data
            speed = next(float(it.get('speed_kts', 300.0)) for it in (items or []) if isinstance(it, dict) and str(it.get('name',''))==name)
        except Exception:
            speed = 300.0
        next_id = getattr(wd.RADAR, "_next_id", len(wd.RADAR.contacts) + 1)
        try:
            det = wd.RADAR.catalog.details(name)
        except Exception:
            det = {}
        c = Contact(
            id=int(next_id), name=str(name), allegiance="Hostile",
            x=float(x), y=float(y), course_deg=(float(bearing_deg) + 180.0) % 360.0, speed_kts=float(speed),
            threat="high",
            meta={"spawn": {"bearing_deg": round(float(bearing_deg),1), "range_nm": round(float(range_nm),2), "forced": True},
                  "cap": det}
        )
        try:
            wd.RADAR._next_id = int(next_id) + 1  # type: ignore[attr-defined]
        except Exception:
            pass
        wd.RADAR.contacts.append(c)
        return c
    except Exception:
        return None


# ---- CAP snapshot + engine loop (minimal) ----
def cap_ui_snapshot(wd) -> Dict[str, Any]:
    try:
        if wd.CAP is None:
            return {"ready": False, "pairs": 0, "airframes": 0, "cooldown_s": 0, "committed": 0, "tasks": []}
        snap = wd.CAP.snapshot()
        r = snap.get('readiness') or {}
        missions = list(snap.get('missions') or [])
        try:
            st = wd.ENG.public_state() if hasattr(wd.ENG, 'public_state') else {}
            own_x, own_y = wd.get_own_xy(st)
        except Exception:
            own_x, own_y = (0.0, 0.0)
        now = time.time(); tasks: list[Dict[str, Any]] = []
        for m in missions:
            try:
                cid = int(m.get('id'))
                cell = str(m.get('target_cell') or '')
                tx, ty = cell_to_world(cell) if cell else (None, None)
                rng = None
                if tx is not None and ty is not None:
                    dx = float(tx) - float(own_x); dy = float(ty) - float(own_y)
                    rng = (dx*dx + dy*dy) ** 0.5
                ts = (m.get('timestamps') or {})
                status = str(m.get('status') or '')
                tot_s = None
                try:
                    eta_on = ts.get('eta_onstation')
                    if isinstance(eta_on, (int, float)) and status in ('queued','airborne'):
                        tot_s = max(0, int(eta_on - now))
                except Exception:
                    tot_s = None
                tos_s = None
                try:
                    etd_rtb = ts.get('etd_rtb')
                    if isinstance(etd_rtb, (int, float)) and status == 'onstation':
                        tos_s = max(0, int(etd_rtb - now))
                except Exception:
                    tos_s = None
                vect = bool(ts.get('vector', False))
                cur_cell = cell or '—'
                tasks.append({"n": cid, "cur_cell": cur_cell, "target_cell": cell or '—', "range_nm": (round(rng,1) if isinstance(rng,(int,float)) else None), "status": status, "tot_s": tot_s, "tos_s": tos_s, "vector": vect, "engaged": bool(m.get('last_engagement'))})
            except Exception:
                continue
        committed = len([t for t in tasks if t.get('status') in ('queued','airborne','onstation','rtb','recovering')])
        return {"ready": bool(r.get('available', False)), "pairs": int(r.get('ready_pairs', 0) or 0), "airframes": int(r.get('airframes', 0) or 0), "cooldown_s": int(r.get('cooldown_s', 0) or 0), "committed": int(committed), "tasks": tasks}
    except Exception:
        return {"ready": False, "pairs": 0, "airframes": 0, "cooldown_s": 0, "committed": 0, "tasks": []}


def get_tick_seconds(wd) -> float:
    v = getattr(wd.ENG, "tick_seconds", None)
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    cfg = getattr(wd.ENG, "config", None)
    if isinstance(cfg, dict):
        try:
            w = float(cfg.get("tick_seconds", 0));
            if w > 0: return w
        except Exception:
            pass
    settings = getattr(wd.ENG, "settings", None)
    if isinstance(settings, dict):
        try:
            w = float(settings.get("tick_seconds", 0));
            if w > 0: return w
        except Exception:
            pass
    return 1.0


def engine_thread_run(wd) -> None:
    import random as _rand
    while True:  # minimal, resilient loop
        try:
            dt = clamp(float(get_tick_seconds(wd)), 0.05, 1.0)
        except Exception:
            dt = 1.0
        now = time.time()
        # Engine tick
        try:
            wd.ENG.tick(dt)
        except Exception:
            pass
        # Radar tick + priority/alarms inside
        try:
            st = wd.ENG.public_state() if hasattr(wd.ENG, "public_state") else {}
            ox, oy = radar_xy_from_state(st)
            wd.RADAR.tick(dt, ox, oy)
        except Exception:
            pass
        # CAP tick + auto-engage if primary in range
        try:
            if wd.CAP is not None:
                wd.CAP.tick()
                pid = getattr(wd.RADAR, 'priority_id', None)
                if pid is not None:
                    # compute range to primary
                    tgt = next((c for c in wd.RADAR.contacts if int(getattr(c,'id',-1))==int(pid)), None)
                    if tgt is not None:
                        rng = ((tgt.x-ox)**2 + (tgt.y-oy)**2) ** 0.5
                        wd.CAP.auto_engage(rng, int(pid))
                # ROE ask: if any mission is on-station and near the primary, request authorization
                try:
                    pid = getattr(wd.RADAR, 'priority_id', None)
                    tgt = next((c for c in wd.RADAR.contacts if int(getattr(c,'id',-1))==int(pid)), None) if pid is not None else None
                    if tgt is not None:
                        tx, ty = float(getattr(tgt,'x',0.0)), float(getattr(tgt,'y',0.0))
                        for m in getattr(wd.CAP, 'missions', []) or []:
                            try:
                                if str(getattr(m,'status','')) != 'onstation':
                                    continue
                                mid = int(getattr(m,'id',0))
                                cell = str(getattr(m,'target_cell','') or '')
                                mx, my = cell_to_world(cell) if cell else (ox, oy)
                                dist = ((tx-mx)**2 + (ty-my)**2) ** 0.5
                                if dist > 15.0:
                                    continue
                                if int(getattr(m, 'missiles_left', 1) or 0) <= 0:
                                    continue
                                perm = wd.CAP.permission_state(mid) if hasattr(wd.CAP, 'permission_state') else None
                                required = True if perm is None else bool(perm.get('required', True))
                                authorized = False if perm is None else bool(perm.get('authorized', False))
                                last_prompt = 0.0 if perm is None else float(perm.get('last_prompt_ts') or 0.0)
                                if not required or authorized:
                                    continue
                                meta = getattr(wd, 'CAP_META', {})
                                rec = meta.get(mid) or {}
                                last_prompt = max(last_prompt, float(rec.get('last_request_ts', 0.0) or 0.0))
                                if (not rec.get('asked')) or (now - last_prompt >= 30.0):
                                    rec['asked'] = True
                                    rec['authorized'] = False
                                    rec['last_request_ts'] = now
                                    rec['hold_since_ts'] = rec.get('hold_since_ts') or now
                                    meta[mid] = rec
                                    try:
                                        wd.CAP.mark_permission_prompted(mid, now)
                                    except Exception:
                                        pass
                                    try:
                                        wd.record_officer('Pilot', f'Request permission to engage target {int(pid)} at {dist:.1f} nm.')
                                    except Exception:
                                        pass
                            except Exception:
                                continue
                except Exception:
                    pass
        except Exception:
            pass
        # Resolve pending events
        try:
            due = []
            for ev in list(wd.PENDING_EVENTS):
                if float(ev.get('due', 0)) <= now:
                    due.append(ev)
            for ev in due:
                try:
                    kind = str(ev.get('kind') or '')
                    if kind == 'resolve_fire':
                        # Find target by id and resolve with invariant guard
                        tgt_id = int(ev.get('target_id')) if ev.get('target_id') is not None else None
                        rng = float(ev.get('range_nm') or 0.0)
                        weapon = str(ev.get('weapon') or '')
                        pk = float(ev.get('pk') or 0.6)
                        shot_id = str(ev.get('shot_id') or '')
                        target_name = str(ev.get('target_name') or '')
                        target_class = str(ev.get('target_class') or '')
                        with wd.STATE_LOCK:
                            outcome = _resolve_shot_once(
                                weapon=weapon,
                                target_id=tgt_id,
                                target_name=target_name,
                                target_class=target_class,
                                range_nm=rng,
                                shot_id=shot_id,
                                pk=pk,
                            )
                        # On hit, remove the target from radar contacts
                        if outcome == 'hit' and tgt_id is not None:
                            wd.RADAR.contacts = [c for c in wd.RADAR.contacts if int(getattr(c,'id',-1)) != int(tgt_id)]
                        try:
                            ev_id = 'weapon.result.hit' if outcome == 'hit' else ('weapon.result.miss' if outcome == 'miss' else 'weapon.result.no_effect')
                            wd.record_event(ev_id, {'weapon': weapon, 'target_id': tgt_id, 'range_nm': rng})
                        except Exception:
                            pass
                        try:
                            record_flight({
                                'route': '/weapons.resolve', 'method': 'INT', 'status': 200, 'duration_ms': 0,
                                'request': {'weapon': weapon, 'rng': rng, 'target_id': tgt_id},
                                'response': {'hit': bool(outcome == 'hit')}
                            })
                        except Exception:
                            pass
                    # ignore arming_ready (load_arming handles it)
                finally:
                    try:
                        wd.PENDING_EVENTS.remove(ev)
                    except Exception:
                        pass
        except Exception:
            pass
        # Enemy attack when inside 1 nm (cooldown per contact)
        try:
            hlth = _load_health()
            att = getattr(wd, 'ATTACK_STATE', {})
            for c in list(wd.RADAR.contacts):
                try:
                    if str(getattr(c,'allegiance','')) != 'Hostile':
                        continue
                    dist = ((c.x-ox)**2 + (c.y-oy)**2) ** 0.5
                    if dist <= 1.0:
                        cid = int(getattr(c,'id',-1))
                        last = float(att.get(cid, 0.0))
                        if now - last >= 15.0:
                            att[cid] = now
                            try:
                                meta = getattr(c, 'meta', {}) or {}
                                weapon_name = str(meta.get('primary_weapon') or getattr(c, 'primary_weapon', '')).lower()
                                if 'bomb' in weapon_name:
                                    hit_prob = 0.6
                                    attempts = 2
                                elif 'missile' in weapon_name:
                                    hit_prob = 0.75
                                    attempts = 1
                                else:
                                    hit_prob = 0.5
                                    attempts = 1
                            except Exception:
                                hit_prob = 0.5
                                attempts = 1

                            results: list[dict[str, Any]] = []
                            for attempt_idx in range(1, attempts+1):
                                hit = (_rand.random() < hit_prob)
                                result_entry = {'attempt': attempt_idx, 'event': 'hit' if hit else 'miss'}
                                results.append(result_entry)
                                if hit:
                                    if int(hlth.get('lives', 1)) > 0:
                                        hlth['lives'] = int(hlth.get('lives', 1)) - 1
                                        _save_health(hlth)
                                    try:
                                        wd.record_event('enemy.bomb.hit', {
                                            'contact_id': cid,
                                            'name': getattr(c, 'name', ''),
                                            'range_nm': round(dist, 2),
                                            'attempt': attempt_idx
                                        })
                                    except Exception:
                                        pass
                                    try:
                                        eng = load_eng_sys()
                                        sys_list = [s for s in eng.get('systems', []) if str(s.get('status')) == 'OK']
                                        if sys_list:
                                            s = _rand.choice(sys_list)
                                            s['status'] = 'Offline'
                                            s['timer_s'] = 0
                                            s['last_damaged_ts'] = now
                                            s['response_deadline_ts'] = now + 120.0
                                            save_eng_sys(eng)
                                            try:
                                                wd.record_event('eng.system.timer', {'system': s.get('name','System'), 'seconds': s.get('timer_s', 0)})
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                                else:
                                    try:
                                        wd.record_event('enemy.bomb.miss', {
                                            'contact_id': cid,
                                            'name': getattr(c, 'name', ''),
                                            'range_nm': round(dist, 2),
                                            'attempt': attempt_idx
                                        })
                                    except Exception:
                                        pass

                            if results:
                                try:
                                    with wd.STATE_LOCK:
                                        wd.AUDIO_STATE['enemy_bomb'] = {'ts': time.time(), 'events': results}
                                except Exception:
                                    pass
                except Exception:
                    continue
        except Exception:
            pass
        # Tick ENG repairs counting down timers
        try:
            eng = load_eng_sys()
            changed = False
            teams_total = int(eng.get('teams_total', 0) or 0)
            for s in eng.get('systems', []) or []:
                try:
                    status = str(s.get('status', 'OK'))
                    assigned = bool(s.get('team_assigned'))
                    timer = int(s.get('timer_s', 0) or 0)
                    resp_deadline = float(s.get('response_deadline_ts', 0.0) or 0.0)

                    if status == 'Offline':
                        if assigned and timer <= 0:
                            s['status'] = 'Damaged'
                            s['timer_s'] = 120
                            s['last_damaged_ts'] = now
                            s['response_deadline_ts'] = 0.0
                            changed = True
                            status = 'Damaged'
                            timer = 120
                        elif not assigned and resp_deadline and now >= resp_deadline:
                            s['status'] = 'Damaged'
                            s['response_deadline_ts'] = 0.0
                            changed = True
                            status = 'Damaged'

                    if assigned and timer > 0:
                        new_timer = max(0, timer - int(dt))
                        if new_timer != timer:
                            s['timer_s'] = new_timer
                            changed = True
                        if new_timer == 0:
                            s['status'] = 'OK'
                            s['last_damaged_ts'] = 0.0
                            s['response_deadline_ts'] = 0.0
                            if assigned:
                                s['team_assigned'] = False
                                current_free = int(eng.get('teams_free', 0) or 0)
                                eng['teams_free'] = min(teams_total, current_free + 1)
                            changed = True
                    else:
                        s['timer_s'] = timer
                except Exception:
                    continue
            if changed:
                save_eng_sys(eng)
        except Exception:
            pass
        time.sleep(dt)
