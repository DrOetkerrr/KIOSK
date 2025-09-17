from __future__ import annotations

import os, sys, time, threading, logging, hashlib
from pathlib import Path
from typing import Any, Dict
from datetime import datetime, timezone

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


def _bp_safe_register(import_path: str, attr: str = "bp") -> None:
    try:
        mod = __import__(import_path, fromlist=[attr])
        bp = getattr(mod, attr)
        app.register_blueprint(bp)
    except Exception:
        pass


# Register blueprints
_bp_safe_register('projects.falklandV2.routes.command')
_bp_safe_register('projects.falklandV2.routes.radar')
_bp_safe_register('projects.falklandV2.routes.radar_dev')
_bp_safe_register('projects.falklandV2.routes.weapons')
_bp_safe_register('projects.falklandV2.routes.cap')
_bp_safe_register('projects.falklandV2.routes.radio')
_bp_safe_register('projects.falklandV2.routes.nav')
_bp_safe_register('projects.falklandV2.routes.skirmish')
_bp_safe_register('projects.falklandV2.routes.roadmap')
_bp_safe_register('projects.falklandV2.routes.contacts')
_bp_safe_register('projects.falklandV2.routes.pages')
_bp_safe_register('projects.falklandV2.routes.diag')
_bp_safe_register('projects.falklandV2.routes.flight')
_bp_safe_register('projects.falklandV2.routes.eng')


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


def _bind_runtime(rt: GameRuntime) -> None:
    global ENG, STATE_LOCK, AUDIO_STATE, record_flight, record_radio
    global trigger_alarm, clear_alarm, stamp_cap_launch
    global LOG_DIR, FLIGHT_PATH, FLIGHT_MAX_BYTES
    global DATA_DIR, STATE_DIR, AMMO_PATH, ARMING_PATH, WEAP_CATALOG_PATH
    global CONTACTS_PATH, CREW_PATH, ALARM_CFG_PATH, HEALTH_PATH
    global TTS_DIR, VOICE_EVENTS_PATH, SKIRMISHES_PATH, ROADMAP_PATH, VOICES_DIR
    global _load_json, _save_json, _load_health, _save_health
    global load_ammo, save_ammo, load_arming, save_arming, compute_in_range
    global RADAR, CAP

    ENG = rt.engine
    STATE_LOCK = rt.state_lock
    AUDIO_STATE = rt.audio_state
    record_flight = rt.record_flight
    record_radio = rt.record_radio
    trigger_alarm = rt.trigger_alarm
    clear_alarm = rt.clear_alarm
    stamp_cap_launch = rt.stamp_cap_launch

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
    hook = globals().get('record_event')
    if CAP is not None and callable(hook):
        try:
            CAP._event_hook = hook  # type: ignore[attr-defined]
        except Exception:
            pass


_bind_runtime(RUNTIME)
RUNTIME.register_rebinder(_bind_runtime)

# Grid helpers
clamp = core.clamp
world_to_board = core.world_to_board
board_to_cell = core.board_to_cell
cell_for_world = core.cell_for_world
ship_cell_from_state = core.ship_cell_from_state
radar_xy_from_state = core.radar_xy_from_state
cell_to_world = core.cell_to_world
WORLD_N = core.WORLD_N
BOARD_N = core.BOARD_N
BOARD_MIN = core.BOARD_MIN

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

# Event feed for stations console
EVENT_QUEUE: list[Dict[str, Any]] = []
EVENT_MAX = 64


def record_event(event_id: str, data: Dict[str, Any] | None = None, *, text: str | None = None) -> None:
    try:
        payload = {
            'id': str(event_id),
            'ts': time.time(),
            'data': dict(data or {})
        }
        payload['text'] = text or format_event_text(event_id, payload['data'])
        with STATE_LOCK:
            EVENT_QUEUE.append(payload)
            if len(EVENT_QUEUE) > EVENT_MAX:
                del EVENT_QUEUE[0:len(EVENT_QUEUE)-EVENT_MAX]
    except Exception:
        pass


if CAP is not None:
    try:
        CAP._event_hook = record_event  # type: ignore[attr-defined]
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
PENDING_EVENTS: list[Dict[str, Any]] = []
ATTACK_STATE: Dict[int, float] = {}

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
                        'class_name': (data or {}).get('allegiance') or '',
                        'range_nm': rng,
                        'speed': speed
                    })
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
    RADAR.seed_test_contacts(float(ox0), float(oy0), count=10)
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
def _schedule_shot_result(weapon: str, target_id: int, target_name: str, target_class: str, range_nm: float) -> None:
    try:
        nm = str(weapon or '')
        # class-based flight time & Pk defaults
        try:
            wrec = next((w for w in WEAP_CATALOG if w.get('name') == nm), None)
            cls = (wrec or {}).get('class', 'Other')
        except Exception:
            cls = 'Other'
        if cls in ('Missile','SAM'):
            delay = 4.0 + 6.0 * float(range_nm)
            pk = 0.75
        elif cls in ('Gun',):
            delay = 2.0 * float(range_nm)
            pk = 0.5
        else:
            delay = 3.0 * float(range_nm)
            pk = 0.4
        PENDING_EVENTS.append({'due': time.time()+delay, 'kind': 'resolve_fire', 'weapon': nm, 'target_id': int(target_id), 'range_nm': float(range_nm), 'pk': float(pk)})
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


@app.get("/data/tts/<path:filename>")
def data_tts(filename: str):
    try:
        base = TTS_DIR
        return send_from_directory(str(base), filename)
    except Exception as e:
        logging.exception("/data/tts error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 404


@app.get("/api/status")
def api_status():
    t0 = time.time(); route = "/api/status"
    try:
        from projects.falklandV2.subsystems.status import build as build_status
        payload = build_status()
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
            "grid": {"world_n": WORLD_N, "board_n": BOARD_N, "scheme": "A1..Z26"},
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


def record_officer(role: str, text: str) -> None:
    role_str = str(role or "OFFICER"); msg = str(text or ""); low = msg.lower()
    prio = (role_str in ("Fire Control",)) or any(w in low for w in ("priority", "threat", "hit", "miss", "locked", "destroyed"))
    with STATE_LOCK:
        RADIO_QUEUE.append({"role": role_str, "text": msg, "prio": bool(prio), "enq_ts": time.time()})


def officer_say(role: str, key: str, ctx: Dict[str, Any] | None = None, fallback: str | None = None) -> None:
    tpl = _crew_msg(role, key)
    text = _fmt_msg(tpl, ctx or {}) if tpl else (fallback or "")
    if text:
        record_officer(role, text)


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
