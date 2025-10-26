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
import threading
import contextlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

try:
    import requests  # used by TTS
except Exception:  # pragma: no cover
    requests = None  # type: ignore

# ---- Logging ----
LOG = logging.getLogger(__name__)

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

# Cache last known-good ENG state to survive transient JSON load errors (eg. concurrent writes)
_ENG_SYS_CACHE: Dict[str, Any] | None = None
_SPAWN_BOOTSTRAP_LOCK = threading.Lock()

# Map engineering system identifiers to UI-facing labels so events and logs
# can present meaningful names even if the persisted state lacks them.
ENG_SYSTEM_LABELS = {
    'Navigation': 'NAV station',
    'Radar': 'RDR station',
    'FireControl_Weapons': 'FCR / Weapons',
    'COMMS': 'COMMS station',
    'Engine_Propulsion': 'Engine / Propulsion',
    'Rudder_Steering': 'Rudder / Steering',
    'Hull': 'Hull',
}


def _runtime_mission_settings(wd) -> Dict[str, Any]:
    runtime = getattr(wd, 'RUNTIME', None)
    if runtime is not None and hasattr(runtime, 'mission_settings'):
        try:
            settings = runtime.mission_settings()
            if isinstance(settings, dict):
                return settings
        except Exception:
            return {}
    return {}


def _mission_hostiles_allowed(wd) -> bool:
    settings = _runtime_mission_settings(wd)
    try:
        return bool(settings.get('hostile_spawns', True))
    except Exception:
        return True


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
    # Spec: Belgrano max lives = 8 (tracked separately)
    if 'belgrano_max_lives' not in obj: obj['belgrano_max_lives'] = 8
    if 'belgrano_lives' not in obj: obj['belgrano_lives'] = obj['belgrano_max_lives']
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
        'belgrano_max_lives': 8,
        'belgrano_lives': 8,
    }
    _save_health(base)


def _clone_eng_state(obj: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(json.dumps(obj))
    except Exception:
        return dict(obj)


def reset_eng_state() -> None:
    base = _eng_defaults_from_validation()
    try:
        _save_json(ENG_SYS_PATH, base)
    except Exception:
        pass
    global _ENG_SYS_CACHE
    _ENG_SYS_CACHE = _clone_eng_state(base)


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
            sys_id = str(s.get('id') or '')
            items.append({
                'id': sys_id,
                'name': ENG_SYSTEM_LABELS.get(sys_id, sys_id or 'System'),
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
    global _ENG_SYS_CACHE
    obj = _load_json(ENG_SYS_PATH, None)
    if isinstance(obj, dict):
        mutated = False
        try:
            systems = obj.get('systems') if isinstance(obj.get('systems'), list) else []
            for sys in systems or []:
                try:
                    sys_id = str(sys.get('id') or '')
                    label = sys.get('name') or sys.get('label')
                    if not label:
                        label = ENG_SYSTEM_LABELS.get(sys_id, sys_id or 'System')
                        sys['name'] = label
                        mutated = True
                    elif 'name' not in sys:
                        sys['name'] = label
                        mutated = True
                except Exception:
                    continue
        except Exception:
            pass
        if mutated:
            try:
                _save_json(ENG_SYS_PATH, obj)
            except Exception:
                pass
        _ENG_SYS_CACHE = _clone_eng_state(obj)
        return obj

    # If the file exists but could not be parsed (eg. concurrent write), reuse last good cache.
    if ENG_SYS_PATH.exists() and _ENG_SYS_CACHE is not None:
        return _clone_eng_state(_ENG_SYS_CACHE)

    # File missing or no cache yet: seed defaults (persist if the file truly is absent).
    obj = _eng_defaults_from_validation()
    if not ENG_SYS_PATH.exists():
        try:
            _save_json(ENG_SYS_PATH, obj)
        except Exception:
            pass
    _ENG_SYS_CACHE = _clone_eng_state(obj)
    return obj


def save_eng_sys(obj: Dict[str, Any]) -> None:
    _save_json(ENG_SYS_PATH, obj)
    global _ENG_SYS_CACHE
    _ENG_SYS_CACHE = _clone_eng_state(obj)


# ---- Engineering repair helpers ----
def _advance_eng_repairs(eng: Dict[str, Any], dt: float, now: float) -> bool:
    """Advance repair timers by dt seconds. Returns True if state mutated."""
    changed = False
    try:
        teams_total = int(eng.get('teams_total', 0) or 0)
    except Exception:
        teams_total = 0

    systems = eng.get('systems', []) if isinstance(eng.get('systems'), list) else []
    for s in systems or []:
        try:
            status = str(s.get('status', 'OK'))
            assigned = bool(s.get('team_assigned'))
            timer = float(s.get('timer_s', 0) or 0.0)
            resp_deadline = float(s.get('response_deadline_ts', 0.0) or 0.0)

            if status == 'Offline':
                if assigned and timer <= 0.0:
                    s['status'] = 'Damaged'
                    s['timer_s'] = 120.0
                    s['last_damaged_ts'] = now
                    s['response_deadline_ts'] = 0.0
                    status = 'Damaged'
                    timer = 120.0
                    changed = True
                elif (not assigned) and resp_deadline and now >= resp_deadline:
                    s['status'] = 'Damaged'
                    s['response_deadline_ts'] = 0.0
                    status = 'Damaged'
                    changed = True

            if assigned and timer > 0.0:
                new_timer = max(0.0, timer - float(dt))
                new_timer = 0.0 if new_timer < 1e-6 else round(new_timer, 3)
                if new_timer != timer:
                    s['timer_s'] = new_timer
                    changed = True
                if new_timer == 0.0:
                    s['status'] = 'OK'
                    s['last_damaged_ts'] = 0.0
                    s['response_deadline_ts'] = 0.0
                    if assigned:
                        s['team_assigned'] = False
                        current_free = int(eng.get('teams_free', 0) or 0)
                        eng['teams_free'] = min(teams_total, current_free + 1)
                    changed = True
            else:
                # Normalise stored timer value
                if timer != float(s.get('timer_s', 0) or 0.0):
                    s['timer_s'] = timer
        except Exception:
            continue

    return changed


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
        '4.5 inch mk.8': 'gun_4_5in',
        '4.5 inch mk.8 gun': 'gun_4_5in',
        '4.5" mk.8': 'gun_4_5in',
        '4.5" mk.8 gun': 'gun_4_5in',
        'sea dart sam': 'seacat',
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


def _record_event_guard(wd, event_id: str, payload: Dict[str, Any], *, context: Dict[str, Any] | None = None) -> None:
    try:
        wd.record_event(event_id, payload)
    except Exception as exc:
        LOG.exception("record_event failed for %s", event_id)
        try:
            record_flight({
                'route': '/event.error',
                'method': 'INT',
                'status': 500,
                'duration_ms': 0,
                'request': {
                    'event': event_id,
                    'payload': payload,
                    'context': context or {},
                },
                'response': {'error': str(exc)},
            })
        except Exception:
            pass


def _enemy_ship_name(name: str | None, fallback: str, target_name: str | None = None) -> str:
    if name:
        return str(name)
    if target_name:
        return str(target_name)
    return fallback


def _enemy_ship_trigger_retreat(wd, contact, state: Dict[str, Any]) -> None:
    try:
        st = wd.ENG.public_state() if hasattr(wd.ENG, 'public_state') else {}
        ox, oy = radar_xy_from_state(st)
    except Exception:
        ox = oy = 0.0
    try:
        dx = float(contact.x) - float(ox)
        dy = float(contact.y) - float(oy)
    except Exception:
        dx = dy = 0.0
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        retreat_heading = (float(getattr(contact, 'course_deg', 0.0)) + 180.0) % 360.0
    else:
        retreat_heading = math.degrees(math.atan2(dx, -dy)) % 360.0
    try:
        contact.course_deg = retreat_heading
        contact.threat = 'low'
    except Exception:
        pass
    meta = getattr(contact, 'meta', {}) or {}
    meta['retreating'] = True
    meta['retreat_heading'] = retreat_heading
    meta['retreat_since'] = time.time()
    contact.meta = meta
    state['fleeing'] = True


def apply_enemy_ship_damage(wd, contact_id: int, *, weapon: str, target_name: str | None = None, target_class: str | None = None) -> tuple[bool, bool]:
    """Apply damage to hostile surface contacts. Returns (handled, sunk)."""
    try:
        cid = int(contact_id)
    except Exception:
        return (False, False)

    contact = next((c for c in getattr(wd.RADAR, 'contacts', []) if int(getattr(c, 'id', -1)) == cid), None)
    if contact is None:
        return (False, False)

    try:
        allegiance = str(getattr(contact, 'allegiance', '')).lower()
    except Exception:
        allegiance = ''
    if allegiance != 'hostile':
        return (False, False)

    klass = str(target_class or '').lower()
    if not klass:
        try:
            meta = getattr(contact, 'meta', {}) or {}
            klass = str((meta.get('cap') or {}).get('class') or meta.get('class') or '').lower()
        except Exception:
            klass = ''
    if klass != 'ship':
        return (False, False)

    weapon_l = str(weapon or '').lower()
    if 'sea dart' in weapon_l or 'seacat' in weapon_l:
        damage = 1.0
    elif 'bomb' in weapon_l:
        damage = 2.0
    else:
        return (False, False)

    state = wd.ENEMY_SURFACE_STATE.setdefault(cid, {
        'name': str(getattr(contact, 'name', f'Ship {cid}')),
        'hp': 4.0,
        'max_hp': 4.0,
        'fleeing': False,
    })

    # Sync with meta surface info if present
    meta = getattr(contact, 'meta', {}) or {}
    surf = meta.get('surface_ship') if isinstance(meta.get('surface_ship'), dict) else {}
    try:
        max_hp = float(surf.get('max_hp', state.get('max_hp', 4.0)))
    except Exception:
        max_hp = float(state.get('max_hp', 4.0))
    if max_hp <= 0:
        max_hp = 4.0
    state['max_hp'] = max_hp
    try:
        current_hp = float(state.get('hp', max_hp))
    except Exception:
        current_hp = max_hp
    current_hp = max(0.0, current_hp - float(damage))
    state['hp'] = current_hp
    surf = {**(surf or {}), 'hp': current_hp, 'max_hp': max_hp}
    meta['surface_ship'] = surf
    contact.meta = meta

    name = _enemy_ship_name(state.get('name'), f'Ship {cid}', target_name)
    sunk = False

    if current_hp <= 0.0:
        sunk = True
        try:
            wd.RADAR.contacts = [c for c in wd.RADAR.contacts if int(getattr(c, 'id', -1)) != cid]
        except Exception:
            pass
        wd.ENEMY_SURFACE_STATE.pop(cid, None)
        try:
            _record_event_guard(wd, 'enemy.surface.sunk', {'id': cid, 'name': name})
        except Exception:
            pass
        try:
            record_flight({
                'route': '/enemy.surface.sunk', 'method': 'INT', 'status': 200, 'duration_ms': 0,
                'request': {'contact_id': cid, 'weapon': weapon},
                'response': {'name': name}
            })
        except Exception:
            pass
        return (True, True)

    # Trigger retreat at 50% or lower once
    if current_hp <= (max_hp / 2.0) and not state.get('fleeing'):
        try:
            _enemy_ship_trigger_retreat(wd, contact, state)
            _record_event_guard(wd, 'enemy.surface.flee', {'id': cid, 'name': name, 'hp': current_hp, 'max_hp': max_hp})
        except Exception:
            pass

    wd.ENEMY_SURFACE_STATE[cid] = state
    return (True, False)
def _record_enemy_attack_event(
    wd,
    attack_kind: str,
    outcome: str,
    payload: Dict[str, Any],
    *,
    context: Dict[str, Any] | None = None,
) -> None:
    """Record generic and attack-type specific events for enemy outcomes."""
    _record_event_guard(wd, f'enemy.attack.{outcome}', dict(payload), context=context)
    try:
        kind = str(attack_kind or '').lower()
    except Exception:
        kind = attack_kind
    if kind == 'bomb':
        bomb_payload = {
            'target': payload.get('target'),
            'name': payload.get('name'),
            'weapon': payload.get('weapon'),
        }
        bomb_payload = {k: v for k, v in bomb_payload.items() if v not in (None, '')}
        _record_event_guard(wd, f'enemy.bomb.{outcome}', bomb_payload, context=context)
    elif kind in ('attack', 'gun'):
        surface_payload = {
            'target': payload.get('target'),
            'name': payload.get('name') or f"Contact #{payload.get('contact_id')}",
            'weapon': payload.get('weapon'),
        }
        surface_payload = {k: v for k, v in surface_payload.items() if v not in (None, '')}
        _record_event_guard(wd, f'enemy.surface.{outcome}', surface_payload, context=context)


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
    "cap_recovery": None,
    "enemy_bomb": None,
    "shots_in_flight": [],
    "intro": None,
}

# Enemy hit throttle to avoid clustered multiple system offlines in a single instant
ENEMY_HIT_GUARD: Dict[str, Any] = {"last_ts": 0.0}


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


def _skip_intro_flag() -> bool:
    try:
        flag = str(os.environ.get('SKIP_INTRO', '0')).strip().lower()
    except Exception:
        flag = '0'
    return flag in {'1', 'true', 'yes', 'on'}


def build_intro_payload(
    sound_file: str = "intro.wav",
    *,
    volume: float = 0.85,
    start_bridge: bool = True,
    on_end_url: str | None = "/audio/intro_complete",
    ts: float | None = None,
) -> Dict[str, Any] | None:
    if _skip_intro_flag():
        return None
    try:
        file_val = str(sound_file).strip() or "intro.wav"
    except Exception:
        file_val = "intro.wav"
    if not file_val.startswith('/'):
        try:
            candidate = (DATA_DIR / 'sounds' / file_val).resolve()
            if not candidate.exists():
                file_val = "intro.wav"
        except Exception:
            file_val = "intro.wav"
    try:
        vol = float(volume)
    except Exception:
        vol = 0.85
    payload: Dict[str, Any] = {
        "file": file_val,
        "vol": max(0.0, min(1.0, vol)),
        "start_bridge": bool(start_bridge),
        "ts": float(ts if ts is not None else time.time()),
    }
    if on_end_url:
        try:
            payload['on_end_url'] = str(on_end_url)
        except Exception:
            payload['on_end_url'] = "/audio/intro_complete"
    return payload


def stamp_intro(
    sound_file: str = "intro.wav",
    *,
    volume: float = 0.85,
    start_bridge: bool = True,
    on_end_url: str | None = "/audio/intro_complete",
    ts: float | None = None,
) -> None:
    payload = build_intro_payload(sound_file, volume=volume, start_bridge=start_bridge, on_end_url=on_end_url, ts=ts)
    try:
        from .. import webdash as wd  # type: ignore
        lock = getattr(wd, 'STATE_LOCK', None)
    except Exception:
        lock = None
    target = payload if payload else None
    if lock:
        try:
            with lock:
                AUDIO_STATE['intro'] = target
        except Exception:
            AUDIO_STATE['intro'] = target
    else:
        AUDIO_STATE['intro'] = target


def _stamp_cap_audio(slot: str, sound_file: str, volume: float, fade_s: float, *, fade_in_ms: int | float | None = None, on_end_url: str | None = None) -> None:
    try:
        from .. import webdash as wd  # type: ignore
        with wd.STATE_LOCK:
            rec = {"file": str(sound_file), "vol": float(max(0.0, min(1.0, volume))), "fade_s": float(max(0.0, fade_s)), "ts": time.time()}
            try:
                if fade_in_ms is not None:
                    rec['fade_in_ms'] = int(max(0, float(fade_in_ms)))
            except Exception:
                pass
            try:
                if on_end_url:
                    rec['on_end_url'] = str(on_end_url)
            except Exception:
                pass
            AUDIO_STATE[slot] = rec
    except Exception:
        pass


def stamp_cap_launch(sound_file: str = "SHAR.wav", volume: float = 0.10, fade_s: float = 2.0, *, fade_in_ms: int | float | None = None, on_end_url: str | None = None) -> None:
    _stamp_cap_audio('cap_launch', sound_file, volume, fade_s, fade_in_ms=fade_in_ms, on_end_url=on_end_url)


def stamp_cap_recovery(sound_file: str = "SHAR_landing.wav", volume: float = 0.12, fade_s: float = 2.0, *, fade_in_ms: int | float | None = None, on_end_url: str | None = None) -> None:
    selected = str(sound_file)
    try:
        candidate = Path(selected)
        if not candidate.is_absolute():
            data_sound = (DATA_DIR / 'sounds' / candidate).resolve()
            if not data_sound.exists():
                selected = 'SHAR.wav'
    except Exception:
        selected = str(sound_file)
    _stamp_cap_audio('cap_recovery', selected, volume, fade_s, fade_in_ms=fade_in_ms, on_end_url=on_end_url)


# ---- Grid conversion (canonical AA00 on 40×40; captain sub-board 30×30) ----
WORLD_N = 40
try:
    from projects.falklandV2.core.engine import BOARD_N as _BOARD_N  # type: ignore
except Exception:  # pragma: no cover - fallback when engine is unavailable
    _BOARD_N = 26
BOARD_N = int(_BOARD_N)
from projects.falklandV2.grid.coords import parse_coord as _parse_label, format_coord as _fmt_label, center_subboard
from projects.falklandV2.grid.mapping import world_to_label as _world_to_label, label_to_world as _label_to_world
from projects.falklandV2.engine_adapter import (
    world_to_cell as adapter_world_to_cell,
    cell_to_world as adapter_cell_to_world,
    ship_cell_from_state as adapter_ship_cell_from_state,
    radar_xy_from_state as adapter_radar_xy_from_state,
)

# Centered 30×30 sub-board inside 40×40 (AF05 .. BI34)
(_SUB_TL_C, _SUB_TL_R), (_SUB_BR_C, _SUB_BR_R) = center_subboard(40, 40, 30, 30)


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def cell_for_world(row: float, col: float) -> str:
    # world y=row, x=col
    return _world_to_label(float(col), float(row), world_n=float(WORLD_N))


def ship_cell_from_state(state: Dict[str, Any]) -> str:
    return adapter_ship_cell_from_state(state)


def radar_xy_from_state(state: Dict[str, Any]) -> tuple[float, float]:
    return adapter_radar_xy_from_state(state)


def cell_to_world(cell: str) -> tuple[float, float]:
    return adapter_cell_to_world(cell)


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

ARM_DELAY_DEFAULT = 5.0


def weapon_arm_delay(name: str) -> float:
    """Return the arming delay (seconds) for a given weapon, defaults to spec."""
    try:
        rec = WEAP_MAP.get(str(name))
        if isinstance(rec, dict):
            delay = rec.get('arm_delay_s')
            if delay is not None:
                return float(delay)
    except Exception:
        pass
    return ARM_DELAY_DEFAULT


def weapon_cooldown(name: str) -> float:
    """Return the cooldown duration (seconds) for a weapon according to class/spec."""
    try:
        rec = WEAP_MAP.get(str(name))
        cls = str((rec or {}).get('class', 'Other')).lower()
        if isinstance(rec, dict) and rec.get('cooldown_s') is not None:
            return float(rec['cooldown_s'])
    except Exception:
        cls = 'other'
    if cls == 'missile':
        return 8.0
    if cls == 'sam':
        return 6.0
    if cls == 'decoy':
        return 5.0
    if cls == 'gun':
        return 2.0
    return 2.0


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
    # Respect explicit zeros from state; overlay normalized values onto defaults
    merged = dict(base)
    for k, v in normalized.items():
        try:
            merged[k] = max(0, int(v))
        except Exception:
            continue
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
                cooldown = float(v.get('cooldown_until', 0) or 0)
                if armed:
                    normalized[nm] = 'Armed'
                elif until > now:
                    normalized[nm] = 'Arming'
                elif until > 0 and until <= now:
                    normalized[nm] = 'Armed'; v['armed'] = True; v['arming_until'] = 0; dirty = True
                else:
                    normalized[nm] = 'Safe'
                # Clamp stale cooldowns (e.g., persisted far-future timestamps) once the weapon is armed
                if armed and cooldown > 0:
                    if cooldown <= now:
                        v['cooldown_until'] = 0.0; dirty = True
                    elif cooldown - now > 600:
                        # Cooldowns longer than 10 minutes are considered stale; reset
                        v['cooldown_until'] = 0.0; dirty = True
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
    existing = _load_json(ARMING_PATH, {})
    if not isinstance(existing, dict):
        existing = {}
    now = time.time()

    def _coerce_state(val: Any) -> str:
        s = str(val or '').strip()
        if not s:
            return 'Safe'
        s_low = s.lower()
        if s_low.startswith('armed'):
            return 'Armed'
        if s_low.startswith('arming'):
            return 'Arming'
        return 'Safe'

    for name, state in (d or {}).items():
        nm = str(name)
        state_norm = _coerce_state(state)
        container = existing
        if 'weapons' in existing and isinstance(existing.get('weapons'), dict):
            container = existing['weapons']
        rec = container.get(nm) if isinstance(container, dict) else None
        if not isinstance(rec, dict):
            rec = {'armed': False, 'arming_until': 0.0, 'cooldown_until': 0.0}
        cooldown = rec.get('cooldown_until', 0.0)
        if state_norm == 'Armed':
            rec['armed'] = True
            rec['arming_until'] = 0.0
        elif state_norm == 'Arming':
            rec['armed'] = False
            existing_until = float(rec.get('arming_until', 0.0) or 0.0)
            if existing_until > now:
                rec['arming_until'] = existing_until
            else:
                rec['arming_until'] = now + weapon_arm_delay(nm)
        else:
            rec['armed'] = False
            rec['arming_until'] = 0.0
            cooldown = 0.0
        rec['cooldown_until'] = float(cooldown or 0.0)
        rec['state'] = state_norm
        if isinstance(container, dict):
            container[nm] = rec
        else:
            existing[nm] = rec
    _save_json(ARMING_PATH, existing)


def _primary_class(primary: Dict[str,Any] | None) -> str | None:
    if not primary or not isinstance(primary, dict):
        return None
    name = str(primary.get('name',''))
    return TARGET_CLASS_BY_NAME.get(name)


def compute_in_range(weapon_name: str, primary: Dict[str,Any] | None) -> bool:
    if not primary: return False
    w = WEAP_MAP.get(weapon_name)
    if not w: return False
    rng = None
    try:
        rng = float(primary.get('range_nm'))
    except Exception:
        alt = primary.get('Range') if isinstance(primary, dict) else None
        if alt is None:
            alt = primary.get('range') if isinstance(primary, dict) else None
        if alt is not None:
            try:
                rng = float(alt)
            except Exception:
                rng = None
    if rng is None:
        # Unknown distance; treat as within envelope so downstream gating can decide.
        return True
    klass = _primary_class(primary)
    supports = [str(x) for x in (w.get('supports') or [])]
    if supports:
        if klass:
            if klass not in supports:
                return False
        else:
            # Unknown class: allow range gating to decide so UI/system stay permissive.
            klass = None
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
        # Parse own cell into 0-based master indices
        try:
            col_i, row_i = _parse_label(cell)
        except Exception:
            col_i, row_i = (0, 0)
        for e in escorts[:3]:
            try:
                dx, dy = (e.get('offset_cells') or [0,0])
                # Clamp within 30×30 centered sub-board bounds
                nc = int(clamp(col_i + int(dx), _SUB_TL_C, _SUB_BR_C))
                nr = int(clamp(row_i + int(dy), _SUB_TL_R, _SUB_BR_R))
                ec = _fmt_label(nc, nr)
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
    "pilot.bombsaway": {"role":"Pilot","intent":"Bomb release","hint":"Bombs away on {target}!"},
    "pilot.target_hit": {"role":"Pilot","intent":"Bomb impact","hint":"Target {target} hit!"},
    "pilot.target_miss": {"role":"Pilot","intent":"Bomb impact","hint":"Negative impact, {target} still standing."},
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
            wd.record_officer(str(r), txt, event_id=str(event_id), event_ctx=ctx or {})
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
    # Normalize any accidental leading labels like "text:" before TTS
    try:
        import re
        m = re.match(r"^(?:\s*(?:text|txt)\s*[:,-]?\s+)(.*)$", txt, flags=re.IGNORECASE)
        if m and m.group(1):
            before = txt
            txt = m.group(1).strip().strip('"\'\u201c\u201d')
            try:
                record_flight({'route': '/tts.normalized', 'method': 'INT', 'status': 200, 'duration_ms': 0,
                               'request': {'role': role}, 'response': {'before': before[:160], 'after': txt[:160]}})
            except Exception:
                pass
    except Exception:
        pass
    voice_spec = _crew_voice(role).strip()
    provider_env = os.environ.get('TTS_PROVIDER', '').strip().lower()
    provider_default = provider_env or 'openai'
    forced_piper = (provider_env == 'piper')
    forced_openai = (provider_env == 'openai')
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
    # Force OpenAI globally if requested, regardless of crew.json
    if forced_openai and provider != 'openai':
        provider = 'openai'
        # Map Piper-style voices to OpenAI default when forcing
        if not voice_id or voice_id.lower().endswith('.onnx') or '-' in voice_id:
            voice_id = os.environ.get('OPENAI_TTS_VOICE', 'alloy')
    def _hash_name(ext: str) -> tuple[str, Path]:
        h = hashlib.sha1(f"{provider}|{voice_id}|{txt}".encode('utf-8')).hexdigest()[:20]
        fname = f"{h}.{ext}"; return fname, (TTS_DIR / fname)
    # Log TTS input for diagnostics
    try:
        record_flight({
            'route': '/tts.input',
            'method': 'INT',
            'status': 200,
            'duration_ms': 0,
            'request': {'role': role, 'provider': provider, 'voice': voice_id},
            'response': {'text': txt[:500]},
        })
    except Exception:
        pass

    import shutil, subprocess

    def _macos_try(voice_hint: str) -> str | None:  # pragma: no cover
        try:
            if not shutil.which('say'):
                return None
            voice_name = voice_hint or os.environ.get('TTS_MACOS_VOICE', 'Daniel')
            fname, aiff = _hash_name('aiff')
            m4a = TTS_DIR / (fname[:-5] + 'm4a')
            if m4a.exists():
                return f"/data/tts/{m4a.name}"
            if not aiff.exists():
                subprocess.run(["say", "-v", voice_name, "-o", str(aiff), txt], check=True, timeout=20)
            try:
                if shutil.which('afconvert'):
                    subprocess.run(["afconvert", str(aiff), str(m4a), "-f", "mp4f", "-d", "aac"], check=True, timeout=20)
                    return f"/data/tts/{m4a.name}"
            except Exception:
                logging.warning("macOS afconvert failed; falling back to AIFF", exc_info=True)
            return f"/data/tts/{aiff.name}"
        except Exception as e:
            logging.warning("macOS TTS error: %s", e)
            return None

    if provider == 'macos':
        return _macos_try(voice_id or 'Daniel')
    if provider == 'piper':  # pragma: no cover
        try:
            piper_bin = os.environ.get('TTS_PIPER_BIN', 'piper')
            if not shutil.which(piper_bin):
                logging.error("Piper binary not found: %s (set TTS_PIPER_BIN)", piper_bin)
                if forced_piper:
                    return None
            else:
                model_dir = Path(os.environ.get('TTS_PIPER_MODEL_DIR', str(VOICES_DIR)))
                model_path = Path(voice_id)
                if not model_path.exists():
                    model_path = model_dir / (voice_id + ('' if voice_id.endswith('.onnx') else '.onnx'))
                if not model_path.exists():
                    logging.error("Piper model not found: %s (set TTS_PIPER_MODEL_DIR or TTS_PIPER_DEFAULT_MODEL)", model_path)
                    if forced_piper:
                        return None
                else:
                    fname_wav, wav = _hash_name('wav')
                    if not wav.exists():
                        cmd = [piper_bin, "--model", str(model_path), "--output_file", str(wav), "--text", txt]
                        ls = os.environ.get('TTS_PIPER_LENGTH'); ns = os.environ.get('TTS_PIPER_NOISE'); nw = os.environ.get('TTS_PIPER_NOISEW')
                        if ls: cmd += ["--length_scale", str(ls)]
                        if ns: cmd += ["--noise_scale", str(ns)]
                        if nw: cmd += ["--noise_w", str(nw)]
                        # Suppress noisy stderr from piper; capture to log on failure
                        try:
                            subprocess.run(cmd, check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                        except subprocess.CalledProcessError as e:
                            logging.error("Piper failed: %s", e.stderr.strip() if e.stderr else e)
                            if forced_piper:
                                return None
                            # Fall through to OpenAI/macOS fallback
                        except Exception as e:
                            logging.error("Piper exec error: %s", e)
                            if forced_piper:
                                return None
                    if wav.exists():
                        m4a = TTS_DIR / (fname_wav[:-3] + 'm4a')
                        try:
                            subprocess.run(["afconvert", str(wav), str(m4a), "-f", "mp4f", "-d", "aac"], check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            try:
                                record_flight({'route': '/tts.output', 'method': 'INT', 'status': 200, 'duration_ms': 0,
                                               'request': {'role': role, 'provider': 'piper', 'voice': voice_id},
                                               'response': {'file': f"/data/tts/{m4a.name}", 'ext': 'm4a'}})
                            except Exception:
                                pass
                            return f"/data/tts/{m4a.name}"
                        except Exception:
                            mp3 = TTS_DIR / (fname_wav[:-3] + 'mp3')
                            try:
                                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), str(mp3)], check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                try:
                                    record_flight({'route': '/tts.output', 'method': 'INT', 'status': 200, 'duration_ms': 0,
                                                   'request': {'role': role, 'provider': 'piper', 'voice': voice_id},
                                                   'response': {'file': f"/data/tts/{mp3.name}", 'ext': 'mp3'}})
                                except Exception:
                                    pass
                                return f"/data/tts/{mp3.name}"
                            except Exception:
                                try:
                                    record_flight({'route': '/tts.output', 'method': 'INT', 'status': 200, 'duration_ms': 0,
                                                   'request': {'role': role, 'provider': 'piper', 'voice': voice_id},
                                                   'response': {'file': f"/data/tts/{wav.name}", 'ext': 'wav'}})
                                except Exception:
                                    pass
                                return f"/data/tts/{wav.name}"
        except Exception as e:
            logging.error("Piper TTS error: %s", e)
            if forced_piper:
                return None
        # If we reach here and not forced, allow fallback below
    # Default: OpenAI TTS
    if forced_piper:
        # Do not silently fall back when Piper is forced
        logging.error("TTS_PROVIDER=piper set; skipping OpenAI fallback for role=%s", role)
        return None
    key = os.environ.get('OPENAI_API_KEY')
    if not key or requests is None:
        fallback = _macos_try(voice_id or 'Daniel')
        if fallback:
            return fallback
        return None
    model = os.environ.get('OPENAI_TTS_MODEL', 'gpt-4o-mini-tts')
    voice = voice_id or os.environ.get('OPENAI_TTS_VOICE', 'alloy')
    h = hashlib.sha1(f"{model}|{voice}|{txt}".encode('utf-8')).hexdigest()[:20]
    fname = f"{h}.mp3"; fpath = TTS_DIR / fname
    if fpath.exists():
        try:
            record_flight({'route': '/tts.output', 'method': 'INT', 'status': 200, 'duration_ms': 0,
                           'request': {'role': role, 'provider': 'openai', 'voice': voice},
                           'response': {'file': f"/data/tts/{fname}", 'ext': 'mp3', 'cached': True}})
        except Exception:
            pass
        return f"/data/tts/{fname}"
    url = "https://api.openai.com/v1/audio/speech"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"model": model, "input": txt, "voice": voice, "format": "mp3"}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code == 200:
            fpath.write_bytes(r.content)
            try:
                record_flight({'route': '/tts.output', 'method': 'INT', 'status': 200, 'duration_ms': 0,
                               'request': {'role': role, 'provider': 'openai', 'voice': voice},
                               'response': {'file': f"/data/tts/{fname}", 'ext': 'mp3', 'cached': False}})
            except Exception:
                pass
            return f"/data/tts/{fname}"
        else:
            logging.warning("OpenAI TTS failed %s: %s", r.status_code, r.text[:200])
            fallback = _macos_try(voice_id or 'Daniel')
            if fallback:
                return fallback
            return None
    except Exception as e:
        logging.warning("OpenAI TTS error: %s", e)
        fallback = _macos_try(voice_id or 'Daniel')
        if fallback:
            return fallback
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
def _ship_origin_xy(wd) -> tuple[float, float]:
    def _finite_pair(x: float, y: float) -> bool:
        try:
            return math.isfinite(float(x)) and math.isfinite(float(y))
        except Exception:
            return False

    try:
        if hasattr(wd, "ENG") and hasattr(wd.ENG, "public_state"):
            st = wd.ENG.public_state() or {}
        else:
            st = {}
    except Exception:
        st = {}
    if st:
        try:
            ox, oy = radar_xy_from_state(st)
            if _finite_pair(ox, oy) and (abs(ox) >= 1e-3 or abs(oy) >= 1e-3):
                return float(ox), float(oy)
        except Exception:
            pass

    eng_obj = getattr(wd, "ENG", None)
    if eng_obj is not None and hasattr(eng_obj, "_ship_xy"):
        try:
            sx, sy = eng_obj._ship_xy()
            if _finite_pair(sx, sy) and (abs(sx) >= 1e-3 or abs(sy) >= 1e-3):
                return float(sx), float(sy)
        except Exception:
            pass

    runtime = getattr(wd, "RUNTIME", None)
    if runtime is not None:
        cache = getattr(runtime, "_engine_state_cache", None)
        if isinstance(cache, dict):
            ship_state = cache.get("ship", {})
            if isinstance(ship_state, dict):
                pos = ship_state.get("pos", {}) if isinstance(ship_state, dict) else {}
                try:
                    ox = float(pos.get("x"))
                    oy = float(pos.get("y"))
                    if _finite_pair(ox, oy) and (abs(ox) >= 1e-3 or abs(oy) >= 1e-3):
                        return ox, oy
                except Exception:
                    pass
        repo = getattr(runtime, "state_repo", None)
        if repo is not None:
            try:
                data = repo.load_json(repo.state_dir / "falklands_state.json", {})
            except Exception:
                data = {}
            if isinstance(data, dict):
                for candidate in (data.get("ship"), data.get("ship_position")):
                    if isinstance(candidate, dict):
                        col = candidate.get("col") or candidate.get("col_f")
                        row = candidate.get("row") or candidate.get("row_f")
                        if col is not None and row is not None:
                            st_fallback = {"ship": {"col": col, "row": row}}
                            try:
                                ox, oy = radar_xy_from_state(st_fallback)
                                if _finite_pair(ox, oy) and (abs(ox) >= 1e-3 or abs(oy) >= 1e-3):
                                    return float(ox), float(oy)
                            except Exception:
                                pass

    mid = float(WORLD_N) / 2.0
    return mid, mid


def spawn_initial_friendlies(wd) -> None:
    own_x, own_y = _ship_origin_xy(wd)
    try:
        with _SPAWN_BOOTSTRAP_LOCK:
            radar = getattr(wd, 'RADAR', None)
            if radar is None:
                return
            radar_lock = getattr(radar, '_lock', None)
            lock_ctx = radar_lock if hasattr(radar_lock, "__enter__") else contextlib.nullcontext()
            with lock_ctx:
                if getattr(radar, 'contacts', None):
                    return
                friend_bearings = (45.0, 315.0)
                allow_hostiles = False

                for b in friend_bearings:
                    try:
                        r = random.uniform(6.0, 12.0)
                        radar.force_spawn(own_x, own_y, 'Friendly', bearing_deg=b, range_nm=r)
                    except Exception:
                        continue

                if allow_hostiles:
                    hostile_bearings = (135.0, 225.0)
                    for b in hostile_bearings:
                        try:
                            r = random.uniform(10.0, 18.0)
                            radar.force_spawn(own_x, own_y, 'Hostile', bearing_deg=b, range_nm=r)
                        except Exception:
                            continue
    except Exception:
        pass


def spawn_hostile_by_name(wd, own_x: float, own_y: float, *, name: str, range_nm: float, bearing_deg: float):
    if not _mission_hostiles_allowed(wd):
        return None
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
        cap_obj = getattr(wd, 'CAP', None)
        cfg = getattr(cap_obj, 'cfg', {}) if cap_obj is not None else {}
        try:
            pair_size = int((cfg.get('default_pair_size') or 2) or 2)
        except Exception:
            pair_size = 2
        if pair_size <= 0:
            pair_size = 2
        try:
            aim9_cfg = ((cfg.get('weapons') or {}).get('aim9') or {})
        except Exception:
            aim9_cfg = {}
        try:
            missiles_per_pair = int(aim9_cfg.get('missiles_total', 4) or 0)
        except Exception:
            missiles_per_pair = 0
        if missiles_per_pair <= 0:
            missiles_per_pair = 4 if pair_size <= 2 else pair_size * 2
        missiles_per_airframe = float(missiles_per_pair) / float(pair_size) if pair_size > 0 else float(missiles_per_pair)
        try:
            airframes_available = int(getattr(cap_obj, 'airframe_pool_total', 0) or 0) if cap_obj is not None else 0
        except Exception:
            airframes_available = 0
        sidewinders_pool = max(0.0, missiles_per_airframe * max(0, airframes_available))
        sidewinders_committed = 0.0
        sidewinders_inventory = None
        try:
            inv = aim9_cfg.get('inventory_total')
            if inv is not None:
                sidewinders_inventory = int(inv)
        except Exception:
            sidewinders_inventory = None
        try:
            st = wd.ENG.public_state() if hasattr(wd.ENG, 'public_state') else {}
            own_x, own_y = wd.get_own_xy(st)
        except Exception:
            own_x, own_y = (0.0, 0.0)
        now = time.time()
        tasks: list[Dict[str, Any]] = []
        meta_all = getattr(wd, 'CAP_META', {}) if hasattr(wd, 'CAP_META') else {}
        contacts = list(getattr(wd.RADAR, 'contacts', []) or [])
        contact_by_id: Dict[int, Any] = {}
        contacts_by_cell: Dict[str, list[Any]] = {}
        for c in contacts:
            try:
                cid = int(getattr(c, 'id', -1))
                if cid >= 0:
                    contact_by_id[cid] = c
                cell_label = cell_for_world(float(getattr(c, 'y', 0.0)), float(getattr(c, 'x', 0.0)))
                contacts_by_cell.setdefault(cell_label, []).append(c)
            except Exception:
                continue

        def _as_xy(val: Any) -> tuple[float, float] | None:
            if isinstance(val, (list, tuple)) and len(val) == 2:
                try:
                    return (float(val[0]), float(val[1]))
                except Exception:
                    return None
            return None

        def _cell_to_xy(cell_label: str) -> tuple[float, float] | None:
            if not cell_label:
                return None
            try:
                return cell_to_world(cell_label)
            except Exception:
                return None

        def _xy_to_cell(x_val: float, y_val: float) -> str:
            try:
                return cell_for_world(float(y_val), float(x_val))
            except Exception:
                return '—'

        def _progress(now_val: float, start: Any, end: Any) -> float | None:
            try:
                s = float(start)
                e = float(end)
                if e <= s:
                    return 1.0
                return max(0.0, min(1.0, (now_val - s) / (e - s)))
            except Exception:
                return None

        allowed_statuses = {"queued", "airborne", "onstation", "rtb", "recovering"}
        for m in missions:
            try:
                mid = int(m.get('id'))
                status = str(m.get('status') or '')
                status_lc = status.lower()
                if status_lc not in allowed_statuses:
                    continue
                loadout_value = m.get('loadout')
                loadout_lower = str(loadout_value or '').lower()
                meta_rec = meta_all.get(mid) if isinstance(meta_all, dict) else None
                origin_cell = str(m.get('origin_cell') or (meta_rec or {}).get('origin_cell') or '')
                target_cell_raw = str((meta_rec or {}).get('target_cell') or m.get('target_cell') or '')
                target_id = (meta_rec or {}).get('target_id')
                target_name = str((meta_rec or {}).get('target_name') or '')
                target_contact = None
                if target_id:
                    try:
                        target_contact = contact_by_id.get(int(target_id))
                    except Exception:
                        target_contact = None
                if target_contact is None:
                    try:
                        eng = m.get('last_engagement') or {}
                        eng_tid = eng.get('target_id')
                        if eng_tid is not None:
                            target_contact = contact_by_id.get(int(eng_tid))
                            if target_id is None:
                                target_id = eng_tid
                    except Exception:
                        pass
                if target_contact is None and target_cell_raw:
                    candidates = contacts_by_cell.get(target_cell_raw)
                    if candidates:
                        target_contact = candidates[0]

                tx = ty = None
                target_cell = target_cell_raw
                if target_contact is not None:
                    try:
                        tx = float(getattr(target_contact, 'x', own_x))
                        ty = float(getattr(target_contact, 'y', own_y))
                    except Exception:
                        tx = ty = None
                    try:
                        target_cell = cell_for_world(float(getattr(target_contact, 'y', 0.0)), float(getattr(target_contact, 'x', 0.0)))
                    except Exception:
                        pass
                    if not target_name:
                        target_name = str(getattr(target_contact, 'name', '') or '')
                elif target_cell_raw:
                    try:
                        tx, ty = cell_to_world(target_cell_raw)
                    except Exception:
                        tx = ty = None

                rng = None
                if tx is not None and ty is not None:
                    # Distance from Hermes to the target/contact
                    dx_ht = float(tx) - float(own_x)
                    dy_ht = float(ty) - float(own_y)
                    rng = math.hypot(dx_ht, dy_ht)

                aircraft_range_nm = None
                origin_xy = _as_xy(m.get('origin_xy')) or _as_xy((meta_rec or {}).get('origin_xy'))
                if not origin_xy and origin_cell:
                    origin_xy = _cell_to_xy(origin_cell)
                if target_cell and target_cell != '—':
                    target_xy_from_cell = _cell_to_xy(target_cell)
                else:
                    target_xy_from_cell = None
                target_xy = None
                if target_contact is not None:
                    try:
                        target_xy = (float(getattr(target_contact, 'x', own_x)), float(getattr(target_contact, 'y', own_y)))
                    except Exception:
                        target_xy = None
                if target_xy is None:
                    target_xy = target_xy_from_cell

                if status_lc in ('queued', 'recovering', 'complete'):
                    aircraft_range_nm = 0.0 if target_xy is not None else None
                elif status_lc in ('airborne', 'onstation', 'cap'):
                    try:
                        pos_xy = _as_xy(m.get('position_xy'))
                        if pos_xy is None:
                            pos_xy = _as_xy((meta_rec or {}).get('cur_xy'))
                        if pos_xy is None and pos_cell:
                            pos_xy = _cell_to_xy(pos_cell)
                        if pos_xy is None:
                            est_cell = _estimated_pos_cell()
                            if est_cell and est_cell != '—':
                                pos_xy = _cell_to_xy(est_cell)
                    except Exception:
                        pos_xy = None
                    if pos_xy is None and origin_xy and target_xy:
                        try:
                            ts_local = (m.get('timestamps') or {})
                            start_xy = _as_xy(ts_local.get('vector_start_xy')) or origin_xy
                            start_ts = ts_local.get('airborne', ts_local.get('launch', ts_local.get('created')))
                            if ts_local.get('vector_start_time') is not None:
                                start_ts = ts_local.get('vector_start_time')
                            eta_on = ts_local.get('eta_onstation')
                            prog = _progress(now, start_ts, eta_on) if eta_on is not None else None
                            if prog is None:
                                prog = 0.0
                            px = start_xy[0] + (target_xy[0] - start_xy[0]) * prog
                            py = start_xy[1] + (target_xy[1] - start_xy[1]) * prog
                            pos_xy = (px, py)
                        except Exception:
                            pos_xy = None
                    if pos_xy is not None and target_xy is not None:
                        aircraft_range_nm = math.hypot(float(target_xy[0]) - float(pos_xy[0]), float(target_xy[1]) - float(pos_xy[1]))
                elif status_lc == 'rtb':
                    if origin_xy is not None:
                        try:
                            ts_local = (m.get('timestamps') or {})
                            start_ts = ts_local.get('rtb', ts_local.get('etd_rtb'))
                            eta_rec = ts_local.get('eta_recovery')
                            prog = _progress(now, start_ts, eta_rec) if eta_rec is not None else None
                            if prog is None:
                                prog = 0.0
                            mid_xy = target_xy if target_xy is not None else target_xy_from_cell or origin_xy
                            px = mid_xy[0] + (origin_xy[0] - mid_xy[0]) * prog
                            py = mid_xy[1] + (origin_xy[1] - mid_xy[1]) * prog
                            aircraft_range_nm = math.hypot(float(origin_xy[0]) - float(px), float(origin_xy[1]) - float(py))
                            target_xy = mid_xy
                        except Exception:
                            aircraft_range_nm = None

                display_range_nm = rng
                if aircraft_range_nm is not None:
                    display_range_nm = aircraft_range_nm

                if loadout_lower == 'aim9' and status_lc in ('queued', 'airborne', 'onstation', 'rtb', 'recovering'):
                    try:
                        missiles_left_val = float(m.get('missiles_left') or 0)
                    except Exception:
                        missiles_left_val = 0.0
                    if missiles_left_val > 0:
                        sidewinders_committed += missiles_left_val

                # Predictive helpers relative to CAP station center
                fox2_eta_s = None
                roe_eta_s = None
                pk_now = None
                feasibility = None
                recommendation = None
                # Resolve target kinematics if available
                try:
                    course_deg = float(getattr(target_contact, 'course_deg', getattr(target_contact, 'course', 0.0))) if target_contact is not None else None
                except Exception:
                    course_deg = None
                try:
                    speed_kts = float(getattr(target_contact, 'speed_kts', getattr(target_contact, 'speed', 0.0))) if target_contact is not None else None
                except Exception:
                    speed_kts = None
                try:
                    mx, my = cell_to_world(str(m.get('target_cell') or target_cell)) if (m.get('target_cell') or target_cell) else (own_x, own_y)
                except Exception:
                    mx, my = (own_x, own_y)
                if (tx is not None and ty is not None and course_deg is not None and speed_kts is not None):
                    dxs, dys = float(mx) - float(tx), float(my) - float(ty)
                    dist_center = math.hypot(dxs, dys)
                    rad = math.radians(float(course_deg) % 360.0)
                    vx = math.sin(rad) * (float(speed_kts) / 3600.0)
                    vy = -math.cos(rad) * (float(speed_kts) / 3600.0)
                    if dist_center > 1e-6:
                        ux, uy = dxs / dist_center, dys / dist_center
                        closure = vx * ux + vy * uy
                    else:
                        closure = 0.0
                    def _eta(R: float):
                        if dist_center <= R:
                            return 0.0
                        if closure <= 1e-9:
                            return None
                        return max(0.0, (dist_center - R) / closure)
                    roe_eta_s = _eta(15.0)
                    fox2_eta_s = _eta(5.0)
                    try:
                        if hasattr(wd.CAP, '_pk_for_range') and dist_center is not None:
                            pk_now = float(wd.CAP._pk_for_range(float(dist_center)))
                    except Exception:
                        pk_now = None
                    # Feasibility vs time-to-ship arrival
                    dxh, dyh = float(own_x) - float(tx), float(own_y) - float(ty)
                    dist_ship = math.hypot(dxh, dyh)
                    if dist_ship > 1e-6:
                        uxh, uyh = dxh / dist_ship, dyh / dist_ship
                        closure_ship = vx * uxh + vy * uyh
                        tti_ship = None if closure_ship <= 1e-9 else max(0.0, (dist_ship - 3.0) / max(closure_ship, 1e-9))
                    else:
                        tti_ship = 0.0
                    if fox2_eta_s is None:
                        feasibility = 'not closing' if closure <= 1e-9 else 'unknown'
                    elif tti_ship is None:
                        feasibility = 'not closing'
                    else:
                        cushion = float(tti_ship) - float(fox2_eta_s)
                        feasibility = 'good' if cushion > 10 else ('fair' if -10 <= cushion <= 10 else 'poor')
                    try:
                        if fox2_eta_s is not None and (m.get('permission', {}).get('authorized') or False):
                            recommendation = f"CAP FOX2 in {int(round(fox2_eta_s))}s" + (f" (Pk {pk_now:.2f})" if pk_now is not None else "")
                        elif roe_eta_s is not None and m.get('permission', {}).get('required', True):
                            recommendation = f"CAP ask in {int(round(roe_eta_s))}s"
                    except Exception:
                        recommendation = None
                ts = (m.get('timestamps') or {})
                tot_s = None
                try:
                    eta_on = ts.get('eta_onstation')
                    if isinstance(eta_on, (int, float)) and status_lc in ('queued','airborne'):
                        tot_s = max(0, int(eta_on - now))
                except Exception:
                    tot_s = None
                tos_s = None
                try:
                    etd_rtb = ts.get('etd_rtb')
                    if isinstance(etd_rtb, (int, float)) and status_lc == 'onstation':
                        tos_s = max(0, int(etd_rtb - now))
                except Exception:
                    tos_s = None
                vect = bool(ts.get('vector', False))
                target_cell_disp = target_cell or '—'
                pos_cell = origin_cell or (meta_rec or {}).get('origin_cell') or ''
                def _estimated_pos_cell() -> str:
                    follow_mode = str(m.get('follow') or '').strip().lower()
                    if follow_mode == 'hermes':
                        return target_cell_disp if target_cell_disp != '—' else (origin_cell or '—')
                    origin_xy = _as_xy(m.get('origin_xy')) or _as_xy((meta_rec or {}).get('origin_xy'))
                    if not origin_xy and origin_cell:
                        origin_xy = _cell_to_xy(origin_cell)
                    target_xy = None
                    if target_cell:
                        target_xy = _cell_to_xy(target_cell)

                    if status_lc in ('onstation', 'cap'):
                        return target_cell_disp if target_cell_disp != '—' else (origin_cell or '—')

                    if status_lc == 'queued':
                        return origin_cell or (target_cell_disp if target_cell_disp != '—' else '—')

                    if status_lc == 'airborne':
                        ts_local = (m.get('timestamps') or {})
                        start_xy = _as_xy(ts_local.get('vector_start_xy'))
                        if start_xy is None:
                            start_xy = origin_xy
                        if start_xy and target_xy:
                            launch_ts = ts_local.get('airborne', ts_local.get('launch', ts_local.get('created')))
                            if ts_local.get('vector_start_time') is not None:
                                launch_ts = ts_local.get('vector_start_time')
                            eta_on = ts_local.get('eta_onstation')
                            prog = _progress(now, launch_ts, eta_on) if eta_on is not None else None
                            if prog is None:
                                prog = 0.0
                            x = start_xy[0] + (target_xy[0] - start_xy[0]) * prog
                            y = start_xy[1] + (target_xy[1] - start_xy[1]) * prog
                            return _xy_to_cell(x, y)

                    if status_lc == 'rtb' and origin_xy and target_xy:
                        ts_local = (m.get('timestamps') or {})
                        start = ts_local.get('rtb', ts_local.get('etd_rtb'))
                        eta_rec = ts_local.get('eta_recovery')
                        prog = _progress(now, start, eta_rec) if eta_rec is not None else None
                        if prog is None:
                            prog = 0.0
                        x = target_xy[0] + (origin_xy[0] - target_xy[0]) * prog
                        y = target_xy[1] + (origin_xy[1] - target_xy[1]) * prog
                        return _xy_to_cell(x, y)

                    if status_lc in ('recovering', 'complete'):
                        return origin_cell or (target_cell_disp if target_cell_disp != '—' else '—')

                    if target_xy:
                        return _xy_to_cell(*target_xy)
                    if origin_xy:
                        return _xy_to_cell(*origin_xy)
                    if target_cell_disp != '—':
                        return target_cell_disp
                    if origin_cell:
                        return origin_cell
                    return '—'

                pos_cell = _estimated_pos_cell()
                follow_mode = str(m.get('follow') or '').strip().lower()
                if follow_mode == 'hermes':
                    target_label = 'Hermes (Defend)'
                elif target_name:
                    target_label = f"{target_name} @ {target_cell_disp}" if target_cell_disp != '—' else target_name
                else:
                    target_label = target_cell_disp
                tasks.append({
                    "n": mid,
                    "loadout": loadout_value,
                    "follow": m.get('follow'),
                    "cur_cell": pos_cell or '—',
                    "origin_cell": origin_cell or (meta_rec or {}).get('origin_cell') or '',
                    "target_cell": target_cell_disp,
                    "target_name": target_name,
                    "target_label": target_label,
                    "target_id": target_id,
                    "range_nm": (round(float(display_range_nm), 1) if isinstance(display_range_nm, (int, float)) and display_range_nm is not None else None),
                    "status": status,
                    "tot_s": tot_s,
                    "tos_s": tos_s,
                    "vector": vect,
                    "engaged": bool(m.get('last_engagement')),
                    "permission": m.get('permission') or {},
                    "missiles_left": m.get('missiles_left'),
                    "roe_eta_s": (None if roe_eta_s is None else float(roe_eta_s)),
                    "fox2_eta_s": (None if fox2_eta_s is None else float(fox2_eta_s)),
                    "pk_now": (None if pk_now is None else round(float(pk_now), 2)),
                    "feasibility": feasibility,
                    "rec": recommendation,
                })
            except Exception:
                continue
        committed = len([t for t in tasks if t.get('status') in ('queued','airborne','onstation','rtb','recovering')])
        committed_pairs = int(committed)
        committed_airframes = int(round(max(0.0, float(committed_pairs) * float(pair_size))))
        total_sidewinders = max(0.0, sidewinders_pool + sidewinders_committed)
        if sidewinders_inventory is not None:
            try:
                total_sidewinders = min(total_sidewinders, float(sidewinders_inventory))
            except Exception:
                pass
        payload = {
            "ready": bool(r.get('available', False)),
            "pairs": int(r.get('ready_pairs', 0) or 0),
            "airframes": int(r.get('airframes', 0) or 0),
            "cooldown_s": int(r.get('cooldown_s', 0) or 0),
            "committed": committed_pairs,
            "tasks": tasks,
            "committed_pairs": committed_pairs,
            "committed_airframes": committed_airframes,
            "sidewinders_pool": int(round(max(0.0, sidewinders_pool))),
            "sidewinders_committed": int(round(max(0.0, sidewinders_committed))),
            "sidewinders": int(round(total_sidewinders)),
            "pair_size": pair_size,
        }
        return payload
    except Exception:
        return {
            "ready": False,
            "pairs": 0,
            "airframes": 0,
            "cooldown_s": 0,
            "committed": 0,
            "tasks": [],
            "committed_pairs": 0,
            "committed_airframes": 0,
            "sidewinders_pool": 0,
            "sidewinders_committed": 0,
            "sidewinders": 0,
            "pair_size": 2,
        }


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
    last_heartbeat = time.time()
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
        # Cull transient missile contacts (spawned on enemy attack) when TTL expires
        try:
            now_ts = time.time()
            kept = []
            for c in getattr(wd.RADAR, 'contacts', []) or []:
                try:
                    meta = getattr(c, 'meta', {}) or {}
                    if str(meta.get('kind','')) == 'missile':
                        ttl = float(meta.get('ttl_ts', 0.0) or 0.0)
                        if ttl and now_ts >= ttl:
                            continue
                except Exception:
                    pass
                kept.append(c)
            wd.RADAR.contacts = kept
        except Exception:
            pass
        # CAP tick + auto-engage if primary in range
        # Keep CAP exceptions local to each sub-step to avoid killing the loop
        if wd.CAP is not None:
            try:
                wd.CAP.tick()
            except Exception:
                pass
            # Dynamic follow: retarget CAP missions flagged to follow Hermes to Hermes' current cell
            try:
                st2 = wd.ENG.public_state() if hasattr(wd.ENG, "public_state") else {}
                ship = (st2 or {}).get('ship', {}) if isinstance(st2, dict) else {}
                try:
                    course_deg2 = float(ship.get('heading', 0.0) or 0.0)
                except Exception:
                    course_deg2 = 0.0
                try:
                    ox2, oy2 = radar_xy_from_state(st2)
                except Exception:
                    ox2, oy2 = (ox, oy)
                try:
                    ship_cell2 = wd.ship_cell_from_state(st2)
                except Exception:
                    ship_cell2 = None
                if ship_cell2:
                    try:
                        sx2, sy2 = cell_to_world(ship_cell2)
                        ox2, oy2 = float(sx2), float(sy2)
                    except Exception:
                        pass
                convoy = getattr(wd, 'CONVOY', None)
                if convoy is not None:
                    try:
                        hx, hy, hermes_cell = convoy.escort_world_cell('hermes', ox2, oy2, course_deg2)
                        hx, hy = float(hx), float(hy)
                    except Exception:
                        hx, hy = ox2, oy2
                        hermes_cell = ship_cell2
                else:
                    hx, hy = ox2, oy2
                    hermes_cell = ship_cell2
                hermes_cell_norm = None
                if hermes_cell:
                    try:
                        hermes_cell_norm = str(hermes_cell).strip().upper()
                    except Exception:
                        hermes_cell_norm = None
                for m in getattr(wd.CAP, 'missions', []) or []:
                    try:
                        if str(getattr(m, 'kind', '')) != 'cap':
                            continue
                        if str(getattr(m, 'follow', '')) != 'hermes':
                            continue
                        # Update station center to Hermes current cell
                        if hermes_cell_norm and hermes_cell_norm != 'AA00':
                            setattr(m, 'target_cell', hermes_cell_norm)
                    except Exception:
                        continue
            except Exception:
                pass
            pid = getattr(wd.RADAR, 'priority_id', None)
            tgt = next((c for c in wd.RADAR.contacts if int(getattr(c,'id',-1))==int(pid)), None) if pid is not None else None
            # Auto-engage based on distance from nearest on-station CAP center
            try:
                if tgt is not None:
                    rng = ((tgt.x-ox)**2 + (tgt.y-oy)**2) ** 0.5
                    try:
                        dmins=[]
                        for m in getattr(wd.CAP, 'missions', []) or []:
                            if str(getattr(m,'status','')) != 'onstation':
                                continue
                            cell = str(getattr(m,'target_cell','') or '')
                            if not cell:
                                continue
                            mx, my = cell_to_world(cell)
                            dmins.append(((tgt.x - mx)**2 + (tgt.y - my)**2) ** 0.5)
                        if dmins:
                            rng = min(dmins)
                    except Exception:
                        pass
                    wd.CAP.auto_engage(rng, int(pid))
            except Exception:
                pass
            # (Radar already injects CAP flights; no fallback injection here.)
            # ROE ask window near 15 nm of station center
            try:
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
            # Remove targets on confirmed CAP hits (robust fallback)
            try:
                meta_all = getattr(wd, 'CAP_META', {}) if hasattr(wd, 'CAP_META') else {}
                for m in list(getattr(wd.CAP, 'missions', []) or []):
                    eng = getattr(m, 'last_engagement', None)
                    if not isinstance(eng, dict) or not eng.get('hit'):
                        continue
                    tid = eng.get('target_id'); when = eng.get('when')
                    if tid is None:
                        continue
                    mid = int(getattr(m,'id',0)) if hasattr(m,'id') else int((m.get('id') or 0))
                    rec = meta_all.setdefault(mid, {}) if isinstance(meta_all, dict) else {}
                    if rec.get('last_cap_hit_when') == when and rec.get('last_cap_hit_tid') == tid:
                        continue
                    try:
                        wd.RADAR.contacts = [c for c in wd.RADAR.contacts if int(getattr(c,'id',-1)) != int(tid)]
                    except Exception:
                        pass
                    rec['last_cap_hit_when'] = when
                    rec['last_cap_hit_tid'] = tid
                    if isinstance(meta_all, dict):
                        meta_all[mid] = rec
            except Exception:
                pass
        # Resupply (Sea King) completion check and refill
        try:
            st = getattr(wd, 'RESUPPLY', {}) if hasattr(wd, 'RESUPPLY') else {}
            if isinstance(st, dict):
                now_ts = time.time()
                eta = float(st.get('eta_ts', 0.0) or 0.0)
                stage = str(st.get('stage') or '')
                # Arrival moment → play sound, enter landing stage, set fallback completion deadline
                if bool(st.get('active', False)) and eta > 0.0 and now_ts >= eta and stage != 'landing':
                    try:
                        stamp_cap_launch('Seaking.wav', volume=0.20, fade_s=2.0, fade_in_ms=1200, on_end_url='/resupply/complete')
                    except Exception:
                        pass
                    st['stage'] = 'landing'
                    st['ready_announced'] = st.get('ready_announced', False)
                    if not st.get('ready_announced'):
                        st['ready_announced'] = True
                        try:
                            wd.record_event('resupply.ready', {
                                'origin_cell': st.get('origin_cell'),
                                'eta_ts': eta,
                            })
                        except Exception:
                            pass
                    st['complete_after_ts'] = now_ts + 15.0  # fallback deadline
                # Fallback completion if UI didn’t notify after audio end
                if str(st.get('stage') or '') == 'landing':
                    due = float(st.get('complete_after_ts', 0.0) or 0.0)
                    if due > 0.0 and now_ts >= due:
                        try:
                            # Refill ammo to defaults; preserve any higher-than-default values
                            cur = load_ammo(); base = {**WEAP_DEFAULT_AMMO, **_ammo_defaults_from_ship()}
                            out = dict(cur)
                            for k, v in base.items():
                                try:
                                    if int(cur.get(k, 0)) < int(v):
                                        out[k] = int(v)
                                except Exception:
                                    out[k] = int(v)
                            save_ammo(out)
                        except Exception:
                            pass
                        st['active'] = False
                        st['stage'] = 'complete'
                        st['completed_ts'] = now_ts
                        st['eta_ts'] = 0.0
                        st['ready_announced'] = False
                        try:
                            wd.record_event('resupply.complete', {'completed_ts': now_ts})
                        except Exception:
                            pass
                        try:
                            cap_obj = getattr(wd, 'CAP', None)
                            if cap_obj is not None:
                                cfg_weapons = cap_obj.cfg.setdefault('weapons', {})
                                aim9_cfg = cfg_weapons.setdefault('aim9', {})
                                default_pair = int(aim9_cfg.get('missiles_total', 4) or 4)
                                aim9_cfg['inventory_total'] = int(aim9_cfg.get('inventory_total', 40) or 40)
                                for mission in list(getattr(cap_obj, 'missions', []) or []):
                                    try:
                                        if getattr(mission, 'loadout', 'aim9') != 'aim9':
                                            continue
                                        if getattr(mission, 'status', '') in ('airborne', 'onstation'):
                                            continue
                                        mission.missiles_left = getattr(mission, 'missiles_total', default_pair)
                                    except Exception:
                                        continue
                        except Exception:
                            pass
        except Exception:
            pass
            # Cue: Fox Two soon (<=30s to 5 nm ring)
            try:
                if tgt is not None:
                    meta = getattr(wd, 'CAP_META', {}) if hasattr(wd, 'CAP_META') else {}
                    for m in getattr(wd.CAP, 'missions', []) or []:
                        if str(getattr(m,'status','')) != 'onstation':
                            continue
                        if int(getattr(m, 'missiles_left', 0) or 0) <= 0:
                            continue
                        perm = wd.CAP.permission_state(int(getattr(m,'id',0))) if hasattr(wd.CAP,'permission_state') else None
                        if not (perm and bool(perm.get('authorized', False))):
                            continue
                        cell = str(getattr(m,'target_cell','') or '')
                        if not cell:
                            continue
                        mx, my = cell_to_world(cell)
                        dxs, dys = float(mx) - float(tgt.x), float(my) - float(tgt.y)
                        dist_center = (dxs*dxs + dys*dys) ** 0.5
                        crs = float(getattr(tgt,'course_deg', getattr(tgt,'course', 0.0)))
                        spd = float(getattr(tgt,'speed_kts', getattr(tgt,'speed', 0.0)))
                        rad = math.radians(crs % 360.0)
                        vx = math.sin(rad) * (spd/3600.0)
                        vy = -math.cos(rad) * (spd/3600.0)
                        if dist_center > 1e-6:
                            ux, uy = dxs / dist_center, dys / dist_center
                            closure = vx * ux + vy * uy
                        else:
                            closure = 0.0
                        eta_fox2 = None if closure <= 1e-9 or dist_center <= 5.0 else (dist_center - 5.0) / closure
                        if eta_fox2 is not None and eta_fox2 <= 30.0:
                            rec = meta.setdefault(int(getattr(m,'id',0)), {})
                            last_cue = float(rec.get('fox2_cue_ts') or 0.0)
                            if now - last_cue >= 25.0:
                                try:
                                    wd.record_officer('Pilot', 'Fox Two in 30 seconds.')
                                except Exception:
                                    pass
                                rec['fox2_cue_ts'] = now
                                meta[int(getattr(m,'id',0))] = rec
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
                            handled = False
                            sunk = False
                            if outcome == 'hit' and tgt_id is not None:
                                if str(target_class or '').lower() == 'ship':
                                    handled, sunk = apply_enemy_ship_damage(wd, tgt_id, weapon=weapon, target_name=target_name, target_class=target_class)
                                if not handled:
                                    wd.RADAR.contacts = [c for c in wd.RADAR.contacts if int(getattr(c,'id',-1)) != int(tgt_id)]
                        try:
                            ev_id = 'weapon.result.hit' if outcome == 'hit' else ('weapon.result.miss' if outcome == 'miss' else 'weapon.result.no_effect')
                            wd.record_event(ev_id, {
                                'weapon': weapon,
                                'target_id': tgt_id,
                                'target': target_name,
                                'range_nm': rng,
                                'shooter': 'Sheffield'
                            })
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
                    elif kind == 'arming_ready':
                        weapon = str(ev.get('weapon') or '')
                        if weapon:
                            try:
                                raw = wd._load_json(wd.ARMING_PATH, {})
                                if isinstance(raw, dict):
                                    rec = raw.get(weapon)
                                    if not isinstance(rec, dict):
                                        rec = {}
                                    rec['armed'] = True
                                    rec['arming_until'] = 0.0
                                    raw[weapon] = rec
                                    wd._save_json(wd.ARMING_PATH, raw)
                                    try:
                                        current = wd.load_arming() if callable(getattr(wd, 'load_arming', None)) else {}
                                        if isinstance(current, dict):
                                            current[weapon] = 'Armed'
                                            if callable(getattr(wd, 'save_arming', None)):
                                                wd.save_arming(current)
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            try:
                                if hasattr(wd, 'clear_weapon_arming'):
                                    wd.clear_weapon_arming(weapon, target_state='Armed', armed=True)
                            except Exception:
                                pass
                            try:
                                wd.record_event('weapon.reload.complete', {'name': weapon, 'source': 'arming'})
                            except Exception:
                                pass
                    elif kind == 'weapon_reload_ready':
                        weapon = str(ev.get('weapon') or '')
                        if weapon:
                            try:
                                raw = wd._load_json(wd.ARMING_PATH, {})
                                if isinstance(raw, dict):
                                    container = raw.get('weapons') if isinstance(raw.get('weapons'), dict) else raw
                                    if not isinstance(container, dict):
                                        container = raw
                                    rec = container.get(weapon)
                                    if isinstance(rec, dict):
                                        rec['cooldown_until'] = 0.0
                                        rec['state'] = 'Armed'
                                        container[weapon] = rec
                                        wd._save_json(wd.ARMING_PATH, raw)
                            except Exception:
                                pass
                            try:
                                wd.record_event('weapon.reload.complete', {'name': weapon, 'source': 'reload'})
                            except Exception:
                                pass
                finally:
                    try:
                        wd.PENDING_EVENTS.remove(ev)
                    except Exception:
                        pass
        except Exception:
            pass
        # Enemy attack when inside weapon envelope (cooldown per contact)
        try:
            if not _mission_hostiles_allowed(wd):
                return
            hlth = _load_health()
            att = getattr(wd, 'ATTACK_STATE', {})
            st_attack = wd.ENG.public_state() if hasattr(wd.ENG, 'public_state') else {}
            ship_state = st_attack.get('ship') if isinstance(st_attack, dict) else {}
            try:
                ship_heading = float((ship_state or {}).get('heading', 0.0))
            except Exception:
                ship_heading = 0.0
            convoy = getattr(wd, 'CONVOY', None)
            herm_x = herm_y = None
            if convoy is not None:
                try:
                    hx, hy = convoy.escort_world_position('hermes', float(ox), float(oy), ship_heading)
                    herm_x, herm_y = float(hx), float(hy)
                except Exception:
                    herm_x = herm_y = None
            grid_cell_nm = 1.0
            try:
                eng = getattr(wd, 'ENG', None)
                pool = getattr(eng, 'pool', None) if eng is not None else None
                grid = getattr(pool, 'grid', None) if pool is not None else None
                cell_nm = float(getattr(grid, 'cell_nm', 1.0)) if grid is not None else 1.0
                if cell_nm > 0.0:
                    grid_cell_nm = cell_nm
            except Exception:
                grid_cell_nm = 1.0
            for c in list(wd.RADAR.contacts):
                try:
                    if str(getattr(c,'allegiance','')) != 'Hostile':
                        continue
                    meta = getattr(c, 'meta', {}) or {}
                    if meta.get('retreating'):
                        continue
                    dist_ship_raw = ((c.x-ox)**2 + (c.y-oy)**2) ** 0.5
                    dist_herm_raw = None
                    if herm_x is not None and herm_y is not None:
                        dist_herm_raw = ((c.x-herm_x)**2 + (c.y-herm_y)**2) ** 0.5
                    dist_ship_nm = dist_ship_raw * grid_cell_nm
                    dist_herm_nm = (dist_herm_raw * grid_cell_nm) if dist_herm_raw is not None else None

                    # Determine hostile attack kind and envelope
                    meta = getattr(c, 'meta', {}) or {}
                    weapon_name = str(meta.get('primary_weapon') or getattr(c, 'primary_weapon', '')).lower()
                    cap_meta = meta.get('cap', {}) if isinstance(meta.get('cap'), dict) else {}
                    try:
                        env_min = float(cap_meta.get('min_range_nm')) if cap_meta.get('min_range_nm') is not None else None
                    except Exception:
                        env_min = None
                    try:
                        env_max = float(cap_meta.get('max_range_nm')) if cap_meta.get('max_range_nm') is not None else None
                    except Exception:
                        env_max = None
                    if env_min is None or env_max is None:
                        if 'rocket' in weapon_name:
                            env_min, env_max = 0.2, 0.8
                        elif 'missile' in weapon_name:
                            env_min, env_max = 2.0, 15.0
                        elif 'gun' in weapon_name or 'cannon' in weapon_name:
                            env_min, env_max = 0.2, 1.5
                        else:
                            # Default to bomb-like release envelope
                            env_min, env_max = 0.1, 1.0
                    attack_kind = ('rocket' if 'rocket' in weapon_name else
                                   'missile' if 'missile' in weapon_name else
                                   'gun' if ('gun' in weapon_name or 'cannon' in weapon_name) else
                                   'bomb' if 'bomb' in weapon_name else 'attack')

                    target_options = []
                    # Gate by envelope instead of hard 1.0 nm
                    def _in_env(dnm: float) -> bool:
                        try:
                            return (dnm >= float(env_min)) and (dnm <= float(env_max))
                        except Exception:
                            return dnm <= 1.0
                    if _in_env(dist_ship_nm):
                        target_options.append(('Sheffield', dist_ship_nm))
                    if dist_herm_nm is not None and _in_env(dist_herm_nm):
                        target_options.append(('Hermes', dist_herm_nm))
                    if target_options:
                        target_name_choice, target_dist_nm = _rand.choice(target_options)
                        cid = int(getattr(c,'id',-1))
                        entry = att.get(cid)
                        if not isinstance(entry, dict):
                            entry = {}
                        last = float(entry.get('last', 0.0) or 0.0)
                        if now - last >= 15.0:
                            if LOG.isEnabledFor(logging.DEBUG):
                                LOG.debug(
                                    "enemy attack gating: contact=%s target=%s raw_ship=%.3f nm_ship=%.3f raw_herm=%s nm_herm=%s cell_nm=%.3f",
                                    getattr(c, 'id', 'n/a'),
                                    target_name_choice,
                                    dist_ship_raw,
                                    dist_ship_nm,
                                    '—' if dist_herm_raw is None else f"{dist_herm_raw:.3f}",
                                    '—' if dist_herm_nm is None else f"{dist_herm_nm:.3f}",
                                    grid_cell_nm,
                                )
                            try:
                                record_flight({
                                    'route': '/enemy.attack.gate',
                                    'method': 'INT',
                                    'status': 200,
                                    'duration_ms': 0,
                                    'request': {
                                        'contact_id': cid,
                                        'target': target_name_choice,
                                        'dist_ship_raw': round(dist_ship_raw, 3),
                                        'dist_ship_nm': round(dist_ship_nm, 3),
                                        'dist_herm_raw': None if dist_herm_raw is None else round(dist_herm_raw, 3),
                                        'dist_herm_nm': None if dist_herm_nm is None else round(dist_herm_nm, 3),
                                        'cell_nm': grid_cell_nm,
                                        'env_min_nm': env_min,
                                        'env_max_nm': env_max,
                                        'attack_kind': attack_kind,
                                    },
                                    'response': {'in_envelope': True},
                                })
                            except Exception:
                                pass
                            entry['last'] = now
                            ammo_key = None
                            ammo_left = 0
                            attempt_cap = 1
                            try:
                                ammo_key = None
                                ammo_default = 0
                                attempt_cap = 1
                                if 'bomb' in weapon_name:
                                    hit_prob = 0.6
                                    ammo_key = 'bombs_left'
                                    ammo_default = 2
                                    attempt_cap = 2
                                elif 'missile' in weapon_name:
                                    hit_prob = 0.75
                                    ammo_key = 'missiles_left'
                                    ammo_default = 1
                                else:
                                    hit_prob = 0.5
                                    ammo_key = 'guns_left'
                                    ammo_default = 1

                                ammo_left = entry.get(ammo_key) if ammo_key else None
                                if ammo_left is None:
                                    ammo_left = ammo_default
                                ammo_left = int(ammo_left)
                                if ammo_left <= 0:
                                    if ammo_key:
                                        entry[ammo_key] = max(0, ammo_left)
                                    att[cid] = entry
                                    continue
                                attempts = max(1, min(attempt_cap, ammo_left))
                                if ammo_key:
                                    entry[ammo_key] = ammo_left
                            except Exception:
                                hit_prob = 0.5
                                attempts = 1
                                ammo_key = None
                                ammo_left = attempts

                            results: list[dict[str, Any]] = []
                            attempts_used = 0
                            system_offlined = False
                            hermes_hit = False
                            system_name = None
                            weapon_display = str(meta.get('primary_weapon') or getattr(c, 'primary_weapon', '') or '').strip()
                            if not weapon_display:
                                weapon_display = attack_kind.title()
                            attacker_name = str(getattr(c, 'name', '') or '').strip() or 'Hostile'
                            event_context = {'source': 'enemy_attack', 'contact_id': cid}
                            for attempt_idx in range(1, attempts+1):
                                # Announce enemy attack (fire)
                                try:
                                    _record_event_guard(
                                        wd,
                                        'enemy.attack.fire',
                                        {
                                            'contact_id': cid,
                                            'name': attacker_name,
                                            'weapon': weapon_display,
                                            'range_nm': round(target_dist_nm, 2) if target_dist_nm is not None else None,
                                            'attempt': attempt_idx,
                                            'target': target_name_choice,
                                            'attack_kind': attack_kind,
                                        },
                                        context=event_context,
                                    )
                                except Exception:
                                    pass
                                # Spawn a transient hostile missile contact so UI shows incoming even if shooter drops
                                if attack_kind == 'missile':
                                    try:
                                        from ..radar import Contact, WORLD_N  # late import
                                        ttl = now + 20.0
                                        meta = {'kind': 'missile', 'ttl_ts': ttl}
                                        next_id = getattr(wd.RADAR, "_next_id", len(getattr(wd.RADAR,'contacts',[]) or []) + 1)
                                        mc = Contact(id=int(next_id), name='Incoming weapon', allegiance='Hostile', x=float(getattr(c,'x',ox)), y=float(getattr(c,'y',oy)), course_deg=float(getattr(c,'course_deg',0.0)), speed_kts=450.0, threat='high', meta=meta)
                                        try:
                                            wd.RADAR._next_id = int(next_id) + 1  # type: ignore[attr-defined]
                                        except Exception:
                                            pass
                                        wd.RADAR.contacts.append(mc)
                                    except Exception:
                                        pass
                                hit = (_rand.random() < hit_prob)
                                result_entry = {
                                    'attempt': attempt_idx,
                                    'event': 'hit' if hit else 'miss',
                                    'target': target_name_choice,
                                    'weapon': weapon_display,
                                }
                                results.append(result_entry)
                                attempts_used += 1
                                if hit:
                                    if target_name_choice == 'Hermes':
                                        try:
                                            if int(hlth.get('hermes_lives', 1)) > 0:
                                                hlth['hermes_lives'] = max(0, int(hlth.get('hermes_lives', 1)) - 1)
                                                _save_health(hlth)
                                                hermes_hit = True
                                                try:
                                                    if int(hlth.get('hermes_lives', 0)) <= 0 and not wd.AUDIO_FLAGS.get('hermes_out_announced'):
                                                        wd.AUDIO_FLAGS['hermes_out_announced'] = True
                                                        wd.record_event('eng.hermes.outofaction', {
                                                            'weapon': weapon_display,
                                                            'when': now,
                                                        })
                                                except Exception:
                                                    pass
                                        except Exception:
                                            pass
                                    else:
                                        try:
                                            if int(hlth.get('lives', 1)) > 0:
                                                hlth['lives'] = max(0, int(hlth.get('lives', 1)) - 1)
                                                _save_health(hlth)
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
                                                system_name = str(s.get('name') or s.get('label') or ENG_SYSTEM_LABELS.get(str(s.get('id') or ''), s.get('id') or 'System'))
                                                s['name'] = system_name
                                                save_eng_sys(eng)
                                                system_offlined = True
                                                try:
                                                    _record_event_guard(
                                                        wd,
                                                        'eng.system.offline',
                                                        {'system': system_name},
                                                        context={'source': 'enemy_attack', 'contact_id': cid},
                                                    )
                                                except Exception:
                                                    pass
                                                _record_event_guard(
                                                    wd,
                                                    'eng.system.timer',
                                                    {'system': system_name, 'seconds': s.get('timer_s', 0)},
                                                    context={'source': 'enemy_attack', 'contact_id': cid},
                                                )
                                                try:
                                                    record_flight({
                                                        'route': '/enemy/system.offline',
                                                        'method': 'INT',
                                                        'status': 200,
                                                        'duration_ms': 0,
                                                        'request': {
                                            'contact_id': cid,
                                            'contact_name': attacker_name,
                                            'range_nm': round(target_dist_nm, 2) if target_dist_nm is not None else None,
                                            'target': target_name_choice,
                                            'attack_kind': attack_kind,
                                            'weapon': weapon_display,
                                                        },
                                                        'response': {
                                                            'system': system_name,
                                                            'status': 'Offline',
                                                            'reason': 'enemy_hit'
                                                        }
                                                    })
                                                except Exception:
                                                    pass
                                        except Exception:
                                            pass
                                        try:
                                            if int(hlth.get('lives', 0)) <= 0 and not wd.AUDIO_FLAGS.get('abandon_ship_announced'):
                                                wd.AUDIO_FLAGS['abandon_ship_announced'] = True
                                                wd.record_event('eng.abandon_ship', {'when': now})
                                        except Exception:
                                            pass
                                    payload = {
                                        'contact_id': cid,
                                        'name': attacker_name,
                                        'weapon': weapon_display,
                                        'range_nm': round(target_dist_nm, 2) if target_dist_nm is not None else None,
                                        'attempt': attempt_idx,
                                        'target': target_name_choice,
                                        'attack_kind': attack_kind,
                                    }
                                    _record_enemy_attack_event(wd, attack_kind, 'hit', payload, context=event_context)
                                    try:
                                        record_flight({
                                            'route': '/enemy.attack.hit',
                                            'method': 'INT',
                                            'status': 200,
                                            'duration_ms': 0,
                                            'request': {
                                                'contact_id': cid,
                                                'contact_name': attacker_name,
                                                'attempt': attempt_idx,
                                                'target': target_name_choice,
                                                'attack_kind': attack_kind,
                                                'range_nm': round(target_dist_nm, 2) if target_dist_nm is not None else None,
                                                'weapon': weapon_display,
                                            },
                                            'response': {
                                                'result': 'hit',
                                                'system': system_name,
                                                'system_offlined': bool(system_offlined),
                                                'hermes_hit': bool(target_name_choice == 'Hermes' and hermes_hit),
                                            }
                                        })
                                    except Exception:
                                        pass
                                else:
                                    payload = {
                                        'contact_id': cid,
                                        'name': attacker_name,
                                        'weapon': weapon_display,
                                        'range_nm': round(target_dist_nm, 2) if target_dist_nm is not None else None,
                                        'attempt': attempt_idx,
                                        'target': target_name_choice,
                                        'attack_kind': attack_kind,
                                    }
                                    _record_enemy_attack_event(wd, attack_kind, 'miss', payload, context=event_context)
                                    try:
                                        record_flight({
                                            'route': '/enemy.attack.miss',
                                            'method': 'INT',
                                            'status': 200,
                                            'duration_ms': 0,
                                            'request': {
                                                'contact_id': cid,
                                                'contact_name': attacker_name,
                                                'attempt': attempt_idx,
                                                'target': target_name_choice,
                                                'attack_kind': attack_kind,
                                                'range_nm': round(target_dist_nm, 2) if target_dist_nm is not None else None,
                                                'weapon': weapon_display,
                                            },
                                            'response': {
                                                'result': 'miss'
                                            }
                                        })
                                    except Exception:
                                        pass

                            if results:
                                try:
                                    with wd.STATE_LOCK:
                                        wd.AUDIO_STATE['enemy_bomb'] = {'ts': time.time(), 'events': results}
                                except Exception:
                                    pass
                            if ammo_key:
                                try:
                                    current = int(entry.get(ammo_key, ammo_left))
                                except Exception:
                                    current = ammo_left
                                entry[ammo_key] = max(0, current - attempts_used)
                            att[cid] = entry
                            if any(r.get('event') == 'hit' for r in results):
                                # Global throttle: at most one enemy-hit side effect per 1.5s
                                try:
                                    last = float(ENEMY_HIT_GUARD.get('last_ts', 0.0) or 0.0)
                                except Exception:
                                    last = 0.0
                                if now - last < 1.5:
                                    ENEMY_HIT_GUARD['last_ts'] = last
                                    continue
                                ENEMY_HIT_GUARD['last_ts'] = now
                                if target_name_choice == 'Sheffield' and system_offlined:
                                    try:
                                        trigger_alarm('red-alert.wav', message='Sheffield hit! Critical damage reported.', role='Bridge', loop=False)
                                    except Exception:
                                        pass
                                if target_name_choice == 'Hermes' and hermes_hit:
                                    try:
                                        trigger_alarm('red-alert.wav', message='Hermes hit! Critical damage reported.', role='Bridge', loop=False)
                                    except Exception:
                                        pass
                except Exception:
                    continue
            try:
                wd.ATTACK_STATE = att
            except Exception:
                pass
        except Exception:
            pass
        # Tick ENG repairs counting down timers
        try:
            eng = load_eng_sys()
            if _advance_eng_repairs(eng, dt, now):
                save_eng_sys(eng)
        except Exception:
            pass
        try:
            now = time.time()
            if (now - last_heartbeat) >= 10.0:
                last_heartbeat = now
                record_flight({
                    'route': '/engine.heartbeat',
                    'method': 'INT',
                    'status': 200,
                    'duration_ms': 0,
                    'request': {},
                    'response': {'ts': now}
                })
        except Exception:
            pass
        time.sleep(dt)
