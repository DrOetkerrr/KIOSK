from __future__ import annotations

import time
from typing import Any, Dict
from flask import Blueprint, jsonify, request

bp = Blueprint("eng", __name__)


def _lazy():
    from ..webdash import STATE_LOCK, record_flight, _load_json, _save_json, record_event
    from ..subsystems.webcore import load_eng_sys, save_eng_sys
    from ..subsystems.webcore import HEALTH_PATH, _load_health, _save_health
    return locals()


@bp.get('/eng/systems')
def eng_systems():
    L = _lazy(); t0 = time.time(); route = '/eng/systems'
    st = L['load_eng_sys']()
    L['record_flight']({"route": route, "method": "GET", "status": 200, "duration_ms": int((time.time()-t0)*1000), "request": {}, "response": {"ok": True}})
    return jsonify({"ok": True, **st})


@bp.post('/eng/assign')
def eng_assign():
    L = _lazy(); t0 = time.time(); route = '/eng/assign'
    try:
        data = request.get_json(silent=True) or {}
        sys_id = str(data.get('id') or '').strip()
        if not sys_id:
            return jsonify({"ok": False, "error": "missing id"}), 400
        st = L['load_eng_sys']()
        if int(st.get('teams_free', 0)) <= 0:
            return jsonify({"ok": False, "error": "no_teams"}), 400
        for s in st.get('systems', []):
            if str(s.get('id')) == sys_id:
                if bool(s.get('team_assigned')):
                    return jsonify({"ok": True, **st})
                s['team_assigned'] = True
                st['teams_free'] = max(0, int(st.get('teams_free', 0)) - 1)
                status = str(s.get('status'))
                if status in ('Offline', 'Damaged') and int(s.get('timer_s', 0)) <= 0:
                    s['status'] = 'Damaged'
                    s['timer_s'] = 120
                    s['last_damaged_ts'] = time.time()
                    s['response_deadline_ts'] = 0.0
                    try:
                        L['record_event']('eng.system.timer', {'system': s.get('name','System'), 'seconds': s.get('timer_s', 0)})
                    except Exception:
                        pass
                try:
                    L['record_event']('eng.repair.deployed', {'system': s.get('name','System')})
                except Exception:
                    pass
                break
        L['save_eng_sys'](st)
        L['record_flight']({"route": route, "method": "POST", "status": 200, "duration_ms": int((time.time()-t0)*1000), "request": {"id": sys_id}, "response": {"ok": True}})
        return jsonify({"ok": True, **st})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post('/eng/release')
def eng_release():
    L = _lazy(); t0 = time.time(); route = '/eng/release'
    try:
        data = request.get_json(silent=True) or {}
        sys_id = str(data.get('id') or '').strip()
        if not sys_id:
            return jsonify({"ok": False, "error": "missing id"}), 400
        st = L['load_eng_sys']()
        for s in st.get('systems', []):
            if str(s.get('id')) == sys_id:
                if bool(s.get('team_assigned')):
                    s['team_assigned'] = False
                    st['teams_free'] = min(int(st.get('teams_total', 0) or 0), int(st.get('teams_free', 0)) + 1)
                break
        L['save_eng_sys'](st)
        L['record_flight']({"route": route, "method": "POST", "status": 200, "duration_ms": int((time.time()-t0)*1000), "request": {"id": sys_id}, "response": {"ok": True}})
        return jsonify({"ok": True, **st})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
