from __future__ import annotations

import time
from flask import Blueprint, jsonify, request

bp = Blueprint("mission", __name__)


def _lazy():
    from ..webdash import RUNTIME, record_flight
    return {
        'RUNTIME': RUNTIME,
        'record_flight': record_flight,
    }


@bp.get('/mission/status')
def mission_status():
    L = _lazy(); t0 = time.time(); route = '/mission/status'
    snap = L['RUNTIME'].mission_snapshot()
    payload = {"ok": True, "mission": snap}
    try:
        L['record_flight']({
            "route": route,
            "method": "GET",
            "status": 200,
            "duration_ms": int((time.time() - t0) * 1000),
            "request": {},
            "response": payload,
        })
    except Exception:
        pass
    return jsonify(payload)


@bp.post('/mission/decision')
def mission_decision():
    L = _lazy(); t0 = time.time(); route = '/mission/decision'
    data = request.get_json(silent=True) or {}
    decision_id = str(data.get('id') or data.get('decision_id') or '').strip()
    choice = str(data.get('choice') or '').strip()
    if not decision_id or not choice:
        return jsonify({"ok": False, "error": "missing_params"}), 400
    res = L['RUNTIME'].apply_mission_decision(decision_id, choice)
    status = 200 if res.get('ok') else 400
    try:
        L['record_flight']({
            "route": route,
            "method": "POST",
            "status": status,
            "duration_ms": int((time.time() - t0) * 1000),
            "request": {"id": decision_id, "choice": choice},
            "response": res,
        })
    except Exception:
        pass
    return jsonify(res), status


@bp.post('/mission/select')
def mission_select():
    L = _lazy(); t0 = time.time(); route = '/mission/select'
    data = request.get_json(silent=True) or {}
    mission_id = str(data.get('id') or data.get('mission_id') or '').strip()
    if not mission_id:
        return jsonify({"ok": False, "error": "missing_mission"}), 400
    res = L['RUNTIME'].activate_mission(mission_id)
    status = 200 if res.get('ok') else 400
    try:
        L['record_flight']({
            "route": route,
            "method": "POST",
            "status": status,
            "duration_ms": int((time.time() - t0) * 1000),
            "request": {"mission_id": mission_id},
            "response": res,
        })
    except Exception:
        pass
    return jsonify(res), status
