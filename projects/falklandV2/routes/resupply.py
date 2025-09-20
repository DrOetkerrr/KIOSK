from __future__ import annotations

"""Sea King resupply routes.

Endpoints
- POST /resupply/launch  → launch Sea King from Hermes, play heli sound, ETA optional
- GET  /resupply/status  → current resupply status
- POST /resupply/cancel  → cancel active resupply
"""

import time
import logging
from flask import Blueprint, jsonify, request

bp = Blueprint("resupply", __name__)


def _lazy():
    # Late import to avoid circular deps; reuse webdash singletons
    from ..webdash import (
        RESUPPLY, record_flight, stamp_cap_launch, record_officer,
        ENG, CONVOY, radar_xy_from_state, world_to_cell, ship_cell_from_state,
    )
    return locals()


@bp.get("/resupply/status")
def resupply_status():
    L = _lazy()
    try:
        state = dict(L['RESUPPLY']) if isinstance(L.get('RESUPPLY'), dict) else {"active": False}
        return jsonify({"ok": True, "resupply": state})
    except Exception as e:
        logging.exception("/resupply/status error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/resupply/launch")
def resupply_launch():
    L = _lazy(); t0 = time.time(); route = "/resupply/launch"
    try:
        body = request.get_json(silent=True) or {}
        try:
            eta_s = int(body.get('eta_s') or 180)
        except Exception:
            eta_s = 180
        state = L['RESUPPLY']
        now = time.time()
        state['active'] = True
        state['started_ts'] = now
        state['eta_ts'] = now + max(5, int(eta_s))
        state['stage'] = 'enroute'
        # Hermes confirmation
        try:
            L['record_officer']('Pilot', 'Resupply on its way.')
        except Exception:
            pass
        # Seed origin_cell so radar can place Sea King contact
        try:
            st = L['ENG'].public_state() if hasattr(L['ENG'], 'public_state') else {}
            own_x, own_y = L['radar_xy_from_state'](st)
            ship = (st or {}).get('ship', {}) if isinstance(st, dict) else {}
            try:
                crs = float(ship.get('heading', 0.0) or 0.0)
            except Exception:
                crs = 0.0
            convoy = L.get('CONVOY')
            if convoy is not None:
                hx, hy, hermes_cell = convoy.escort_world_cell('hermes', own_x, own_y, crs)
            else:
                hermes_cell = L['ship_cell_from_state'](st)
            state['origin_cell'] = hermes_cell
        except Exception:
            pass
        payload = {"ok": True, "resupply": {"active": True, "eta_s": int(state['eta_ts'] - now)}}
        L['record_flight']({"route": route, "method": request.method, "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": body, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/resupply/launch error: %s", e)
        payload = {"ok": False, "error": str(e)}
        _lazy()['record_flight']({"route": route, "method": request.method, "status": 500,
                                 "duration_ms": int((time.time()-t0)*1000),
                                 "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.post("/resupply/cancel")
def resupply_cancel():
    L = _lazy(); t0 = time.time(); route = "/resupply/cancel"
    try:
        state = L['RESUPPLY']
        state['active'] = False
        state['stage'] = 'idle'
        state['eta_ts'] = 0.0
        state['started_ts'] = 0.0
        payload = {"ok": True, "resupply": dict(state)}
        L['record_flight']({"route": route, "method": request.method, "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/resupply/cancel error: %s", e)
        payload = {"ok": False, "error": str(e)}
        _lazy()['record_flight']({"route": route, "method": request.method, "status": 500,
                                 "duration_ms": int((time.time()-t0)*1000),
                                 "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.post("/resupply/complete")
def resupply_complete():
    """Finalize resupply after Sea King audio finishes (client callback)."""
    L = _lazy(); t0 = time.time(); route = "/resupply/complete"
    try:
        state = L['RESUPPLY']
        # Only allow when landing/in-progress
        stg = str(state.get('stage') or '')
        if stg not in ('landing', 'enroute'):
            return jsonify({"ok": False, "error": "not_pending"}), 400
        # Perform refill (duplicate of fallback logic in engine loop)
        try:
            from ..subsystems import webcore as core  # type: ignore
            cur = core.load_ammo(); base = {**core.WEAP_DEFAULT_AMMO, **core._ammo_defaults_from_ship()}
            out = dict(cur)
            for k, v in base.items():
                try:
                    if int(cur.get(k, 0)) < int(v):
                        out[k] = int(v)
                except Exception:
                    out[k] = int(v)
            core.save_ammo(out)
        except Exception:
            pass
        now = time.time()
        state['active'] = False
        state['stage'] = 'complete'
        state['completed_ts'] = now
        state['eta_ts'] = 0.0
        try:
            L['record_officer']('Pilot', 'Sea King resupply complete.')
        except Exception:
            pass
        payload = {"ok": True, "resupply": dict(state)}
        L['record_flight']({"route": route, "method": request.method, "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/resupply/complete error: %s", e)
        payload = {"ok": False, "error": str(e)}
        _lazy()['record_flight']({"route": route, "method": request.method, "status": 500,
                                 "duration_ms": int((time.time()-t0)*1000),
                                 "request": {}, "response": payload})
        return jsonify(payload), 500
