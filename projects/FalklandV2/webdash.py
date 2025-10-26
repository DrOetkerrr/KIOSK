from __future__ import annotations

import os, sys, time, threading, logging, hashlib, math, contextlib, wave, json, faulthandler, re
from pathlib import Path
from typing import Any, Dict, Callable
from datetime import datetime, timezone
from collections import deque

from flask import jsonify, send_from_directory, render_template, request, redirect, send_file, url_for  # type: ignore

# Repo root for absolute imports
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Third-party used by TTS helpers (imported in core)
import requests  # noqa: F401

# Core helpers (paths, JSON, weapons, audio, grid, voice, recorders)
from projects.falklandV2.subsystems import webcore as core
from projects.falklandV2.subsystems import response_schema
from projects.falklandV2.runtime_service import GameRuntime
from projects.falklandV2.web import create_app, runtime as runtime_mgr, fallbacks as fallback_mgr
from projects.falklandV2 import watchdog

# Engine + Radar
from projects.falklandV2.core.engine import Engine
from projects.falklandV2.radar import Radar, Contact, HOSTILES, WORLD_N, HOSTILE_SPEED_SCALE  # noqa: F401
from projects.falklandV2.subsystems.hermes_cap import HermesCAP
from projects.falklandV2.radar_render import render_radar_png, render_test_pattern_png
from projects.falklandV2.radar_snapshot import build_radar_view
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
app = create_app()
WATCHDOG_ENABLED = os.environ.get('DISABLE_WATCHDOG', '').strip().lower() not in ('1', 'true', 'yes')

LOG_DIR = REPO_ROOT / 'logs'
try:
    LOG_DIR.mkdir(exist_ok=True)
except Exception:
    pass

# Ensure CAP fallbacks remain available when imported via webdash module
fallback_mgr.ensure_cap_fallbacks(app, sys.modules[__name__])


class StatusPollMonitor:
    """Tracks /api/status polling cadence and logs gaps for observability."""

    def __init__(self, *, warn_after_s: float = 8.0, check_interval_s: float = 5.0, warn_every_s: float = 30.0):
        self.warn_after_s = max(1.0, float(warn_after_s))
        self.check_interval_s = max(1.0, float(check_interval_s))
        self.warn_every_s = max(5.0, float(warn_every_s))
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._last_ok_ts: float = 0.0
        self._last_ok_duration_ms: float | None = None
        self._last_failure_ts: float = 0.0
        self._consecutive_failures: int = 0
        self._last_error: str | None = None
        self._last_warn_ts: float = 0.0
        self._started = False
        self.start()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        self._schedule()

    def stop(self) -> None:
        with self._lock:
            timer = self._timer
            self._timer = None
            self._started = False
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _schedule(self) -> None:
        timer = threading.Timer(self.check_interval_s, self._check_gap)
        timer.daemon = True
        with self._lock:
            if not self._started:
                return
            try:
                timer.start()
            except Exception:
                return
            self._timer = timer

    def _check_gap(self) -> None:
        now = time.time()
        with self._lock:
            last_ok = self._last_ok_ts
            last_warn = self._last_warn_ts
        gap = None
        if last_ok > 0.0:
            gap = now - last_ok
        if gap is not None and gap > self.warn_after_s and (now - last_warn) >= self.warn_every_s:
            self._emit_gap_warning(gap, now)
            with self._lock:
                self._last_warn_ts = now
        self._schedule()

    def _emit_gap_warning(self, gap: float, now: float) -> None:
        msg = f"/api/status polls quiet for {gap:.1f}s (warn>{self.warn_after_s:.1f}s)"
        logging.warning(msg)
        rf = globals().get('record_flight')
        if callable(rf):
            try:
                rf({
                    'route': '/diag.status.poll_gap',
                    'method': 'INT',
                    'status': 200,
                    'duration_ms': 0,
                    'request': {'gap_s': round(gap, 2), 'warn_threshold_s': self.warn_after_s},
                    'response': {'ts': now}
                })
            except Exception:
                pass

    def record_success(self, duration_ms: float | int | None) -> None:
        now = time.time()
        with self._lock:
            self._last_ok_ts = now
            try:
                self._last_ok_duration_ms = float(duration_ms) if duration_ms is not None else None
            except Exception:
                self._last_ok_duration_ms = None
            self._consecutive_failures = 0
            self._last_error = None
        try:
            watchdog.record_frontend_poll(now)
        except Exception:
            pass

    def record_failure(self, error: str | None = None) -> None:
        now = time.time()
        with self._lock:
            self._last_failure_ts = now
            self._consecutive_failures += 1
            if error:
                self._last_error = str(error)

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            last_ok = self._last_ok_ts
            last_duration = self._last_ok_duration_ms
            last_fail = self._last_failure_ts
            consecutive = self._consecutive_failures
            last_error = self._last_error
        gap = None
        if last_ok > 0.0:
            gap = max(0.0, now - last_ok)
        snapshot: Dict[str, Any] = {
            'last_ok_ts': last_ok,
            'last_ok_iso': datetime.fromtimestamp(last_ok, timezone.utc).isoformat() if last_ok else None,
            'last_duration_ms': last_duration,
            'gap_s': gap,
            'consecutive_failures': consecutive,
            'last_failure_ts': last_fail,
            'last_failure_iso': datetime.fromtimestamp(last_fail, timezone.utc).isoformat() if last_fail else None,
            'last_error': last_error,
        }
        return snapshot


STATUS_POLL_MONITOR = StatusPollMonitor()


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

RUNTIME = runtime_mgr.init_runtime(port=PORT, reset=True)
runtime_mgr.attach_runtime(app, RUNTIME)
APP_STARTED = RUNTIME.app_started

RADAR: Radar | None = None
CAP: HermesCAP | None = None


def _ensure_audio_flags() -> Dict[str, Any]:
    global AUDIO_FLAGS
    try:
        AUDIO_FLAGS
    except NameError:
        AUDIO_FLAGS = {}
    if not isinstance(AUDIO_FLAGS, dict):
        AUDIO_FLAGS = {}
    return AUDIO_FLAGS


def get_audio_flags() -> Dict[str, Any]:
    """Expose mutable audio flag storage to other subsystems."""
    return _ensure_audio_flags()


def _radio_audio_lookup(key: str | None) -> Dict[str, Any] | None:
    if not key:
        return None
    info = RADIO_AUDIO_LIBRARY.get(str(key).upper())
    if info:
        return dict(info)
    return None


def _reset_audio_state() -> None:
    try:
        existing_intro = AUDIO_STATE.get('intro')
    except Exception:
        existing_intro = None
    if not existing_intro:
        try:
            existing_intro = core.build_intro_payload()
        except Exception:
            existing_intro = None
    base = {
        "last_launch": None,
        "last_result": None,
        "radio": None,
        "alarm": None,
        "cap_launch": None,
        "cap_recovery": None,
        "enemy_bomb": None,
        "shots_in_flight": [],
        "intro": existing_intro,
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
    try:
        globals()['LAST_PRIMARY_UI'] = None
    except Exception:
        pass
    try:
        globals()['LAST_PRIMARY_UI'] = None
    except Exception:
        pass


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
                ARMING_PENDING.clear()
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
        watchdog.record_runtime_tick('reset_globals', time.time())
    except Exception:
        pass
    try:
        RADIO_STATE['busy_until'] = 0.0
    except Exception:
        pass


ARMING_PENDING: Dict[str, float] = {}
ARMING_STATE: Dict[str, Dict[str, float | str | bool]] = {}
_TTS_LOCK = threading.Lock()
_TTS_IN_FLIGHT: set[tuple[str, str]] = set()


def _default_weapon_record() -> Dict[str, float | str | bool]:
    return {'state': 'Safe', 'armed': False, 'arming_until': 0.0, 'cooldown_until': 0.0}


def _persist_arming_state_locked() -> None:
    payload: Dict[str, Any] = {}
    for name, rec in ARMING_STATE.items():
        if not isinstance(rec, dict):
            continue
        payload[name] = {
            'state': str(rec.get('state', 'Safe')),
            'armed': bool(rec.get('armed', False)),
            'arming_until': float(rec.get('arming_until', 0.0) or 0.0),
            'cooldown_until': float(rec.get('cooldown_until', 0.0) or 0.0),
        }
    save_fn = globals().get('_save_json')
    if save_fn is None or 'ARMING_PATH' not in globals():
        return
    try:
        merged = dict(payload)
        merged['weapons'] = payload
        save_fn(ARMING_PATH, merged)
    except Exception:
        pass


def _init_arming_state() -> None:
    lock = globals().get('STATE_LOCK')
    if lock is None:
        return
    load_fn = globals().get('_load_json')
    if load_fn is None or 'ARMING_PATH' not in globals():
        return
    raw = load_fn(ARMING_PATH, {})
    weapons_section = raw.get('weapons') if isinstance(raw, dict) else None
    if isinstance(weapons_section, dict):
        raw_map = weapons_section
    elif isinstance(raw, dict):
        raw_map = raw
    else:
        raw_map = {}
    with lock:
        ARMING_STATE.clear()
        for name, rec in raw_map.items():
            if not isinstance(rec, dict):
                continue
            ARMING_STATE[name] = {
                'state': str(rec.get('state', 'Safe')),
                'armed': bool(rec.get('armed', False)),
                'arming_until': float(rec.get('arming_until', 0.0) or 0.0),
                'cooldown_until': float(rec.get('cooldown_until', 0.0) or 0.0),
            }


def get_weapon_state(name: str) -> Dict[str, float | str | bool]:
    if not name:
        return _default_weapon_record()
    lock = globals().get('STATE_LOCK')
    if lock is None:
        return _default_weapon_record()
    with lock:
        if not ARMING_STATE:
            _init_arming_state()
        rec = ARMING_STATE.get(name)
        if rec is None:
            rec = _default_weapon_record()
            ARMING_STATE[name] = rec
        return dict(rec)


def update_weapon_state(name: str, **changes: Any) -> Dict[str, float | str | bool]:
    if not name:
        return _default_weapon_record()
    lock = globals().get('STATE_LOCK')
    if lock is None:
        return _default_weapon_record()
    with lock:
        if not ARMING_STATE:
            _init_arming_state()
        rec = ARMING_STATE.setdefault(name, _default_weapon_record())
        for key, value in changes.items():
            if value is None:
                continue
            if key in ('arming_until', 'cooldown_until'):
                try:
                    rec[key] = float(value)
                except Exception:
                    continue
            elif key == 'armed':
                rec[key] = bool(value)
            elif key == 'state':
                rec[key] = str(value)
            else:
                rec[key] = value
        _persist_arming_state_locked()
        return dict(rec)


def _watchdog_reset(details: Dict[str, Any]) -> None:
    reason = "; ".join(details.get("reasons", [])) or "watchdog"
    logging.warning("Watchdog initiating soft reset: %s", reason)
    try:
        runtime = globals().get('RUNTIME')
        if runtime is None:
            watchdog.note_reset(reason, False, error="runtime unavailable")
            return
        runtime.reset_state()
        _reset_runtime_globals()
        watchdog.note_reset(reason, True)
        watchdog.record_runtime_tick('reset', time.time())
    except Exception as exc:
        watchdog.note_reset(reason, False, error=str(exc))
        logging.exception("Watchdog reset failed: %s", exc)


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

    try:
        runtime_mgr.attach_runtime(app, rt)
    except Exception:
        pass

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
    rt_load_arming = rt.load_arming

    def _load_arming_with_pending() -> Dict[str, str]:
        now = time.time()
        try:
            base = rt_load_arming() if callable(rt_load_arming) else {}
        except Exception:
            base = {}
        out: Dict[str, str] = {}
        if isinstance(base, dict):
            out.update({str(k): str(v) for k, v in base.items()})
        try:
            pending = pending_arming_snapshot(now)
        except Exception:
            pending = {}
        lock = globals().get('STATE_LOCK')
        if lock is not None:
            with lock:
                if not ARMING_STATE:
                    _init_arming_state()
                for name, rec in ARMING_STATE.items():
                    if not isinstance(rec, dict):
                        continue
                    state_val = str(rec.get('state', 'Safe'))
                    armed = bool(rec.get('armed', False))
                    arming_until = float(rec.get('arming_until', 0.0) or 0.0)
                    if arming_until <= now and state_val == 'Arming':
                        state_val = 'Armed' if armed else 'Safe'
                    if armed and state_val != 'Arming':
                        state_val = 'Armed'
                    base_val = out.get(name)
                    if base_val == 'Armed' and state_val != 'Armed':
                        state_val = 'Armed'
                    out[name] = state_val
        for nm, left in (pending or {}).items():
            if left > 0:
                out[nm] = 'Arming'
        _prune_arming_pending(now)
        return out

    _init_arming_state()
    load_arming = _load_arming_with_pending
    save_arming = rt.save_arming
    compute_in_range = rt.compute_in_range

    RADAR = rt.radar
    CAP = rt.cap
    try:
        if RADAR is not None and hasattr(RADAR, 'bind_wave_schedule'):
            RADAR.bind_wave_schedule(getattr(rt, 'wave_schedule', None))  # type: ignore[arg-type]
    except Exception:
        pass
    # Resupply state (Sea King) and CAP hooks
    global RESUPPLY
    if 'RESUPPLY' not in globals() or RESUPPLY is None:
        RESUPPLY = {"active": False, "eta_ts": 0.0, "started_ts": 0.0, "stage": None,
                    "origin_cell": None, "origin_xy": None, "target_cell": None, "target_xy": None}
    hook = globals().get('record_event')
    cap = globals().get('CAP')
    if cap is not None and callable(hook):
        try:
            cap._event_hook = hook  # type: ignore[attr-defined]
        except Exception:
            pass
    if cap is not None and hasattr(cap, 'bind_voice_hook'):
        try:
            cap.bind_voice_hook(voice_emit)  # type: ignore[attr-defined]
        except Exception:
            pass
    # Provide resupply state to radar for Sea King injection
    try:
        if RADAR is not None:
            RADAR.resupply_state_provider = (lambda: RESUPPLY)
    except Exception:
        pass
    try:
        if RADAR is not None:
            RADAR.cap_missions_provider = (lambda: CAP.snapshot().get("missions") if CAP is not None else [])
    except Exception:
        pass
    try:
        watchdog.record_runtime_tick('bind', time.time())
    except Exception:
        pass
    if WATCHDOG_ENABLED and not app.config.get('TESTING', False):
        try:
            watchdog.start(_watchdog_reset)
        except Exception:
            logging.exception("Watchdog failed to start")


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
SOUND_CLIP_CACHE: Dict[str, Dict[str, Any]] = {}
CELL_AUDIO_PATTERN = re.compile(r"^([A-Z]{2})(\d{1,3})$")


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


def _sounds_audio_dir() -> Path:
    try:
        base = DATA_DIR / 'sounds'
        if base.exists():
            return base
    except Exception:
        pass
    try:
        base = core.DATA_DIR / 'sounds'
        if base.exists():
            return base
    except Exception:
        pass
    return Path(__file__).parent / 'data' / 'sounds'


def _sound_clip_lookup(name: str) -> Dict[str, Any] | None:
    key = str(name or '').strip().upper()
    if not key:
        return None
    cached = SOUND_CLIP_CACHE.get(key)
    if cached:
        return dict(cached)
    base = _sounds_audio_dir()
    path = base / f"{key}.wav"
    if not path.exists():
        return None
    dur = _wav_duration_seconds(path)
    if dur <= 0.0:
        dur = 0.6
    info = {
        'file': f"/data/sounds/{path.name}",
        'duration': round(dur, 3),
    }
    SOUND_CLIP_CACHE[key] = info
    return dict(info)


def _cell_audio_tokens(cell: str | None) -> list[str]:
    if not cell:
        return []
    try:
        label = str(cell).strip().upper()
    except Exception:
        return []
    match = CELL_AUDIO_PATTERN.match(label)
    if not match:
        return []
    letters, digits = match.groups()
    tokens: list[str] = [letters[0], letters[1]]
    if digits:
        num_token = digits if len(digits) >= 2 else digits.rjust(2, '0')
        tokens.append(num_token)
    return tokens


def _compose_contact_spawn_audio(base: Dict[str, Any] | None, ctx: Dict[str, Any]) -> Dict[str, Any] | None:
    base_info = dict(base or {})
    playlist: list[str] = []
    durations: list[float] = []
    first_file = base_info.get('file')
    if isinstance(first_file, str) and first_file:
        playlist.append(first_file)
        try:
            durations.append(float(base_info.get('duration') or 0.0))
        except Exception:
            pass
    tokens = _cell_audio_tokens((ctx or {}).get('cell'))
    token_clips: list[Dict[str, Any]] = []
    missing = False
    for token in tokens:
        clip = _sound_clip_lookup(token)
        if clip is None:
            logging.debug("Radar coordinate audio clip missing for token %s", token)
            missing = True
            break
        token_clips.append(clip)
    if not missing:
        for clip in token_clips:
            file_path = clip.get('file')
            if isinstance(file_path, str) and file_path:
                playlist.append(file_path)
                try:
                    durations.append(float(clip.get('duration') or 0.0))
                except Exception:
                    pass
    if not playlist:
        return base_info or None
    total = 0.0
    for val in durations:
        try:
            total += float(val)
        except Exception:
            continue
    if total <= 0.0:
        try:
            total = float(base_info.get('duration') or 0.0)
        except Exception:
            total = 0.0
    if total <= 0.0:
        total = len(playlist) * 0.9
    base_info['playlist'] = playlist
    if 'file' not in base_info and playlist:
        base_info['file'] = playlist[0]
    base_info['duration'] = round(total, 3)
    return base_info

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
CAP = RUNTIME.cap
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

def _prune_arming_pending(now: float) -> None:
    lock = globals().get('STATE_LOCK')
    if lock is None:
        return
    with lock:
        expired = [nm for nm, until in ARMING_PENDING.items() if until <= now]
        for nm in expired:
            ARMING_PENDING.pop(nm, None)


def mark_weapon_arming(name: str, until_ts: float) -> None:
    if not name:
        return
    now = time.time()
    lock = globals().get('STATE_LOCK')
    if lock is None:
        return
    with lock:
        if until_ts > now:
            ARMING_PENDING[name] = float(until_ts)
            rec = ARMING_STATE.setdefault(name, {})
            rec['state'] = 'Arming'
            rec['arming_until'] = float(until_ts)
            rec.setdefault('armed', False)
            _persist_arming_state_locked()
        else:
            ARMING_PENDING.pop(name, None)
            rec = ARMING_STATE.setdefault(name, {})
            rec['arming_until'] = 0.0
            _persist_arming_state_locked()


def clear_weapon_arming(name: str, *, target_state: str = 'Safe', armed: bool | None = None) -> None:
    if not name:
        return
    lock = globals().get('STATE_LOCK')
    if lock is None:
        return
    with lock:
        ARMING_PENDING.pop(name, None)
        rec = ARMING_STATE.setdefault(name, {})
        rec['arming_until'] = 0.0
        if target_state:
            rec['state'] = str(target_state)
        if armed is None:
            armed = str(rec.get('state', '')).lower() == 'armed'
        rec['armed'] = bool(armed)
        rec['cooldown_until'] = float(rec.get('cooldown_until') or 0.0)
        _persist_arming_state_locked()


def pending_arming_snapshot(now: float | None = None) -> Dict[str, int]:
    now = float(now if now is not None else time.time())
    lock = globals().get('STATE_LOCK')
    if lock is None:
        return {}
    with lock:
        snapshot = dict(ARMING_PENDING)
    out: Dict[str, int] = {}
    prune: list[str] = []
    for nm, until in snapshot.items():
        if until <= now:
            prune.append(nm)
            continue
        out[nm] = max(0, int(round(until - now)))
    if prune:
        with lock:
            for nm in prune:
                ARMING_PENDING.pop(nm, None)
    return out


def pending_arming_left(name: str, now: float | None = None) -> int:
    snap = pending_arming_snapshot(now)
    try:
        return int(snap.get(name, 0))
    except Exception:
        return 0


def weapon_arm_delay(name: str) -> float:
    try:
        return float(core.weapon_arm_delay(name))
    except Exception:
        try:
            return float(getattr(core, 'ARM_DELAY_DEFAULT', 5.0))
        except Exception:
            return 5.0


def weapon_cooldown(name: str) -> float:
    try:
        return float(core.weapon_cooldown(name))
    except Exception:
        return 2.0

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
                    cell = None
                    if wx is not None and wy is not None:
                        try:
                            cell = world_to_cell(float(wx), float(wy))
                        except Exception:
                            cell = None
                    record_event('radar.contact.spawn', {
                        'id': cid,
                        'name': name,
                        'class_name': cls or (data or {}).get('allegiance') or '',
                        'allegiance': (data or {}).get('allegiance'),
                        'range_nm': rng,
                        'speed': speed,
                        'cell': cell
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

if 'RADAR' not in globals() or RADAR is None:
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
        try:
            response_schema.embed_schema_version(payload, "status")
        except Exception:
            logging.exception("status schema embed failed")
        schema_spec = None
        try:
            schema_spec = response_schema.get_schema("status")
        except Exception:
            schema_spec = None
        build_ms = int((time.time() - build_start) * 1000)
        try:
            STATUS_POLL_MONITOR.record_success(build_ms)
        except Exception:
            pass
        try:
            diag = payload.setdefault('diag', {}) if isinstance(payload, dict) else {}
            if isinstance(diag, dict):
                diag.setdefault('build_ms', build_ms)
                try:
                    diag.setdefault('status_poll', STATUS_POLL_MONITOR.snapshot())
                except Exception:
                    pass
                try:
                    diag.setdefault('watchdog', watchdog.snapshot())
                except Exception:
                    pass
                if schema_spec is not None:
                    client_schema = request.headers.get('X-Stations-Schema', '').strip()
                    if client_schema and client_schema != schema_spec.version:
                        diag.setdefault('schema', {})['client_version'] = client_schema
                        diag['schema']['server_version'] = schema_spec.version
        except Exception:
            pass
        if schema_spec is not None:
            try:
                errors = response_schema.validate("status", payload) or []
                if errors:
                    logging.warning("/api/status schema validation issues: %s", "; ".join(errors))
            except Exception:
                logging.exception("/api/status schema validation failure")
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
        response = jsonify(payload)
        if schema_spec is not None:
            try:
                response.headers['X-Schema-Version'] = schema_spec.version
            except Exception:
                pass
        return response
    except Exception as e:
        logging.exception("/api/status error: %s", e)
        try:
            STATUS_POLL_MONITOR.record_failure(str(e))
        except Exception:
            pass
        diag_snapshot: Dict[str, Any] = {}
        try:
            diag_snapshot = STATUS_POLL_MONITOR.snapshot()
        except Exception:
            diag_snapshot = {}
        payload = {"ok": False, "error": str(e)}
        if diag_snapshot:
            payload['diag'] = {'status_poll': diag_snapshot}
        try:
            response_schema.embed_schema_version(payload, "status")
        except Exception:
            pass
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


@app.route("/sys/radar", methods=["GET", "POST"])
def sys_radar_view() -> str:
    from projects.falklandV2.subsystems.status import build as build_status

    # TRMNL plugins may POST to fetch content; treat POST like GET.
    if request.method == "POST":
        logging.info("POST /sys/radar received; returning snapshot for external client")

    payload = build_status()
    context = build_radar_view(payload)
    return render_template("sys_radar.html", **context)


@app.route("/sys/radar/docs", methods=["GET", "POST"])
def sys_radar_docs() -> str:
    """Minimal helper page for TRMNL plugin management callbacks."""
    if request.method == "POST":
        logging.info("POST /sys/radar/docs received; returning status for external client")
    info = {
        "ok": True,
        "message": "Hermes radar feed is online.",
        "endpoints": {
            "snapshot": "/sys/radar",
            "install": "/trmnl/install",
        },
        "generated": datetime.now(timezone.utc).isoformat(),
    }
    return jsonify(info)


def _render_trmnl_snapshot() -> Path:
    from projects.falklandV2.subsystems.status import build as build_status

    payload = build_status()
    context = build_radar_view(payload)
    output = REPO_ROOT / "tmp" / "trmnl" / "radar_snapshot.png"
    return render_radar_png(context, output)


@app.get("/trmnl/radar/latest.png")
def trmnl_radar_image():
    """Serve the latest radar snapshot as a PNG for TRMNL devices."""
    try:
        path = _render_trmnl_snapshot()
        response = send_file(path, mimetype="image/png", max_age=0, conditional=False)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    except Exception as exc:
        logging.exception("Failed to render TRMNL radar image: %s", exc)
        return jsonify({"ok": False, "error": "render_failed"}), 500


@app.get("/trmnl/radar/redirect")
def trmnl_radar_redirect():
    """Return a Redirect-plugin payload pointing to the radar snapshot."""
    try:
        path = _render_trmnl_snapshot()
        image_url = url_for("trmnl_radar_image", _external=True)
        filename = f"radar-{int(path.stat().st_mtime)}"
        payload = {
            "filename": filename,
            "url": image_url,
            "refresh_rate": 60,
        }
        return jsonify(payload)
    except Exception as exc:
        logging.exception("Failed to build TRMNL redirect payload: %s", exc)
        return jsonify({"ok": False, "error": "render_failed"}), 500


@app.get("/trmnl/testcard.png")
def trmnl_testcard_image():
    """Serve a high-contrast test pattern to validate TRMNL rendering."""
    try:
        path = REPO_ROOT / "tmp" / "trmnl" / "testcard.png"
        render_test_pattern_png(path)
        response = send_file(path, mimetype="image/png", max_age=0, conditional=False)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    except Exception as exc:
        logging.exception("Failed to render TRMNL testcard image: %s", exc)
        return jsonify({"ok": False, "error": "render_failed"}), 500


@app.get("/trmnl/install")
def trmnl_install() -> str:
    """Lightweight install handshake for TRMNL BYOD plugins."""
    callback = request.args.get("installation_callback_url")
    code = request.args.get("code")
    if callback:
        try:
            return redirect(callback, code=302)
        except Exception:
            pass
    body = [
        "<html>",
        "<body style='font-family: sans-serif; background:#0b121a; color:#e2edf9; padding:2rem;'>",
        "<h1>Hermes Radar</h1>",
        "<p>Installation handshake completed.</p>",
        "<p>If you reached this page in a browser you can close it now.</p>",
    ]
    if code:
        body.append(f"<p>Install code: <code>{code}</code></p>")
    body.append("</body></html>")
    return "\n".join(body), 200, {"Content-Type": "text/html; charset=utf-8"}


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
        'SEADART': 'WPN_SEADART_ARMING',
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
    'radar.scan.start': lambda ctx: 'RDR_SCAN_START',
    'radar.scan.complete': lambda ctx: 'RDR_SCAN_COMPLETE',
    'cap.launch': lambda ctx: 'SHAR2_TAKING_OFF',
    'cap.intercept.launch': lambda ctx: 'SHAR2_TAKING_OFF',
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
    'nav.set.course.ack': lambda ctx: 'NAV_COURSE_SET',
    'nav.set.speed.ack': lambda ctx: 'NAV_SPEED_SET',
    'nav.hermes.close_in.request': lambda ctx: 'NAV_HERMES_IN',
    'nav.hermes.stand_off.request': lambda ctx: 'NAV_HERMES_OUT',
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
    if event_id == 'radar.contact.spawn':
        combo = _compose_contact_spawn_audio(info, event_ctx or {})
        if combo:
            return dict(combo)
    if info:
        return dict(info)
    return None


def is_permission_request_radio(role: str | None, text: str | None) -> bool:
    """Detect pilot permission-request radio chatter for conditional suppression."""
    role_norm = str(role or '').strip().lower()
    if role_norm != 'pilot':
        return False
    msg = str(text or '').lower()
    return 'permission to engage' in msg or 'request permission' in msg


def suppress_permission_request_audio(window_s: float = 4.0) -> None:
    """Prevent stale permission requests from playing once clearance is granted."""
    try:
        deadline = time.time() + max(0.5, float(window_s))
    except Exception:
        deadline = time.time() + 4.0
    flags = _ensure_audio_flags()
    prev = 0.0
    try:
        prev = float(flags.get('suppress_permission_request_until', 0.0) or 0.0)
    except Exception:
        prev = 0.0
    flags['suppress_permission_request_until'] = max(prev, deadline)
    try:
        with STATE_LOCK:
            if isinstance(RADIO_QUEUE, list) and RADIO_QUEUE:
                RADIO_QUEUE[:] = [
                    entry for entry in RADIO_QUEUE
                    if not is_permission_request_radio(entry.get('role'), entry.get('text'))
                ]
            current = AUDIO_STATE.get('radio')
            if is_permission_request_radio((current or {}).get('role'), (current or {}).get('text')):
                AUDIO_STATE['radio'] = None
                try:
                    RADIO_STATE['busy_until'] = min(time.time(), float(RADIO_STATE.get('busy_until', 0.0) or 0.0))
                except Exception:
                    pass
    except Exception:
        pass


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
    if str(event_id) == 'cap.permission.authorized':
        suppress_permission_request_audio()
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
            playlist_val = audio_info.get('playlist')
            if isinstance(playlist_val, (list, tuple)):
                seq = [str(p) for p in playlist_val if isinstance(p, str) and p]
                if seq:
                    entry['playlist'] = seq
                    if 'file' not in entry:
                        entry['file'] = seq[0]
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


def schedule_radio_tts(role: str, text: str) -> None:
    role_key = str(role or "").strip()
    message = str(text or "").strip()
    if not message:
        return
    key = (role_key, message)
    with _TTS_LOCK:
        if key in _TTS_IN_FLIGHT:
            return
        _TTS_IN_FLIGHT.add(key)

    def _worker() -> None:
        try:
            path = core._tts_synthesize(message, role_key)
            if not path:
                return
            try:
                with STATE_LOCK:
                    current = AUDIO_STATE.get('radio')
                    if not isinstance(current, dict):
                        return
                    cur_role = str(current.get('role') or "").strip()
                    cur_text = str(current.get('text') or "").strip()
                    if cur_role != role_key or cur_text != message:
                        return
                    updated = dict(current)
                    updated['file'] = path
                    if not updated.get('dur'):
                        try:
                            words = max(1, len(message.split()))
                            commas = message.count(',') + message.count(';') + message.count('—')
                            pauses = message.count('.') + message.count('!') + message.count('?') + message.count(':')
                            hyphen_pauses = message.count('-')
                            chars = len(message)
                            est = 0.32 * words + 0.18 * commas + 0.16 * pauses + 0.04 * hyphen_pauses + 0.45
                            if chars > 90:
                                est += 0.0025 * (chars - 90)
                            updated['dur'] = float(max(0.95, min(8.5, est)))
                        except Exception:
                            pass
                    AUDIO_STATE['radio'] = updated
            except Exception:
                logging.exception("failed to update radio TTS state", exc_info=True)
        except Exception:
            logging.exception("radio TTS worker failed", exc_info=True)
        finally:
            with _TTS_LOCK:
                _TTS_IN_FLIGHT.discard(key)

    threading.Thread(target=_worker, name=f"radio-tts:{role_key}", daemon=True).start()


def officer_say(role: str, key: str, ctx: Dict[str, Any] | None = None, fallback: str | None = None) -> None:
    """Emit a crew radio line.
    Prefers game event templates for consistency; falls back to crew.json.
    // Invariant guard: consistency suite — align crew messages to event/radio base
    """
    # Map common role+key pairs to canonical event ids
    ROLE_KEY_TO_EVENT = {
        ('Fire Control', 'locked'): 'weapon.target.locked',
        ('Fire Control', 'unlocked'): 'weapon.target.unlocked',
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
    host = os.environ.get("HOST") or os.environ.get("FLASK_RUN_HOST") or "0.0.0.0"
    app.run(host=host, port=PORT, debug=False, threaded=True)
