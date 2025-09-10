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
import requests

# ---- engine import (absolute) ----
from projects.falklands.core.engine import Engine
from projects.falklandV2.radar import Radar, Contact, HOSTILES, WORLD_N, HOSTILE_SPEED_SCALE
from projects.falklandV2.subsystems.hermes_cap import HermesCAP
# Prefer relative import; fallback to absolute when executed as a script
try:
    from .engine_adapter import world_to_cell, contact_to_ui, get_own_xy
except Exception:
    from projects.falklandV2.engine_adapter import world_to_cell, contact_to_ui, get_own_xy


# ---- Flask app ----
TPL_DIR = Path(__file__).parent / "templates"
app = Flask(__name__, template_folder=str(TPL_DIR))
try:
    # Register blueprints (split routes)
    from projects.falklandV2.routes.command import bp as command_bp
    app.register_blueprint(command_bp)
    from projects.falklandV2.routes.radar import bp as radar_bp
    app.register_blueprint(radar_bp)
    from projects.falklandV2.routes.weapons import bp as weapons_bp
    app.register_blueprint(weapons_bp)
except Exception:
    # Keep server working even if blueprint import fails
    pass


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
# Frontend polls /api/status.audio; sound.js plays files for last_launch/last_result and alarm
AUDIO_STATE: Dict[str, Any] = {"last_launch": None, "last_result": None, "radio": None, "alarm": None, "cap_launch": None}
# Defense + motion runtime state
DEFENSE_STATE: Dict[str, Any] = {"chaff_until": 0.0, "turn_until": 0.0}
MOTION_STATE: Dict[str, Any] = {"last_heading": None, "last_ts": 0.0}
SKIRMISH_ACTIVE: Dict[str, Any] = {"id": None, "started_ts": None}
NAV_STATE: Dict[str, Any] = {"last_cell": None, "turn_target": None, "turn_hold_since": 0.0, "boundary_cooldown_until": 0.0}
RADIO_QUEUE: list[Dict[str, Any]] = []  # items: {role, text, prio, enq_ts}
RADIO_STATE: Dict[str, Any] = {"busy_until": 0.0}
STATE_LOCK = threading.Lock()

# CAP subsystem (Hermes CAP). Will be initialized after DATA_DIR is set below.
CAP: HermesCAP | None = None
# CAP mission meta (runtime-only): origin_xy at launch, and permission flags
CAP_META: Dict[int, Dict[str, Any]] = {}

# Pending delayed events (e.g., shot results); each item:
# { 'due': float_ts, 'kind': 'resolve_shot', 'weapon': str, 'target_id': int,
#   'target_name': str, 'target_class': str, 'range_nm': float }
PENDING_EVENTS: list[Dict[str, Any]] = []
ATTACK_STATE: Dict[int, float] = {}

# ---- Skirmish storage helpers ----
def _load_skirmishes() -> Dict[str, Any]:
    try:
        obj = _load_json(SKIRMISHES_PATH, {})
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

def _save_skirmishes(obj: Dict[str, Any]) -> None:
    try:
        _save_json(SKIRMISHES_PATH, obj)
    except Exception:
        pass

def _skirmish_next_id(db: Dict[str, Any]) -> int:
    try:
        items = db.get('items') or {}
        ids = [int(k) for k in items.keys()]
        return (max(ids) + 1) if ids else 1
    except Exception:
        return 1

def _skirmish_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# ---- Roadmap storage helpers ----
def _load_roadmap() -> Dict[str, Any]:
    try:
        obj = _load_json(ROADMAP_PATH, {})
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

def _save_roadmap(obj: Dict[str, Any]) -> None:
    try:
        _save_json(ROADMAP_PATH, obj)
    except Exception:
        pass

def _init_roadmap_if_missing() -> None:
    db = _load_roadmap()
    if db.get('items'):
        return
    items = [
        {"id": 1, "title": "Radio Framework V2 for all stations", "desc": "Extend events→role→intent→hint across Nav, Radar, Fire Control, Weapons, Engineering.", "status": "pending", "order": 1},
        {"id": 2, "title": "Damage model + Repair Teams + Win Conditions", "desc": "Lives, hit effects, repair teams, win/loss.", "status": "pending", "order": 2},
        {"id": 3, "title": "Skirmish/Mission Editor foundation", "desc": "Evolve skirmish into mission editor light.", "status": "pending", "order": 3},
        {"id": 4, "title": "Radio content refinement", "desc": "More human, context-rich lines.", "status": "pending", "order": 4},
        {"id": 5, "title": "Voice commands MVP", "desc": "Speak key commands, reduce buttons.", "status": "pending", "order": 5},
        {"id": 6, "title": "Atmosphere & polish", "desc": "Small touches that add immersion.", "status": "pending", "order": 6},
        {"id": 7, "title": "Coordinated enemy attacks", "desc": "Pre-planned waves via mission editor light.", "status": "pending", "order": 7},
        {"id": 8, "title": "Raspberry Pi Kiosk", "desc": "Package for standalone device.", "status": "pending", "order": 8},
    ]
    _save_roadmap({"items": items, "updated": _skirmish_now_iso()})

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

# ---- Alarm helpers ----
def trigger_alarm(sound: str = "red-alert.wav", *, message: str | None = None, role: str | None = None, loop: bool = False) -> None:
    """Stamp an alarm in AUDIO_STATE and optionally queue a radio/crew message.
    `sound` may be a filename (e.g., 'red-alert.wav').
    """
    try:
        with STATE_LOCK:
            # Always one-shot (no loop) per operator intent
            AUDIO_STATE['alarm'] = {"file": str(sound), "loop": False, "ts": time.time()}
        if message:
            record_officer(role or 'Captain', message)
        try:
            record_flight({
                "route": "/alarm.trigger", "method": "INT", "status": 200,
                "duration_ms": 0,
                "request": {"sound": sound, "loop": loop, "role": role, "message": message},
                "response": {"ok": True}
            })
        except Exception:
            pass
    except Exception:
        pass

def clear_alarm() -> None:
    try:
        with STATE_LOCK:
            AUDIO_STATE['alarm'] = {"stop": True, "ts": time.time()}
        record_flight({"route": "/alarm.clear", "method": "INT", "status": 200, "duration_ms": 0,
                       "request": {}, "response": {"ok": True}})
    except Exception:
        pass

def stamp_cap_launch(sound_file: str = "SHAR.wav", volume: float = 0.10, fade_s: float = 2.0) -> None:
    """Stamp a CAP launch audio cue for the frontend to play once at a low volume with fade-out."""
    try:
        with STATE_LOCK:
            AUDIO_STATE['cap_launch'] = {"file": str(sound_file), "vol": float(max(0.0, min(1.0, volume))), "fade_s": float(max(0.0, fade_s)), "ts": time.time()}
    except Exception:
        pass

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

def _cap_flight_time_seconds(range_nm: float) -> float:
    """Rudimentary Sidewinder time-of-flight for CAP engagements.
    Keep it short and punchy so radio feels responsive.
    """
    r = max(0.0, float(range_nm))
    return max(1.0, 2.0 + 0.5 * r)

def _schedule_shot_result(weapon_name: str, target_id: int, target_name: str, target_class: str, range_nm: float) -> None:
    due = time.time() + _flight_time_seconds(weapon_name, range_nm)
    with STATE_LOCK:
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
    with STATE_LOCK:
        _evs = list(PENDING_EVENTS)
    if not _evs:
        return
    remaining: list[Dict[str, Any]] = []
    for ev in _evs:
        if float(ev.get('due', 0.0)) <= now and ev.get('kind') == 'resolve_shot':
            try:
                wid = int(ev.get('target_id'))
                tname = str(ev.get('target_name'))
                tclass = str(ev.get('target_class'))
                wname = str(ev.get('weapon'))
                rng = float(ev.get('range_nm', 0.0))
                # Locate target
                tgt = next((c for c in RADAR.contacts if int(getattr(c, 'id', -1)) == wid), None)
                tcell = None
                try:
                    if tgt is not None:
                        tcell = world_to_cell(float(getattr(tgt, 'x', 0.0)), float(getattr(tgt, 'y', 0.0)))
                except Exception:
                    tcell = None
                pk = _hit_probability(wname, tclass, rng)
                hit = (random.random() < pk)
                if hit and tgt is not None:
                    # Remove contact
                    try:
                        RADAR.contacts = [c for c in RADAR.contacts if int(getattr(c, 'id', -1)) != wid]
                    except Exception:
                        pass
                    officer_say('Fire Control', 'hit', {'name': tname, 'id': wid})
                    with STATE_LOCK:
                        AUDIO_STATE['last_result'] = {'event': 'hit', 'ts': now}
                else:
                    officer_say('Fire Control', 'miss', {'name': tname, 'id': wid})
                    with STATE_LOCK:
                        AUDIO_STATE['last_result'] = {'event': 'miss', 'ts': now}
                # Record engagement result with attacker/target context
                try:
                    ship_cell = ship_cell_from_state(ENG.public_state() if hasattr(ENG, 'public_state') else {})
                except Exception:
                    ship_cell = None
                try:
                    record_flight({
                        'route': '/engagement.result', 'method': 'INT', 'status': 200, 'duration_ms': 0,
                        'request': {},
                        'response': {
                            'result': ('hit' if hit else 'miss'),
                            'attacker_id': 'own',
                            'attacker_name': 'HMS Sheffield',
                            'attacker_cell': ship_cell,
                            'weapon': wname,
                            'range_nm': rng,
                            'target_id': wid,
                            'target_name': tname,
                            'target_cell': tcell,
                        }
                    })
                except Exception:
                    pass
            except Exception:
                continue
        elif float(ev.get('due', 0.0)) <= now and ev.get('kind') == 'arming_ready':
            try:
                wname = str(ev.get('weapon'))
                officer_say('Weapons', 'ready', {'weapon': wname})
            except Exception:
                pass
        elif float(ev.get('due', 0.0)) <= now and ev.get('kind') == 'cap_resolve':
            try:
                hit = bool(ev.get('hit'))
                tid = int(ev.get('target_id', 0))
                tname = str(ev.get('target_name', 'Target'))
                rng = None
                try:
                    rng = float(ev.get('range_nm')) if ev.get('range_nm') is not None else None
                except Exception:
                    rng = None
                wname = str(ev.get('weapon') or 'AIM-9 Sidewinder')
                if hit:
                    # Remove target if still present
                    try:
                        RADAR.contacts = [c for c in RADAR.contacts if int(getattr(c,'id',-1)) != tid]
                    except Exception:
                        pass
                    voice_emit('pilot.splash', {'name': tname}, fallback='Splash one bandit.', role='Pilot')
                    with STATE_LOCK:
                        AUDIO_STATE['last_result'] = {'event': 'hit', 'ts': now}
                else:
                    voice_emit('pilot.miss', {'name': tname}, fallback='Missile missed.', role='Pilot')
                    with STATE_LOCK:
                        AUDIO_STATE['last_result'] = {'event': 'miss', 'ts': now}
                # Record CAP engagement result
                try:
                    record_flight({
                        'route': '/engagement.result', 'method': 'INT', 'status': 200, 'duration_ms': 0,
                        'request': {},
                        'response': {
                            'result': ('hit' if hit else 'miss'),
                            'attacker_id': 'cap',
                            'attacker_name': 'CAP',
                            'attacker_cell': None,
                            'weapon': wname,
                            'range_nm': rng,
                            'target_id': tid,
                            'target_name': tname,
                            'target_cell': None,
                        }
                    })
                except Exception:
                    pass
            except Exception:
                pass
        elif float(ev.get('due', 0.0)) <= now and ev.get('kind') == 'hostile_attack':
            try:
                w = str(ev.get('weapon','attack'))
                base = float(ev.get('base', 0.3))
                # Simple hit roll
                import random as _r
                # Apply defense rules for Exocet missiles
                intercepted = False
                defense = {}
                if 'exocet' in w:
                    # Record baseline
                    defense['base'] = round(base, 3)
                    # Gather context
                    now_ts = time.time()
                    with STATE_LOCK:
                        chaff_active = (now_ts <= float(DEFENSE_STATE.get('chaff_until', 0.0)))
                        hard_turn = (now_ts <= float(DEFENSE_STATE.get('turn_until', 0.0)))
                    # Determine current range from missile contact (if available)
                    cur_rng = float(ev.get('range_nm', 0.0))
                    try:
                        mid = ev.get('missile_id')
                        if mid is not None:
                            mc = next((c for c in RADAR.contacts if int(getattr(c,'id',-1)) == int(mid)), None)
                            if mc is not None:
                                stship = ENG.public_state() if hasattr(ENG,'public_state') else {}
                                ox, oy = radar_xy_from_state(stship)
                                dx = float(getattr(mc,'x',0.0)) - float(ox)
                                dy = float(getattr(mc,'y',0.0)) - float(oy)
                                cur_rng = (dx*dx + dy*dy) ** 0.5
                    except Exception:
                        pass
                    # Load weapon states
                    arming = {}
                    ammo = {}
                    try:
                        arming = load_arming(); ammo = load_ammo()
                    except Exception:
                        arming, ammo = {}, {}
                    # Sea Dart intercept up to 20%
                    try:
                        sd_armed = (arming.get('Sea Dart SAM') == 'Armed')
                        sd_ammo = int(ammo.get('Sea Dart SAM', 0)) > 0
                    except Exception:
                        sd_armed = False; sd_ammo = False
                    if sd_armed and sd_ammo:
                        if _r.random() < 0.20:
                            intercepted = True
                            defense['sea_dart'] = 'intercept'
                        else:
                            defense['sea_dart'] = 'no_effect'
                    # Guns intercept up to 30% (close-in)
                    try:
                        g1_armed = (arming.get('20mm GAM-BO1 (twin)') == 'Armed') and (int(ammo.get('20mm GAM-BO1 (twin)', 0)) > 0)
                        g2_armed = (arming.get('20mm Oerlikon') == 'Armed') and (int(ammo.get('20mm Oerlikon', 0)) > 0)
                    except Exception:
                        g1_armed = g2_armed = False
                    if not intercepted and (g1_armed or g2_armed):
                        # Prefer effectiveness when very close; do not require explicit range sensing for simplicity
                        if _r.random() < 0.30:
                            intercepted = True
                            defense['guns'] = 'intercept'
                        else:
                            defense['guns'] = 'no_effect'
                    # Chaff increases miss by 60% (reduce hit prob)
                    if chaff_active:
                        base = max(0.0, base - 0.60)
                        defense['chaff'] = True
                    else:
                        defense['chaff'] = False
                    # Hard 90° turn at ≥90% speed adds 25% miss chance (reduce hit prob)
                    if hard_turn:
                        base = max(0.0, base - 0.25)
                        defense['maneuver'] = True
                    else:
                        defense['maneuver'] = False
                    defense['final_base'] = round(base, 3)
                # Final hit after defenses
                hit = (False if intercepted else (_r.random() < base))
                # On hit, decrement health
                if hit:
                    h = _load_health()
                    tgt = str(ev.get('target','HMS Sheffield'))
                    if 'hermes' in tgt.lower():
                        h['hermes_lives'] = max(0, int(h.get('hermes_lives',3)) - 1)
                    else:
                        h['lives'] = max(0, int(h.get('lives',3)) - 1)
                    _save_health(h)
                    officer_say('Engineering','damage', {'system':'Hull'})
                    with STATE_LOCK:
                        AUDIO_STATE['last_result'] = {'event': 'hit', 'ts': now}
                else:
                    record_officer('Fire Control', 'Incoming attack missed.')
                    with STATE_LOCK:
                        AUDIO_STATE['last_result'] = {'event': 'miss', 'ts': now}
                # Record hostile attack resolution with context
                try:
                    aid = int(ev.get('contact_id', 0))
                    aname = str(ev.get('contact_name', 'Hostile'))
                    rng = float(ev.get('range_nm', 0.0))
                    tlabel = str(ev.get('target', 'HMS Sheffield'))
                    missile_id = ev.get('missile_id')
                    # Attacker cell if still tracked
                    acell = None
                    try:
                        c = next((c for c in RADAR.contacts if int(getattr(c,'id',-1)) == aid), None)
                        if c is not None:
                            acell = world_to_cell(float(getattr(c,'x',0.0)), float(getattr(c,'y',0.0)))
                    except Exception:
                        acell = None
                    # Target cell: derive from current state
                    try:
                        stship = ENG.public_state() if hasattr(ENG,'public_state') else {}
                        own_cell = ship_cell_from_state(stship)
                    except Exception:
                        own_cell = None
                    tcell = own_cell
                    try:
                        if 'hermes' in tlabel.lower() and own_cell is not None:
                            # Estimate Hermes cell using convoy offsets
                            i = 0
                            while i < len(own_cell) and own_cell[i].isalpha():
                                i += 1
                            col_letters = own_cell[:i] or 'A'
                            row_str = own_cell[i:] or '1'
                            cc = 0
                            for ch in col_letters:
                                cc = cc*26 + (ord(ch) - ord('A') + 1)
                            rr = int(row_str)
                            convoy = _load_json(DATA_DIR / 'convoy.json', {})
                            escorts = convoy.get('escorts', []) if isinstance(convoy, dict) else []
                            herm = next((e for e in escorts if str(e.get('name','')).lower().find('hermes')>=0), None)
                            if herm:
                                dx, dy = herm.get('offset_cells', [-2,3])
                                tcell = board_to_cell(int(clamp(rr+int(dy),1,BOARD_N)), int(clamp(cc+int(dx),1,BOARD_N)))
                    except Exception:
                        pass
                    rec_resp = {
                        'route': '/engagement.result', 'method': 'INT', 'status': 200, 'duration_ms': 0,
                        'request': {},
                        'response': {
                            'result': ('hit' if hit else 'miss'),
                            'attacker_id': aid,
                            'attacker_name': aname,
                            'attacker_cell': acell,
                            'weapon': w,
                            'range_nm': rng,
                            'target_name': tlabel,
                            'target_cell': tcell,
                        }
                    }
                    if 'defense' in locals() and defense:
                        try:
                            rec_resp['response']['defense'] = defense  # type: ignore[index]
                        except Exception:
                            pass
                    record_flight(rec_resp)
                    # Remove missile contact if we created one
                    try:
                        mid = int(missile_id) if missile_id is not None else None
                    except Exception:
                        mid = None
                    if mid is not None:
                        try:
                            RADAR.contacts = [c for c in RADAR.contacts if int(getattr(c,'id',-1)) != mid]
                            RADAR.rec.log('missile.resolved', {'id': mid, 'result': ('hit' if hit else 'miss'), 'target': tlabel})
                        except Exception:
                            pass
                except Exception:
                    pass
            except Exception:
                pass
        else:
            remaining.append(ev)
    # swap
    with STATE_LOCK:
        PENDING_EVENTS.clear()
        PENDING_EVENTS.extend(remaining)

def _process_radio_queue() -> None:
    now = time.time()
    with STATE_LOCK:
        try:
            busy_until = float(RADIO_STATE.get('busy_until', 0.0))
        except Exception:
            busy_until = 0.0
    if now < busy_until:
        return
    with STATE_LOCK:
        has_items = bool(RADIO_QUEUE)
    if not has_items:
        return
    # Priority first, then FIFO
    with STATE_LOCK:
        try:
            RADIO_QUEUE.sort(key=lambda it: (not it.get('prio', False), it.get('enq_ts', 0.0)))
        except Exception:
            pass
        it = RADIO_QUEUE.pop(0)
    role = str(it.get('role', 'OFFICER'))
    text = str(it.get('text', ''))
    # Estimate speech duration (pre-TTS)
    try:
        words = max(1, len([w for w in text.split() if w]))
    except Exception:
        words = 6
    dur = max(0.8, min(8.0, 0.6 + 0.4 * words))
    # Try synthesize TTS (cached) if key present; keep duration estimate
    file_url = None
    try:
        file_url = _tts_synthesize(text, role)
    except Exception:
        file_url = None
    with STATE_LOCK:
        AUDIO_STATE['radio'] = {'role': role, 'text': text, 'ts': now, 'dur': dur, **({'file': file_url} if file_url else {})}
    try:
        record_flight({'route': '/radio.officer', 'method': 'INT', 'status': 200, 'duration_ms': 0,
                       'request': {}, 'response': {'role': role, 'text': text}})
    except Exception:
        pass
    with STATE_LOCK:
        RADIO_STATE['busy_until'] = now + dur + 0.3


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
                own_x, own_y = radar_xy_from_state(st)
                # NAV: grid enter + turn complete
                try:
                    cell = ship_cell_from_state(st)
                    with STATE_LOCK:
                        last_cell = NAV_STATE.get('last_cell')
                        NAV_STATE['last_cell'] = cell
                    if cell and cell != last_cell:
                        try:
                            voice_emit('nav.grid.enter', {'cell': cell}, fallback=f'Captain, entering grid {cell}.', role='Navigation')
                        except Exception:
                            pass
                    # Boundary warning: within 1 cell of board edge
                    try:
                        # parse cell into row, col indices
                        j=0
                        while j < len(cell) and cell[j].isalpha(): j+=1
                        letters = cell[:j] or 'A'; row_s = cell[j:] or '1'
                        col_i=0
                        for ch in letters: col_i = col_i*26 + (ord(ch)-ord('A')+1)
                        row_i = int(row_s)
                        # margin to edges (1..26)
                        m_north = max(0, row_i-1)
                        m_south = max(0, 26-row_i)
                        m_west  = max(0, col_i-1)
                        m_east  = max(0, 26-col_i)
                        minm = min(m_north, m_south, m_west, m_east)
                        nowb = time.time()
                        with STATE_LOCK:
                            bc = float(NAV_STATE.get('boundary_cooldown_until') or 0.0)
                        if minm <= 1 and nowb >= bc:
                            # pick cardinal and recommended course back toward center
                            if minm == m_north: edge='north'
                            elif minm == m_south: edge='south'
                            elif minm == m_west: edge='west'
                            else: edge='east'
                            # recommended heading toward center (13,13) in board idx → world center
                            def _letters_to_idx(s):
                                k=0
                                for ch in s: k=k*26+(ord(ch)-ord('A')+1)
                                return k
                            center_x = float(BOARD_MIN) + float(13-1)
                            center_y = float(BOARD_MIN) + float(13-1)
                            # own world
                            owx, owy = radar_xy_from_state(st)
                            import math as _m
                            rec_hdg = int(round((_m.degrees(_m.atan2(center_x-owx, -(center_y-owy))) % 360.0)))
                            try:
                                voice_emit('nav.patrol.boundary.warn', {'edge_cardinal': edge, 'rec_hdg': rec_hdg}, fallback=f'Warning: leaving patrol area to the {edge}. Recommend course {rec_hdg}°.', role='Navigation')
                            except Exception:
                                pass
                            with STATE_LOCK:
                                NAV_STATE['boundary_cooldown_until'] = nowb + 60.0
                    except Exception:
                        pass
                    # Turn complete: within ±2° of target for >=2s
                    ship = (st or {}).get('ship', {}) if isinstance(st, dict) else {}
                    hdg = float(ship.get('heading', 0.0))
                    now_ts = time.time()
                    with STATE_LOCK:
                        tgt = NAV_STATE.get('turn_target')
                        hold = float(NAV_STATE.get('turn_hold_since') or 0.0)
                    def _adiff(a,b):
                        d=(a-b+540.0)%360.0-180.0; return abs(d)
                    if isinstance(tgt, (int, float)):
                        if _adiff(hdg, float(tgt)) <= 2.0:
                            if hold <= 0:
                                with STATE_LOCK:
                                    NAV_STATE['turn_hold_since'] = now_ts
                            elif (now_ts - hold) >= 2.0:
                                # Announce once and clear target
                                try:
                                    voice_emit('nav.turn.complete', {'hdg': round(float(tgt))}, fallback=f'Steady on course {round(float(tgt))}°.', role='Navigation')
                                except Exception:
                                    pass
                                with STATE_LOCK:
                                    NAV_STATE['turn_target'] = None
                                    NAV_STATE['turn_hold_since'] = 0.0
                        else:
                            with STATE_LOCK:
                                NAV_STATE['turn_hold_since'] = 0.0
                except Exception:
                    pass
                # Motion-derived defenses: detect hard 90° turn at ≥90% max speed
                try:
                    ship = (st or {}).get('ship', {}) if isinstance(st, dict) else {}
                    hdg = float(ship.get('heading', 0.0))
                    spd = float(ship.get('speed', 0.0))
                    now_ts = time.time()
                    # Load max speed (cached via ship.json)
                    try:
                        ship_cfg = _load_json(DATA_DIR / 'ship.json', {})
                        vmax = float((ship_cfg.get('speed_max_kts') or 32.0))
                    except Exception:
                        vmax = 32.0
                    with STATE_LOCK:
                        lh = MOTION_STATE.get('last_heading')
                        lt = float(MOTION_STATE.get('last_ts') or 0.0)
                        MOTION_STATE['last_heading'] = hdg
                        MOTION_STATE['last_ts'] = now_ts
                    def _angdiff(a: float, b: float) -> float:
                        d = (a - b + 540.0) % 360.0 - 180.0
                        return abs(d)
                    if lh is not None and vmax > 0:
                        ddeg = _angdiff(hdg, float(lh))
                        dt_turn = max(0.001, now_ts - (lt or now_ts))
                        if (ddeg >= 90.0 - 1e-6) and (spd >= 0.9 * vmax):
                            with STATE_LOCK:
                                DEFENSE_STATE['turn_until'] = now_ts + 20.0
                            try:
                                RADAR.rec.log('maneuver.hard_turn', {'deg': round(ddeg,1), 'speed_kts': spd})
                            except Exception:
                                pass
                except Exception:
                    pass
                RADAR.tick(dt, own_x, own_y)
            except Exception:
                pass
            # Advance CAP missions and auto-engage if a target is locked
            try:
                if CAP is not None:
                    CAP.tick()
                    # CAP mission status transitions + winchester calls
                    try:
                        for m in (CAP.missions or []):
                            try:
                                mid = int(getattr(m, 'id', 0))
                                status = str(getattr(m, 'status', ''))
                                meta = CAP_META.get(mid) or {}
                                prev = meta.get('last_status')
                                if status != prev:
                                    if status == 'onstation':
                                        cell = str(getattr(m, 'target_cell', '') or '')
                                        voice_emit('pilot.station', {'cell': cell}, fallback='On station at %s.' % (cell,), role='Pilot')
                                    elif status == 'rtb':
                                        voice_emit('pilot.rtb', {}, fallback='Winchester, RTB.', role='Pilot')
                                    elif status == 'recovering':
                                        voice_emit('pilot.inbound', {}, fallback='Inbound for landing.', role='Pilot')
                                    meta['last_status'] = status
                                    CAP_META[mid] = meta
                                # Winchester detection
                                ml = int(getattr(m, 'missiles_left', 0) or 0)
                                if ml <= 0 and not meta.get('winchester', False):
                                    voice_emit('pilot.winchester', {}, fallback='Winchester. Out of Sidewinders.', role='Pilot')
                                    # Encourage RTB (if not already transitioning)
                                    if status not in ('rtb','recovering','complete'):
                                        voice_emit('pilot.rtb', {}, fallback='Winchester, RTB.', role='Pilot')
                                    meta['winchester'] = True
                                    CAP_META[mid] = meta
                            except Exception:
                                continue
                    except Exception:
                        pass
                    # Determine locked target id and range using RADAR priority/PRIMARY_ID
                    tid = None
                    try:
                        # Prefer explicit PRIMARY_ID if set via /api/command
                        tid = (int(PRIMARY_ID) if ('PRIMARY_ID' in globals() and PRIMARY_ID is not None) else None)  # type: ignore[name-defined]
                    except Exception:
                        tid = None
                    if tid is None:
                        # Fallback to RADAR priority (closest hostile)
                        tid = RADAR.priority_id
                    if tid is not None:
                        tgt = next((c for c in RADAR.contacts if int(getattr(c, 'id', -1)) == int(tid)), None)
                        if tgt is not None:
                            # Compute effective missile distance from station center (allow station radius + AIM-9 range)
                            try:
                                onst = [m for m in (CAP.missions or []) if getattr(m, 'status', '') == 'onstation' and getattr(m, 'missiles_left', 0) > 0]
                            except Exception:
                                onst = []
                            if onst:
                                # Use the nearest station to the target
                                def dist_from_station(m):
                                    try:
                                        sx, sy = cell_to_world(str(getattr(m,'target_cell','') or ''))
                                        dx = float(getattr(tgt,'x',0.0)) - float(sx)
                                        dy = float(getattr(tgt,'y',0.0)) - float(sy)
                                        return (dx*dx + dy*dy) ** 0.5, float(getattr(m,'station_radius_nm',5.0))
                                    except Exception:
                                        return (1e9, 0.0)
                                dist, rad = min((dist_from_station(m) for m in onst), key=lambda t: t[0])
                                eff = max(0.0, float(dist) - float(rad))
                                res = CAP.auto_engage(eff, int(tid))
                            else:
                                # Fallback to own-ship distance if no station yet
                                dx = float(getattr(tgt, 'x', 0.0)) - float(own_x)
                                dy = float(getattr(tgt, 'y', 0.0)) - float(own_y)
                                rng_nm = (dx*dx + dy*dy) ** 0.5
                                res = CAP.auto_engage(rng_nm, int(tid))
                            if isinstance(res, dict):
                                # Immediate pilot call: Fox Two (repeat if two shots)
                                try:
                                    shots = int(res.get('shots', 1))
                                except Exception:
                                    shots = 1
                                if shots >= 2:
                                    voice_emit('pilot.fox2', {}, fallback='Fox Two, Fox Two!', role='Pilot')
                                else:
                                    voice_emit('pilot.fox2', {}, fallback='Fox Two!', role='Pilot')
                                # Schedule result call (splash/miss) with small time-of-flight delay
                                try:
                                    rng = float(res.get('range_nm', 0.0))
                                except Exception:
                                    rng = 0.0
                                due = time.time() + _cap_flight_time_seconds(rng)
                                try:
                                    tname = str(getattr(tgt,'name','Target'))
                                except Exception:
                                    tname = 'Target'
                                with STATE_LOCK:
                                    PENDING_EVENTS.append({'due': due, 'kind': 'cap_resolve', 'hit': bool(res.get('hit', False)), 'target_id': int(res.get('target_id', 0)), 'target_name': tname, 'range_nm': rng, 'weapon': 'AIM-9 Sidewinder'})
                    else:
                        # No explicit lock: check each on-station mission and auto-engage nearest hostile in Sidewinder range
                        try:
                            onst = [m for m in (CAP.missions or []) if getattr(m, 'status', '') == 'onstation' and getattr(m, 'missiles_left', 0) > 0]
                        except Exception:
                            onst = []
                        for m in onst:
                            try:
                                # Station center at mission target cell
                                mc = str(getattr(m, 'target_cell', '') or '')
                                if not mc:
                                    continue
                                sx, sy = cell_to_world(mc)
                                # Find nearest hostile within Sidewinder envelope
                                candidates = [c for c in RADAR.contacts if str(getattr(c,'allegiance','')).lower()=='hostile']
                                if not candidates:
                                    continue
                                # Compute distance from station center, then effective missile distance = max(0, dist - station_radius)
                                def dnm(c):
                                    dx = float(getattr(c,'x',0.0)) - float(sx)
                                    dy = float(getattr(c,'y',0.0)) - float(sy)
                                    return (dx*dx + dy*dy) ** 0.5
                                nearest = min(candidates, key=dnm)
                                dist = dnm(nearest)
                                # Use CAP Sidewinder envelope if available; else default 5 nm
                                sw_max = getattr(CAP, 'sw_max_nm', 5.0)
                                sw_min = getattr(CAP, 'sw_min_nm', 1.0)
                                try:
                                    rad = float(getattr(m, 'station_radius_nm', 5.0))
                                except Exception:
                                    rad = 5.0
                                eff = max(0.0, float(dist) - rad)
                                if sw_min <= eff <= sw_max:
                                    res = CAP.auto_engage(eff, int(getattr(nearest,'id',0)))
                                    if isinstance(res, dict):
                                        # Fox Two call
                                        try:
                                            shots = int(res.get('shots', 1))
                                        except Exception:
                                            shots = 1
                                        if shots >= 2:
                                            voice_emit('pilot.fox2', {}, fallback='Fox Two, Fox Two!', role='Pilot')
                                        else:
                                            voice_emit('pilot.fox2', {}, fallback='Fox Two!', role='Pilot')
                                        # Schedule splash/miss
                                        try:
                                            rng = float(res.get('range_nm', 0.0))
                                        except Exception:
                                            rng = 0.0
                                        due = time.time() + _cap_flight_time_seconds(rng)
                                        tname = str(getattr(nearest,'name','Target'))
                                        with STATE_LOCK:
                                            PENDING_EVENTS.append({'due': due, 'kind': 'cap_resolve', 'hit': bool(res.get('hit', False)), 'target_id': int(res.get('target_id', 0)), 'target_name': tname, 'range_nm': rng, 'weapon': 'AIM-9 Sidewinder'})
                            except Exception:
                                continue
                        # En-route detection: ask permission when a target appears within 15 nm ahead
                        try:
                            ahead_nm = 15.0
                            airborne = [m for m in (CAP.missions or []) if getattr(m, 'status', '') == 'airborne' and getattr(m, 'missiles_left', 0) > 0]
                        except Exception:
                            airborne = []
                        for m in airborne:
                            try:
                                mid = int(getattr(m, 'id', 0))
                                meta = CAP_META.get(mid) or {}
                                asked = bool(meta.get('asked', False))
                                sx, sy = cell_to_world(str(getattr(m,'target_cell','') or ''))
                                ts = getattr(m, 'ts', None) or getattr(m, 'timestamps', {})
                                t_launch = float(ts.get('launch', 0.0)) if isinstance(ts, dict) else 0.0
                                deck = float(getattr(m,'deck_cycle_s', 180))
                                outb = float(getattr(m,'outbound_s', 60))
                                prog = 0.0
                                try:
                                    prog = max(0.0, min(1.0, (time.time() - (t_launch + deck)) / max(1.0, outb)))
                                except Exception:
                                    prog = 0.0
                                ox, oy = meta.get('origin_xy', radar_xy_from_state(ENG.public_state() if hasattr(ENG,'public_state') else {}))
                                nx = float(ox) + (sx - float(ox)) * prog
                                ny = float(oy) + (sy - float(oy)) * prog
                                host = [c for c in RADAR.contacts if str(getattr(c,'allegiance','')).lower()=='hostile']
                                if not host:
                                    continue
                                def dnm_c(c):
                                    dx = float(getattr(c,'x',0.0)) - nx
                                    dy = float(getattr(c,'y',0.0)) - ny
                                    return (dx*dx + dy*dy) ** 0.5
                                nearest = min(host, key=dnm_c)
                                dist = dnm_c(nearest)
                                if dist <= ahead_nm and not asked:
                                    voice_emit('pilot.request.engage', {'range_nm': round(dist,1)}, fallback='Contact ahead %.1f nm. Request permission to engage.' % (dist,), role='Pilot')
                                    meta['asked'] = True; CAP_META[mid] = meta
                            except Exception:
                                continue
            except Exception:
                pass
            # process any due engagement events (hit/miss radio + sounds)
            try:
                _process_due_events()
            except Exception:
                pass
            # Hostile attack loop (minimal threat model)
            try:
                st2 = ENG.public_state() if hasattr(ENG, 'public_state') else {}
                own_x, own_y = radar_xy_from_state(st2)
                now_t = time.time()
                hostiles = [c for c in RADAR.contacts if str(getattr(c,'allegiance','')).lower()=='hostile']
                for c in hostiles:
                    try:
                        cid = int(getattr(c,'id',-1))
                        last = float(ATTACK_STATE.get(cid, 0.0))
                        if (now_t - last) < 12.0:
                            continue
                        cap = getattr(c,'meta',{}).get('cap',{})
                        pt = cap.get('primary_target')
                        if pt not in (None,'', 'ship') and not (isinstance(pt, list) and 'ship' in [str(x).lower() for x in pt]):
                            continue
                        # Choose target: default ship; prefer own or Hermes randomly when 'ship'
                        target_label = 'HMS Sheffield'
                        tx, ty = own_x, own_y
                        try:
                            if pt == 'ship' or (isinstance(pt, list) and 'ship' in [str(x).lower() for x in pt]):
                                # Compute Hermes world from convoy offsets
                                stship = ENG.public_state() if hasattr(ENG,'public_state') else {}
                                own_cell = ship_cell_from_state(stship)
                                # Parse indices
                                j=0
                                while j < len(own_cell) and own_cell[j].isalpha(): j+=1
                                cletters = own_cell[:j] or 'A'; rstr = own_cell[j:] or '1'
                                ci=0
                                for ch in cletters: ci=ci*26+(ord(ch)-ord('A')+1)
                                ri=int(rstr)
                                convoy = _load_json(DATA_DIR / 'convoy.json', {})
                                escorts = convoy.get('escorts', []) if isinstance(convoy, dict) else []
                                hermes = next((e for e in escorts if str(e.get('name','')).lower().find('hermes')>=0), None)
                                if hermes:
                                    dx_cells, dy_cells = int(hermes.get('offset_cells',[ -2,3])[0]), int(hermes.get('offset_cells',[-2,3])[1])
                                    hermes_cell = board_to_cell(int(clamp(ri+dy_cells,1,BOARD_N)), int(clamp(ci+dx_cells,1,BOARD_N)))
                                    hx, hy = cell_to_world(hermes_cell)
                                    # Random pick between own and Hermes
                                    if random.random() < 0.5:
                                        target_label, tx, ty = 'HMS Hermes', hx, hy
                        except Exception:
                            pass
                        dx = float(getattr(c,'x',0.0)) - float(tx)
                        dy = float(getattr(c,'y',0.0)) - float(ty)
                        rng = (dx*dx + dy*dy) ** 0.5
                        rmin = cap.get('min_range_nm'); rmax = cap.get('max_range_nm')
                        try:
                            rminf = float(rmin) if rmin is not None else 0.0
                            rmaxf = float(rmax) if rmax is not None else 9999.0
                        except Exception:
                            rminf, rmaxf = 0.0, 9999.0
                        if not (rminf <= rng <= rmaxf):
                            continue
                        # Schedule hostile attack
                        w = str(cap.get('primary_weapon') or '').lower()
                        if 'exocet' in w:
                            travel = 4.0 + 6.0 * rng; base = 0.7
                            kind = 'exocet'
                            # Spawn a radar-visible missile contact that tracks to the target
                            try:
                                mx = float(getattr(c, 'x', 0.0)); my = float(getattr(c, 'y', 0.0))
                                # Bearing from launch point to target
                                import math as _m
                                bdeg = (_m.degrees(_m.atan2(float(tx) - mx, -(float(ty) - my))) % 360.0) if (tx is not None and ty is not None) else float(getattr(c,'course_deg',0.0))
                                # Speed chosen so that arrival ~= scheduled travel (accounting for HOSTILE_SPEED_SCALE)
                                try:
                                    ms_kts = (float(rng) * 3600.0) / max(1.0, float(travel)) / float(HOSTILE_SPEED_SCALE)
                                except Exception:
                                    ms_kts = 600.0
                                mid = int(getattr(RADAR, '_next_id', 1))
                                mcontact = Contact(
                                    id=mid,
                                    name='Exocet',
                                    allegiance='Hostile',
                                    x=mx, y=my,
                                    course_deg=float(bdeg),
                                    speed_kts=float(ms_kts),
                                    threat='high',
                                    meta={'kind': 'missile', 'parent_id': cid, 'weapon': 'Exocet AM39', 'target': target_label, 'target_xy': [float(tx), float(ty)] if (tx is not None and ty is not None) else None}
                                )
                                # Append to radar and increment id
                                try:
                                    RADAR.contacts.append(mcontact)
                                    setattr(RADAR, '_next_id', mid + 1)
                                except Exception:
                                    pass
                                # Log missile spawn for the recorder
                                try:
                                    RADAR.rec.log('missile.spawn', {
                                        'id': mid, 'name': 'Exocet', 'from_contact_id': cid,
                                        'world_xy': [round(mx,2), round(my,2)], 'course_deg': round(bdeg,1),
                                        'speed_kts': round(ms_kts,1), 'target': target_label, 'range_nm': round(rng,2)
                                    })
                                except Exception:
                                    pass
                            except Exception:
                                mid = None
                        elif 'bomb' in w:
                            travel = max(1.0, 2.0 * rng); base = 0.4
                            kind = 'bombs'
                        elif 'rocket' in w:
                            travel = max(1.0, 1.5 * rng); base = 0.3
                            kind = 'rockets'
                        elif '6-inch' in w or 'gun' in w:
                            travel = max(1.0, 1.2 * rng); base = 0.25
                            kind = 'gun'
                        else:
                            travel = max(1.0, 1.5 * rng); base = 0.3
                            kind = 'attack'
                        with STATE_LOCK:
                            PENDING_EVENTS.append({'due': now_t + travel, 'kind': 'hostile_attack', 'contact_id': cid,
                                                   'contact_name': str(getattr(c,'name','Hostile')),
                                                   'weapon': kind, 'base': base, 'range_nm': rng, 'target': target_label,
                                                   **({'missile_id': mid} if (kind == 'exocet' and 'mid' in locals()) else {})})
                        ATTACK_STATE[cid] = now_t
                        # Immediate warning + red alert if impact very soon or very close
                        try:
                            officer_say('Fire Control', 'locked', {'name': str(getattr(c,'name','Hostile')), 'id': cid, 'range_nm': round(rng,1)},
                                        fallback=f"Incoming {kind} at {rng:.1f} nm.")
                        except Exception:
                            pass
                        if (travel <= 12.0) or (rng <= 1.2):
                            trigger_alarm('red-alert.wav', message=f"Red alert, incoming {kind}!", role='Fire Control')
                        # Optional pre-call can be added later
                    except Exception:
                        continue
            except Exception:
                pass
            try:
                _process_radio_queue()
            except Exception:
                pass
            time.sleep(dt)
        except Exception as e:
            logging.exception("engine_thread: tick failed: %s", e)
            time.sleep(0.5)


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
CREW_PATH = DATA_DIR / "crew.json"
ALARM_CFG_PATH = DATA_DIR / "alarms.json"
HEALTH_PATH = STATE_DIR / "health.json"
TTS_DIR = STATE_DIR / "tts"
TTS_DIR.mkdir(parents=True, exist_ok=True)
VOICE_EVENTS_PATH = DATA_DIR / "voice_events.json"
SKIRMISHES_PATH = STATE_DIR / "skirmishes.json"
ROADMAP_PATH = STATE_DIR / "roadmap.json"
VOICES_DIR = STATE_DIR / "voices"
VOICES_DIR.mkdir(parents=True, exist_ok=True)

# Initialize CAP now that DATA_DIR is available
try:
    CAP = HermesCAP(DATA_DIR)
except Exception:
    CAP = None

# ---- Grid conversion (world 40×40 → board A..Z × 1..26) ----
WORLD_N = 40
BOARD_N = 26
BOARD_MIN = (WORLD_N - BOARD_N) / 2.0  # center Captain board inside world (7.0)

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
    """Map world (row,y), (col,x) 0..WORLD_N into Captain board 1..BOARD_N centered at BOARD_MIN.
    Clamps outside positions to board edges.
    """
    def mapv(v: float) -> int:
        # Shift by BOARD_MIN so that A1 starts at BOARD_MIN
        t = (v - BOARD_MIN)
        return int(round(clamp(1.0 + t, 1.0, float(BOARD_N))))
    return mapv(row), mapv(col)

def board_to_cell(row_i: int, col_i: int) -> str:
    return f"{_idx_to_letters(int(col_i))}{int(row_i)}"

def cell_for_world(row: float, col: float) -> str:
    r_i, c_i = world_to_board(row, col)
    return board_to_cell(r_i, c_i)

def ship_cell_from_state(state: Dict[str, Any]) -> str:
    """Robustly map legacy (0..100) or V3 (0..40) world coords to captain cell A..Z × 1..26.
    - If row/col exceed WORLD_N, treat them as 0..100 and rescale into 0..WORLD_N.
    - Then apply centered board mapping (BOARD_MIN offset) via cell_for_world.
    """
    ship = (state or {}).get('ship', {}) if isinstance(state, dict) else {}
    try:
        col = float(ship.get('col', 0.0))
        row = float(ship.get('row', 0.0))
    except Exception:
        col, row = 0.0, 0.0
    # Detect scale: if clearly larger than WORLD_N, assume legacy 0..100 scale
    if (col > float(WORLD_N)) or (row > float(WORLD_N)):
        try:
            # Default legacy world span
            legacy_span = 100.0
            # Rescale to 0..WORLD_N
            col = (float(col) / legacy_span) * float(WORLD_N)
            row = (float(row) / legacy_span) * float(WORLD_N)
        except Exception:
            pass
    return cell_for_world(row, col)

def radar_xy_from_state(state: Dict[str, Any]) -> tuple[float, float]:
    """Return own ship (x,y) in Radar's world units.
    Converts legacy 0..100 coordinates to 0..WORLD_N when detected.
    """
    try:
        x, y = get_own_xy(state)
        xf, yf = float(x), float(y)
    except Exception:
        xf, yf = 0.0, 0.0
    if xf > float(WORLD_N) or yf > float(WORLD_N):
        # Legacy scale → rescale into Radar world
        try:
            legacy_span = 100.0
            xf = (xf / legacy_span) * float(WORLD_N)
            yf = (yf / legacy_span) * float(WORLD_N)
        except Exception:
            pass
    return (xf, yf)

def cell_to_world(cell: str) -> tuple[float, float]:
    """Convert a board cell like 'K13' to world (x,y) coordinates (nm), centered board.
    Approximates to the center of the cell index using 1 nm per cell.
    """
    s = str(cell or "").strip().upper()
    if not s:
        return (0.0, 0.0)
    # Split letters+digits
    i = 0
    while i < len(s) and s[i].isalpha():
        i += 1
    col_letters = s[:i] or "A"
    row_str = s[i:] or "1"
    # Letters to 1-based index
    col_i = 0
    for ch in col_letters:
        col_i = col_i * 26 + (ord(ch) - ord('A') + 1)
    try:
        row_i = int(row_str)
    except Exception:
        row_i = 1
    col_i = max(1, min(BOARD_N, col_i))
    row_i = max(1, min(BOARD_N, row_i))
    # Map board index back to world coordinate using BOARD_MIN offset
    x = float(BOARD_MIN) + float(col_i - 1)
    y = float(BOARD_MIN) + float(row_i - 1)
    return (x, y)

# ---- CAP UI adapter ----
def _cap_ui_snapshot() -> Dict[str, Any]:
    try:
        if CAP is None:
            return {"ready": False, "pairs": 0, "airframes": 0, "cooldown_s": 0, "committed": 0, "tasks": []}
        snap = CAP.snapshot()
        r = snap.get('readiness') or {}
        missions = list(snap.get('missions') or [])
        # own ship world xy from public_state
        try:
            st = ENG.public_state() if hasattr(ENG, 'public_state') else {}
            own_x, own_y = get_own_xy(st)
        except Exception:
            own_x, own_y = (0.0, 0.0)
        now = time.time()
        tasks: list[Dict[str, Any]] = []
        for m in missions:
            try:
                cid = int(m.get('id'))
                cell = str(m.get('target_cell') or '')
                tx, ty = cell_to_world(cell) if cell else (None, None)
                rng = None
                if tx is not None and ty is not None:
                    dx = float(tx) - float(own_x)
                    dy = float(ty) - float(own_y)
                    rng = (dx*dx + dy*dy) ** 0.5
                engaged = bool(m.get('last_engagement'))
                # Time-over-target (seconds until on-station)
                ts = (m.get('timestamps') or {})
                eta_on = ts.get('eta_onstation')
                status = str(m.get('status') or '')
                tot_s = None
                try:
                    if isinstance(eta_on, (int, float)) and status in ('queued','airborne'):
                        tot_s = max(0, int(eta_on - now))
                except Exception:
                    tot_s = None
                # Time-on-station remaining (seconds until RTB)
                tos_s = None
                try:
                    etd_rtb = ts.get('etd_rtb')
                    if isinstance(etd_rtb, (int, float)) and status == 'onstation':
                        tos_s = max(0, int(etd_rtb - now))
                except Exception:
                    tos_s = None
                # Approximate current position cell
                cur_cell = None
                try:
                    meta = CAP_META.get(cid, {})
                    # Hermes origin fallback from convoy
                    st_state = ENG.public_state() if hasattr(ENG, 'public_state') else {}
                    own_cell = ship_cell_from_state(st_state)
                    # Hermes cell
                    def _hermes_cell() -> str:
                        try:
                            # Parse own cell to indices
                            i=0
                            while i < len(own_cell) and own_cell[i].isalpha():
                                i+=1
                            col_letters=own_cell[:i] or 'A'; row_str=own_cell[i:] or '1'
                            cc=0
                            for ch in col_letters: cc=cc*26+(ord(ch)-ord('A')+1)
                            rr=int(row_str)
                            convoy = _load_json(DATA_DIR / 'convoy.json', {})
                            escorts = convoy.get('escorts', []) if isinstance(convoy, dict) else []
                            herm = next((e for e in escorts if str(e.get('name','')).lower().find('hermes')>=0), None)
                            if herm:
                                dx, dy = herm.get('offset_cells', [-2,3])
                                return board_to_cell(int(clamp(rr+int(dy),1,BOARD_N)), int(clamp(cc+int(dx),1,BOARD_N)))
                        except Exception:
                            pass
                        return own_cell
                    if status in ('queued','recovering','complete'):
                        cur_cell = _hermes_cell()
                    elif status == 'onstation':
                        cur_cell = cell or _hermes_cell()
                    else:
                        # In flight (airborne or rtb)
                        # Determine origin world (Hermes or stored)
                        try:
                            ox, oy = meta.get('origin_xy', None) or (None, None)
                        except Exception:
                            ox, oy = (None, None)
                        if ox is None or oy is None:
                            hc = _hermes_cell(); hx, hy = cell_to_world(hc)
                            ox, oy = hx, hy
                        if tx is not None and ty is not None:
                            if status == 'airborne':
                                t_launch = float(ts.get('launch', 0.0)) if isinstance(ts, dict) else 0.0
                                deck = int(m.get('deck_cycle_s') or 0)
                                outb = int(m.get('outbound_s') or 1)
                                prog = 0.0
                                try:
                                    prog = max(0.0, min(1.0, (now - (t_launch + deck)) / max(1.0, outb)))
                                except Exception:
                                    prog = 0.0
                                cx = float(ox) + (float(tx) - float(ox)) * prog
                                cy = float(oy) + (float(ty) - float(oy)) * prog
                            elif status == 'rtb':
                                t_rtb = float(ts.get('rtb', now))
                                inb = int(m.get('inbound_s') or 1)
                                prog = 0.0
                                try:
                                    prog = max(0.0, min(1.0, (now - t_rtb) / max(1.0, inb)))
                                except Exception:
                                    prog = 0.0
                                cx = float(tx) + (float(ox) - float(tx)) * prog
                                cy = float(ty) + (float(oy) - float(ty)) * prog
                            else:
                                cx, cy = float(ox), float(oy)
                            try:
                                cur_cell = world_to_cell(float(cx), float(cy))
                            except Exception:
                                cur_cell = None
                except Exception:
                    cur_cell = None
                tasks.append({
                    "n": cid,
                    "cur_cell": cur_cell or '—',
                    "target_cell": cell or '—',
                    "range_nm": (round(rng, 1) if isinstance(rng, (int, float)) else None),
                    "status": status,
                    "tot_s": tot_s,
                    "tos_s": tos_s,
                    "engaged": engaged,
                })
            except Exception:
                continue
        committed = len([t for t in tasks if t.get('status') in ('airborne','onstation','rtb','recovering')])
        return {
            "ready": bool(r.get('available', False)),
            "pairs": int(r.get('ready_pairs', 0) or 0),
            "airframes": int(r.get('airframes', 0) or 0),
            "cooldown_s": int(r.get('cooldown_s', 0) or 0),
            "committed": int(committed),
            "tasks": tasks,
        }
    except Exception:
        return {"ready": False, "pairs": 0, "airframes": 0, "cooldown_s": 0, "committed": 0, "tasks": []}

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

def _load_health() -> Dict[str, Any]:
    try:
        obj = _load_json(HEALTH_PATH, {})
        if not isinstance(obj, dict):
            obj = {}
    except Exception:
        obj = {}
    # defaults
    if 'max_lives' not in obj: obj['max_lives'] = 3
    if 'lives' not in obj: obj['lives'] = obj['max_lives']
    if 'hermes_max_lives' not in obj: obj['hermes_max_lives'] = 3
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

def load_alarm_cfg() -> Dict[str, Any]:
    obj = _load_json(ALARM_CFG_PATH, {})
    return obj if isinstance(obj, dict) else {}

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
    # All systems default to Safe; captain must arm explicitly (/weapons/arm)
    "MM38 Exocet": "Safe",
    "4.5 inch Mk.8 gun": "Safe",
    "Sea Dart SAM": "Safe",
    "20mm Oerlikon": "Safe",
    "20mm GAM-BO1 (twin)": "Safe",
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
    # Own ship (robust mapping across legacy/new engines)
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
    out.append({
        'id': 'own',
        'name': own_name,
        'class': own_class,
        'cell': cell,
        'speed': ship.get('speed', 0),
        'heading': ship.get('heading', 0),
        'status': {'health_pct': health_pct},
    })
    # Convoy escorts (Hermes/Glamorgan) relative offsets if available
    convoy = _load_json(DATA_DIR / 'convoy.json', {})
    escorts = convoy.get('escorts', []) if isinstance(convoy, dict) else []
    # Compute own board indices to offset (parse from cell)
    try:
        # Extract letters (col) and digits (row)
        i = 0
        while i < len(cell) and cell[i].isalpha():
            i += 1
        col_letters = cell[:i] or 'A'
        row_str = cell[i:] or '1'
        # letters→index (1-based)
        cc = 0
        for ch in col_letters:
            cc = cc * 26 + (ord(ch) - ord('A') + 1)
        rr = int(row_str)
        r_i, c_i = int(max(1, min(BOARD_N, rr))), int(max(1, min(BOARD_N, cc)))
    except Exception:
        r_i, c_i = (13, 11)
    def _escort_cell(dx: int, dy: int) -> str:
        rr = int(clamp(r_i + dy, 1, BOARD_N)); cc = int(clamp(c_i + dx, 1, BOARD_N))
        # Enforce minimum Chebyshev separation of 2 cells from own ship
        if abs(cc - c_i) < 2:
            step_x = 2 if (dx or 1) > 0 else -2
            cc = int(clamp(c_i + step_x, 1, BOARD_N))
        if abs(rr - r_i) < 2:
            step_y = 2 if (dy or 1) > 0 else -2
            rr = int(clamp(r_i + step_y, 1, BOARD_N))
        return board_to_cell(rr, cc)
    # Compute lagged course/speed for escorts to simulate following with delay
    eff_course, eff_speed = _convoy_lagged(float(ship.get('heading', 0.0)), float(ship.get('speed', 0.0)))
    # Hermes
    hermes = next((e for e in escorts if str(e.get('name','')).lower().find('hermes')>=0), None)
    if hermes:
        dx, dy = hermes.get('offset_cells', [-2,3])
        hermes_cell = _escort_cell(int(dx), int(dy))
        # Health overlay for Hermes
        try:
            hl = _load_health(); hm = int(hl.get('hermes_max_lives',3)); hlv = int(hl.get('hermes_lives',hm));
            hermes_hp = int(round(100.0 * (hlv / max(1, hm))))
        except Exception:
            hermes_hp = 100
        out.append({
            'id':'hermes',
            'name': hermes.get('name'),
            'class': hermes.get('class','Carrier'),
            'cell': hermes_cell,
            'speed': eff_speed,
            'heading': eff_course,
            'status': {'health_pct': hermes_hp},
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
        glam_cell = _escort_cell(int(dx), int(dy))
        # Ensure minimum separation from Hermes as well
        try:
            # Parse hermes_cell back to indices
            def _parse_cell(s: str):
                j=0
                while j < len(s) and s[j].isalpha(): j+=1
                cl=s[:j] or 'A'; rs=int(s[j:] or '1')
                ci=0
                for ch in cl: ci=ci*26+(ord(ch)-ord('A')+1)
                return int(max(1,min(BOARD_N,rs))), int(max(1,min(BOARD_N,ci)))
            hr, hc = _parse_cell(hermes_cell) if hermes else (r_i, c_i)
            gr, gc = _parse_cell(glam_cell)
            if max(abs(gr-hr), abs(gc-hc)) < 2:
                # push glam further along its intended direction
                gr = int(clamp(gr + (2 if (dy or 1)>0 else -2), 1, BOARD_N))
                gc = int(clamp(gc + (2 if (dx or 1)>0 else -2), 1, BOARD_N))
                glam_cell = board_to_cell(gr, gc)
        except Exception:
            pass
        out.append({
            'id':'glamorgan',
            'name': glam.get('name'),
            'class': glam.get('class','DD'),
            'cell': glam_cell,
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

def record_officer(role: str, text: str) -> None:
    """Queue an officer radio line; playback + logging handled by radio queue.
    Target/threat messages are priority. UI renders '/radio.officer' lines; we log when playback starts.
    """
    role_str = str(role or "OFFICER")
    msg = str(text or "")
    low = msg.lower()
    prio = (role_str in ("Fire Control",)) or any(w in low for w in ("priority", "threat", "hit", "miss", "locked", "destroyed"))
    with STATE_LOCK:
        RADIO_QUEUE.append({"role": role_str, "text": msg, "prio": bool(prio), "enq_ts": time.time()})

def _crew_voice(role: str) -> str:
    try:
        r = (CREW.get('roles') or {}).get(role)
        v = (r or {}).get('voice')
        voice = (str(v) if v else os.environ.get('OPENAI_TTS_VOICE', 'alloy')).strip().lower()
        # Map unsupported aliases to valid voices
        if voice == 'ash':
            voice = 'alloy'
        return voice
    except Exception:
        return os.environ.get('OPENAI_TTS_VOICE', 'alloy')

# ---- Voice events (catalog + emitter) ----
VOICE_EVENTS_DEFAULT: Dict[str, Dict[str, Any]] = {
    "pilot.intercept.launch": {
        "role": "Pilot",
        "intent": "Acknowledge Hermes for intercept",
        "hint": "Hermes, intercept bogey, vector to {cell}."
    },
    "pilot.fox2": {"role":"Pilot","intent":"Missile fired","hint":"Fox Two!"},
    "pilot.splash": {"role":"Pilot","intent":"Kill confirm","hint":"Splash one bandit."},
    "radar.scan.start": {"role":"Radar","intent":"Acknowledge scanning","hint":"Captain, scanning radar."},
    "radar.scan.complete": {"role":"Radar","intent":"Scan complete","hint":"Captain, radar scan complete: {contacts} contact(s); hostiles {hostiles}; friendlies {friendlies}."},
    "hostile.attack.warn": {"role":"Fire Control","intent":"Inbound threat warning","hint":"Incoming {weapon} at {range_nm} nm."},
    "engineering.damage": {"role":"Engineering","intent":"Damage acknowledged","hint":"Captain, hit on {system}. Damage control responding."},
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
                        if not ev:
                            continue
                        events[ev] = {
                            'role': (it.get('role') or 'Ensign'),
                            'intent': (it.get('intent') or ''),
                            'hint': (it.get('hint') or ''),
                        }
                    except Exception:
                        continue
            return events or dict(VOICE_EVENTS_DEFAULT)
    except Exception:
        pass
    return dict(VOICE_EVENTS_DEFAULT)

VOICE_EVENTS: Dict[str, Dict[str, Any]] = _load_voice_events()

def voice_emit(event_id: str, ctx: Dict[str, Any] | None = None, *, fallback: str | None = None, role: str | None = None) -> None:
    ev = VOICE_EVENTS.get(str(event_id)) or {}
    r = role or ev.get('role') or 'Ensign'
    templ = ev.get('hint') or fallback or ''
    if not templ:
        return
    try:
        txt = _fmt_msg(templ, ctx or {})
    except Exception:
        txt = templ
    if txt:
        record_officer(str(r), txt)

def _tts_synthesize(text: str, role: str) -> str | None:
    """Synthesize text to speech via selected provider and cache.
    Provider selection: from crew voice "provider:voice" or TTS_PROVIDER env, default 'openai'.
    Returns relative URL path "/data/tts/<file>" or None on failure.
    """
    txt = (text or '').strip()
    if not txt:
        return None
    voice_spec = _crew_voice(role).strip()
    provider_default = os.environ.get('TTS_PROVIDER', '').strip().lower() or 'openai'
    if ':' in voice_spec:
        provider, voice_id = voice_spec.split(':', 1)
        provider = provider.strip().lower(); voice_id = voice_id.strip()
    else:
        provider, voice_id = provider_default, voice_spec

    def _hash_name(ext: str) -> tuple[str, Path]:
        h = hashlib.sha1(f"{provider}|{voice_id}|{txt}".encode('utf-8')).hexdigest()[:20]
        fname = f"{h}.{ext}"
        return fname, (TTS_DIR / fname)

    # macOS provider using 'say' + afconvert (AAC)
    if provider == 'macos':
        try:
            fname, aiff = _hash_name('aiff')
            if (TTS_DIR / (fname[:-5] + 'm4a')).exists():
                return f"/data/tts/{fname[:-5] + 'm4a'}"
            if aiff.exists():
                # Try convert only
                pass
            else:
                import subprocess, shlex
                cmd = ["say", "-v", voice_id or 'Daniel', "-o", str(aiff), txt]
                subprocess.run(cmd, check=True, timeout=20)
            # Convert to m4a if afconvert exists
            m4a = TTS_DIR / (fname[:-5] + 'm4a')
            try:
                import subprocess
                subprocess.run(["afconvert", str(aiff), str(m4a), "-f", "mp4f", "-d", "aac"], check=True, timeout=20)
                return f"/data/tts/{m4a.name}"
            except Exception:
                # Fallback serve AIFF
                return f"/data/tts/{fname}"
        except Exception as e:
            logging.warning("macOS TTS error: %s", e)
            return None

    # Piper provider
    if provider == 'piper':
        try:
            piper_bin = os.environ.get('TTS_PIPER_BIN', 'piper')
            model_dir = Path(os.environ.get('TTS_PIPER_MODEL_DIR', str(VOICES_DIR)))
            model_path = Path(voice_id)
            if not model_path.exists():
                model_path = model_dir / (voice_id + ('' if voice_id.endswith('.onnx') else '.onnx'))
            if not model_path.exists():
                logging.warning("Piper model not found: %s", model_path)
                return None
            fname_wav, wav = _hash_name('wav')
            if not wav.exists():
                import subprocess
                cmd = [piper_bin, "--model", str(model_path), "--output_file", str(wav), "--text", txt]
                # Optional tuning
                ls = os.environ.get('TTS_PIPER_LENGTH')
                ns = os.environ.get('TTS_PIPER_NOISE')
                nw = os.environ.get('TTS_PIPER_NOISEW')
                if ls: cmd += ["--length_scale", str(ls)]
                if ns: cmd += ["--noise_scale", str(ns)]
                if nw: cmd += ["--noise_w", str(nw)]
                subprocess.run(cmd, check=True, timeout=30)
            # Convert to m4a if possible; else mp3 via ffmpeg; else serve wav
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
            logging.warning("Piper TTS error: %s", e)
            return None

    # Default: OpenAI provider
    key = os.environ.get('OPENAI_API_KEY')
    if not key:
        return None
    model = os.environ.get('OPENAI_TTS_MODEL', 'gpt-4o-mini-tts')
    voice = voice_id or os.environ.get('OPENAI_TTS_VOICE', 'alloy')
    h = hashlib.sha1(f"{model}|{voice}|{txt}".encode('utf-8')).hexdigest()[:20]
    fname = f"{h}.mp3"
    fpath = TTS_DIR / fname
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

# (moved earlier) crew helpers defined above

# ---- Radio content helpers ----
def _radar_summary_ctx(own_x: float, own_y: float) -> Dict[str, Any]:
    try:
        contacts = list(RADAR.contacts)
    except Exception:
        contacts = []
    n = len(contacts)
    hostiles = [c for c in contacts if str(getattr(c,'allegiance','')).lower()== 'hostile']
    friendlies = [c for c in contacts if str(getattr(c,'allegiance','')).lower()== 'friendly']
    # Choose nearest hostile (threat)
    def dnm(c):
        dx = float(getattr(c,'x',0.0)) - float(own_x)
        dy = float(getattr(c,'y',0.0)) - float(own_y)
        return (dx*dx + dy*dy) ** 0.5
    top = (min(hostiles, key=dnm) if hostiles else None)
    return {
        'contacts': n,
        'hostiles': len(hostiles),
        'friendlies': len(friendlies),
        'threat': (str(getattr(top,'name','')) if top else '—'),
        'range_nm': (round(dnm(top),1) if top else None),
        'threat_level': (str(getattr(top,'threat','')) if top else '—')
    }

# ---- Simple rule-based Officer AI (Phase 2, step 1) ----
def _ai_parse(text: str) -> list[dict]:
    """Return a list of action dicts parsed from a free-form command.
    Actions: radar_scan, radar_lock{id}, radar_unlock, cap_request, cap_to_cell{cell,minutes?,radius_nm?}
    """
    actions: list[dict] = []
    s = (text or '').strip()
    low = s.lower()
    import re
    # scan
    if 'scan' in low and ('radar' in low or low.startswith('scan')):
        actions.append({'kind':'radar_scan'})
    # unlock (checked before lock)
    if 'unlock' in low:
        actions.append({'kind':'radar_unlock'})
    # lock <id>
    if 'lock' in low:
        m = re.search(r"lock\D*(\d+)", low)
        if m:
            actions.append({'kind':'radar_lock', 'id': int(m.group(1))})
    # CAP request to locked/priority
    if ('cap' in low) and any(w in low for w in ('request','launch','vector')) and ('to' not in low):
        actions.append({'kind':'cap_request'})
    # CAP to cell (e.g., "cap to K13 for 12 minutes radius 8")
    if 'cap' in low and 'to' in low:
        # find cell like K13
        m = re.search(r"\b([a-z]{1,2})(\d{1,2})\b", low)
        cell = None
        if m:
            col, row = m.group(1).upper(), int(m.group(2))
            if 1 <= row <= 26:
                cell = f"{col}{row}"
        # minutes
        mm = re.search(r"(for|minutes?)\s*(\d{1,2})", low)
        minutes = int(mm.group(2)) if mm else None
        # radius
        rm = re.search(r"radius\s*(\d{1,2})", low)
        radius = int(rm.group(1)) if rm else None
        if cell:
            actions.append({'kind':'cap_to_cell', 'cell': cell, 'minutes': minutes, 'radius_nm': radius})
    return actions

def _ai_exec(actions: list[dict]) -> list[str]:
    """Execute parsed actions via existing helpers. Return list of textual confirmations."""
    msgs: list[str] = []
    try:
        st = ENG.public_state() if hasattr(ENG, 'public_state') else {}
        own_x, own_y = radar_xy_from_state(st)
    except Exception:
        own_x, own_y = (0.0, 0.0)
    for a in actions:
        k = a.get('kind')
        try:
            if k == 'radar_scan':
                officer_say('Radar', 'scanning', {})
                try: RADAR.scan(own_x, own_y)
                except Exception: pass
                try:
                    ctx = _radar_summary_ctx(own_x, own_y)
                    officer_say('Radar', 'scan_report', ctx, fallback=f"Captain, radar scan complete: {ctx['contacts']} contact(s), hostiles {ctx['hostiles']}, friendlies {ctx['friendlies']}.")
                except Exception: pass
                msgs.append('RADAR: scanned')
            elif k == 'radar_unlock':
                try:
                    globals()['PRIMARY_ID'] = None
                except Exception: pass
                try:
                    RADAR.priority_id = None  # type: ignore[attr-defined]
                except Exception: pass
                officer_say('Fire Control', 'unlocked', {})
                msgs.append('RADAR: unlocked')
            elif k == 'radar_lock':
                cid = int(a.get('id'))
                target = None
                for c in RADAR.contacts:
                    if int(getattr(c,'id',-1)) == cid:
                        target = c; break
                if not target:
                    msgs.append(f'RADAR: lock failed (#{cid} not found)')
                else:
                    try: globals()['PRIMARY_ID'] = cid
                    except Exception: pass
                    try: RADAR.priority_id = cid  # type: ignore[attr-defined]
                    except Exception: pass
                    # Officer radio (Fire Control)
                    try:
                        ui = contact_to_ui(target, (own_x, own_y))
                        officer_say('Fire Control','locked',{'name': ui.get('name'), 'id': cid, 'range_nm': ui.get('range_nm')})
                    except Exception:
                        officer_say('Fire Control','locked',{'id': cid})
                    msgs.append(f'RADAR: locked #{cid}')
            elif k == 'cap_request':
                # Determine target as in /cap/request
                tid = None
                try:
                    tid = int(PRIMARY_ID) if ('PRIMARY_ID' in globals() and PRIMARY_ID is not None) else None  # type: ignore[name-defined]
                except Exception:
                    tid = None
                if tid is None:
                    tid = RADAR.priority_id
                tgt = next((c for c in RADAR.contacts if int(getattr(c,'id',-1)) == int(tid)), None) if tid is not None else None
                if not tgt or CAP is None:
                    msgs.append('CAP: no locked target or CAP unavailable')
                else:
                    dx = float(getattr(tgt,'x',0.0)) - float(own_x)
                    dy = float(getattr(tgt,'y',0.0)) - float(own_y)
                    rng = (dx*dx + dy*dy) ** 0.5
                    cell = world_to_cell(float(getattr(tgt,'x',0.0)), float(getattr(tgt,'y',0.0)))
                    res = CAP.request_cap_to_cell(cell, distance_nm=float(rng))
                    if res.get('ok'):
                        msgs.append(f'Hermes: CAP pair launching to {cell}')
                        try: stamp_cap_launch()
                        except Exception: pass
                    else:
                        msgs.append(f'CAP denied: {res.get("message","denied")}')
            elif k == 'cap_to_cell':
                if CAP is None:
                    msgs.append('CAP unavailable')
                else:
                    cell = str(a.get('cell') or '')
                    minutes = a.get('minutes', None)
                    radius_nm = a.get('radius_nm', None)
                    tx, ty = cell_to_world(cell)
                    dx, dy = float(tx) - float(own_x), float(ty) - float(own_y)
                    rng = (dx*dx + dy*dy) ** 0.5
                    res = CAP.request_cap_to_cell(cell, distance_nm=float(rng), station_minutes=(float(minutes) if minutes is not None else None), radius_nm=(float(radius_nm) if radius_nm is not None else None))
                    if res.get('ok'):
                        msgs.append(f'Hermes: CAP pair launching to {cell}')
                        try: stamp_cap_launch()
                        except Exception: pass
                    else:
                        msgs.append(f'CAP denied: {res.get("message","denied")}')
        except Exception:
            continue
    return msgs

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

@app.route("/radio/ask", methods=["GET", "POST"])
def radio_ask():
    from flask import request
    t0 = time.time(); route = "/radio/ask"
    try:
        txt = _arg_or_json(request, 'text', '')  # type: ignore[name-defined]
        if not txt:
            payload = {"ok": False, "error": "missing text"}
            record_flight({"route": route, "method": request.method, "status": 400,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
            return jsonify(payload), 400
        s = str(txt).strip()
        head = s.split(':',1)[0].split(',',1)[0].strip().lower()
        role = 'Ensign'
        if head.startswith(('nav','navigation')):
            role = 'Navigation'
        elif head.startswith(('radar','search')):
            role = 'Radar'
        elif head.startswith(('weap','weapon')):
            role = 'Weapons'
        elif head.startswith(('fire control','fire','fc')):
            role = 'Fire Control'
        elif head.startswith(('eng','engineer','engineering')):
            role = 'Engineering'
        reply = 'Captain, acknowledged.'
        try:
            st = ENG.public_state() if hasattr(ENG, 'public_state') else {}
            ship = (st or {}).get('ship', {})
            hdg = int(float(ship.get('heading', 0)))
            spd = int(float(ship.get('speed', 0)))
            cell = cell_for_world(float(ship.get('row',50.0)), float(ship.get('col',50.0)))
            low = s.lower()
            if role == 'Navigation' and ('course' in low or 'speed' in low or 'grid' in low):
                reply = f"Captain, ship steady on course {hdg}°, speed {spd} knots, grid {cell}."
            elif role == 'Radar' and ('nearest' in low or 'contacts' in low):
                own_x, own_y = get_own_xy(st)
                if RADAR.contacts:
                    c = min(RADAR.contacts, key=lambda k: ((k.x-own_x)**2 + (k.y-own_y)**2))
                    rng = round(((c.x-own_x)**2 + (c.y-own_y)**2) ** 0.5, 2)
                    reply = f"Captain, nearest contact ID {c.id}, range {rng} nm."
                else:
                    reply = "Captain, no contacts on scope."
            elif role == 'Weapons' and ('status' in low or 'readiness' in low or 'ammo' in low):
                arming = load_arming(); ammo = load_ammo()
                ready = [f"{k} {arming.get(k)} ({ammo.get(k,0)})" for k in ammo.keys()]
                reply = "Captain, weapons: " + "; ".join(ready[:3]) + ("…" if len(ready)>3 else "")
            # CAP request via radio (e.g., "Fire Control: Request CAP", "CAP launch")
            if ('cap' in low) and any(w in low for w in ('request','launch','vector')):
                # Determine locked/priority target
                tid = None
                try:
                    if 'PRIMARY_ID' in globals() and PRIMARY_ID is not None:  # type: ignore[name-defined]
                        tid = int(PRIMARY_ID)  # type: ignore[name-defined]
                except Exception:
                    tid = None
                if tid is None:
                    tid = getattr(RADAR, 'priority_id', None)
                tgt = next((c for c in RADAR.contacts if int(getattr(c, 'id', -1)) == int(tid)), None) if tid is not None else None
                if tgt is None:
                    reply = "Captain, no locked or selected target for CAP."
                else:
                    own_x, own_y = get_own_xy(st)
                    dx = float(getattr(tgt,'x',0.0)) - float(own_x)
                    dy = float(getattr(tgt,'y',0.0)) - float(own_y)
                    rng = (dx*dx + dy*dy) ** 0.5
                    try:
                        tcell = world_to_cell(float(getattr(tgt,'x',0.0)), float(getattr(tgt,'y',0.0)))
                    except Exception:
                        tcell = cell
                    if CAP is None:
                        reply = "Captain, CAP unavailable."
                    else:
                        res = CAP.request_cap_to_cell(tcell, distance_nm=float(rng))
                        if res.get('ok'):
                            reply = f"Hermes: CAP pair launching to {tcell}."
                            try:
                                stamp_cap_launch()
                            except Exception:
                                pass
                        else:
                            reply = f"Hermes: unable to launch — {res.get('message','denied')}"
        except Exception:
            pass
        record_officer(role, reply)
        payload = {"ok": True, "role": role, "reply": reply}
        record_flight({"route": route, "method": request.method, "status": 200,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {"text": txt}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radio/ask error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({"route": route, "method": request.method, "status": 500,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload), 500

@app.post("/radio/ai")
def radio_ai():
    from flask import request
    t0 = time.time(); route = "/radio/ai"
    try:
        txt = _arg_or_json(request, 'text', '')  # type: ignore[name-defined]
        if not txt:
            payload = {"ok": False, "error": "missing text"}
            record_flight({"route": route, "method": request.method, "status": 400,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
            return jsonify(payload), 400
        actions = _ai_parse(str(txt))
        if not actions:
            # Offer a brief help nudge
            record_officer('Ensign', "Captain, say 'Scan radar', 'Lock <id>', 'Request CAP', or 'CAP to K13'.")
            payload = {"ok": True, "actions": [], "reply": "HELP"}
            record_flight({"route": route, "method": request.method, "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {"text": txt}, "response": payload})
            return jsonify(payload)
        msgs = _ai_exec(actions)
        # Summarize as a single officer reply line
        if msgs:
            record_officer('Ensign', f"Captain, { '; '.join(msgs) }.")
        payload = {"ok": True, "actions": actions, "messages": msgs}
        record_flight({"route": route, "method": request.method, "status": 200,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {"text": txt}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radio/ai error: %s", e)
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


@app.get("/data/tts/<path:filename>")
def data_tts(filename: str):
    try:
        base = TTS_DIR
        return send_from_directory(str(base), filename)
    except Exception as e:
        logging.exception("/data/tts error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 404


@app.post("/alarm/trigger")
def alarm_trigger():
    from flask import request
    t0 = time.time(); route = "/alarm/trigger"
    try:
        data = request.get_json(silent=True) or {}
        sound = data.get('sound') or data.get('file') or 'red-alert.wav'
        # Always one-shot; ignore loop flag from client
        loop = False
        role = data.get('role') or 'Captain'
        msg = data.get('message') or None
        trigger_alarm(str(sound), message=(str(msg) if msg else None), role=str(role), loop=False)
        payload = {"ok": True}
        record_flight({"route": route, "method": request.method, "status": 200,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {"sound": sound, "loop": loop, "role": role, "message": msg}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/alarm/trigger error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({"route": route, "method": request.method, "status": 500,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload), 500

@app.get("/cap/roe")
def cap_roe():
    try:
        # Return minimal per-mission auth/asked flags
        info = {int(k): {"asked": bool(v.get('asked', False)), "authorized": bool(v.get('authorized', False))} for k,v in CAP_META.items()}
        return jsonify({"ok": True, "missions": info})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/cap/authorize")
def cap_authorize():
    from flask import request
    t0 = time.time(); route = "/cap/authorize"
    try:
        data = request.get_json(silent=True) or {}
        mid = int(data.get('id', 0))
        auth = bool(data.get('authorize', True))
        if mid <= 0 or mid not in CAP_META:
            payload = {"ok": False, "error": "unknown mission id"}
            record_flight({"route": route, "method": request.method, "status": 400,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": data, "response": payload})
            return jsonify(payload), 400
        CAP_META[mid]['authorized'] = auth
        CAP_META[mid]['asked'] = False
        # Radio feedback
        if auth:
            officer_say('Pilot','cleared', {})
        else:
            officer_say('Pilot','hold', {})
        payload = {"ok": True, "id": mid, "authorized": auth}
        record_flight({"route": route, "method": request.method, "status": 200,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": data, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/cap/authorize error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({"route": route, "method": request.method, "status": 500,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload), 500

@app.post("/alarm/clear")
def alarm_clear():
    t0 = time.time(); route = "/alarm/clear"
    try:
        clear_alarm()
        payload = {"ok": True}
        record_flight({"route": route, "method": "POST", "status": 200,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/alarm/clear error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({"route": route, "method": "POST", "status": 500,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload), 500


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
            # Overlay health into state so Own Fleet shows damage
            st_state = dict(payload.get('state', {})) if isinstance(payload.get('state', {}), dict) else {}
            hlth = _load_health()
            st_state['lives'] = int(hlth.get('lives', 3))
            st_state['max_lives'] = int(hlth.get('max_lives', 3))
            payload['ownfleet'] = _ownfleet_snapshot(st_state)
        except Exception:
            payload['ownfleet'] = []
        # Build contacts: radar first
        try:
            st = payload.get("state") or (ENG.public_state() if hasattr(ENG, "public_state") else {})
            own_xy = radar_xy_from_state(st)
            radar_list = [contact_to_ui(c, own_xy) for c in RADAR.contacts]
            # Ensure cell comes from shared world_to_cell(x, y)
            for d, c in zip(radar_list, RADAR.contacts):
                try:
                    d['cell'] = world_to_cell(c.x, c.y)
                except Exception:
                    pass
                # Include target class (Aircraft, Ship, Helicopter) for UI and audio cues
                try:
                    nm = str(d.get('name',''))
                    cls = TARGET_CLASS_BY_NAME.get(nm)
                    if cls:
                        d['class'] = cls
                except Exception:
                    pass
                # Label missile contacts explicitly
                try:
                    if str(getattr(c, 'meta', {}).get('kind','')) == 'missile':
                        d['class'] = 'Missile'
                except Exception:
                    pass
                # Include capability summary from contact meta (primary weapon + range)
                try:
                    cap = getattr(c, 'meta', {}).get('cap', {})
                    pw = cap.get('primary_weapon')
                    rmin = cap.get('min_range_nm')
                    rmax = cap.get('max_range_nm')
                    if pw: d['primary_weapon'] = pw
                    if rmin is not None: d['min_nm'] = rmin
                    if rmax is not None: d['max_nm'] = rmax
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
        # CAP snapshot (UI-friendly shape)
        try:
            payload["cap"] = _cap_ui_snapshot()
        except Exception:
            payload["cap"] = {"ready": False, "pairs": 0, "airframes": 0, "cooldown_s": 0, "committed": 0, "tasks": []}
        # Optional debug-contacts appended after radar list only if enabled
        try:
            if 'DEBUG_CONTACTS_ON' in globals() and DEBUG_CONTACTS_ON:  # type: ignore[name-defined]
                payload["contacts"] = payload.get("contacts", []) + list(DEBUG_CONTACTS)
        except Exception:
            pass
        # Ship cell string (A..Z + 1..26)
        try:
            s = payload.get('state',{})
            payload['ship_cell'] = ship_cell_from_state(s)
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
            with STATE_LOCK:
                payload['audio'] = dict(AUDIO_STATE)
        except Exception:
            payload['audio'] = {'last_launch': None, 'last_result': None, 'radio': None}
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

@app.get("/cap/readiness")
def cap_readiness():
    try:
        if CAP is None:
            return jsonify({"ok": False, "error": "CAP unavailable"}), 503
        return jsonify({"ok": True, "readiness": CAP.readiness()})
    except Exception as e:
        logging.exception("/cap/readiness error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/cap/status")
def cap_status():
    try:
        if CAP is None:
            return jsonify({"ok": False, "error": "CAP unavailable"}), 503
        return jsonify({"ok": True, "cap": CAP.snapshot()})
    except Exception as e:
        logging.exception("/cap/status error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get('/nav/hermes/close_in')
def nav_hermes_close_in():
    t0 = time.time(); route = '/nav/hermes/close_in'
    try:
        import math as _m
        st = ENG.public_state() if hasattr(ENG, 'public_state') else {}
        own_cell = ship_cell_from_state(st)
        j=0
        while j < len(own_cell) and own_cell[j].isalpha(): j+=1
        cletters = own_cell[:j] or 'A'; rstr = own_cell[j:] or '1'
        ci=0
        for ch in cletters: ci=ci*26+(ord(ch)-ord('A')+1)
        ri=int(rstr)
        convoy = _load_json(DATA_DIR / 'convoy.json', {})
        escorts = convoy.get('escorts', []) if isinstance(convoy, dict) else []
        hermes = next((e for e in escorts if str(e.get('name','')).lower().find('hermes')>=0), None)
        if hermes:
            dx_cells, dy_cells = int(hermes.get('offset_cells',[-2,3])[0]), int(hermes.get('offset_cells',[-2,3])[1])
            hermes_cell = board_to_cell(int(clamp(ri+dy_cells,1,BOARD_N)), int(clamp(ci+dx_cells,1,BOARD_N)))
        else:
            hermes_cell = 'L17'
        hx, hy = cell_to_world(hermes_cell)
        ox, oy = radar_xy_from_state(st)
        dx, dy = hx-ox, hy-oy
        rng = (dx*dx+dy*dy)**0.5
        brg = int(round((_m.degrees(_m.atan2(dx, -dy)) % 360.0)))
        rec_hdg = brg
        try:
            voice_emit('nav.hermes.close_in.request', {'ref_brg': brg, 'ref_rng': round(rng,1), 'rec_hdg': rec_hdg}, fallback=f'Recommend closing on Hermes: bearing {brg}°, range {rng:.1f} nm. New course {rec_hdg}°.', role='Navigation')
        except Exception:
            pass
        payload = {"ok": True, "bearing": brg, "range_nm": round(rng,1), "recommend_hdg": rec_hdg}
        record_flight({"route": route, "method": "GET", "status": 200, "duration_ms": int((time.time()-t0)*1000), "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/nav/hermes/close_in error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get('/nav/hermes/stand_off')
def nav_hermes_stand_off():
    t0 = time.time(); route = '/nav/hermes/stand_off'
    try:
        import math as _m
        st = ENG.public_state() if hasattr(ENG, 'public_state') else {}
        own_cell = ship_cell_from_state(st)
        j=0
        while j < len(own_cell) and own_cell[j].isalpha(): j+=1
        cletters = own_cell[:j] or 'A'; rstr = own_cell[j:] or '1'
        ci=0
        for ch in cletters: ci=ci*26+(ord(ch)-ord('A')+1)
        ri=int(rstr)
        convoy = _load_json(DATA_DIR / 'convoy.json', {})
        escorts = convoy.get('escorts', []) if isinstance(convoy, dict) else []
        hermes = next((e for e in escorts if str(e.get('name','')).lower().find('hermes')>=0), None)
        if hermes:
            dx_cells, dy_cells = int(hermes.get('offset_cells',[-2,3])[0]), int(hermes.get('offset_cells',[-2,3])[1])
            hermes_cell = board_to_cell(int(clamp(ri+dy_cells,1,BOARD_N)), int(clamp(ci+dx_cells,1,BOARD_N)))
        else:
            hermes_cell = 'L17'
        hx, hy = cell_to_world(hermes_cell)
        ox, oy = radar_xy_from_state(st)
        dx, dy = hx-ox, hy-oy
        rng = (dx*dx+dy*dy)**0.5
        brg = int(round((_m.degrees(_m.atan2(dx, -dy)) % 360.0)))
        standoff = 3
        try:
            voice_emit('nav.hermes.stand_off.request', {'ref_brg': brg, 'ref_rng': round(rng,1), 'standoff_nm': standoff}, fallback=f'Recommend Hermes stand-off {standoff} nm; current bearing {brg}°, range {rng:.1f} nm.', role='Navigation')
        except Exception:
            pass
        payload = {"ok": True, "bearing": brg, "range_nm": round(rng,1), "standoff_nm": standoff}
        record_flight({"route": route, "method": "GET", "status": 200, "duration_ms": int((time.time()-t0)*1000), "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/nav/hermes/stand_off error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/cap/request")
def cap_request():
    from flask import request
    t0 = time.time(); route = "/cap/request"
    try:
        if CAP is None:
            payload = {"ok": False, "error": "CAP unavailable"}
            record_flight({"route": route, "method": request.method, "status": 503,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
            return jsonify(payload), 503
        data = request.get_json(silent=True) or {}
        # Determine target: explicit id or current PRIMARY_ID / RADAR.priority_id
        tid = data.get("id")
        try:
            tid = int(tid) if tid is not None else tid
        except Exception:
            tid = None
        if tid is None:
            try:
                tid = int(PRIMARY_ID) if ('PRIMARY_ID' in globals() and PRIMARY_ID is not None) else None  # type: ignore[name-defined]
            except Exception:
                tid = None
        if tid is None:
            tid = RADAR.priority_id
        tgt = next((c for c in RADAR.contacts if int(getattr(c, 'id', -1)) == int(tid)) , None) if tid is not None else None
        if tgt is None:
            payload = {"ok": False, "error": "no locked/selected target"}
            record_flight({"route": route, "method": request.method, "status": 400,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": data, "response": payload})
            return jsonify(payload), 400
        # Compute distance and target cell
        st = ENG.public_state() if hasattr(ENG, "public_state") else {}
        own_x, own_y = radar_xy_from_state(st)
        dx = float(getattr(tgt, 'x', 0.0)) - float(own_x)
        dy = float(getattr(tgt, 'y', 0.0)) - float(own_y)
        rng_nm = (dx*dx + dy*dy) ** 0.5
        try:
            # world_to_cell expects (x, y)
            cell = world_to_cell(float(getattr(tgt, 'x', 0.0)), float(getattr(tgt, 'y', 0.0)))
        except Exception:
            cell = "K13"
        res = CAP.request_cap_to_cell(cell, distance_nm=float(rng_nm))
        status = 200 if res.get("ok") else 400
        payload = {"ok": bool(res.get("ok")), "message": res.get("message"), "mission": res.get("mission")}
        if res.get('ok'):
            try:
                stamp_cap_launch()
            except Exception:
                pass
            try:
                voice_emit('pilot.cap.launch', {'cell': cell}, fallback='Hermes, proceeding to CAP station at %s.' % (cell,), role='Pilot')
            except Exception:
                pass
            # Seed CAP_META for en-route detection/permission
            try:
                mid = int((res.get('mission') or {}).get('id'))
                st2 = ENG.public_state() if hasattr(ENG, 'public_state') else {}
                ox, oy = radar_xy_from_state(st2)
                CAP_META[mid] = {"origin_xy": (ox, oy), "asked": False, "authorized": False}
            except Exception:
                pass
        record_flight({"route": route, "method": request.method, "status": status,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {"id": tid, "cell": cell, "range_nm": round(rng_nm,2)}, "response": payload})
        return jsonify(payload), status
    except Exception as e:
        logging.exception("/cap/request error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({"route": route, "method": request.method, "status": 500,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload), 500

@app.post("/cap/launch_to")
def cap_launch_to():
    from flask import request
    t0 = time.time(); route = "/cap/launch_to"
    try:
        if CAP is None:
            payload = {"ok": False, "error": "CAP unavailable"}
            record_flight({"route": route, "method": request.method, "status": 503,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
            return jsonify(payload), 503
        data = request.get_json(silent=True) or {}
        cell = str(data.get("cell") or "").strip().upper()
        if not cell:
            payload = {"ok": False, "error": "missing cell"}
            record_flight({"route": route, "method": request.method, "status": 400,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": data, "response": payload})
            return jsonify(payload), 400
        # Compute distance from own ship to requested cell center
        st = ENG.public_state() if hasattr(ENG, "public_state") else {}
        own_x, own_y = radar_xy_from_state(st)
        tx, ty = cell_to_world(cell)
        dx, dy = float(tx) - float(own_x), float(ty) - float(own_y)
        rng_nm = (dx*dx + dy*dy) ** 0.5
        # Allow overrides; default to your requested 10 min on-station, 10 nm radius
        try:
            sm = data.get('station_minutes', None)
            rm = data.get('radius_nm', None)
        except Exception:
            sm = None; rm = None
        if sm is None:
            sm = 10
        if rm is None:
            rm = 10
        res = CAP.request_cap_to_cell(cell, distance_nm=float(rng_nm), station_minutes=float(sm), radius_nm=float(rm))
        status = 200 if res.get("ok") else 400
        payload = {"ok": bool(res.get("ok")), "message": res.get("message"), "mission": res.get("mission")}
        if res.get('ok'):
            try:
                stamp_cap_launch()
            except Exception:
                pass
            try:
                voice_emit('pilot.cap.launch', {'cell': cell}, fallback='Hermes, proceeding to CAP station at %s.' % (cell,), role='Pilot')
            except Exception:
                pass
            try:
                mid = int((res.get('mission') or {}).get('id'))
                CAP_META[mid] = {"origin_xy": (own_x, own_y), "asked": False, "authorized": False}
            except Exception:
                pass
        record_flight({"route": route, "method": request.method, "status": status,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {"cell": cell, "range_nm": round(rng_nm,2)}, "response": payload})
        return jsonify(payload), status
    except Exception as e:
        logging.exception("/cap/launch_to error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({"route": route, "method": request.method, "status": 500,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload), 500

"""/api/command moved to blueprint in routes/command.py"""

@app.get("/__old/weapons/catalog")
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

@app.post("/__old/weapons/arm")
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
        # Schedule officer readiness call when arming completes
        if state == 'Armed':
            try:
                PENDING_EVENTS.append({'due': time.time()+5.0, 'kind': 'arming_ready', 'weapon': name})
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

@app.post("/__old/weapons/fire")
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
        # test mode (require ARMED, consume ammo like real shot; no range gating)
        if mode == 'test':
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
                RADAR.rec.log('weapons.fire', {'name': name, 'mode': 'test', 'ammo': ammo[name]})
                RADAR.rec.log('radio.msg', {'kind': 'FIRE', 'text': f'TEST {name}'})
            except Exception:
                pass
            # Stamp audio launch so frontend plays sound for test fire
            try:
                with STATE_LOCK:
                    AUDIO_STATE['last_launch'] = {'weapon': _sound_key_for_weapon(name), 'ts': time.time()}
            except Exception:
                pass
            # Chaff effect window
            try:
                if _normalize_weapon_name(name) == 'Corvus chaff':
                    with STATE_LOCK:
                        DEFENSE_STATE['chaff_until'] = time.time() + 60.0
                    try:
                        RADAR.rec.log('defense.chaff', {'active_for_s': 60})
                    except Exception:
                        pass
            except Exception:
                pass
            payload = {'ok': True, 'result': 'TEST FIRED', 'name': name, 'ammo': ammo[name]}
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
            with STATE_LOCK:
                AUDIO_STATE['last_launch'] = {'weapon': _sound_key_for_weapon(name), 'ts': time.time()}
        except Exception:
            pass
        # Chaff effect window on real
        try:
            if _normalize_weapon_name(name) == 'Corvus chaff':
                with STATE_LOCK:
                    DEFENSE_STATE['chaff_until'] = time.time() + 60.0
                try:
                    RADAR.rec.log('defense.chaff', {'active_for_s': 60})
                except Exception:
                    pass
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
            # Auto-alarm on close threat if enabled in alarm config
            if event == "ship.alarm.threat_close":
                try:
                    cfg = load_alarm_cfg()
                    auto = (cfg.get('auto') or {}).get('threat_close') or {}
                    if bool(auto.get('enabled', False)):
                        msg_tpl = str(auto.get('message') or 'Combat alarm! Threat inside {range_nm} nm.')
                        rng = None
                        try:
                            rng = float((data or {}).get('range_nm'))
                        except Exception:
                            rng = None
                        try:
                            thresh = float(auto.get('threshold_nm', 3.0))
                        except Exception:
                            thresh = 3.0
                        if (rng is None) or (rng <= thresh):
                            msg = msg_tpl.format(range_nm=(f"{rng:.1f}" if isinstance(rng, (int,float)) else "?"))
                            trigger_alarm(str(auto.get('sound') or 'red-alert.wav'), message=msg, role=str(auto.get('role') or 'Fire Control'), loop=False)
                except Exception:
                    pass
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
try:
    # Provide CAP effects to RADAR so spawn/intercept logic can use it
    RADAR.cap_effects_provider = (lambda: CAP.current_effects() if CAP is not None else {"active": False})
except Exception:
    pass

# Seed a few friendly contacts at startup so the world isn't empty
def _spawn_initial_friendlies() -> None:
    try:
        st = ENG.public_state() if hasattr(ENG, "public_state") else {}
        own_x, own_y = radar_xy_from_state(st)
        # Only seed if radar list is currently empty to avoid duplicates on hot-reload
        if getattr(RADAR, 'contacts', None):
            return
        # Three bearings around the ship; moderate ranges
        bearings = [45.0, 135.0, 315.0]
        for b in bearings:
            try:
                r = random.uniform(6.0, 12.0)
                RADAR.force_spawn(own_x, own_y, 'Friendly', bearing_deg=b, range_nm=r)
            except Exception:
                continue
    except Exception:
        pass

_spawn_initial_friendlies()

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

def _spawn_hostile_by_name(own_x: float, own_y: float, *, name: str, range_nm: float, bearing_deg: float) -> Contact:
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
    next_id = getattr(RADAR, "_next_id", len(RADAR.contacts) + 1)
    try:
        det = RADAR.catalog.details(name)
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
        RADAR._next_id = int(next_id) + 1  # type: ignore[attr-defined]
    except Exception:
        pass
    RADAR.contacts.append(c)
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
        own_x, own_y = radar_xy_from_state(st)
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
        own_x, own_y = radar_xy_from_state(st)
        # Back-compat demo spawn — use Hostile via RADAR
        c = RADAR.force_spawn(own_x, own_y, "Hostile", bearing_deg=315.0, range_nm=random.uniform(8.0, 14.0))
        ui = contact_to_ui(c, (own_x, own_y))
        try:
            ui['cell'] = world_to_cell(c.x, c.y)
        except Exception:
            pass
        # Officer radio (Radar) — best effort if function is available
        try:
            officer_say('Radar','contact',{'type': ui.get('type'), 'bearing': round((315.0)%360), 'range_nm': ui.get('range_nm'), 'speed': ui.get('speed')})
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
            officer_say('Radar','contact',{'type': ui.get('type'), 'bearing': round((315.0)%360), 'range_nm': ui.get('range_nm'), 'speed': ui.get('speed')})
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
            officer_say('Radar','contact',{'type': ui.get('type'), 'bearing': round((315.0)%360), 'range_nm': ui.get('range_nm'), 'speed': ui.get('speed')})
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
        except Exception:
            pass
        try:
            officer_say('Radar','contact',{'type': klass, 'bearing': round((bearing_deg)%360), 'range_nm': ui.get('range_nm'), 'speed': ui.get('speed')})
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

@app.get("/radar/spawn_by_name")
def radar_spawn_by_name():
    """Debug: Spawn a specific hostile by exact name with catalog capabilities.
    Query params: name (required), range (nm, default 18), bearing (deg, default 315)
    """
    t0 = time.time(); route = "/radar/spawn_by_name"
    try:
        from flask import request
        name = (request.args.get('name') or '').strip()
        if not name:
            payload = {"ok": False, "error": "missing name"}
            record_flight({"route": route, "method": "GET", "status": 400,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
            return jsonify(payload), 400
        try:
            rng = float(request.args.get('range') or 18.0)
        except Exception:
            rng = 18.0
        try:
            bearing_deg = float(request.args.get('bearing') or 315.0)
        except Exception:
            bearing_deg = 315.0
        st = ENG.public_state() if hasattr(ENG, "public_state") else {}
        own_x, own_y = radar_xy_from_state(st)
        c = _spawn_hostile_by_name(own_x, own_y, name=name, range_nm=rng, bearing_deg=bearing_deg)
        ui = contact_to_ui(c, (own_x, own_y))
        try:
            ui['cell'] = world_to_cell(c.x, c.y)
        except Exception:
            pass
        payload = {"ok": True, "added": ui, "count": len(RADAR.contacts)}
        record_flight({"route": route, "method": "GET", "status": 200,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {"name": name, "range": rng, "bearing": bearing_deg}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radar/spawn_by_name error: %s", e)
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

@app.get("/skirmish")
def skirmish_page():
    try:
        return render_template('skirmish.html')
    except Exception as e:
        logging.exception("/skirmish page error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/menu")
def menu_page():
    try:
        return render_template('menu.html')
    except Exception as e:
        logging.exception("/menu page error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get('/roadmap')
def roadmap_page():
    try:
        return render_template('roadmap.html')
    except Exception as e:
        logging.exception("/roadmap page error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get('/roadmap/list')
def roadmap_list():
    try:
        _init_roadmap_if_missing()
        db = _load_roadmap()
        items = db.get('items') or []
        items = sorted(items, key=lambda it: it.get('order', 0))
        return jsonify({"ok": True, "items": items, "updated": db.get('updated')})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post('/roadmap/set_status')
def roadmap_set_status():
    from flask import request
    try:
        data = request.get_json(silent=True) or {}
        iid = int(data.get('id'))
        status = str(data.get('status')).strip().lower()
        if status not in ('pending','in_progress','done'):
            return jsonify({"ok": False, "error": "bad status"}), 400
    except Exception:
        return jsonify({"ok": False, "error": "bad params"}), 400
    _init_roadmap_if_missing()
    db = _load_roadmap(); items = list(db.get('items') or [])
    changed = False
    # enforce single in_progress
    if status == 'in_progress':
        for it in items:
            if it.get('status') == 'in_progress' and int(it.get('id',0)) != iid:
                it['status'] = 'pending'; changed = True
    for it in items:
        if int(it.get('id',0)) == iid:
            it['status'] = status; changed = True
            break
    if changed:
        db['items'] = items; db['updated'] = _skirmish_now_iso(); _save_roadmap(db)
    return jsonify({"ok": True, "items": items})

@app.get('/contacts/catalog')
def contacts_catalog():
    """Return contacts catalog (filterable by ?hostile=1 or ?friendly=1)."""
    try:
        data = _load_json(CONTACTS_PATH, [])
        items = data.get('items') if isinstance(data, dict) else data
        arr = []
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            name = str(it.get('name',''))
            if not name:
                continue
            arr.append({'name': name, 'type': it.get('type') or it.get('class') or '', 'allegiance': it.get('allegiance') or ''})
        from flask import request
        if request.args.get('hostile'):
            arr = [x for x in arr if str(x.get('allegiance','')).title() == 'Hostile']
        if request.args.get('friendly'):
            arr = [x for x in arr if str(x.get('allegiance','')).title() == 'Friendly']
        return jsonify({"ok": True, "items": arr})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

def _skirmish_apply_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Apply skirmish config: own heading/speed, arming, spawn hostiles."""
    applied: Dict[str, Any] = {"spawned": []}
    # Nav
    try:
        own = cfg.get('own', {}) if isinstance(cfg, dict) else {}
        hdg = own.get('heading_deg'); spd = own.get('speed_kts')
        kv = []
        if isinstance(hdg, (int, float)): kv.append(f"heading={float(hdg)}")
        if isinstance(spd, (int, float)): kv.append(f"speed={float(spd)}")
        if kv:
            try:
                ENG.exec_slash("/nav set " + " ".join(kv))
            except Exception:
                pass
    except Exception:
        pass
    # Arming
    try:
        arm = cfg.get('arm', {}) if isinstance(cfg, dict) else {}
        if isinstance(arm, dict):
            current = load_arming()
            for k, v in arm.items():
                nm = _normalize_weapon_name(str(k))
                current[nm] = _coerce_arming(v)
            save_arming(current)
    except Exception:
        pass
    # Spawns
    try:
        st = ENG.public_state() if hasattr(ENG, 'public_state') else {}
        own_x, own_y = radar_xy_from_state(st)
        for h in (cfg.get('hostiles') or []):
            try:
                name = str(h.get('name','Hostile'))
                # Support either (cell) or (range+bearing)
                if h.get('cell'):
                    cell = str(h.get('cell'))
                    tx, ty = cell_to_world(cell)
                    dx = float(tx) - float(own_x)
                    dy = float(ty) - float(own_y)
                    rng = (dx*dx + dy*dy) ** 0.5
                    import math as _m
                    bearing = (_m.degrees(_m.atan2(dx, -dy)) % 360.0)
                else:
                    rng = float(h.get('range_nm', 10.0))
                    bearing = float(h.get('bearing_deg', 315.0))
                c = _spawn_hostile_by_name(own_x, own_y, name=name, range_nm=rng, bearing_deg=float(bearing))
                ui = contact_to_ui(c, (own_x, own_y))
                try:
                    ui['cell'] = world_to_cell(c.x, c.y)
                except Exception:
                    pass
                applied['spawned'].append(ui)
            except Exception:
                continue
    except Exception:
        pass
    return applied

def _skirmish_summarize(start_epoch: float, stop_epoch: float) -> Dict[str, Any]:
    out = {"missiles": {"spawned": 0, "resolved": 0, "hits": 0, "misses": 0, "sea_dart": 0, "guns": 0}}
    try:
        if not FLIGHT_PATH.exists():
            return out
        with FLIGHT_PATH.open('r', encoding='utf-8') as f:
            for ln in f:
                try:
                    rec = json.loads(ln)
                except Exception:
                    continue
                ts = rec.get('ts')
                try:
                    t = datetime.fromisoformat(ts.replace('Z','+00:00')).timestamp() if isinstance(ts, str) else 0.0
                except Exception:
                    t = 0.0
                if not (start_epoch <= t <= stop_epoch):
                    continue
                route = rec.get('route','')
                if route == '/radar/missile.spawn':
                    out['missiles']['spawned'] += 1
                elif route == '/engagement.result':
                    resp = rec.get('response') or {}
                    w = str(resp.get('weapon',''))
                    if 'exocet' in w:
                        out['missiles']['resolved'] += 1
                        if str(resp.get('result')) == 'hit':
                            out['missiles']['hits'] += 1
                        else:
                            out['missiles']['misses'] += 1
                        d = resp.get('defense') or {}
                        if isinstance(d, dict):
                            if d.get('sea_dart') == 'intercept': out['missiles']['sea_dart'] += 1
                            if d.get('guns') == 'intercept': out['missiles']['guns'] += 1
    except Exception:
        pass
    return out

@app.get('/skirmish/list')
def skirmish_list():
    try:
        db = _load_skirmishes()
        items = db.get('items') or {}
        lst = []
        for k, v in items.items():
            try:
                it = dict(v)
                it['id'] = int(k)
                lst.append(it)
            except Exception:
                continue
        lst.sort(key=lambda x: x.get('id', 0))
        return jsonify({"ok": True, "items": lst, "active": SKIRMISH_ACTIVE.get('id')})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get('/skirmish/get')
def skirmish_get():
    try:
        from flask import request
        sid = int(request.args.get('id', '0'))
    except Exception:
        return jsonify({"ok": False, "error": "missing id"}), 400
    db = _load_skirmishes(); items = db.get('items') or {}
    it = items.get(str(sid))
    if not it:
        return jsonify({"ok": False, "error": "not found"}), 404
    obj = dict(it); obj['id'] = sid
    return jsonify({"ok": True, "item": obj, "active": SKIRMISH_ACTIVE.get('id')})

@app.post('/skirmish/create')
def skirmish_create():
    from flask import request
    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        data = {}
    db = _load_skirmishes(); items = db.get('items') or {}
    sid = _skirmish_next_id(db)
    name = str(data.get('name') or f'Skirmish {sid}')
    notes = str(data.get('notes') or '')
    cfg = data.get('config') or {
        'own': {'heading_deg': 120.0, 'speed_kts': 32.0},
        'arm': {'Sea Dart SAM': 'Armed', '20mm GAM-BO1 (twin)': 'Armed', '20mm Oerlikon': 'Armed'},
        'hostiles': [{'name': 'Super Étendard', 'range_nm': 12.0, 'bearing_deg': 315.0}],
    }
    rec = {'name': name, 'notes': notes, 'created_ts': _skirmish_now_iso(), 'config': cfg, 'status': 'ready', 'outcomes': [], 'run': None}
    items[str(sid)] = rec
    db['items'] = items
    _save_skirmishes(db)
    return jsonify({"ok": True, "id": sid, "item": {**rec, 'id': sid}})

@app.post('/skirmish/start')
def skirmish_start():
    from flask import request
    try:
        sid = int((request.get_json(silent=True) or {}).get('id', 0) or (request.args.get('id') or 0))
    except Exception:
        return jsonify({"ok": False, "error": "missing id"}), 400
    if not sid:
        return jsonify({"ok": False, "error": "missing id"}), 400
    with STATE_LOCK:
        if SKIRMISH_ACTIVE.get('id') not in (None, 0):
            return jsonify({"ok": False, "error": "skirmish already running", "active": SKIRMISH_ACTIVE.get('id')}), 409
    db = _load_skirmishes(); items = db.get('items') or {}
    it = items.get(str(sid))
    if not it:
        return jsonify({"ok": False, "error": "not found"}), 404
    cfg = it.get('config') or {}
    applied = _skirmish_apply_config(cfg if isinstance(cfg, dict) else {})
    now_ep = time.time(); now_iso = _skirmish_now_iso()
    it['status'] = 'running'; it['run'] = {'started_ts': now_iso, 'started_epoch': now_ep}
    items[str(sid)] = it; db['items'] = items; _save_skirmishes(db)
    with STATE_LOCK:
        SKIRMISH_ACTIVE['id'] = sid; SKIRMISH_ACTIVE['started_ts'] = now_iso
    record_flight({"route": "/skirmish.start", "method": "INT", "status": 200, "duration_ms": 0, "request": {"id": sid}, "response": {"ok": True}})
    return jsonify({"ok": True, "id": sid, "applied": applied})

@app.post('/skirmish/stop')
def skirmish_stop():
    from flask import request
    try:
        sid = int((request.get_json(silent=True) or {}).get('id', 0) or (request.args.get('id') or 0))
    except Exception:
        sid = None
    db = _load_skirmishes(); items = db.get('items') or {}
    if not sid:
        sid = (SKIRMISH_ACTIVE.get('id') or 0)
    it = items.get(str(sid))
    if not it:
        return jsonify({"ok": False, "error": "not found or not running"}), 404
    run = it.get('run') or {}
    start_ep = float(run.get('started_epoch', 0.0))
    stop_ep = time.time(); stop_iso = _skirmish_now_iso()
    summary = _skirmish_summarize(start_ep, stop_ep)
    it['status'] = 'stopped'; it['run'] = {**run, 'stopped_ts': stop_iso, 'stopped_epoch': stop_ep}
    it.setdefault('outcomes', []).append({'started_ts': run.get('started_ts'), 'stopped_ts': stop_iso, 'summary': summary})
    items[str(sid)] = it; db['items'] = items; _save_skirmishes(db)
    with STATE_LOCK:
        SKIRMISH_ACTIVE['id'] = None; SKIRMISH_ACTIVE['started_ts'] = None
    record_flight({"route": "/skirmish.stop", "method": "INT", "status": 200, "duration_ms": 0, "request": {"id": sid}, "response": {"ok": True, "summary": summary}})
    return jsonify({"ok": True, "id": sid, "summary": summary})

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


# ---- Crew config (roles, voices, messages) ----
def _load_crew() -> Dict[str, Any]:
    try:
        data = _load_json(CREW_PATH, {})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

CREW = _load_crew()

def _crew_msg(role: str, key: str) -> str | None:
    try:
        r = (CREW.get('roles') or {}).get(role)
        if not isinstance(r, dict):
            return None
        msgs = r.get('messages')
        if not isinstance(msgs, dict):
            return None
        tpl = msgs.get(key)
        return str(tpl) if tpl else None
    except Exception:
        return None

def _fmt_msg(tpl: str, ctx: Dict[str, Any]) -> str:
    class _Safe(dict):
        def __missing__(self, k):
            return "?"
    try:
        return tpl.format_map(_Safe(**{k: ("—" if v is None else v) for k, v in (ctx or {}).items()}))
    except Exception:
        return tpl

def officer_say(role: str, key: str, ctx: Dict[str, Any] | None = None, fallback: str | None = None) -> None:
    tpl = _crew_msg(role, key)
    text = _fmt_msg(tpl, ctx or {}) if tpl else (fallback or "")
    if text:
        record_officer(role, text)

# ---- Main ----
if __name__ == "__main__":
    # Keep logs concise
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    info = _template_info("index.html")
    print(f"[webdash] templates -> {info['template_folder']} | index -> {info.get('path')} | sha1={info.get('sha1')}")
    # Start the background engine thread only after all functions/routes are defined
    try:
        _t = threading.Thread(target=engine_thread, daemon=True)
        _t.start()
    except Exception:
        pass
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
