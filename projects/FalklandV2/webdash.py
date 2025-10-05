from __future__ import annotations

import os, sys, time, threading, logging, hashlib, math, contextlib, wave, json, faulthandler
from pathlib import Path
from typing import Any, Dict, Callable
from datetime import datetime, timezone
from collections import deque

from flask import Flask, jsonify, send_from_directory  # type: ignore

# Repo root for absolute imports
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Third-party used by TTS helpers (imported in core)
import requests  # noqa: F401

# Core helpers (paths, JSON, weapons, audio, grid, voice, recorders)
from projects.falklandV2.subsystems import webcore as core
from projects.falklandV2.runtime_service import GameRuntime

# Engine + Radar
from projects.falklands.core.engine import Engine
from projects.falklandV2.radar import Radar, Contact, HOSTILES, WORLD_N, HOSTILE_SPEED_SCALE  # noqa: F401
from projects.falklandV2.subsystems.hermes_cap import HermesCAP
try:
    from projects.falklandV2.subsystems.convoy import Convoy
except Exception:
    Convoy = None  # type: ignore

# Engine adapter helpers used by routes and status
try:
    from .engine_adapter import world_to_cell, contact_to_ui, get_own_xy
except Exception:
    from projects.falklandV2.engine_adapter import world_to_cell, contact_to_ui, get_own_xy


# ---- Flask app ----
TPL_DIR = Path(__file__).parent / "templates"
app = Flask(__name__, template_folder=str(TPL_DIR))

LOG_DIR = REPO_ROOT / 'logs'
try:
    LOG_DIR.mkdir(exist_ok=True)
except Exception:
    pass


def _bp_safe_register(import_path: str, attr: str = "bp") -> None:
    """Register a blueprint, logging import/registration failures instead of failing silently.
    This makes missing-route bugs (404) easier to diagnose.
    """
    try:
        mod = __import__(import_path, fromlist=[attr])
        bp = getattr(mod, attr)
        app.register_blueprint(bp)
    except Exception as e:
        try:
            logging.exception("Failed to register blueprint %s.%s: %s", import_path, attr, e)
        except Exception:
            pass


# Register blueprints
_bp_safe_register('projects.falklandV2.routes.command')
_bp_safe_register('projects.falklandV2.routes.radar')
_bp_safe_register('projects.falklandV2.routes.radar_dev')
_bp_safe_register('projects.falklandV2.routes.weapons')
_bp_safe_register('projects.falklandV2.routes.cap')
_bp_safe_register('projects.falklandV2.routes.radio')
_bp_safe_register('projects.falklandV2.routes.interpreter')
_bp_safe_register('projects.falklandV2.routes.nav')
_bp_safe_register('projects.falklandV2.routes.skirmish')
_bp_safe_register('projects.falklandV2.routes.roadmap')
_bp_safe_register('projects.falklandV2.routes.contacts')
_bp_safe_register('projects.falklandV2.routes.pages')
_bp_safe_register('projects.falklandV2.routes.diag')
_bp_safe_register('projects.falklandV2.routes.flight')
_bp_safe_register('projects.falklandV2.routes.eng')
_bp_safe_register('projects.falklandV2.routes.resupply')
_bp_safe_register('projects.falklandV2.routes.mission')
_bp_safe_register('projects.falklandV2.routes.audio')

# --- Fallbacks for critical routes when a blueprint fails to load ---
def _ensure_cap_fallbacks():
    try:
        from flask import request, jsonify  # local import to avoid top-level dependency
        existing = {r.rule for r in app.url_map.iter_rules()}
        # Fallback for POST /cap/request (Intercept)
        if '/cap/request' not in existing:
            @app.post('/cap/request')
            def _cap_request_fallback():
                try:
                    if CAP is None:
                        return jsonify({"ok": False, "error": "CAP unavailable"}), 503
                    data = request.get_json(silent=True) or {}
                    # Resolve target: prefer explicit id -> PRIMARY_ID -> radar.priority_id
                    tid = data.get('id')
                    try:
                        tid = int(tid) if tid is not None else tid
                    except Exception:
                        tid = None
                    if tid is None:
                        pid = globals().get('PRIMARY_ID')
                        try:
                            tid = int(pid) if pid is not None else None
                        except Exception:
                            tid = None
                    if tid is None:
                        tid = getattr(RADAR, 'priority_id', None)
                    tgt = next((c for c in getattr(RADAR, 'contacts', []) if int(getattr(c,'id',-1)) == int(tid)), None) if tid is not None else None
                    # Accept client-provided cell when the exact target object no longer exists
                    cs = data.get('cell'); fallback_cell = (str(cs).strip().upper() if cs else None)
                    if tgt is None and not fallback_cell:
                        return jsonify({"ok": False, "error": "no locked/selected target"}), 400
                    st = ENG.public_state() if hasattr(ENG, 'public_state') else {}
                    own_x, own_y = radar_xy_from_state(st)
                    ship = (st or {}).get('ship', {}) if isinstance(st, dict) else {}
                    try:
                        course_deg = float(ship.get('heading', 0.0) or 0.0)
                    except Exception:
                        course_deg = 0.0
                    convoy = globals().get('CONVOY')
                    if convoy is not None:
                        hx, hy, hermes_cell = convoy.escort_world_cell('hermes', own_x, own_y, course_deg)
                    else:
                        hx, hy = own_x, own_y
                        hermes_cell = ship_cell_from_state(st)
                    def _contact_class(contact):
                        if contact is None:
                            return None
                        try:
                            meta = getattr(contact, 'meta', {}) or {}
                            if isinstance(meta, dict):
                                cap_meta = meta.get('cap') or {}
                                cls = cap_meta.get('class') if isinstance(cap_meta, dict) else None
                                if not cls:
                                    cls = meta.get('class') or meta.get('type')
                                if cls:
                                    return str(cls).title()
                        except Exception:
                            pass
                        try:
                            cls = getattr(contact, 'class', None)
                            if cls:
                                return str(cls).title()
                        except Exception:
                            pass
                        try:
                            cls = getattr(contact, 'type', None)
                            if cls:
                                return str(cls).title()
                        except Exception:
                            pass
                        try:
                            name = getattr(contact, 'name', None)
                            if name:
                                cls = TARGET_CLASS_BY_NAME.get(str(name))
                                if cls:
                                    return str(cls).title()
                        except Exception:
                            pass
                        return None
                    def _normalize_loadout(value):
                        if not value:
                            return ''
                        v = str(value).strip().lower()
                        if v in ('aim9','aim-9','sidewinder','missile'):
                            return 'aim9'
                        if v in ('bomb','bombs','mk82','iron'):
                            return 'bombs'
                        if v == 'auto':
                            return ''
                        return ''
                    if tgt is not None:
                        dx = float(getattr(tgt,'x',0.0)) - float(hx)
                        dy = float(getattr(tgt,'y',0.0)) - float(hy)
                        rng_nm = (dx*dx + dy*dy) ** 0.5
                        try:
                            cell = world_to_cell(float(getattr(tgt,'x',0.0)), float(getattr(tgt,'y',0.0)))
                        except Exception:
                            cell = 'K13'
                    else:
                        tx, ty = cell_to_world(fallback_cell)
                        dx, dy = float(tx) - float(hx), float(ty) - float(hy)
                        rng_nm = (dx*dx + dy*dy) ** 0.5
                        cell = fallback_cell
                    target_class = _contact_class(tgt)
                    requested_loadout = _normalize_loadout(data.get('loadout'))
                    surface_classes = {'Ship', 'Surface', 'Carrier', 'Escort', 'Landing Craft', 'Merchant', 'Convoy'}
                    air_classes = {'Aircraft', 'Helicopter', 'Missile', 'Bomber', 'Fighter'}
                    auto_default = 'aim9'
                    if target_class and target_class in surface_classes:
                        auto_default = 'bombs'
                    loadout = requested_loadout or auto_default
                    if loadout == 'bombs' and target_class and target_class in air_classes:
                        loadout = 'aim9'
                    elif loadout == 'aim9' and target_class and target_class in surface_classes and not requested_loadout:
                        loadout = 'bombs'
                    res = CAP.request_cap_to_cell(
                        cell,
                        distance_nm=float(rng_nm),
                        origin_xy=(hx, hy),
                        origin_cell=hermes_cell,
                        mission_kind='intercept',
                        loadout=loadout,
                    )
                    status = 200 if res.get('ok') else 400
                    payload = {"ok": bool(res.get('ok')), "message": res.get('message'), "mission": res.get('mission'), "loadout": loadout, "target_class": target_class}
                    try:
                        record_flight({"route": '/cap/request.fallback', "method": request.method, "status": status,
                                       "duration_ms": 0, "request": {"cell": cell, "range_nm": round(rng_nm,2), "loadout": loadout, "target_class": target_class}, "response": payload})
                    except Exception:
                        pass
                    return jsonify(payload), status
                except Exception as e:
                    try:
                        record_flight({"route": '/cap/request.fallback', "method": request.method, "status": 500,
                                       "duration_ms": 0, "request": {}, "response": {"ok": False, "error": str(e)}})
                    except Exception:
                        pass
                    return jsonify({"ok": False, "error": str(e)}), 500
        # Fallback for POST /cap/launch_to (CAP station)
        if '/cap/launch_to' not in existing:
            @app.post('/cap/launch_to')
            def _cap_launch_to_fallback():
                try:
                    if CAP is None:
                        return jsonify({"ok": False, "error": "CAP unavailable"}), 503
                    data = request.get_json(silent=True) or {}
                    cell = str(data.get('cell') or '').strip().upper()
                    if not cell:
                        return jsonify({"ok": False, "error": "missing cell"}), 400
                    st = ENG.public_state() if hasattr(ENG, 'public_state') else {}
                    own_x, own_y = radar_xy_from_state(st)
                    ship = (st or {}).get('ship', {}) if isinstance(st, dict) else {}
                    try:
                        course_deg = float(ship.get('heading', 0.0) or 0.0)
                    except Exception:
                        course_deg = 0.0
                    convoy = globals().get('CONVOY')
                    if convoy is not None:
                        hx, hy, hermes_cell = convoy.escort_world_cell('hermes', own_x, own_y, course_deg)
                    else:
                        hx, hy = own_x, own_y
                        hermes_cell = ship_cell_from_state(st)
                    tx, ty = cell_to_world(cell)
                    dx, dy = float(tx) - float(hx), float(ty) - float(hy)
                    rng_nm = (dx*dx + dy*dy) ** 0.5
                    sm = data.get('station_minutes', 20)
                    rm = data.get('radius_nm', 5)
                    follow = None
                    try:
                        f = data.get('follow')
                        follow = str(f).strip().lower() if f else None
                    except Exception:
                        follow = None
                    try:
                        loadout_raw = str((data.get('loadout') or 'aim9')).lower()
                    except Exception:
                        loadout_raw = 'aim9'
                    loadout = 'bombs' if loadout_raw in ('bomb', 'bombs') else 'aim9'
                    loadout_forced = None
                    if follow == 'hermes' and loadout != 'aim9':
                        loadout = 'aim9'
                        loadout_forced = 'hermes_follow'
                    res = CAP.request_cap_to_cell(
                        cell,
                        distance_nm=float(rng_nm),
                        station_minutes=float(sm),
                        radius_nm=float(rm),
                        origin_xy=(hx, hy),
                        origin_cell=hermes_cell,
                        loadout=loadout,
                        follow=follow,
                    )
                    status = 200 if res.get('ok') else 400
                    mission = res.get('mission') or {}
                    actual_loadout = str(mission.get('loadout') or loadout)
                    payload = {"ok": bool(res.get('ok')), "message": res.get('message'), "mission": mission, "loadout": actual_loadout}
                    if loadout_forced and actual_loadout == 'aim9':
                        payload['loadout_forced'] = loadout_forced
                    try:
                        record_flight({"route": '/cap/launch_to.fallback', "method": request.method, "status": status,
                                       "duration_ms": 0, "request": {"cell": cell, "range_nm": round(rng_nm,2), "loadout": loadout, "follow": follow}, "response": payload})
                    except Exception:
                        pass
                    return jsonify(payload), status
                except Exception as e:
                    try:
                        record_flight({"route": '/cap/launch_to.fallback', "method": request.method, "status": 500,
                                       "duration_ms": 0, "request": {}, "response": {"ok": False, "error": str(e)}})
                    except Exception:
                        pass
                    return jsonify({"ok": False, "error": str(e)}), 500
    except Exception:
        pass

_ensure_cap_fallbacks()


# One-shot startup selftest
try:
    from projects.falklandV2.routes.diag import run_selftest as _run_selftest

    def _startup_selftest():
        try:
            _run_selftest()
        except Exception:
            pass

    threading.Timer(1.5, _startup_selftest).start()
except Exception:
    pass


@app.get('/favicon.ico')
def favicon():
    return ('', 204)


# ---- Globals and re-exports for routes ----
try:
    PORT = int(os.environ.get("PORT", "5055"))
except Exception:
    PORT = 5055

try:
    APP_VERSION = os.environ.get("APP_VERSION") or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
except Exception:
    APP_VERSION = "dev"

RUNTIME = GameRuntime(port=PORT)
APP_STARTED = RUNTIME.app_started
try:
    RUNTIME.reset_state()
except Exception:
    pass


def _ensure_audio_flags() -> Dict[str, Any]:
    global AUDIO_FLAGS
    try:
        AUDIO_FLAGS
    except NameError:
        AUDIO_FLAGS = {}
    if not isinstance(AUDIO_FLAGS, dict):
        AUDIO_FLAGS = {}
    return AUDIO_FLAGS


def _reset_audio_state() -> None:
    base = {
        "last_launch": None,
        "last_result": None,
        "radio": None,
        "alarm": None,
        "cap_launch": None,
        "cap_recovery": None,
        "enemy_bomb": None,
        "shots_in_flight": [],
    }
    AUDIO_STATE.clear()
    AUDIO_STATE.update(base)


def _coerce_contact_id(val: Any) -> int | None:
    try:
        return int(val)
    except Exception:
        return None


def set_primary_contact(contact_id: Any, *, manual: bool = True) -> None:
    """Persist the current radar lock, optionally driving the radar module too."""
    global PRIMARY_ID
    cid = _coerce_contact_id(contact_id)
    PRIMARY_ID = cid
    if not manual:
        return
    radar = globals().get('RADAR')
    try:
        if radar is None:
            return
        if cid is None:
            if hasattr(radar, 'clear_manual_lock'):
                radar.clear_manual_lock()  # type: ignore[attr-defined]
            else:
                radar.priority_id = None  # type: ignore[attr-defined]
        else:
            if hasattr(radar, 'set_manual_lock'):
                radar.set_manual_lock(cid)  # type: ignore[attr-defined]
            else:
                radar.priority_id = cid  # type: ignore[attr-defined]
    except Exception:
        pass


def clear_primary_contact(*, manual: bool = True) -> None:
    set_primary_contact(None, manual=manual)


def _reset_runtime_globals() -> None:
    clear_primary_contact(manual=False)
    _ensure_audio_flags().clear()
    try:
        _reset_audio_state()
    except Exception:
        pass
    try:
        with STATE_LOCK:
            try:
                PENDING_EVENTS.clear()
            except Exception:
                pass
            try:
                DEFENSE_STATE.update({"chaff_until": 0.0, "turn_until": 0.0})
            except Exception:
                pass
            try:
                MOTION_STATE.update({"last_heading": None, "last_ts": 0.0})
            except Exception:
                pass
            try:
                SKIRMISH_ACTIVE.update({"id": None, "started_ts": None})
            except Exception:
                pass
            try:
                NAV_STATE.update({"last_cell": None, "turn_target": None, "turn_hold_since": 0.0, "boundary_cooldown_until": 0.0})
            except Exception:
                pass
            try:
                CAP_META.clear()
            except Exception:
                pass
            try:
                ATTACK_STATE.clear()
            except Exception:
                pass
            try:
                ENEMY_SURFACE_STATE.clear()
            except Exception:
                pass
    except Exception:
        pass
    try:
        RADIO_QUEUE.clear()
    except Exception:
        pass
    try:
        RADIO_HISTORY.clear()
    except Exception:
        pass
    try:
        RADIO_STATE['busy_until'] = 0.0
    except Exception:
        pass


def _bind_runtime(rt: GameRuntime) -> None:
    global ENG, STATE_LOCK, AUDIO_STATE, record_flight, record_radio
    global trigger_alarm, clear_alarm, stamp_cap_launch, stamp_cap_recovery
    global LOG_DIR, FLIGHT_PATH, FLIGHT_MAX_BYTES
    global DATA_DIR, STATE_DIR, AMMO_PATH, ARMING_PATH, WEAP_CATALOG_PATH
    global CONTACTS_PATH, CREW_PATH, ALARM_CFG_PATH, HEALTH_PATH
    global TTS_DIR, VOICE_EVENTS_PATH, SKIRMISHES_PATH, ROADMAP_PATH, VOICES_DIR
    global _load_json, _save_json, _load_health, _save_health
    global load_ammo, save_ammo, load_arming, save_arming, compute_in_range
    global RADAR, CAP
    global RESUPPLY

    ENG = rt.engine
    STATE_LOCK = rt.state_lock
    AUDIO_STATE = rt.audio_state
    _reset_runtime_globals()
    record_flight = rt.record_flight
    record_radio = rt.record_radio
    trigger_alarm = rt.trigger_alarm
    clear_alarm = rt.clear_alarm
    stamp_cap_launch = rt.stamp_cap_launch
    stamp_cap_recovery = rt.stamp_cap_recovery

    LOG_DIR = rt.log_dir
    FLIGHT_PATH = rt.flight_path
    FLIGHT_MAX_BYTES = rt.flight_max_bytes

    DATA_DIR = rt.data_dir
    STATE_DIR = rt.state_dir
    AMMO_PATH = rt.ammo_path
    ARMING_PATH = rt.arming_path
    WEAP_CATALOG_PATH = rt.weap_catalog_path
    CONTACTS_PATH = rt.contacts_path
    CREW_PATH = rt.crew_path
    ALARM_CFG_PATH = rt.alarm_cfg_path
    HEALTH_PATH = rt.health_path
    TTS_DIR = rt.tts_dir
    VOICE_EVENTS_PATH = rt.voice_events_path
    SKIRMISHES_PATH = rt.skirmishes_path
    ROADMAP_PATH = rt.roadmap_path
    VOICES_DIR = rt.voices_dir

    try:
        _reload_radio_audio_library(DATA_DIR / 'radiomsg')
    except Exception:
        pass

    _load_json = core._load_json
    _save_json = core._save_json
    _load_health = core._load_health
    _save_health = core._save_health

    load_ammo = rt.load_ammo
    save_ammo = rt.save_ammo
    load_arming = rt.load_arming
    save_arming = rt.save_arming
    compute_in_range = rt.compute_in_range

    RADAR = rt.radar
    CAP = rt.cap
    try:
        if RADAR is not None and hasattr(RADAR, 'bind_wave_schedule'):
            RADAR.bind_wave_schedule(getattr(rt, 'wave_schedule', None))  # type: ignore[arg-type]
    except Exception:
        pass
    # Resupply state (Sea King)
    if 'RESUPPLY' not in globals():
        RESUPPLY = {"active": False, "eta_ts": 0.0, "started_ts": 0.0, "stage": None}
    hook = globals().get('record_event')
    if CAP is not None and callable(hook):
        try:
            CAP._event_hook = hook  # type: ignore[attr-defined]
        except Exception:
            pass
    if CAP is not None and hasattr(CAP, 'bind_voice_hook'):
        try:
            CAP.bind_voice_hook(voice_emit)  # type: ignore[attr-defined]
        except Exception:
            pass
    # Provide resupply state to radar for Sea King injection
    try:
        if RADAR is not None:
            RADAR.resupply_state_provider = (lambda: RESUPPLY)
    except Exception:
        pass


_bind_runtime(RUNTIME)
RUNTIME.register_rebinder(_bind_runtime)

# Grid helpers (canonical AA00)
clamp = core.clamp
cell_for_world = core.cell_for_world
ship_cell_from_state = core.ship_cell_from_state
radar_xy_from_state = core.radar_xy_from_state
cell_to_world = core.cell_to_world
WORLD_N = core.WORLD_N
BOARD_N = getattr(core, 'BOARD_N', 26)
try:
    from projects.falklandV2.grid.config import MASTER_COLS as GRID_COLS, MASTER_ROWS as GRID_ROWS
except Exception:
    GRID_COLS = 40; GRID_ROWS = 40

# Weapons helpers
WEAP_CATALOG = core.WEAP_CATALOG
WEAP_MAP = core.WEAP_MAP
TARGET_CLASS_BY_NAME = core.TARGET_CLASS_BY_NAME
load_alarm_cfg = core.load_alarm_cfg

# Voice helpers
VOICE_EVENTS = core.VOICE_EVENTS
voice_emit = core.voice_emit
_crew_voice = core._crew_voice
_tts_synthesize = core._tts_synthesize
_sound_key_for_weapon = core._sound_key_for_weapon
format_event_text = core.format_event_text

# Radio queues
RADIO_QUEUE: list[Dict[str, Any]] = []
RADIO_STATE: Dict[str, Any] = {"busy_until": 0.0}
RADIO_HISTORY: deque[Dict[str, Any]] = deque(maxlen=32)
AUDIO_FLAGS: Dict[str, Any] = {}
PRIMARY_ID: int | None = None


RADIO_RECENT_WINDOW_S = 4.0
RADIO_RECENT_MESSAGES: Dict[tuple[str, str], float] = {}

# Event feed for stations console
EVENT_QUEUE: list[Dict[str, Any]] = []
EVENT_MAX = 64
RADIO_SCRIPT_PATH = DATA_DIR / "radio.md"


def _load_radio_script() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    try:
        if RADIO_SCRIPT_PATH.exists():
            for raw_line in RADIO_SCRIPT_PATH.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if " – " in line:
                    event_id, msg = line.split(" – ", 1)
                elif " - " in line:
                    event_id, msg = line.split(" - ", 1)
                else:
                    continue
                event_id = event_id.strip()
                msg = msg.strip().strip(' "\u201c\u201d')
                if event_id and msg:
                    mapping[event_id] = msg
    except Exception:
        pass
    return mapping


RADIO_EVENTS: Dict[str, str] = _load_radio_script()

RADIO_AUDIO_DIR = core.DATA_DIR / "radiomsg"
RADIO_AUDIO_LIBRARY: Dict[str, Dict[str, Any]] = {}


def _wav_duration_seconds(path: Path) -> float:
    try:
        with contextlib.closing(wave.open(str(path), 'rb')) as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 0
            if rate <= 0:
                return 0.0
            return max(0.0, frames / float(rate))
    except Exception:
        return 0.0


def _build_radio_audio_library(base: Path) -> Dict[str, Dict[str, Any]]:
    lib: Dict[str, Dict[str, Any]] = {}
    try:
        if not base.exists():
            return lib
        for wav_path in sorted(base.glob('*.wav')):
            try:
                key = wav_path.stem.upper()
                if not key:
                    continue
                dur = _wav_duration_seconds(wav_path)
                if dur <= 0.0:
                    dur = 2.5
                lib[key] = {
                    'file': f"/data/radiomsg/{wav_path.name}",
                    'duration': round(dur, 3),
                }
            except Exception:
                continue
    except Exception:
        return lib
    return lib


def _reload_radio_audio_library(base: Path | None = None) -> None:
    global RADIO_AUDIO_DIR, RADIO_AUDIO_LIBRARY
    try:
        source = base or (DATA_DIR / 'radiomsg')
    except Exception:
        source = core.DATA_DIR / 'radiomsg'
    RADIO_AUDIO_DIR = source
    RADIO_AUDIO_LIBRARY = _build_radio_audio_library(source)


_reload_radio_audio_library(core.DATA_DIR / 'radiomsg')

RADIO_ROLE_CHANNEL: Dict[str, int] = {
    'Navigation': 1,
    'Radar': 2,
    'Weapons': 3,
    'Fire Control': 3,
    'Pilot': 6,
    'Engineering': 5,
    'Bridge': 4,
    'Ensign': 4,
    'Captain': 4,
    'XO': 4,
}

GUARD_EVENT_PREFIXES: tuple[str, ...] = (
    'cap.',
    'enemy.attack.',
    'enemy.bomb.',
    'enemy.surface.',
    'weapon.result.',
)

GUARD_EVENT_IDS: set[str] = {
    'radar.contact.spawn',
    'eng.system.offline',
    'ship.alarm.threat_close',
}

RADIO_SKIP_VOICE: set[str] = {
    'nav.course.set',
    'nav.speed.set',
    'weapon.target.locked',
    'weapon.target.unlocked',
}


def _radio_role_for_event(event_id: str) -> str:
    eid = str(event_id)
    if eid in ('radar.target.locked', 'radar.target.unlocked'):
        return 'Fire Control'
    if eid.startswith('radar.'):
        return 'Radar'
    if eid.startswith('weapon.'):
        return 'Fire Control'
    if eid.startswith('cap.'):
        return 'Pilot'
    if eid.startswith('eng.'):
        return 'Engineering'
    if eid.startswith('nav.'):
        return 'Navigation'
    if eid.startswith('enemy.'):
        return 'Engineering'
    if eid.startswith('resupply.'):
        return 'Pilot'
    if eid.startswith('ship.'):
        return 'Radar'
    return 'Ensign'


def _radio_channel_for_event(event_id: str, *, default_role: str | None = None) -> int | None:
    eid = str(event_id)
    if eid in GUARD_EVENT_IDS:
        return 6
    for prefix in GUARD_EVENT_PREFIXES:
        if eid.startswith(prefix):
            return 6
    # Guard the SHAR pilots even if event uses misc prefix
    if eid.startswith('cap.'):
        return 6
    if default_role:
        return RADIO_ROLE_CHANNEL.get(default_role, None)
    return None


def _emit_radio_event(event_id: str, ctx: Dict[str, Any]) -> None:
    eid = str(event_id)
    tpl = RADIO_EVENTS.get(eid)
    if not tpl:
        return
    safe_ctx = {k: ("—" if v is None else v) for k, v in (ctx or {}).items()}
    try:
        text = tpl.format_map(safe_ctx)
    except Exception:
        text = tpl
    if text:
        if eid in RADIO_SKIP_VOICE:
            return
        role = _radio_role_for_event(event_id)
        channel = _radio_channel_for_event(event_id, default_role=role)
        record_officer(role, text, channel=channel, event_id=eid, event_ctx=ctx)


def _contact_label(contact_id: Any) -> str | None:
    try:
        cid = int(contact_id)
    except Exception:
        return None
    radar = globals().get('RADAR')
    if radar is None:
        return None
    try:
        for c in getattr(radar, 'contacts', []) or []:
            try:
                if int(getattr(c, 'id', -1)) == cid:
                    name = getattr(c, 'name', None)
                    if name:
                        return str(name)
            except Exception:
                continue
    except Exception:
        pass
    return None


def _enrich_event_payload(event_id: str, data: Dict[str, Any]) -> None:
    eid = str(event_id)
    if eid.startswith('weapon.'):
        data.setdefault('shooter', 'Sheffield')
        if 'name' in data and 'weapon' not in data:
            data['weapon'] = data.get('name')
        if 'weapon' in data and 'name' not in data:
            data['name'] = data.get('weapon')
        if not data.get('target') and data.get('target_id') is not None:
            label = _contact_label(data.get('target_id'))
            if label:
                data['target'] = label
        data.setdefault('target', 'Target')
    if eid.startswith('cap.weapon'):
        data.setdefault('shooter', 'Shar')
        if 'weapon' not in data and data.get('name'):
            data['weapon'] = data.get('name')
        if 'weapon' in data and 'name' not in data:
            data['name'] = data.get('weapon')
        if not data.get('target') and data.get('target_id') is not None:
            label = _contact_label(data.get('target_id'))
            if label:
                data['target'] = label
        data.setdefault('target', 'Target')
    if eid.startswith('enemy.bomb'):
        tgt = data.get('target')
        if isinstance(tgt, str):
            data['target'] = tgt.title()
    if eid.startswith('enemy.attack'):
        tgt = data.get('target')
        if isinstance(tgt, str):
            data['target'] = tgt.title()


def record_event(event_id: str, data: Dict[str, Any] | None = None, *, text: str | None = None) -> None:
    try:
        payload_data = dict(data or {})
        _enrich_event_payload(event_id, payload_data)
        payload = {
            'id': str(event_id),
            'ts': time.time(),
            'data': payload_data
        }
        payload['text'] = text or format_event_text(event_id, payload['data'])
        with STATE_LOCK:
            EVENT_QUEUE.append(payload)
            if len(EVENT_QUEUE) > EVENT_MAX:
                del EVENT_QUEUE[0:len(EVENT_QUEUE)-EVENT_MAX]
        try:
            _emit_radio_event(event_id, payload['data'])
        except Exception:
            # Radio side-effects are best-effort; do not fail the event
            logging.debug("radio emit failed for %s", event_id, exc_info=True)
    except Exception as exc:
        # Surface failures instead of swallowing them so missing events can be diagnosed
        logging.exception("record_event failed for %s", event_id)
        try:
            record_flight({
                'route': '/event.error',
                'method': 'INT',
                'status': 500,
                'duration_ms': 0,
                'request': {
                    'event': str(event_id),
                    'payload': dict(data or {}),
                },
                'response': {'error': str(exc)},
            })
        except Exception:
            # If even flight logging fails, there is nothing more we can do
            pass


if CAP is not None:
    try:
        CAP._event_hook = record_event  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        if hasattr(CAP, 'bind_voice_hook'):
            CAP.bind_voice_hook(voice_emit)  # type: ignore[attr-defined]
    except Exception:
        pass

try:
    RUNTIME.bind_mission_hooks(event_hook=record_event, voice_hook=voice_emit)
except Exception:
    pass

# NAV and CAP runtime
DEFENSE_STATE: Dict[str, Any] = {"chaff_until": 0.0, "turn_until": 0.0}
MOTION_STATE: Dict[str, Any] = {"last_heading": None, "last_ts": 0.0}
SKIRMISH_ACTIVE: Dict[str, Any] = {"id": None, "started_ts": None}
NAV_STATE: Dict[str, Any] = {"last_cell": None, "turn_target": None, "turn_hold_since": 0.0, "boundary_cooldown_until": 0.0}
CAP: HermesCAP | None = RUNTIME.cap
CONVOY = (Convoy.load(DATA_DIR) if Convoy is not None else None)
CAP_META: Dict[int, Dict[str, Any]] = {}
if CAP is not None:
    try:
        CAP.bind_permission_meta(CAP_META)  # type: ignore[attr-defined]
    except Exception:
        pass
PENDING_EVENTS: list[Dict[str, Any]] = []
ATTACK_STATE: Dict[int, float] = {}
ENEMY_SURFACE_STATE: Dict[int, Dict[str, Any]] = {}

# Debug contacts
DEBUG_CONTACTS = core.DEBUG_CONTACTS
DEBUG_NEXT_ID = core.DEBUG_NEXT_ID
_make_debug_contact = core._make_debug_contact


# ---- Radar instance ----
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
            if event == "radar.contact.new":
                try:
                    cid = (data or {}).get('id')
                    name = (data or {}).get('name')
                    speed = (data or {}).get('speed_kts')
                    cls = str((data or {}).get('class') or '')
                    wx, wy = None, None
                    try:
                        coords = (data or {}).get('world_xy') or []
                        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                            wx, wy = float(coords[0]), float(coords[1])
                    except Exception:
                        wx, wy = None, None
                    rng = None
                    if wx is not None and wy is not None:
                        try:
                            st = ENG.public_state() if hasattr(ENG, 'public_state') else {}
                            ox, oy = radar_xy_from_state(st)
                            rng = round(((wx-ox)**2 + (wy-oy)**2) ** 0.5, 1)
                        except Exception:
                            rng = None
                    record_event('radar.contact.spawn', {
                        'id': cid,
                        'name': name,
                        'class_name': cls or (data or {}).get('allegiance') or '',
                        'allegiance': (data or {}).get('allegiance'),
                        'range_nm': rng,
                        'speed': speed
                    })
                    try:
                        if str((data or {}).get('allegiance', '')).lower() == 'hostile' and cls.lower() == 'ship':
                            sid = int(cid) if cid is not None else None
                            if sid is not None and sid not in ENEMY_SURFACE_STATE:
                                ENEMY_SURFACE_STATE[sid] = {'name': str(name or f'Ship {sid}'), 'hp': 4.0, 'max_hp': 4.0, 'fleeing': False}
                    except Exception:
                        pass
                except Exception:
                    pass
            if event == "ship.alarm.threat_close":
                cfg = load_alarm_cfg()
                auto = (cfg.get('auto') or {}).get('threat_close') or {}
                if bool(auto.get('enabled', False)):
                    rng = (data or {}).get('range_nm')
                    try:
                        thresh = float(auto.get('threshold_nm', 3.0))
                    except Exception:
                        thresh = 3.0
                    if not isinstance(rng, (int, float)) or float(rng) <= thresh:
                        msg = str(auto.get('message') or 'Combat alarm! Threat inside {range_nm} nm.').format(range_nm=(f"{rng:.1f}" if isinstance(rng, (int,float)) else "?"))
                        trigger_alarm(str(auto.get('sound') or 'red-alert.wav'), message=msg, role=str(auto.get('role') or 'Fire Control'), loop=False)
                        try:
                            record_event('ship.alarm.threat_close', {'range_nm': rng})
                        except Exception:
                            pass
        except Exception:
            pass

try:
    _seed = os.environ.get('RADAR_SEED')
    _rng = (None if _seed is None else __import__('random').Random(int(_seed))) or __import__('random').Random()
except Exception:
    _rng = __import__('random').Random()

RADAR = Radar(rec=_RecorderLike(), rng=_rng, catalog_path=os.path.join(os.path.dirname(__file__), 'data', 'contacts.json'))
try:
    RADAR.cap_effects_provider = (lambda: CAP.current_effects() if CAP is not None else {"active": False})
except Exception:
    pass
try:
    st0 = ENG.public_state() if hasattr(ENG, 'public_state') else {}
    ox0, oy0 = radar_xy_from_state(st0)
except Exception:
    ox0, oy0 = (float(WORLD_N) / 2.0, float(WORLD_N) / 2.0)
try:
    core.spawn_initial_friendlies(sys.modules[__name__])  # type: ignore[attr-defined]
except Exception:
    pass


def _spawn_initial_friendlies() -> None:
    core.spawn_initial_friendlies(sys.modules[__name__])  # type: ignore


def _spawn_hostile_by_name(own_x: float, own_y: float, *, name: str, range_nm: float, bearing_deg: float) -> Contact:
    return core.spawn_hostile_by_name(sys.modules[__name__], own_x, own_y, name=name, range_nm=range_nm, bearing_deg=bearing_deg)  # type: ignore


# ---- Helpers ----
def _radar_summary_ctx(own_x: float, own_y: float) -> Dict[str, Any]:
    try:
        total = len(getattr(RADAR, 'contacts', []) or [])
        hostiles = len([c for c in RADAR.contacts if str(getattr(c,'allegiance','')).lower()=='hostile'])
        friendlies = len([c for c in RADAR.contacts if str(getattr(c,'allegiance','')).lower()=='friendly'])
        return {"contacts": total, "hostiles": hostiles, "friendlies": friendlies}
    except Exception:
        return {"contacts": 0, "hostiles": 0, "friendlies": 0}

# ---- Fire resolution scheduler ----
def _pk_from_range(weapon_name: str, weapon_class: str, range_nm: float, wrec: dict | None) -> float:
    try:
        mn = float((wrec or {}).get('min_nm', 0.0) or 0.0)
    except Exception:
        mn = 0.0
    try:
        mx = float((wrec or {}).get('max_nm', mn))
    except Exception:
        mx = mn
    span = max(1e-6, mx - mn) if mx > mn else max(1.0, mx if mx > 0.0 else 1.0)
    norm = (range_nm - mn) / span if span else 0.0
    norm = max(0.0, min(1.0, norm))

    cls = (weapon_class or '').title()
    if cls in ('Sam', 'Missile'):
        close_pk, far_pk = 0.82, 0.35
    elif cls in ('Gun',):
        close_pk, far_pk = 0.58, 0.22
    elif cls in ('Decoy',):
        close_pk, far_pk = 0.55, 0.25
    else:
        close_pk, far_pk = 0.6, 0.3

    pk = close_pk - (close_pk - far_pk) * norm

    if mn > 0.0 and range_nm < mn:
        below = max(0.0, mn - range_nm)
        pk *= max(0.25, 1.0 - (below / span))

    if mx > mn and range_nm > mx:
        over = min(1.0, (range_nm - mx) / span)
        pk = max(0.05, far_pk * (1.0 - 0.7 * over))

    return max(0.05, min(0.95, pk))


def _schedule_shot_result(weapon: str, target_id: int, target_name: str, target_class: str, range_nm: float, target_cell: str | None = None) -> None:
    try:
        nm = str(weapon or '')
        # class-based flight time & Pk defaults
        try:
            wrec = next((w for w in WEAP_CATALOG if w.get('name') == nm), None)
            cls = (wrec or {}).get('class', 'Other')
        except Exception:
            cls = 'Other'
        if cls in ('Missile','SAM'):
            # Mach 3 ≈ 1,000 m/s ≈ 1.94 nm/s. add 3 s boost phase.
            delay = 3.0 + (float(range_nm) / 1.94)
        elif cls in ('Gun',):
            delay = 2.0 * float(range_nm)
        else:
            delay = 3.0 * float(range_nm)
        pk = _pk_from_range(nm, cls, float(range_nm), wrec)
        fired_ts = time.time()
        target_id_int = int(target_id)
        due_ts = fired_ts + delay
        shot_id = f"{nm}:{int(fired_ts * 1000)}:{target_id_int}"
        PENDING_EVENTS.append({
            'due': due_ts,
            'kind': 'resolve_fire',
            'weapon': nm,
            'target_id': target_id_int,
            'range_nm': float(range_nm),
            'pk': float(pk),
            'shot_id': shot_id,
            'target_name': target_name,
            'target_class': target_class,
            'fired_ts': fired_ts,
            'target_cell': target_cell
        })
        try:
            with STATE_LOCK:
                shots = AUDIO_STATE.get('shots_in_flight')
                if not isinstance(shots, list):
                    shots = []
                entry = {
                    'id': shot_id,
                    'weapon': nm,
                    'target_id': target_id_int,
                    'target_name': target_name,
                    'target_class': target_class,
                    'range_nm': float(range_nm),
                    'pk': float(pk),
                    'fired_ts': fired_ts,
                    'due_ts': due_ts,
                    'result': None,
                    'result_ts': 0.0,
                    'cleanup_ts': 0.0,
                    'target_cell': target_cell
                }
                AUDIO_STATE['shots_in_flight'] = list(shots) + [entry]
        except Exception:
            pass
    except Exception:
        pass


# ---- Diagnostics / data ----
@app.get("/data/sounds/<path:filename>")
def data_sounds(filename: str):
    try:
        base = DATA_DIR / 'sounds'
        return send_from_directory(str(base), filename)
    except Exception as e:
        logging.exception("/data/sounds error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 404


@app.get("/data/radiomsg/<path:filename>")
def data_radiomsg(filename: str):
    try:
        base = DATA_DIR / 'radiomsg'
        return send_from_directory(str(base), filename)
    except Exception as e:
        logging.exception("/data/radiomsg error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 404


@app.get("/data/tts/<path:filename>")
def data_tts(filename: str):
    try:
        base = TTS_DIR
        return send_from_directory(str(base), filename)
    except Exception as e:
        logging.exception("/data/tts error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 404


def _status_diag_snapshot(payload: Dict[str, Any], build_ms: int) -> None:
    try:
        from projects.falklandV2.subsystems import webcore as core  # local import to avoid circulars
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        path = LOG_DIR / f"status_diag_{stamp}.log"
        info: Dict[str, Any] = {
            'ts_iso': datetime.now(timezone.utc).isoformat(),
            'build_ms': build_ms,
        }
        def _len(obj: Any) -> int:
            try:
                return len(obj)
            except Exception:
                return -1
        try:
            info['radio_queue_len'] = _len(RADIO_QUEUE)
            info['pending_events'] = _len(PENDING_EVENTS)
            info['audio_state_keys'] = list((AUDIO_STATE or {}).keys())
            info['cap_missions'] = _len(getattr(CAP, 'missions', [])) if CAP is not None else 0
            info['radar_contacts'] = _len(getattr(RADAR, 'contacts', []))
        except Exception:
            pass
        snapshot = {
            'info': info,
            'payload_keys': list((payload or {}).keys()) if isinstance(payload, dict) else [],
        }
        with path.open('w', encoding='utf-8') as fh:
            json.dump(snapshot, fh, indent=2, default=str)
            fh.write('\n\n[stacktrace]\n')
            faulthandler.dump_traceback(file=fh)
    except Exception:
        logging.exception("status diag snapshot failed")


@app.get("/api/status")
def api_status():
    t0 = time.time(); route = "/api/status"
    try:
        from projects.falklandV2.subsystems.status import build as build_status
        build_start = time.time()
        payload = build_status()
        build_ms = int((time.time() - build_start) * 1000)
        try:
            diag = payload.setdefault('diag', {}) if isinstance(payload, dict) else {}
            if isinstance(diag, dict):
                diag.setdefault('build_ms', build_ms)
        except Exception:
            pass
        if build_ms > 2000:
            try:
                record_flight({
                    "route": "/api/status.slow",
                    "method": "INT",
                    "status": 200,
                    "duration_ms": build_ms,
                    "request": {},
                    "response": {"ok": True, "build_ms": build_ms}
                })
            except Exception:
                logging.exception("/api/status slow-path logging failed")
            _status_diag_snapshot(payload if isinstance(payload, dict) else {}, build_ms)
        record_flight({"route": route, "method": "GET", "status": 200,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/api/status error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({"route": route, "method": "GET", "status": 500,
                       "duration_ms": int((time.time()-t0)*1000),
                       "request": {}, "response": payload})
        return jsonify(payload), 500


@app.get("/health")
def health():
    try:
        hud = ENG.hud_line() if hasattr(ENG, "hud_line") else "OK"
        return jsonify({"ok": True, "hud": hud})
    except Exception as e:
        logging.exception("/health error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/about")
def about():
    t0 = time.time(); route = "/about"
    try:
        radar_path = HERE.parent / "radar.py"
        tpl_folder = Path(app.template_folder).resolve(); index_path = tpl_folder / "index.html"
        def _file_info(p: Path) -> Dict[str, Any]:
            try:
                p = p.resolve(); info: Dict[str, Any] = {"path": str(p), "exists": p.exists()}
                if p.exists(): b = p.read_bytes(); info.update({"size": len(b), "sha1": hashlib.sha1(b).hexdigest()})
                return info
            except Exception:
                return {"path": str(p), "exists": False}
        payload: Dict[str, Any] = {
            "ok": True,
            "files": {"webdash": _file_info(HERE), "radar": _file_info(radar_path), "index": _file_info(index_path)},
            "app": {"port": PORT, "pid": os.getpid(), "started_iso": APP_STARTED.isoformat()},
            "template_folder": str(tpl_folder),
            "grid": {"world_n": WORLD_N, "cols": GRID_COLS, "rows": GRID_ROWS, "scheme": "AA00"},
        }
        record_flight({"route": route, "method": "GET", "status": 200, "duration_ms": int((time.time()-t0)*1000), "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/about error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({"route": route, "method": "GET", "status": 500, "duration_ms": int((time.time()-t0)*1000), "request": {}, "response": payload})
        return jsonify(payload), 500


# ---- Crew config (messages) ----
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


def _channel_for_role(role: str) -> int:
    try:
        ch = RADIO_ROLE_CHANNEL.get(str(role), None)
    except Exception:
        ch = None
    if ch is None:
        return 4
    if not isinstance(ch, int):
        try:
            ch = int(ch)
        except Exception:
            return 4
    if ch < 1 or ch > 6:
        return 4
    return ch


def _normalize_channel(role: str, channel: int | None) -> int:
    if channel is not None:
        try:
            ch = int(channel)
        except Exception:
            ch = _channel_for_role(role)
        else:
            if ch < 1 or ch > 6:
                ch = _channel_for_role(role)
        return ch
    return _channel_for_role(role)


def _radio_audio_lookup(key: str | None) -> Dict[str, Any] | None:
    if not key:
        return None
    try:
        return RADIO_AUDIO_LIBRARY.get(str(key).upper())
    except Exception:
        return None


def _audio_key_from_cap_weapon_fire(ctx: Dict[str, Any]) -> str | None:
    weapon = str((ctx or {}).get('weapon') or '').lower()
    if 'bomb' in weapon:
        return 'SHAR_BOMBS_AWAY'
    if 'aim' in weapon or 'sidewinder' in weapon or 'aim-9' in weapon:
        return 'SHAR_FOX_2'
    return 'SHAR_FOX_2'


def _audio_key_from_cap_weapon_hit(ctx: Dict[str, Any]) -> str | None:
    weapon = str((ctx or {}).get('weapon') or '').lower()
    if 'bomb' in weapon:
        return 'SHAR_BOMBS_AWAY'
    return 'SHAR_SPLASH_BANDIT'


def _audio_key_from_contact_spawn(ctx: Dict[str, Any]) -> str | None:
    info = ctx or {}
    alleg = str(info.get('allegiance') or info.get('class_name') or '').lower()
    if 'friendly' in alleg:
        return 'RDR_NEW_RADAR_CONTACT_FRIENDLY'
    if 'neutral' in alleg:
        return 'RDR_NEW_RADAR_CONTACT_FRIENDLY'
    if 'hostile' in alleg or 'enemy' in alleg:
        return 'RDR_NEW_ENEMY_RADAR_CONTACT'
    return 'RDR_NEW_RADAR_CONTACT_HOSTILE'


def _audio_key_from_cap_rtb(ctx: Dict[str, Any]) -> str | None:
    reason = str((ctx or {}).get('reason') or '').lower()
    if reason == 'winchester':
        return 'SHAR_WINCHESTER'
    return 'SHAR_RETURNING'


def _weapon_kind(name: str) -> str:
    nm = str(name or '').lower()
    if '20mm' in nm:
        return '20MM'
    if 'sea dart' in nm or 'sam' in nm:
        return 'SEADART'
    if 'exocet' in nm:
        return 'EXOCET'
    if 'chaff' in nm:
        return 'CHAFF'
    if '4.5' in nm or 'mk.8' in nm or 'main gun' in nm:
        return 'MAINGUN'
    return 'OTHER'


def _weapon_audio_for_arm(name: str) -> str | None:
    kind = _weapon_kind(name)
    return {
        '20MM': 'WPN_20MM_ARMED_READY',
        'SEADART': 'WPN_SEADART_ARMED',
        'EXOCET': 'WPN_EXOCET_ARMED',
        'MAINGUN': 'WPN_MAINGUN_ARMED',
        'CHAFF': 'WPN_STATION_ARMED',
        'OTHER': 'WPN_STATION_ARMED',
    }.get(kind)


def _weapon_audio_for_safe(name: str) -> str | None:
    kind = _weapon_kind(name)
    return {
        '20MM': 'WPN_20MM_SAFE',
        'SEADART': 'WPN_WEAPON_SAFE',
        'EXOCET': 'WPN_WEAPON_SAFE',
        'MAINGUN': 'WPN_MAINGUN_SAFE',
        'CHAFF': 'WPN_WEAPON_SAFE',
        'OTHER': 'WPN_WEAPON_SAFE',
    }.get(kind)


def _weapon_audio_for_reload_start(name: str) -> str | None:
    kind = _weapon_kind(name)
    return {
        'SEADART': 'WPN_SEADART_RELOADING',
        'EXOCET': 'WPN_EXOCET_RELOADING',
        'MAINGUN': 'WPN_MAINGUN_ARMED',
        '20MM': 'WPN_20MM_ARMED_READY',
    }.get(kind)


def _weapon_audio_for_ready(name: str) -> str | None:
    kind = _weapon_kind(name)
    return {
        '20MM': 'WPN_20MM_LOADED',
        'SEADART': 'WPN_SEADART_ARMED',
        'EXOCET': 'WPN_EXOCET_ARMED',
        'MAINGUN': 'WPN_MAINGUNARMED',
        'CHAFF': 'WPN_STATION_ARMED',
        'OTHER': 'WPN_STATION_ARMED',
    }.get(kind)


def _weapon_audio_for_out_of_ammo(name: str) -> str | None:
    kind = _weapon_kind(name)
    return {
        'MAINGUN': 'WPN_4.5CM_OUTOFAMMO',
        'SEADART': 'WPN_SEADART_OUTOFAMMO',
        'EXOCET': 'WPN_EXOCET_OUTOFAMMO',
    }.get(kind)


def _weapon_audio_for_fire(name: str) -> str | None:
    kind = _weapon_kind(name)
    if kind == 'CHAFF':
        return 'WPN_CHAFF'
    return None


def _weapon_name_from_ctx(ctx: Dict[str, Any]) -> str:
    return str((ctx or {}).get('name') or (ctx or {}).get('weapon') or '').strip()


def _eng_system_audio(ctx: Dict[str, Any]) -> str | None:
    sys_name = str((ctx or {}).get('system') or '').lower()
    if not sys_name:
        return None
    if 'nav' in sys_name:
        return 'ENG_NAV-OFFLINE'
    if 'radar' in sys_name or 'rdr' in sys_name:
        return 'ENG_RDR_OFFLINE'
    if 'fire' in sys_name or 'weapon' in sys_name:
        return 'ENG_WPN_OFFLINE'
    if 'comms' in sys_name:
        return 'ENG_COMMS_OFFLINE'
    if 'rudder' in sys_name or 'steering' in sys_name:
        return 'ENG_RUDDER_DAMAGED'
    if 'hull' in sys_name:
        return 'ENG_HULL_BREACHED'
    if 'engine' in sys_name or 'propulsion' in sys_name:
        return 'ENG_TARGET_HIT'
    return None


def _enemy_attack_audio(outcome: str, ctx: Dict[str, Any]) -> str | None:
    target = str((ctx or {}).get('target') or '').lower()
    if outcome == 'hit':
        if 'hermes' in target:
            return 'ENG_HERMES_HIT'
        if 'sheffield' in target or 'own' in target:
            return 'ENG_SHEFFIELD_HIT'
        return 'ENG_TARGET_HIT'
    return None


def _resupply_audio(event_id: str, ctx: Dict[str, Any]) -> str | None:
    if event_id == 'resupply.launch':
        return 'SEAKING_TAKING_OFF'
    if event_id in ('resupply.ready', 'resupply.complete'):
        return 'SEAKING_READY_RESUPPLY'
    return None


RADIO_EVENT_AUDIO_MAP: Dict[str, Callable[[Dict[str, Any]], str | None]] = {
    'radar.target.locked': lambda ctx: 'RDR_PRIMARY_TARGET_LOCKED',
    'radar.target.unlocked': lambda ctx: 'RDR_PRIMARY_TARGET_UNLOCKED',
    'radar.contact.spawn': _audio_key_from_contact_spawn,
    'cap.launch': lambda ctx: 'SHAR_TAKING_OFF',
    'cap.intercept.launch': lambda ctx: 'SHAR_TAKING_OFF',
    'cap.onstation': lambda ctx: 'SHAR_ON_STATION',
    'cap.weapon.fire': _audio_key_from_cap_weapon_fire,
    'cap.weapon.hit': _audio_key_from_cap_weapon_hit,
    'cap.weapon.miss': lambda ctx: 'SHAR_TARGET_MISSED',
    'cap.mission.rtb': _audio_key_from_cap_rtb,
    'cap.permission.timeout': lambda ctx: 'SHAR_FINAL_APPROACH',
    'cap.permission.authorized': lambda ctx: 'SHAR_ENGAGING',
    'cap.accident.deck': lambda ctx: 'SHAR_RECOVERY_CRASH',
    'cap.accident.inflight': lambda ctx: 'SHAR_DOWN',
    'cap.hazard.grounding': lambda ctx: 'SHAR_FAILURES_RTB',
    'cap.hazard.weather_abort': lambda ctx: 'SHAR_MISSION_ABORT',
    'pilot.intercept.launch': lambda ctx: 'SHAR_TAKING_OFF',
    'pilot.cap.launch': lambda ctx: 'SHAR_TAKING_OFF',
    'pilot.vector': lambda ctx: 'SHAR_ENGAGING',
    'pilot.fox2': lambda ctx: 'SHAR_FOX_2',
    'pilot.splash': lambda ctx: 'SHAR_SPLASH_BANDIT',
    'pilot.bombsaway': lambda ctx: 'SHAR_BOMBS_AWAY',
    'pilot.target_hit': lambda ctx: 'SHAR_SPLASH_BANDIT',
    'pilot.target_miss': lambda ctx: 'SHAR_TARGET_MISSED',
    'pilot.bandit.retreat': lambda ctx: 'SHAR_BANDIT_RETREAT',
    'weapon.arm': lambda ctx: _weapon_audio_for_arm(_weapon_name_from_ctx(ctx)),
    'weapon.safe': lambda ctx: _weapon_audio_for_safe(_weapon_name_from_ctx(ctx)),
    'weapon.target.locked': lambda ctx: 'RDR_PRIMARY_TARGET_LOCKED',
    'weapon.target.unlocked': lambda ctx: 'RDR_PRIMARY_TARGET_UNLOCKED',
    'weapon.fire': lambda ctx: _weapon_audio_for_fire(_weapon_name_from_ctx(ctx)),
    'weapon.result.hit': lambda ctx: 'WPN_TARGET_HIT',
    'weapon.result.miss': lambda ctx: 'WPN_TARGET_MISS',
    'weapon.result.no_effect': lambda ctx: 'WPN_TARGET_MISS',
    'weapon.out_of_ammo': lambda ctx: _weapon_audio_for_out_of_ammo(_weapon_name_from_ctx(ctx)),
    'weapon.reload.start': lambda ctx: _weapon_audio_for_reload_start(_weapon_name_from_ctx(ctx)),
    'weapon.reload.complete': lambda ctx: _weapon_audio_for_ready(_weapon_name_from_ctx(ctx)),
    'eng.system.offline': _eng_system_audio,
    'eng.repair.deployed': lambda ctx: 'ENG_REPAIR_TEAMS_COMMITTED',
    'enemy.attack.hit': lambda ctx: _enemy_attack_audio('hit', ctx),
    'enemy.attack.miss': lambda ctx: _enemy_attack_audio('miss', ctx),
    'enemy.bomb.hit': lambda ctx: _enemy_attack_audio('hit', ctx),
    'enemy.bomb.miss': lambda ctx: _enemy_attack_audio('miss', ctx),
    'enemy.surface.hit': lambda ctx: _enemy_attack_audio('hit', ctx),
    'enemy.surface.miss': lambda ctx: _enemy_attack_audio('miss', ctx),
    'eng.hermes.outofaction': lambda ctx: 'ENG_HEMERS_OUTOFACTION',
    'eng.abandon_ship': lambda ctx: 'ENG_ABANDON_SHIP',
    'ship.alarm.threat_close': lambda ctx: 'RDR_ENEMY_CONTACT_CLOSING_IN',
    'resupply.launch': lambda ctx: _resupply_audio('resupply.launch', ctx),
    'resupply.ready': lambda ctx: _resupply_audio('resupply.ready', ctx),
    'resupply.complete': lambda ctx: _resupply_audio('resupply.complete', ctx),
}


def _event_audio_key(event_id: str | None, ctx: Dict[str, Any] | None) -> str | None:
    if not event_id:
        return None
    handler = RADIO_EVENT_AUDIO_MAP.get(str(event_id))
    if handler is None:
        return None
    try:
        key = handler(ctx or {})
    except Exception:
        key = None
    return key


def _text_audio_key(role: str, msg: str) -> str | None:
    txt = str(msg or '').lower()
    if not txt:
        return None
    role_norm = str(role or '').strip().lower()
    if role_norm == 'pilot':
        if 'permission to engage' in txt or 'request permission' in txt:
            return 'SHAR_PERMISSION_ENGAGE'
        if 'fox two' in txt:
            return 'SHAR_FOX_2'
        if 'splash' in txt:
            return 'SHAR_SPLASH_BANDIT'
        if 'bombs away' in txt:
            return 'SHAR_BOMBS_AWAY'
        if 'target missed' in txt or 'missed target' in txt:
            return 'SHAR_TARGET_MISSED'
        if 'on station' in txt:
            return 'SHAR_ON_STATION'
        if 'winchester' in txt:
            return 'SHAR_WINCHESTER'
        if 'resupply' in txt and ('ready' in txt or 'complete' in txt):
            return 'SEAKING_READY_RESUPPLY'
        if 'resupply' in txt and ('airborne' in txt or 'underway' in txt or 'enroute' in txt):
            return 'SEAKING_TAKING_OFF'
    if role_norm == 'radar':
        if 'locked' in txt and 'unlocked' not in txt:
            return 'RDR_PRIMARY_TARGET_LOCKED'
        if 'unlocked' in txt:
            return 'RDR_PRIMARY_TARGET_UNLOCKED'
        if 'new radar contact' in txt and 'friendly' in txt:
            return 'RDR_NEW_RADAR_CONTACT_FRIENDLY'
        if 'new radar contact' in txt and 'hostile' in txt:
            return 'RDR_NEW_RADAR_CONTACT_HOSTILE'
        if 'closing in' in txt or 'threat close' in txt:
            return 'RDR_ENEMY_CONTACT_CLOSING_IN'
    if role_norm == 'engineering':
        if 'abandon ship' in txt:
            return 'ENG_ABANDON_SHIP'
        if 'hermes' in txt and ('hit' in txt or 'damaged' in txt):
            return 'ENG_HERMES_HIT'
        if 'hermes' in txt and ('out of action' in txt or 'combat ineffective' in txt):
            return 'ENG_HEMERS_OUTOFACTION'
        if 'navigation' in txt and ('offline' in txt or 'down' in txt):
            return 'ENG_NAV-OFFLINE'
        if 'radar' in txt and ('offline' in txt or 'down' in txt):
            return 'ENG_RDR_OFFLINE'
        if ('weapons' in txt or 'fire control' in txt) and ('offline' in txt or 'down' in txt):
            return 'ENG_WPN_OFFLINE'
        if 'comms' in txt and ('offline' in txt or 'down' in txt):
            return 'ENG_COMMS_OFFLINE'
        if 'rudder' in txt or 'steering' in txt:
            return 'ENG_RUDDER_DAMAGED'
        if 'hull' in txt and ('breach' in txt or 'breached' in txt):
            return 'ENG_HULL_BREACHED'
    return None


def _radio_audio_for(role: str, text: str, *, event_id: str | None, event_ctx: Dict[str, Any] | None) -> Dict[str, Any] | None:
    role_name = str(role or '')
    key = _event_audio_key(event_id, event_ctx)
    if not key:
        key = _text_audio_key(role_name, text)
    info = _radio_audio_lookup(key)
    if info:
        return dict(info)
    return None


def _sanitize_radio_prefix(msg: str, role_str: str) -> str:
    cleaned = str(msg or '')
    try:
        original = cleaned
        tokens = ('text', 'txt', 'captain')
        changed = False
        while True:
            stripped = cleaned.lstrip()
            lowered = stripped.lower()
            matched = False
            for token in tokens:
                if lowered.startswith(token):
                    rest = stripped[len(token):]
                    rest = rest.lstrip(" \t\n\r,:-" )
                    cleaned = rest
                    matched = True; changed = True
                    break
            if not matched:
                break
        cleaned = cleaned.strip()
        if changed and cleaned != original:
            try:
                record_flight({
                    'route': '/radio.sanitize', 'method': 'INT', 'status': 200, 'duration_ms': 0,
                    'request': {'role': role_str}, 'response': {'before': original[:120], 'after': cleaned[:120]}
                })
            except Exception:
                pass
    except Exception:
        pass
    return cleaned or ''


def record_officer(role: str, text: str, *, channel: int | None = None,
                   event_id: str | None = None, event_ctx: Dict[str, Any] | None = None) -> None:
    role_str = str(role or "OFFICER"); msg = str(text or "")
    msg = _sanitize_radio_prefix(msg, role_str)
    low = msg.lower()
    prio = (role_str in ("Fire Control",)) or any(w in low for w in ("priority", "threat", "hit", "miss", "locked", "destroyed"))
    channel_id = _normalize_channel(role_str, channel)
    audio_info = _radio_audio_for(role_str, msg, event_id=event_id, event_ctx=event_ctx)
    with STATE_LOCK:
        ts = time.time()
        try:
            key_recent = (role_str, msg.strip())
            last_ts = RADIO_RECENT_MESSAGES.get(key_recent)
            if last_ts is not None and (ts - float(last_ts)) <= RADIO_RECENT_WINDOW_S:
                return
            RADIO_RECENT_MESSAGES[key_recent] = ts
            cutoff = ts - (RADIO_RECENT_WINDOW_S * 2.0)
            for k, stamp in list(RADIO_RECENT_MESSAGES.items()):
                if stamp < cutoff:
                    RADIO_RECENT_MESSAGES.pop(k, None)
        except Exception:
            pass
        # Deduplicate identical consecutive messages within a short window to prevent double playback
        try:
            last = RADIO_QUEUE[-1] if RADIO_QUEUE else None
            if last and str((last or {}).get('text','')).strip() == msg.strip() and int((last or {}).get('channel', 0)) == channel_id and (ts - float((last or {}).get('enq_ts', 0.0))) <= 1.0:
                return
        except Exception:
            pass
        guard_flag = channel_id == 6
        entry = {
            "role": role_str,
            "text": msg,
            "prio": bool(prio),
            "enq_ts": ts,
            "channel": channel_id,
            "guard": guard_flag,
        }
        if event_id:
            entry['event'] = event_id
        if audio_info:
            file_path = audio_info.get('file')
            if file_path:
                entry['file'] = file_path
            try:
                dur = float(audio_info.get('duration') or 0.0)
            except Exception:
                dur = 0.0
            if dur > 0:
                entry['duration'] = dur
        RADIO_QUEUE.append(entry)
        try:
            RADIO_HISTORY.append({"ts": ts, "role": role_str, "text": msg, "channel": channel_id, "guard": guard_flag})
        except Exception:
            pass


def officer_say(role: str, key: str, ctx: Dict[str, Any] | None = None, fallback: str | None = None) -> None:
    """Emit a crew radio line.
    Prefers game event templates for consistency; falls back to crew.json.
    // Invariant guard: consistency suite — align crew messages to event/radio base
    """
    # Map common role+key pairs to canonical event ids
    ROLE_KEY_TO_EVENT = {
        ('Fire Control', 'locked'): 'radar.target.locked',
        ('Fire Control', 'unlocked'): 'radar.target.unlocked',
        ('Radar', 'scanning'): 'radar.scan.start',
        ('Radar', 'scan_report'): 'radar.scan.complete',
        ('Weapons', 'ready'): 'weapon.arm',
        ('Weapons', 'status'): None,  # status is dynamic; leave to fallback or caller
        ('Pilot', 'cleared'): 'cap.permission.authorized',
    }
    ev_id = ROLE_KEY_TO_EVENT.get((str(role), str(key)))
    text = ''
    try:
        if ev_id:
            text = format_event_text(ev_id, ctx or {})
    except Exception:
        text = ''
    if not text:
        tpl = _crew_msg(role, key)
        text = _fmt_msg(tpl, ctx or {}) if tpl else (fallback or "")
    if text:
        record_officer(role, text, event_id=ev_id, event_ctx=ctx or {})


def _arg_or_json(request, key: str, default: str | None = None) -> str | None:
    v = request.args.get(key)
    if v is None and request.is_json:
        try:
            body = request.get_json(silent=True) or {}
            v = body.get(key)
        except Exception:
            v = None
    return v if v is not None else default


ENGINE_THREAD: threading.Thread | None = None


def engine_thread() -> None:
    try:
        core.engine_thread_run(sys.modules[__name__])  # type: ignore
    except Exception:
        pass


def _ensure_engine_thread() -> None:
    global ENGINE_THREAD
    try:
        if ENGINE_THREAD is None or not ENGINE_THREAD.is_alive():
            t = threading.Thread(target=engine_thread, daemon=True)
            t.start()
            ENGINE_THREAD = t
    except Exception:
        ENGINE_THREAD = None


@app.before_request
def _kick_engine_thread() -> None:  # pragma: no cover - runtime bootstrap
    _ensure_engine_thread()


# Start immediately when module is imported (covers python -m usage)
try:
    _ensure_engine_thread()
except Exception:
    pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(f"[webdash] templates -> {TPL_DIR}")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
