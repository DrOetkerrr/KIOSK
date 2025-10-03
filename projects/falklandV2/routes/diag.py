from __future__ import annotations

import time
import logging
import threading
from flask import Blueprint, jsonify, request
from flask import current_app
import os
bp = Blueprint("diag", __name__)


def _lazy():
    from ..webdash import (
        ENG, RADAR, CAP,
        record_flight, record_officer,
        radar_xy_from_state, get_own_xy, contact_to_ui,
        load_ammo, load_arming, WEAP_CATALOG,
        AMMO_PATH, ARMING_PATH, _save_json,
        AUDIO_STATE, PENDING_EVENTS, RADIO_QUEUE, RADIO_STATE, RADIO_HISTORY, NAV_STATE,
        SKIRMISH_ACTIVE, DATA_DIR, RUNTIME, _reset_runtime_globals,
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

    # Radio (best-effort); no audible ping to avoid startup chatter
    try:
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
        req = request.get_json(silent=True) or {}
        clear_tts = bool(req.get('clear_tts', False))
        try:
            L['RUNTIME'].reset_state(clear_tts=clear_tts)
        except Exception:
            pass
        try:
            L['_reset_runtime_globals']()
        except Exception:
            pass
        # Log and return
        try:
            L['record_flight']({"route": "/diag/reset", "method": "POST", "status": 200, "duration_ms": 0,
                               "request": {'clear_tts': clear_tts}, "response": {"ok": True}})
        except Exception:
            pass
        return jsonify({"ok": True, "clear_tts": clear_tts})
    except Exception as e:
        try:
            L['record_flight']({"route": "/diag/reset", "method": "POST", "status": 500, "duration_ms": 0, "request": {}, "response": {"ok": False, "error": str(e)}})
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/diag/routes")
def list_routes():
    try:
        rules = []
        for rule in current_app.url_map.iter_rules():
            try:
                methods = sorted(m for m in (rule.methods or set()) if m not in ("HEAD","OPTIONS"))
            except Exception:
                methods = []
            rules.append({
                'rule': str(rule),
                'endpoint': str(rule.endpoint),
                'methods': methods,
            })
        return jsonify({'ok': True, 'routes': rules})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.post("/diag/quit")
def quit_runtime():
    """Request the hosting process to exit after returning response."""
    L = _lazy()
    try:
        shutdown_func = None
        try:
            shutdown_func = request.environ.get('werkzeug.server.shutdown')
        except Exception:
            shutdown_func = None

        def _shutdown(func: object) -> None:
            time.sleep(0.5)
            try:
                if callable(func):
                    func()
                else:
                    os._exit(0)
            except Exception:
                os._exit(0)

        threading.Thread(target=_shutdown, args=(shutdown_func,), name="shutdown", daemon=True).start()
        try:
            L['record_flight']({
                "route": "/diag/quit",
                "method": "POST",
                "status": 200,
                "duration_ms": 0,
                "request": {},
                "response": {"ok": True}
            })
        except Exception:
            pass
        return jsonify({"ok": True, "exiting": True})
    except Exception as e:
        try:
            L['record_flight']({
                "route": "/diag/quit",
                "method": "POST",
                "status": 500,
                "duration_ms": 0,
                "request": {},
                "response": {"ok": False, "error": str(e)}
            })
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
