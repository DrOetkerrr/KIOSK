from __future__ import annotations

# Diff plan (RADAR Phase‑1 Web):
# - Advance RADAR.tick alongside ENG.tick using ship col/row each loop.
# - Enrich /api/status with catalog-driven RADAR contacts (sorted by range), threats (hostiles), top_threat_id,
#   and primary (when RADAR.priority_id is set).
# - Extend /api/command to handle "/radar lock <id>" and "/radar unlock" against the live RADAR instance.
# - Add /radar/reload_catalog, /radar/force_spawn_hostile, /radar/force_spawn_friendly dev routes using RADAR.force_spawn.
# - Preserve recorder logging, /health, /about, template serving, and existing dev routes.

# Falklands V2 — Web Dashboard (robust server)
# Repairs:
# - Avoid reliance on ENG.game_cfg; uses a safe tick helper.
# - Adds /health endpoint that always returns JSON.
# - Hardens /api/status to never return an empty body.
# - Configurable port via PORT env (default 5000).
# - Engine background thread is resilient and runs as a daemon.

# ---- stdlib imports and repo path setup ----
import os, sys, time, threading, logging, hashlib, pathlib, random, math
from collections import deque
from pathlib import Path
from typing import Any, Dict
import json
from datetime import datetime, timezone

# Compute repo root so `projects.*` absolute imports resolve
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[2]  # .../kiosk
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Server port (default 5055 to match verify tools)
try:
    PORT = int(os.environ.get("PORT", "5055"))
except Exception:
    PORT = 5055

# ---- third-party ----
from flask import Flask, jsonify, render_template, request, send_from_directory  # type: ignore

# ---- engine import (absolute) ----
from projects.falklands.core.engine import Engine
from projects.falklandV2.radar import Radar, Contact, HOSTILES, WORLD_N
# Prefer relative import; fallback to absolute when executed as a script
try:
    from .engine_adapter import world_to_cell, contact_to_ui, get_own_xy
except Exception:
    from projects.falklandV2.engine_adapter import world_to_cell, contact_to_ui, get_own_xy


# ---- Flask app ----
TPL_DIR = Path(__file__).parent / "templates"
app = Flask(__name__, template_folder=str(TPL_DIR))


# ---- Engine instance and helpers ----
ENG = Engine(state_path=Path.home() / "Documents" / "kiosk" / "falklands_state.json")

# App start time (for /about)
APP_STARTED = datetime.now(timezone.utc)

# Convoy lag state (escorts adopt new course/speed after random 30–50s)
_CONVOY_LAG = {"init": False, "last_course": 0.0, "last_speed": 0.0, "last_set": 0.0, "delay_s": 35.0}

def _convoy_lagged(course_deg: float, speed_kts: float) -> tuple[float, float]:
    now = time.time()
    st = _CONVOY_LAG
    if not st["init"]:
        st["last_course"] = float(course_deg)
        st["last_speed"] = float(speed_kts)
        st["last_set"] = now
        st["delay_s"] = random.uniform(30.0, 50.0)
        st["init"] = True
        return st["last_course"], st["last_speed"]
    changed = (abs((float(course_deg) - st["last_course"]) % 360.0) > 0.1) or (abs(float(speed_kts) - st["last_speed"]) > 0.1)
    if changed and (now - st["last_set"]) >= st["delay_s"]:
        st["last_course"] = float(course_deg) % 360.0
        st["last_speed"] = max(0.0, float(speed_kts))
        st["last_set"] = now
        st["delay_s"] = random.uniform(30.0, 50.0)
    return st["last_course"], st["last_speed"]

# ---- Lightweight audio + event scheduler ----
# Frontend polls /api/status.audio; sound.js plays files for last_launch/last_result
AUDIO_STATE: Dict[str, Any] = {"last_launch": None, "last_result": None}

# Pending delayed events (e.g., shot results); each item:
# { 'due': float_ts, 'kind': 'resolve_shot', 'weapon': str, 'target_id': int,
#   'target_name': str, 'target_class': str, 'range_nm': float }
PENDING_EVENTS: list[Dict[str, Any]] = []

def _sound_key_for_weapon(name: str) -> str:
    s = (name or "").lower()
    if "sea dart" in s or "seacat" in s:
        return "seacat"
    if "4.5" in s or "mk.8" in s or "mk8" in s:
        return "gun_4_5in"
    if "oerlikon" in s:
        return "oerlikon_20mm"
    if "gam-bo1" in s or "gam" in s:
        return "gam_bo1_20mm"
    if "exocet" in s or "mm38" in s:
        return "exocet_mm38"
    if "chaff" in s:
        return "corvus_chaff"
    return "weapon_launch"

def _hit_probability(weapon_name: str, target_class: str, range_nm: float) -> float:
    w = (weapon_name or "").lower(); cls = (target_class or "").title()
    r = max(0.0, float(range_nm))
    # Sea Dart vs Aircraft: good PK, slight range degradation
    if "sea dart" in w or "seacat" in w:
        if cls != "Aircraft":
            return 0.15
        # ~0.7 at close, ~0.5 at max 35 nm
        return max(0.2, min(0.85, 0.7 - 0.2 * (r / 35.0)))
    # 4.5-inch vs Ship: moderate PK decreasing with range (≤8 nm envelope)
    if "4.5" in w or "mk.8" in w or "mk8" in w:
        if cls != "Ship":
            return 0.1
        # ~0.45 at 0 nm to ~0.21 at 8 nm
        return max(0.05, min(0.6, 0.45 - 0.03 * r))
    # Exocet vs Ship (placeholder if enabled later)
    if "exocet" in w:
        if cls != "Ship":
            return 0.05
        return max(0.2, min(0.8, 0.6 - 0.01 * r))
    # 20mm: very low PK; treated as barrage
    if "20mm" in w or "oerlikon" in w or "gam" in w:
        return 0.05
    return 0.2

def _flight_time_seconds(weapon_name: str, range_nm: float) -> float:
    w = (weapon_name or "").lower(); r = max(0.0, float(range_nm))
    if "sea dart" in w or "seacat" in w or "exocet" in w:
        return 4.0 + (r * 6.0)
    if "4.5" in w or "mk.8" in w or "mk8" in w:
        return r * 2.0
    # 20mm very short effect time
    if "20mm" in w or "oerlikon" in w or "gam" in w:
        return max(0.5, min(4.0, r * 2.0))
    return max(1.0, r * 2.0)

def _schedule_shot_result(weapon_name: str, target_id: int, target_name: str, target_class: str, range_nm: float) -> None:
    due = time.time() + _flight_time_seconds(weapon_name, range_nm)
    PENDING_EVENTS.append({
        'due': due,
        'kind': 'resolve_shot',
        'weapon': weapon_name,
        'target_id': int(target_id),
        'target_name': str(target_name),
        'target_class': str(target_class),
        'range_nm': float(range_nm),
    })

def _process_due_events() -> None:
    now = time.time()
    if not PENDING_EVENTS:
        return
    remaining: list[Dict[str, Any]] = []
    for ev in PENDING_EVENTS:
        if float(ev.get('due', 0.0)) <= now and ev.get('kind') == 'resolve_shot':
            try:
                wid = int(ev.get('target_id'))
                tname = str(ev.get('target_name'))
                tclass = str(ev.get('target_class'))
                wname = str(ev.get('weapon'))
                rng = float(ev.get('range_nm', 0.0))
                # Locate target
                tgt = next((c for c in RADAR.contacts if int(getattr(c, 'id', -1)) == wid), None)
                pk = _hit_probability(wname, tclass, rng)
                hit = (random.random() < pk)
                if hit and tgt is not None:
                    # Remove contact
                    try:
                        RADAR.contacts = [c for c in RADAR.contacts if int(getattr(c, 'id', -1)) != wid]
                    except Exception:
                        pass
                    record_radio('ENGAGE', f"Hit: {tname}")
                    AUDIO_STATE['last_result'] = {'event': 'hit', 'ts': now}
                else:
                    record_radio('ENGAGE', f"Miss: {tname}")
                    AUDIO_STATE['last_result'] = {'event': 'miss', 'ts': now}
            except Exception:
                continue
        else:
            remaining.append(ev)
    # swap
    PENDING_EVENTS.clear()
    PENDING_EVENTS.extend(remaining)


def get_tick_seconds() -> float:
    """Return engine tick seconds from best available source (default 1.0)."""
    # Common patterns we tolerate
    v = getattr(ENG, "tick_seconds", None)
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    cfg = getattr(ENG, "config", None)
    if isinstance(cfg, dict):
        try:
            w = float(cfg.get("tick_seconds", 0))
            if w > 0:
                return w
        except Exception:
            pass
    settings = getattr(ENG, "settings", None)
    if isinstance(settings, dict):
        try:
            w = float(settings.get("tick_seconds", 0))
            if w > 0:
                return w
        except Exception:
            pass
    return 1.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def engine_thread() -> None:
    """Background ticking loop; resilient to transient errors."""
    while True:
        try:
            dt = _clamp(float(get_tick_seconds()), 0.05, 1.0)
            ENG.tick(dt)
            # Advance radar with own ship position
            try:
                st = ENG.public_state() if hasattr(ENG, "public_state") else {}
                own_x, own_y = get_own_xy(st)
                RADAR.tick(dt, own_x, own_y)
            except Exception:
                pass
            # process any due engagement events (hit/miss radio + sounds)
            try:
                _process_due_events()
            except Exception:
                pass
            time.sleep(dt)
        except Exception as e:
            logging.exception("engine_thread: tick failed: %s", e)
            time.sleep(0.5)


# Start the background thread (daemon)
_t = threading.Thread(target=engine_thread, daemon=True)
_t.start()


# ---- Flight recorder (lightweight) ----
LOG_DIR = REPO_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
FLIGHT_PATH = LOG_DIR / "flight.jsonl"

# ---- Data/State paths ----
DATA_DIR = Path(__file__).parent / "data"
STATE_DIR = Path(__file__).parent / "state"
AMMO_PATH = STATE_DIR / "ammo.json"
ARMING_PATH = STATE_DIR / "arming.json"
WEAP_CATALOG_PATH = DATA_DIR / "weapons_catalog.json"
CONTACTS_PATH = DATA_DIR / "contacts.json"

# ---- Grid conversion (world 40×40 → board A..Z × 1..26) ----
WORLD_N = 40
BOARD_N = 26

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
        t = 1.0 + (clamp(v, 0.0, float(WORLD_N)) * (BOARD_N - 1) / float(WORLD_N))
        return int(round(clamp(t, 1.0, float(BOARD_N))))
    return mapv(row), mapv(col)

def board_to_cell(row_i: int, col_i: int) -> str:
    return f"{_idx_to_letters(int(col_i))}{int(row_i)}"

def cell_for_world(row: float, col: float) -> str:
    r_i, c_i = world_to_board(row, col)
    return board_to_cell(r_i, c_i)

def _truncate(val: Any, max_len: int = 400) -> Any:
    if isinstance(val, str) and len(val) > max_len:
        return val[:max_len] + "…"
    return val

def record_flight(ev: Dict[str, Any]) -> None:
    try:
        base = {"ts": datetime.now(timezone.utc).isoformat(), "hud": None}
        try:
            base["hud"] = ENG.hud_line() if hasattr(ENG, "hud_line") else None
        except Exception:
            base["hud"] = None
        rec = {**base, **ev}
        # truncate long response values
        if isinstance(rec.get("response"), dict):
            rec["response"] = {k: _truncate(v) for k, v in rec["response"].items()}
        with FLIGHT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

# ---- Weapons + Targets catalog helpers ----
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

def _load_weapons_catalog():
    catalog = _load_json(WEAP_CATALOG_PATH, [])
    if not isinstance(catalog, list):
        catalog = []
    m = {str(it.get("name","")): it for it in catalog if isinstance(it, dict)}
    return catalog, m

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

WEAP_CATALOG, WEAP_MAP = _load_weapons_catalog()
TARGET_CLASS_BY_NAME = _load_targets_class_map()

WEAP_DEFAULT_AMMO = {
    "MM38 Exocet": 4,
    "4.5 inch Mk.8 gun": 550,       # HE rounds (design spec)
    "Sea Dart SAM": 26,
    "20mm Oerlikon": 5000,          # design spec
    "20mm GAM-BO1 (twin)": 1850,    # from ship data
    "Corvus chaff": 15,
}
WEAP_DEFAULT_ARMING = {
    "MM38 Exocet": "Safe",
    "4.5 inch Mk.8 gun": "Safe",
    "Sea Dart SAM": "Armed",
    "20mm Oerlikon": "Armed",
    "20mm GAM-BO1 (twin)": "Armed",
    "Corvus chaff": "Safe",
}

# Normalize various legacy/local names to catalog names
def _normalize_weapon_name(name: str) -> str:
    s = (name or "").strip().lower()
    aliases = {
        # SAMs
        "seacat": "Sea Dart SAM",
        "gws-24 seacat sam": "Sea Dart SAM",
        "gws.24 seacat": "Sea Dart SAM",
        "sea dart": "Sea Dart SAM",
        "sea dart sam": "Sea Dart SAM",
        # Guns
        "4.5in": "4.5 inch Mk.8 gun",
        "4.5 inch": "4.5 inch Mk.8 gun",
        "gun_4_5in": "4.5 inch Mk.8 gun",
        "mk.8": "4.5 inch Mk.8 gun",
        # 20mm
        "oerlikon": "20mm Oerlikon",
        "oerlikon_20mm": "20mm Oerlikon",
        "20 mm oerlikon": "20mm Oerlikon",
        "gam-bo1": "20mm GAM-BO1 (twin)",
        "gam_bo1_20mm": "20mm GAM-BO1 (twin)",
        # Missiles/decoys
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
        # numeric truthiness
        if isinstance(v, (int, float)):
            return "Armed" if float(v) != 0.0 else "Safe"
    except Exception:
        pass
    return "Safe"

def _ammo_defaults_from_ship() -> Dict[str, int]:
    """Best-effort defaults sourced from data/ship.json (design spec)."""
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
            # Legacy nested format
            for k, v in raw.get("weapons", {}).items():
                nm = _normalize_weapon_name(str(k))
                amt = 0
                try:
                    if isinstance(v, dict):
                        # prefer 'rounds', then 'ammo', then 'salvoes'
                        if 'rounds' in v:
                            amt = int(v.get('rounds') or 0)
                        elif 'ammo' in v:
                            amt = int(v.get('ammo') or 0)
                        elif 'salvoes' in v:
                            amt = int(v.get('salvoes') or 0)
                        else:
                            amt = int(next(iter(v.values()))) if v else 0
                    else:
                        amt = int(v)
                except Exception:
                    amt = 0
                if nm:
                    normalized[nm] = max(0, int(amt))
        elif isinstance(raw, dict):
            # Flat map; coerce values and normalize names
            for k, v in raw.items():
                nm = _normalize_weapon_name(str(k))
                try:
                    normalized[nm] = max(0, int(v))
                except Exception:
                    continue
    except Exception:
        normalized = {}
    # Merge defaults from design spec and ship.json; avoid showing zeros for high-cap weapons
    base = {**WEAP_DEFAULT_AMMO, **_ammo_defaults_from_ship()}
    merged = dict(base)
    for k, v in normalized.items():
        try:
            vi = int(v)
        except Exception:
            continue
        if vi > 0:
            merged[k] = vi
        else:
            # If incoming value is 0 but base has a positive spec, keep base
            if base.get(k, 0) <= 0:
                merged[k] = 0
    # Opportunistic migration: write back flat normalized map if it differs
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
    """Return arming status per weapon: 'Armed' | 'Arming' | 'Safe'.
    Accepts legacy flat strings or structured records with 'armed' and 'arming_until'.
    Auto-flips to Armed when arming_until passes and persists that change.
    """
    raw = _load_json(ARMING_PATH, {})
    normalized: Dict[str, str] = {}
    dirty = False
    now = time.time()
    try:
        source = {}
        # Flatten possible nested {weapons: {...}}
        if isinstance(raw, dict) and isinstance(raw.get("weapons"), dict):
            source = raw.get("weapons", {})
        elif isinstance(raw, dict):
            source = raw
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
                    # Flip to Armed
                    normalized[nm] = 'Armed'
                    v['armed'] = True
                    v['arming_until'] = 0
                    dirty = True
                else:
                    normalized[nm] = 'Safe'
            else:
                normalized[nm] = _coerce_arming(v)
    except Exception:
        normalized = {}
    merged = {**WEAP_DEFAULT_ARMING, **normalized}
    # Persist any flips (arming complete) in structured file
    try:
        if dirty:
            # Reconstruct structure preserving any existing details
            if isinstance(raw, dict):
                if 'weapons' in raw and isinstance(raw['weapons'], dict):
                    _save_json(ARMING_PATH, raw)
                else:
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
    if not primary:
        return False
    w = WEAP_MAP.get(weapon_name)
    if not w:
        return False
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

# ---- Layout helpers: ownfleet, radio, cap ----
def _ownfleet_snapshot(state: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Return list of units for the Own Fleet box with expected fields.
    Each unit: {id, name, class, cell, speed, heading, status:{health_pct}}
    """
    out: list[Dict[str, Any]] = []
    ship = (state or {}).get('ship', {})
    # Own ship
    try:
        row = float(ship.get('row', 50.0)); col = float(ship.get('col', 50.0))
        cell = cell_for_world(row, col)
    except Exception:
        cell = cell_for_world(50.0, 50.0)
    own_name = _load_json(DATA_DIR / 'ship.json', {}).get('name', 'Own Ship')
    lives = int((state or {}).get('lives', 1) or 1)
    max_lives = int((state or {}).get('max_lives', 1) or 1)
    health_pct = int(round(100.0 * (lives / max(1, max_lives))))
    out.append({
        'id': 'own',
        'name': own_name,
        'class': 'DD',
        'cell': cell,
        'speed': ship.get('speed', 0),
        'heading': ship.get('heading', 0),
        'status': {'health_pct': health_pct},
    })
    # Convoy escorts (Hermes/Glamorgan) relative offsets if available
    convoy = _load_json(DATA_DIR / 'convoy.json', {})
    escorts = convoy.get('escorts', []) if isinstance(convoy, dict) else []
    # Compute own board indices to offset
    try:
        r_i, c_i = world_to_board(float(ship.get('row', 50.0)), float(ship.get('col', 50.0)))
    except Exception:
        r_i, c_i = (13, 11)
    def _escort_cell(dx: int, dy: int) -> str:
        rr = int(clamp(r_i + dy, 1, BOARD_N)); cc = int(clamp(c_i + dx, 1, BOARD_N))
        return board_to_cell(rr, cc)
    # Compute lagged course/speed for escorts to simulate following with delay
    eff_course, eff_speed = _convoy_lagged(float(ship.get('heading', 0.0)), float(ship.get('speed', 0.0)))
    # Hermes
    hermes = next((e for e in escorts if str(e.get('name','')).lower().find('hermes')>=0), None)
    if hermes:
        dx, dy = hermes.get('offset_cells', [-2,3])
        out.append({
            'id':'hermes',
            'name': hermes.get('name'),
            'class': hermes.get('class','Carrier'),
            'cell': _escort_cell(int(dx), int(dy)),
            'speed': eff_speed,
            'heading': eff_course,
            'status': {'health_pct': 100},
        })
    else:
        out.append({
            'id':'hermes', 'name':'HMS Hermes', 'class':'Carrier', 'cell': 'H11',
            'speed': eff_speed, 'heading': eff_course, 'status': {'health_pct':100}
        })
    # Glamorgan
    glam = next((e for e in escorts if str(e.get('name','')).lower().find('glamorgan')>=0), None)
    if glam:
        dx, dy = glam.get('offset_cells', [2,1])
        out.append({
            'id':'glamorgan',
            'name': glam.get('name'),
            'class': glam.get('class','DD'),
            'cell': _escort_cell(int(dx), int(dy)),
            'speed': eff_speed,
            'heading': eff_course,
            'status': {'health_pct': 100},
        })
    else:
        out.append({
            'id':'glamorgan','name':'HMS Glamorgan','class':'DD','cell': 'G12',
            'speed': eff_speed, 'heading': eff_course, 'status': {'health_pct':100}
        })
    return out

def _radio_latest(max_items: int = 4) -> list[Dict[str, Any]]:
    items: list[Dict[str, Any]] = []
    try:
        if not FLIGHT_PATH.exists():
            return []
        lines = FLIGHT_PATH.read_text(encoding='utf-8', errors='ignore').splitlines()
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            route = rec.get('route','')
            if str(route).endswith('radio.msg'):
                ts = str(rec.get('ts',''))
                try:
                    hhmmss = ts.split('T',1)[1].split('+',1)[0].split('Z',1)[0]
                except Exception:
                    hhmmss = ts
                data = rec.get('response') or {}
                items.append({'ts': hhmmss, 'kind': data.get('kind','EVT'), 'text': data.get('text','')})
                if len(items) >= max_items:
                    break
    except Exception:
        return []
    return items

def _cap_snapshot() -> Dict[str, Any]:
    return {'ready': False, 'pairs': 0, 'airframes': 0, 'cooldown_s': 0, 'committed': 0, 'tasks': []}


# ---- Template diagnostics ----
def _template_info(name: str = "index.html") -> Dict[str, Any]:
    p = (pathlib.Path(app.template_folder) / name).resolve()
    info: Dict[str, Any] = {
        "template_folder": str(pathlib.Path(app.template_folder).resolve()),
        "path": str(p),
        "exists": p.exists(),
    }
    if p.exists():
        b = p.read_bytes()
        info.update({
            "size": len(b),
            "mtime": os.path.getmtime(p),
            "sha1": hashlib.sha1(b).hexdigest(),
        })
    return info

def _file_info(p: Path) -> Dict[str, Any]:
    try:
        p = p.resolve()
        info: Dict[str, Any] = {"path": str(p), "exists": p.exists()}
        if p.exists():
            b = p.read_bytes()
            info.update({
                "size": len(b),
                "sha1": hashlib.sha1(b).hexdigest(),
            })
        return info
    except Exception:
        return {"path": str(p), "exists": False}


# ---- Radio helpers ----
def record_radio(kind: str, text: str) -> None:
    """Append a radio line to the flight log for the dashboard Radio box.
    Route is '/radio.msg' so the frontend picks it up directly.
    """
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


# ---- Routes ----
@app.get("/debug/template")
def debug_template():
    return {"ok": True, "index": _template_info("index.html")}, 200


@app.get("/")
def index():
    return render_template("index.html")

@app.get("/about")
def about():
    t0 = time.time()
    route = "/about"
    try:
        webdash_path = HERE
        radar_path = HERE.parent / "radar.py"
        tpl_folder = Path(app.template_folder).resolve()
        index_path = tpl_folder / "index.html"

        payload: Dict[str, Any] = {
            "ok": True,
            "layout_sentinel": "v1",
            "zones": ["CARD-OWNFLEET","CARD-PRIMARY","CARD-WEAPONS","CARD-CAP","CARD-RADIO","CARD-RADAR","CARD-CMDS"],
            "files": {
                "webdash": _file_info(webdash_path),
                "radar": _file_info(radar_path),
                "index": _file_info(index_path),
            },
            "app": {
                "port": PORT,
                "pid": os.getpid(),
                "started_iso": APP_STARTED.isoformat(),
            },
            "template_folder": str(tpl_folder),
            "grid": {"world_n": WORLD_N, "board_n": BOARD_N, "scheme": "A1..Z26"},
        }
        record_flight({
            "route": route, "method": "GET", "status": 200,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/about error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({
            "route": route, "method": "GET", "status": 500,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload), 500


@app.get("/health")
def health():
    try:
        hud = ENG.hud_line() if hasattr(ENG, "hud_line") else "OK"
        return jsonify({"ok": True, "hud": hud})
    except Exception as e:
        logging.exception("/health error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/radio/say", methods=["GET", "POST"])
def radio_say():
    """Append a radio line to the flight log so the Radio box shows it.
    Params: text (required), kind (optional, default 'ENSIGN'). Accepts query or JSON body.
    """
    from flask import request
    t0 = time.time(); route = "/radio/say"
    try:
        # Reuse arg/json helper
        text = _arg_or_json(request, 'text', '')  # type: ignore[name-defined]
        kind = _arg_or_json(request, 'kind', 'ENSIGN')  # type: ignore[name-defined]
        if not text:
            payload = {"ok": False, "error": "missing text"}
            record_flight({"route": route, "method": request.method, "status": 400,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {"kind": kind, "text": text}, "response": payload})
            return jsonify(payload), 400
        record_radio(kind or 'ENSIGN', text)
        payload = {"ok": True, "kind": kind or 'ENSIGN', "text": text}
        record_flight({"route": route, "method": request.method, "status": 200,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {"kind": kind, "text": text}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radio/say error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({"route": route, "method": request.method, "status": 500,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload), 500


@app.get("/data/sounds/<path:filename>")
def data_sounds(filename: str):
    try:
        base = DATA_DIR / 'sounds'
        return send_from_directory(str(base), filename)
    except Exception as e:
        logging.exception("/data/sounds error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 404


@app.get("/api/status")
def api_status():
    t0 = time.time()
    route = "/api/status"
    try:
        payload: Dict[str, Any] = {"ok": True}
        if hasattr(ENG, "public_state"):
            try:
                payload["state"] = ENG.public_state()  # type: ignore
            except Exception:
                payload["hud"] = ENG.hud_line() if hasattr(ENG, "hud_line") else "OK"
        else:
            payload["hud"] = ENG.hud_line() if hasattr(ENG, "hud_line") else "OK"
        # Own fleet snapshot (own ship + escorts)
        try:
            payload['ownfleet'] = _ownfleet_snapshot(payload.get('state', {}))
        except Exception:
            payload['ownfleet'] = []
        # Build contacts: radar first
        try:
            st = payload.get("state") or (ENG.public_state() if hasattr(ENG, "public_state") else {})
            own_xy = get_own_xy(st)
            radar_list = [contact_to_ui(c, own_xy) for c in RADAR.contacts]
            # Ensure cell comes from shared world_to_cell(x, y)
            for d, c in zip(radar_list, RADAR.contacts):
                try:
                    d['cell'] = world_to_cell(c.x, c.y)
                except Exception:
                    pass
        except Exception:
            radar_list = []
        # sort by range asc
        radar_list.sort(key=lambda d: float(d.get('range_nm', 1e9)))
        # threats subset and top_threat_id
        threats = [d for d in radar_list if str(d.get('type','')).lower() == 'hostile']
        payload["contacts"] = radar_list
        payload["threats"] = threats
        payload["top_threat_id"] = (threats[0]["id"] if threats else None)
        # Optional debug-contacts appended after radar list only if enabled
        try:
            if 'DEBUG_CONTACTS_ON' in globals() and DEBUG_CONTACTS_ON:  # type: ignore[name-defined]
                payload["contacts"] = payload.get("contacts", []) + list(DEBUG_CONTACTS)
        except Exception:
            pass
        # Ship cell string (A..Z + 1..26)
        try:
            s = payload.get('state',{}).get('ship',{})
            payload['ship_cell'] = cell_for_world(float(s.get('row',0.0)), float(s.get('col',0.0)))
        except Exception:
            pass
        # Primary from module-level PRIMARY_ID (prefer), otherwise omit
        try:
            if 'PRIMARY_ID' in globals() and PRIMARY_ID is not None:  # type: ignore[name-defined]
                cid = int(PRIMARY_ID)  # type: ignore[name-defined]
                for d in payload.get("contacts", []):
                    try:
                        if int(d.get("id", -1)) == cid:
                            payload["primary"] = d
                            break
                    except Exception:
                        continue
        except Exception:
            pass
        # Weapons snapshot merged
        try:
            ammo = load_ammo(); arming = load_arming()
            primary_ui = payload.get('primary') if isinstance(payload.get('primary'), dict) else None
            def _order_key(rec: Dict[str,Any]):
                nm = rec.get('name','')
                cls = rec.get('class','Other')
                if nm == 'MM38 Exocet':
                    return (0, nm)
                cls_rank = {'Missile':1, 'SAM':2, 'Gun':3, 'Decoy':4}.get(cls, 5)
                return (cls_rank, nm)
            weaps = []
            for w in WEAP_CATALOG:
                nm = w.get('name'); cls = w.get('class')
                rec = {
                    'name': nm,
                    'class': cls,
                    'min_nm': w.get('min_nm'),
                    'max_nm': w.get('max_nm'),
                    'armed': arming.get(nm, 'Safe'),
                    'ammo': ammo.get(nm, 0),
                    'in_range': compute_in_range(nm, primary_ui),
                }
                weaps.append(rec)
            weaps.sort(key=_order_key)
            payload['weapons'] = weaps
        except Exception:
            pass
        # Optional dev primary passthrough
        try:
            if 'DEBUG_PRIMARY' in globals() and DEBUG_PRIMARY:  # type: ignore[name-defined]
                payload["primary"] = DEBUG_PRIMARY  # type: ignore[name-defined]
        except Exception:
            pass
        # Audio snapshot for frontend (last_launch / last_result)
        try:
            payload['audio'] = dict(AUDIO_STATE)
        except Exception:
            payload['audio'] = {'last_launch': None, 'last_result': None}
        record_flight({
            "route": route, "method": "GET", "status": 200,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/api/status error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({
            "route": route, "method": "GET", "status": 500,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload), 500

@app.route("/api/command", methods=["GET", "POST"])
def api_command():
    from flask import request
    t0 = time.time()
    route = "/api/command"
    # Extract cmd from query or JSON body
    cmd = ""
    try:
        if request.method == "GET":
            cmd = (request.args.get("cmd") or "").strip()
        else:
            if request.is_json:
                data = request.get_json(silent=True) or {}
                cmd = str(data.get("cmd", "")).strip()
            if not cmd:
                cmd = (request.args.get("cmd") or "").strip()
    except Exception:
        cmd = ""

    if not cmd:
        payload = {"ok": False, "error": "missing cmd"}
        record_flight({
            "route": route, "method": request.method, "status": 400,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {"cmd": cmd}, "response": payload,
        })
        return jsonify(payload), 400

    try:
        s = cmd.strip()
        # Special-case radar commands handled locally
        if s.lower() == "/radar unlock":
            # Update module-level and RADAR priority for compatibility
            try:
                globals()['PRIMARY_ID'] = None
            except Exception:
                pass
            try:
                RADAR.priority_id = None  # type: ignore[attr-defined]
            except Exception:
                pass
            # Recorder events
            try:
                RADAR.rec.log("radar.unlock", {})
                RADAR.rec.log("radio.msg", {"kind": "UNLOCK", "text": "UNLOCK"})
            except Exception:
                pass
            payload = {"ok": True, "result": "UNLOCKED"}
            record_flight({
                "route": route, "method": request.method, "status": 200,
                "duration_ms": int((time.time()-t0)*1000),
                "request": {"cmd": cmd}, "response": payload,
            })
            return jsonify(payload), 200
        elif s.lower().startswith("/radar lock"):
            parts = s.split()
            cid = parts[-1] if len(parts) >= 3 else ""
            # helper to find by id
            def _radar_find_by_id(cid_val):
                try:
                    cid_i = int(str(cid_val))
                except Exception:
                    return None
                for c in RADAR.contacts:
                    if int(getattr(c, "id", -1)) == cid_i:
                        return c
                return None
            target = _radar_find_by_id(cid)
            if target is None:
                payload = {"ok": False, "error": "contact not found"}
                record_flight({
                    "route": route, "method": request.method, "status": 404,
                    "duration_ms": int((time.time()-t0)*1000),
                    "request": {"cmd": cmd}, "response": payload,
                })
                return jsonify(payload), 404
            tid = int(getattr(target, 'id', 0))
            try:
                globals()['PRIMARY_ID'] = tid
            except Exception:
                pass
            try:
                RADAR.priority_id = tid  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                RADAR.rec.log("radar.lock", {"id": tid})
                RADAR.rec.log("radio.msg", {"kind": "LOCK", "text": f"LOCK id={tid}"})
            except Exception:
                pass
            payload = {"ok": True, "result": f"LOCKED id={tid}"}
            record_flight({
                "route": route, "method": request.method, "status": 200,
                "duration_ms": int((time.time()-t0)*1000),
                "request": {"cmd": cmd}, "response": payload,
            })
            return jsonify(payload), 200
        # Special-case radar scan to use RADAR directly
        elif s.lower().startswith("/radar scan"):
            # compute own_xy
            st = ENG.public_state() if hasattr(ENG, "public_state") else {}
            own_x, own_y = get_own_xy(st)
            try:
                RADAR.scan(own_x, own_y)
            except Exception:
                pass
            result = f"RADAR: scanned, {len(RADAR.contacts)} contact(s)"
        elif hasattr(ENG, "exec_slash"):
            try:
                result = ENG.exec_slash(cmd)  # type: ignore
            except Exception as ee:
                result = f"ERR: {ee}"
        else:
            result = "ERR: command interface unavailable"

        payload = {"ok": True, "result": result}
        record_flight({
            "route": route, "method": request.method, "status": 200,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {"cmd": cmd}, "response": payload,
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/api/command error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({
            "route": route, "method": request.method, "status": 500,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {"cmd": cmd}, "response": payload,
        })
        return jsonify(payload), 500

@app.get("/weapons/catalog")
def weapons_catalog():
    t0 = time.time(); route = "/weapons/catalog"
    try:
        payload = {'ok': True, 'catalog': WEAP_CATALOG}
        record_flight({'route': route, 'method': 'GET', 'status': 200,
                       'duration_ms': int((time.time()-t0)*1000),
                       'request': {}, 'response': payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/weapons/catalog error: %s", e)
        payload = {'ok': False, 'error': str(e)}
        record_flight({'route': route, 'method': 'GET', 'status': 500,
                       'duration_ms': int((time.time()-t0)*1000),
                       'request': {}, 'response': payload})
        return jsonify(payload), 500

def _arg_or_json(request, key: str, default: str | None = None) -> str | None:
    v = request.args.get(key)
    if v is None and request.is_json:
        try:
            body = request.get_json(silent=True) or {}
            v = body.get(key)
        except Exception:
            v = None
    return v if v is not None else default

@app.post("/weapons/arm")
def weapons_arm():
    from flask import request
    t0 = time.time(); route = "/weapons/arm"
    try:
        name = _arg_or_json(request, 'name', '')
        state = _arg_or_json(request, 'state', '')
        if not name or state not in ("Armed","Safe"):
            payload = {'ok': False, 'error': 'bad params'}
            record_flight({'route': route, 'method': 'POST', 'status': 400,
                           'duration_ms': int((time.time()-t0)*1000),
                           'request': {'name': name, 'state': state}, 'response': payload})
            return jsonify(payload), 400
        # Structured arming record with 5s delay
        raw = _load_json(ARMING_PATH, {})
        if not isinstance(raw, dict):
            raw = {}
        rec = {}
        if state == 'Armed':
            rec = {'armed': False, 'arming_until': time.time() + 5.0}
            disp_state = 'Arming'
        else:
            rec = {'armed': False, 'arming_until': 0}
            disp_state = 'Safe'
        raw[name] = rec
        _save_json(ARMING_PATH, raw)
        try:
            RADAR.rec.log('weapons.arm', {'name': name, 'state': state})
        except Exception:
            pass
        payload = {'ok': True, 'name': name, 'state': disp_state}
        record_flight({'route': route, 'method': 'POST', 'status': 200,
                       'duration_ms': int((time.time()-t0)*1000),
                       'request': {'name': name, 'state': state}, 'response': payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/weapons/arm error: %s", e)
        payload = {'ok': False, 'error': str(e)}
        record_flight({'route': route, 'method': 'POST', 'status': 500,
                       'duration_ms': int((time.time()-t0)*1000),
                       'request': {}, 'response': payload})
        return jsonify(payload), 500

@app.post("/weapons/fire")
def weapons_fire():
    from flask import request
    t0 = time.time(); route = "/weapons/fire"
    try:
        name = _arg_or_json(request, 'name', '')
        mode = (_arg_or_json(request, 'mode', 'real') or 'real').lower()
        if not name or mode not in ('real','test'):
            payload = {'ok': False, 'error': 'bad params'}
            record_flight({'route': route, 'method': 'POST', 'status': 400,
                           'duration_ms': int((time.time()-t0)*1000),
                           'request': {'name': name, 'mode': mode}, 'response': payload})
            return jsonify(payload), 400
        # test mode
        if mode == 'test':
            try:
                RADAR.rec.log('weapons.fire', {'name': name, 'mode': 'test'})
                RADAR.rec.log('radio.msg', {'kind': 'FIRE', 'text': f'TEST {name}'})
            except Exception:
                pass
            # Stamp audio launch so frontend plays sound for test fire
            try:
                AUDIO_STATE['last_launch'] = {'weapon': _sound_key_for_weapon(name), 'ts': time.time()}
            except Exception:
                pass
            payload = {'ok': True, 'result': 'TEST FIRED', 'name': name}
            record_flight({'route': route, 'method': 'POST', 'status': 200,
                           'duration_ms': int((time.time()-t0)*1000),
                           'request': {'name': name, 'mode': mode}, 'response': payload})
            return jsonify(payload)
        # real fire
        arming = load_arming(); ammo = load_ammo()
        if arming.get(name, 'Safe') != 'Armed':
            payload = {'ok': False, 'error': 'NOT_ARMED'}
            record_flight({'route': route, 'method': 'POST', 'status': 400,
                           'duration_ms': int((time.time()-t0)*1000),
                           'request': {'name': name, 'mode': mode}, 'response': payload})
            return jsonify(payload), 400
        if ammo.get(name, 0) <= 0:
            payload = {'ok': False, 'error': 'NO_AMMO'}
            record_flight({'route': route, 'method': 'POST', 'status': 400,
                           'duration_ms': int((time.time()-t0)*1000),
                           'request': {'name': name, 'mode': mode}, 'response': payload})
            return jsonify(payload), 400
        # compute range gate with current primary
        primary = None
        try:
            st = (ENG.public_state() if hasattr(ENG, 'public_state') else {})
            own_x, own_y = get_own_xy(st)
            # re-create current primary from PRIMARY_ID or RADAR.priority_id
            pid = None
            if 'PRIMARY_ID' in globals() and PRIMARY_ID is not None:  # type: ignore[name-defined]
                pid = int(PRIMARY_ID)  # type: ignore[name-defined]
            elif getattr(RADAR, 'priority_id', None) is not None:
                pid = int(RADAR.priority_id)  # type: ignore[attr-defined]
            if pid is not None:
                for c in RADAR.contacts:
                    if int(getattr(c, 'id', -1)) == pid:
                        primary = contact_to_ui(c, (own_x, own_y))
                        break
        except Exception:
            primary = None
        if not primary:
            payload = {'ok': False, 'error': 'NO_PRIMARY'}
            record_flight({'route': route, 'method': 'POST', 'status': 400,
                           'duration_ms': int((time.time()-t0)*1000),
                           'request': {'name': name, 'mode': mode}, 'response': payload})
            return jsonify(payload), 400
        if not compute_in_range(name, primary):
            payload = {'ok': False, 'error': 'OUT_OF_RANGE'}
            record_flight({'route': route, 'method': 'POST', 'status': 400,
                           'duration_ms': int((time.time()-t0)*1000),
                           'request': {'name': name, 'mode': mode}, 'response': payload})
            return jsonify(payload), 400
        # consume ammo (salvo sizes)
        try:
            if name in ("20mm Oerlikon", "20mm GAM-BO1 (twin)"):
                dec = 50
            else:
                dec = 1
        except Exception:
            dec = 1
        ammo[name] = int(ammo.get(name, 0)) - int(dec)
        if ammo[name] < 0:
            ammo[name] = 0
        save_ammo(ammo)
        try:
            RADAR.rec.log('weapons.fire', {'name': name, 'mode': 'real', 'ammo': ammo[name]})
            RADAR.rec.log('radio.msg', {'kind': 'FIRE', 'text': f'{name} fired'})
        except Exception:
            pass
        # Audio: stamp launch for frontend
        try:
            AUDIO_STATE['last_launch'] = {'weapon': _sound_key_for_weapon(name), 'ts': time.time()}
        except Exception:
            pass
        # Schedule outcome (hit/miss) at arrival time
        try:
            tid = int(primary.get('id'))  # type: ignore[arg-type]
            rng = float(primary.get('range_nm', 0.0))  # type: ignore[arg-type]
            tname = str(primary.get('name', 'Target'))  # type: ignore[arg-type]
            tclass = TARGET_CLASS_BY_NAME.get(tname) or 'Ship'
            _schedule_shot_result(name, tid, tname, tclass, rng)
        except Exception:
            pass
        payload = {'ok': True, 'result': 'FIRED', 'name': name, 'ammo': ammo[name]}
        record_flight({'route': route, 'method': 'POST', 'status': 200,
                       'duration_ms': int((time.time()-t0)*1000),
                       'request': {'name': name, 'mode': mode}, 'response': payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/weapons/fire error: %s", e)
        payload = {'ok': False, 'error': str(e)}
        record_flight({'route': route, 'method': 'POST', 'status': 500,
                       'duration_ms': int((time.time()-t0)*1000),
                       'request': {}, 'response': payload})
        return jsonify(payload), 500

# ---- Dev-only debug contacts injection ----
# In-memory store of contacts for UI testing (cleared on process restart)
DEBUG_CONTACTS: list[dict] = []
DEBUG_NEXT_ID: int = 1
DEBUG_CONTACTS_ON: bool = False
PRIMARY_ID: int | None = None

def _default_cell_from_engine() -> str:
    try:
        # Prefer public_state to avoid reaching into internals
        if hasattr(ENG, "public_state"):
            st = ENG.public_state()  # type: ignore
            ship = st.get("ship", {}) if isinstance(st, dict) else {}
            col = int(ship.get("col", 50))
            row = int(ship.get("row", 50))
            return f"{col}-{row+1}"
    except Exception:
        pass
    return "50-51"

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
        "cell": cell or _default_cell_from_engine(),
        "name": name or "Contact",
        "type": typ or "Unknown",
        "range_nm": float(range_nm if range_nm is not None else 10.0),
        "course": int(course if course is not None else 90),
        "speed": int(speed if speed is not None else 300),
    }
    return c

@app.get("/debug/spawn_contact")
def debug_spawn_contact():
    from flask import request
    t0 = time.time()
    route = "/debug/spawn_contact"
    try:
        args = request.args
        cell = args.get("cell") or None
        name = args.get("name") or None
        typ = args.get("type") or None
        try:
            rng = float(args.get("range", "")) if args.get("range") is not None else None
        except Exception:
            rng = None
        try:
            crs = int(float(args.get("course", ""))) if args.get("course") is not None else None
        except Exception:
            crs = None
        try:
            spd = int(float(args.get("speed", ""))) if args.get("speed") is not None else None
        except Exception:
            spd = None

        contact = _make_debug_contact(cell=cell, name=name, typ=typ, range_nm=rng, course=crs, speed=spd)
        DEBUG_CONTACTS.append(contact)
        payload = {"ok": True, "added": contact, "count": len(DEBUG_CONTACTS)}
        record_flight({
            "route": route, "method": "GET", "status": 200,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {k: args.get(k) for k in ("cell","name","type","range","course","speed")},
            "response": payload,
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/debug/spawn_contact error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({
            "route": route, "method": "GET", "status": 500,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload), 500

@app.route("/debug/clear_contacts", methods=["POST", "GET"])
def debug_clear_contacts():
    t0 = time.time()
    route = "/debug/clear_contacts"
    try:
        DEBUG_CONTACTS.clear()
        payload = {"ok": True, "cleared": True}
        record_flight({
            "route": route, "method": "POST", "status": 200,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/debug/clear_contacts error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({
            "route": route, "method": "POST", "status": 500,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload), 500

# ---- RADAR instance (after helpers/recorder ready) ----
class _RecorderLike:
    def log(self, event: str, data: dict | None = None) -> None:
        try:
            record_flight({
                "route": f"/radar/{event}",
                "method": "INT",
                "status": 200,
                "duration_ms": 0,
                "request": {},
                "response": {"event": event, **(data or {})},
            })
        except Exception:
            pass

_seed_env = os.environ.get('RADAR_SEED')
_rng = None
try:
    _rng = random.Random(int(_seed_env)) if _seed_env is not None else random.Random()
except Exception:
    _rng = random.Random()

RADAR = Radar(
    rec=_RecorderLike(),
    rng=_rng,
    catalog_path=os.path.join(os.path.dirname(__file__), 'data', 'contacts.json')
)

# ---- Radar demo helpers ----
def _weighted_hostile_pick(rng: random.Random) -> tuple[str, float]:
    total = float(sum(w for _n, _s, w in HOSTILES))
    r = rng.uniform(0.0, total)
    upto = 0.0
    for n, s, w in HOSTILES:
        if upto + w >= r:
            return n, float(s)
        upto += w
    return HOSTILES[-1][0], float(HOSTILES[-1][1])

def _radar_force_spawn(own_x: float, own_y: float) -> Contact:
    bearing_deg = 315.0
    r = random.uniform(8.0, 14.0)
    rad = math.radians(bearing_deg)
    dx = math.sin(rad) * r
    dy = -math.cos(rad) * r
    x = max(0.0, min(float(WORLD_N), own_x + dx))
    y = max(0.0, min(float(WORLD_N), own_y + dy))
    name, speed = _weighted_hostile_pick(random)
    course_deg = (bearing_deg + 180.0) % 360.0
    # next id
    next_id = getattr(RADAR, "_next_id", len(RADAR.contacts) + 1)
    c = Contact(
        id=next_id,
        name=name,
        allegiance="Hostile",
        x=float(x),
        y=float(y),
        course_deg=float(course_deg),
        speed_kts=float(speed),
        threat="high" if name in ("Super Etendard", "Mirage III") else "medium",
        meta={"spawn": {"bearing_deg": round(bearing_deg,1), "range_nm": round(r,2), "surprise": False, "forced": True}}
    )
    # bump next id and append
    try:
        RADAR._next_id = next_id + 1  # type: ignore[attr-defined]
    except Exception:
        pass
    RADAR.contacts.append(c)
    # recorder event
    try:
        RADAR.rec.log("radar.force_spawn", {
            "bearing_deg": round(bearing_deg,1),
            "range_nm": round(r,2),
            "chosen": {"name": name, "speed_kts": speed},
            "target_world_xy": [round(x,2), round(y,2)],
            "ship_world_xy": [round(own_x,2), round(own_y,2)],
        })
    except Exception:
        pass
    return c

@app.get("/debug/cellmap")
def debug_cellmap():
    try:
        n = int(request.args.get("n", 8))
    except Exception:
        n = 8
    try:
        own_x, own_y = get_own_xy(ENG.state)
    except Exception:
        # Fallback to public_state if ENG.state not available
        st = ENG.public_state() if hasattr(ENG, "public_state") else {}
        own_x, own_y = get_own_xy(st)
    out = []
    try:
        for c in RADAR.contacts[:n]:
            out.append({
                "id": c.id,
                "name": c.name,
                "type": c.allegiance,
                "x": round(c.x, 2),
                "y": round(c.y, 2),
                "cell": world_to_cell(c.x, c.y)
            })
    except Exception:
        pass
    return jsonify({"ok": True, "own": {"x": own_x, "y": own_y}, "contacts": out})

@app.get("/radar/force_spawn")
def radar_force_spawn():
    t0 = time.time()
    route = "/radar/force_spawn"
    try:
        st = ENG.public_state() if hasattr(ENG, "public_state") else {}
        own_x, own_y = get_own_xy(st)
        # Back-compat demo spawn — use Hostile via RADAR
        c = RADAR.force_spawn(own_x, own_y, "Hostile", bearing_deg=315.0, range_nm=random.uniform(8.0, 14.0))
        ui = contact_to_ui(c, (own_x, own_y))
        try:
            ui['cell'] = world_to_cell(c.x, c.y)
        except Exception:
            pass
        try:
            RADAR.rec.log("radio.msg", {"kind": "SPAWN", "text": f"{ui.get('type')} {ui.get('name')} r={ui.get('range_nm')} nm at {ui.get('cell')}"})
        except Exception:
            pass
        payload = {"ok": True, "added": ui, "count": len(RADAR.contacts)}
        record_flight({
            "route": route, "method": "GET", "status": 200,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radar/force_spawn error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({
            "route": route, "method": "GET", "status": 500,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload), 500

@app.get("/radar/force_spawn_hostile")
def radar_force_spawn_hostile():
    t0 = time.time(); route = "/radar/force_spawn_hostile"
    try:
        st = ENG.public_state() if hasattr(ENG, "public_state") else {}
        own_x, own_y = get_own_xy(st)
        c = RADAR.force_spawn(own_x, own_y, "Hostile", 315.0, random.uniform(8.0, 14.0))
        ui = contact_to_ui(c, (own_x, own_y))
        try:
            ui['cell'] = world_to_cell(c.x, c.y)
        except Exception:
            pass
        try:
            RADAR.rec.log("radio.msg", {"kind": "SPAWN", "text": f"{ui.get('type')} {ui.get('name')} r={ui.get('range_nm')} nm at {ui.get('cell')}"})
        except Exception:
            pass
        payload = {"ok": True, "added": ui, "count": len(RADAR.contacts)}
        record_flight({"route": route, "method": "GET", "status": 200,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radar/force_spawn_hostile error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({"route": route, "method": "GET", "status": 500,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload), 500

@app.get("/radar/force_spawn_friendly")
def radar_force_spawn_friendly():
    t0 = time.time(); route = "/radar/force_spawn_friendly"
    try:
        st = ENG.public_state() if hasattr(ENG, "public_state") else {}
        own_x, own_y = get_own_xy(st)
        c = RADAR.force_spawn(own_x, own_y, "Friendly", 315.0, random.uniform(8.0, 14.0))
        ui = contact_to_ui(c, (own_x, own_y))
        try:
            ui['cell'] = world_to_cell(c.x, c.y)
        except Exception:
            pass
        try:
            RADAR.rec.log("radio.msg", {"kind": "SPAWN", "text": f"{ui.get('type')} {ui.get('name')} r={ui.get('range_nm')} nm at {ui.get('cell')}"})
        except Exception:
            pass
        payload = {"ok": True, "added": ui, "count": len(RADAR.contacts)}
        record_flight({"route": route, "method": "GET", "status": 200,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radar/force_spawn_friendly error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({"route": route, "method": "GET", "status": 500,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload), 500

@app.get("/radar/force_spawn_near")
def radar_force_spawn_near():
    """Spawn a forced Hostile contact nearby for weapons testing.
    Query params:
      - class: 'Aircraft' | 'Ship' (default 'Aircraft')
      - range: numeric nm from own ship (default 2.5 for Aircraft; 4.0 for Ship)
      - bearing: degrees 0..359 (default 315)
    """
    t0 = time.time(); route = "/radar/force_spawn_near"
    try:
        st = ENG.public_state() if hasattr(ENG, "public_state") else {}
        own_x, own_y = get_own_xy(st)
        # Parse args
        klass = (request.args.get('class') or 'Aircraft').title()
        try:
            rng = float(request.args.get('range') or (2.5 if klass == 'Aircraft' else 4.0))
        except Exception:
            rng = 2.5 if klass == 'Aircraft' else 4.0
        try:
            bearing_deg = float(request.args.get('bearing') or 315.0)
        except Exception:
            bearing_deg = 315.0
        # Compute position
        rad = math.radians(bearing_deg)
        dx = math.sin(rad) * rng
        dy = -math.cos(rad) * rng
        x = max(0.0, min(float(WORLD_N), own_x + dx))
        y = max(0.0, min(float(WORLD_N), own_y + dy))
        # Pick a hostile of requested class from contacts catalog
        try:
            data = _load_json(CONTACTS_PATH, [])
            items = data.get('items') if isinstance(data, dict) else data
            pool = [it for it in (items or []) if isinstance(it, dict) and str(it.get('allegiance','')).title()== 'Hostile' and str(it.get('type','')).title()==klass]
        except Exception:
            pool = []
        if not pool:
            # Fallback name/speed
            name, speed = ('A-4 Skyhawk', 385.0) if klass=='Aircraft' else ('ARA General Belgrano', 22.0)
        else:
            it = random.choice(pool)
            name = str(it.get('name','Contact'))
            try:
                speed = float(it.get('speed_kts', 0.0))
            except Exception:
                speed = 0.0
        # Create contact
        next_id = getattr(RADAR, "_next_id", len(RADAR.contacts) + 1)
        c = Contact(
            id=next_id,
            name=name,
            allegiance="Hostile",
            x=float(x),
            y=float(y),
            course_deg=(bearing_deg + 180.0) % 360.0,
            speed_kts=float(speed),
            threat="high" if klass=='Aircraft' else "medium",
            meta={"spawn": {"bearing_deg": round(bearing_deg,1), "range_nm": round(rng,2), "surprise": False, "forced": True, "class": klass}}
        )
        try:
            RADAR._next_id = next_id + 1  # type: ignore[attr-defined]
        except Exception:
            pass
        RADAR.contacts.append(c)
        # Build UI echo
        ui = contact_to_ui(c, (own_x, own_y))
        try:
            ui['cell'] = world_to_cell(c.x, c.y)
        except Exception:
            pass
        try:
            RADAR.rec.log("radar.force_spawn", {"name": name, "class": klass, "bearing_deg": round(bearing_deg,1), "range_nm": round(rng,2),
                            "target_world_xy": [round(x,2), round(y,2)], "ship_world_xy": [round(own_x,2), round(own_y,2)]})
            RADAR.rec.log("radio.msg", {"kind": "SPAWN", "text": f"Hostile {name} r={ui.get('range_nm')} nm at {ui.get('cell')}"})
        except Exception:
            pass
        payload = {"ok": True, "added": ui, "count": len(RADAR.contacts)}
        record_flight({"route": route, "method": "GET", "status": 200,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {"class": klass, "range": rng, "bearing": bearing_deg}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radar/force_spawn_near error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({"route": route, "method": "GET", "status": 500,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload), 500

@app.get("/radar/reload_catalog")
def radar_reload_catalog():
    t0 = time.time(); route = "/radar/reload_catalog"
    try:
        RADAR.catalog.reload()
        h, f = RADAR.catalog.counts()
        payload = {"ok": True, "counts": {"hostiles": h, "friendlies": f}}
        record_flight({"route": route, "method": "GET", "status": 200,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radar/reload_catalog error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({"route": route, "method": "GET", "status": 500,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload), 500

@app.get("/flight/tail")
def flight_tail():
    try:
        from flask import request
        try:
            n = int(request.args.get("n", "50"))
        except Exception:
            n = 50
        n = max(5, min(200, n))
        if not FLIGHT_PATH.exists():
            return jsonify({"ok": True, "lines": []})
        # Memory-efficient tail: keep only last n lines
        items = []
        with FLIGHT_PATH.open("r", encoding="utf-8") as f:
            dq = deque(f, maxlen=n)
        for ln in reversed(list(dq)):
            try:
                items.append(json.loads(ln))
            except Exception:
                continue
        return jsonify({"ok": True, "lines": items})
    except Exception as e:
        logging.exception("/flight/tail error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ---- Main ----
if __name__ == "__main__":
    # Keep logs concise
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    info = _template_info("index.html")
    print(f"[webdash] templates -> {info['template_folder']} | index -> {info.get('path')} | sha1={info.get('sha1')}")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
