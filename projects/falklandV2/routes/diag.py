from __future__ import annotations

import time
import logging
from flask import Blueprint, jsonify
import os
import importlib

bp = Blueprint("diag", __name__)


def _lazy():
    from ..webdash import (
        ENG, RADAR, CAP,
        record_flight, record_officer,
        radar_xy_from_state, get_own_xy, contact_to_ui,
        load_ammo, load_arming, WEAP_CATALOG,
        AMMO_PATH, ARMING_PATH, _save_json,
        AUDIO_STATE, PENDING_EVENTS, RADIO_QUEUE, RADIO_STATE, NAV_STATE,
        SKIRMISH_ACTIVE, DATA_DIR,
    )
    return locals()


def run_selftest() -> dict:
    """Run diagnostics and return payload; also records to flight log.
    Safe to call at startup (no request context required).
    """
    L = _lazy(); t0 = time.time(); route = "/diag/selftest"
    res: dict = {"nav": {}, "radar": {}, "weapons": {}, "cap": {}, "radio": {}}
    ok_all = True

    # NAV
    try:
        st = L['ENG'].public_state() if hasattr(L['ENG'], 'public_state') else None
        hud = L['ENG'].hud_line() if hasattr(L['ENG'], 'hud_line') else None
        ship = (st or {}).get('ship') if isinstance(st, dict) else None
        nav_ok = bool(ship or hud)
        res['nav'] = {"ok": nav_ok, "has_state": bool(ship), "hud": hud}
        ok_all &= nav_ok
    except Exception as e:
        res['nav'] = {"ok": False, "error": str(e)}; ok_all = False

    # RADAR
    try:
        st = L['ENG'].public_state() if hasattr(L['ENG'], 'public_state') else {}
        ox, oy = L['radar_xy_from_state'](st)
        try:
            L['RADAR'].scan(ox, oy)
        except Exception:
            pass
        cnt = len(getattr(L['RADAR'], 'contacts', []) or [])
        res['radar'] = {"ok": True, "contacts": cnt}
    except Exception as e:
        res['radar'] = {"ok": False, "error": str(e)}; ok_all = False

    # Weapons snapshot
    try:
        ammo = L['load_ammo'](); arming = L['load_arming']()
        cats = L['WEAP_CATALOG']
        items = []
        for w in (cats or []):
            nm = w.get('name');
            if not nm: continue
            items.append({
                'name': nm,
                'armed': arming.get(nm, 'Safe'),
                'ammo': ammo.get(nm, 0),
            })
        res['weapons'] = {"ok": bool(items), "items": items[:6]}
        ok_all &= bool(items)
    except Exception as e:
        res['weapons'] = {"ok": False, "error": str(e)}; ok_all = False

    # CAP
    try:
        if L['CAP'] is None:
            res['cap'] = {"ok": False, "error": "unavailable"}; ok_all = False
        else:
            rd = L['CAP'].readiness()
            snap = L['CAP'].snapshot()
            res['cap'] = {"ok": True, "readiness": rd, "committed": len(snap.get('tasks') or [])}
    except Exception as e:
        res['cap'] = {"ok": False, "error": str(e)}; ok_all = False

    # Radio (best-effort)
    try:
        L['record_officer']('Ensign', 'Self-test ping received.')
        res['radio'] = {"ok": True}
    except Exception as e:
        res['radio'] = {"ok": False, "error": str(e)}; ok_all = False

    payload = {"ok": ok_all, "results": res}
    try:
        L['record_flight']({"route": route, "method": "INT", "status": 200,
                          "duration_ms": int((time.time()-t0)*1000),
                          "request": {}, "response": payload})
    except Exception:
        pass
    return payload


@bp.get("/diag/selftest")
def selftest():
    from flask import jsonify as _jsonify
    payload = run_selftest()
    return _jsonify(payload)


@bp.post("/diag/reset")
def reset_runtime():
    """Reset key runtime pieces: engine, radar contacts, CAP, ammo/arming, audio/radio queues.
    Best-effort and resilient; logs a flight record.
    """
    L = _lazy()
    try:
        # 1) Clear ammo/arming to trigger defaults on next load
        try:
            L['_save_json'](L['AMMO_PATH'], {})
            L['_save_json'](L['ARMING_PATH'], {})
        except Exception:
            pass
        # 2) Clear transient queues/state
        try:
            L['AUDIO_STATE'].update({"last_launch": None, "last_result": None, "radio": None, "alarm": None, "cap_launch": None})
        except Exception:
            pass
        try:
            L['PENDING_EVENTS'].clear()
        except Exception:
            pass
        try:
            L['RADIO_QUEUE'].clear(); L['RADIO_STATE']['busy_until'] = 0.0
        except Exception:
            pass
        try:
            L['NAV_STATE'].update({"turn_target": None, "turn_hold_since": 0.0})
        except Exception:
            pass
        try:
            L['SKIRMISH_ACTIVE'].update({"id": None, "started_ts": None})
        except Exception:
            pass
        # 3) Clear radar contacts + priority
        try:
            L['RADAR'].contacts = []  # type: ignore[attr-defined]
            if hasattr(L['RADAR'], 'priority_id'):
                try:
                    if hasattr(L['RADAR'], 'clear_manual_lock'):
                        L['RADAR'].clear_manual_lock()  # type: ignore[attr-defined]
                    else:
                        L['RADAR'].priority_id = None  # type: ignore[attr-defined]
                except Exception:
                    L['RADAR'].priority_id = None  # type: ignore[attr-defined]
        except Exception:
            pass
        # 4) Rebuild Engine and CAP in the webdash module
        try:
            wd = importlib.import_module('projects.falklandV2.webdash')
            wd.RUNTIME.reset_engine_and_cap()
        except Exception:
            pass
        # 5) Log and return
        try:
            L['record_flight']({"route": "/diag/reset", "method": "POST", "status": 200, "duration_ms": 0, "request": {}, "response": {"ok": True}})
        except Exception:
            pass
        return jsonify({"ok": True})
    except Exception as e:
        try:
            L['record_flight']({"route": "/diag/reset", "method": "POST", "status": 500, "duration_ms": 0, "request": {}, "response": {"ok": False, "error": str(e)}})
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/session/start")
def session_start():
    """Start a tagged session; sets KIOSK_SESSION_ID and logs a marker."""
    from flask import request
    L = _lazy()
    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        data = {}
    sid = str(data.get('id') or '' ).strip() or dt_now_tag()
    os.environ['KIOSK_SESSION_ID'] = sid
    try:
        L['record_flight']({"route": "/session.start", "method": request.method, "status": 200, "duration_ms": 0, "request": {"id": sid}, "response": {"ok": True}})
    except Exception:
        pass
    return jsonify({"ok": True, "id": sid})


@bp.post("/session/end")
def session_end():
    """End a tagged session; unsets KIOSK_SESSION_ID and logs a marker."""
    from flask import request
    L = _lazy()
    sid = os.environ.get('KIOSK_SESSION_ID')
    if 'KIOSK_SESSION_ID' in os.environ:
        del os.environ['KIOSK_SESSION_ID']
    try:
        L['record_flight']({"route": "/session.end", "method": request.method, "status": 200, "duration_ms": 0, "request": {"id": sid}, "response": {"ok": True}})
    except Exception:
        pass
    return jsonify({"ok": True, "id": sid})


def dt_now_tag() -> str:
    import datetime as _dt
    return _dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')


@bp.post("/alarm/trigger")
def alarm_trigger():
    """Trigger an alarm sound and optional radio message (moved from webdash)."""
    L = _lazy(); t0 = time.time(); route = "/alarm/trigger"
    try:
        from flask import request
        data = request.get_json(silent=True) or {}
        sound = data.get('sound') or data.get('file') or 'red-alert.wav'
        role = data.get('role') or 'Captain'
        msg = data.get('message') or None
        try:
            from ..webdash import trigger_alarm
            trigger_alarm(str(sound), message=(str(msg) if msg else None), role=str(role), loop=False)
        except Exception:
            pass
        payload = {"ok": True}
        L['record_flight']({"route": route, "method": "POST", "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {"sound": sound, "role": role, "message": msg}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/alarm/trigger error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({"route": route, "method": "POST", "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.post("/alarm/clear")
def alarm_clear():
    """Clear any active alarm (moved from webdash)."""
    L = _lazy(); t0 = time.time(); route = "/alarm/clear"
    try:
        try:
            from ..webdash import clear_alarm
            clear_alarm()
        except Exception:
            pass
        payload = {"ok": True}
        L['record_flight']({"route": route, "method": "POST", "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/alarm/clear error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({"route": route, "method": "POST", "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500
