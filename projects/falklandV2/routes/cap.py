from __future__ import annotations

import time
import math
import logging
from flask import Blueprint, jsonify, request

bp = Blueprint("cap", __name__)


def _lazy():
    # Import late from webdash to avoid circular imports
    from ..webdash import (
        CAP, CAP_META, RADAR, ENG, CONVOY,
        voice_emit, officer_say, record_flight, record_event,
        radar_xy_from_state, world_to_cell, cell_to_world,
        stamp_cap_launch, ship_cell_from_state
    )
    return locals()


@bp.get("/cap/roe")
def cap_roe():
    try:
        L = _lazy()
        info = {int(k): {"asked": bool(v.get('asked', False)), "authorized": bool(v.get('authorized', False))} for k, v in L['CAP_META'].items()}
        return jsonify({"ok": True, "missions": info})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/cap/authorize")
def cap_authorize():
    L = _lazy(); t0 = time.time(); route = "/cap/authorize"
    try:
        data = request.get_json(silent=True) or {}
        mid = int(data.get('id', 0))
        auth = bool(data.get('authorize', True))
        if mid <= 0 or mid not in L['CAP_META']:
            payload = {"ok": False, "error": "unknown mission id"}
            L['record_flight']({"route": route, "method": request.method, "status": 400,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": data, "response": payload})
            return jsonify(payload), 400
        L['CAP_META'][mid]['authorized'] = auth
        L['CAP_META'][mid]['asked'] = False
        if auth:
            L['officer_say']('Pilot', 'cleared', {})
        else:
            L['officer_say']('Pilot', 'hold', {})
        payload = {"ok": True, "id": mid, "authorized": auth}
        L['record_flight']({"route": route, "method": request.method, "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": data, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/cap/authorize error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L = _lazy()
        L['record_flight']({"route": route, "method": request.method, "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.get("/cap/readiness")
def cap_readiness():
    try:
        L = _lazy()
        if L['CAP'] is None:
            return jsonify({"ok": False, "error": "CAP unavailable"}), 503
        return jsonify({"ok": True, "readiness": L['CAP'].readiness()})
    except Exception as e:
        logging.exception("/cap/readiness error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/cap/status")
def cap_status():
    try:
        L = _lazy()
        if L['CAP'] is None:
            return jsonify({"ok": False, "error": "CAP unavailable"}), 503
        return jsonify({"ok": True, "cap": L['CAP'].snapshot()})
    except Exception as e:
        logging.exception("/cap/status error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/cap/request")
def cap_request():
    L = _lazy(); t0 = time.time(); route = "/cap/request"
    try:
        if L['CAP'] is None:
            payload = {"ok": False, "error": "CAP unavailable"}
            L['record_flight']({"route": route, "method": request.method, "status": 503,
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
                from .. import webdash as _wd  # late import to read PRIMARY_ID
                pid = getattr(_wd, 'PRIMARY_ID', None)
                tid = int(pid) if pid is not None else None
            except Exception:
                tid = None
        if tid is None:
            tid = L['RADAR'].priority_id
        tgt = next((c for c in L['RADAR'].contacts if int(getattr(c, 'id', -1)) == int(tid)), None) if tid is not None else None
        if tgt is None:
            return jsonify({"ok": False, "error": "no locked/selected target"}), 400
        st = L['ENG'].public_state() if hasattr(L['ENG'], "public_state") else {}
        own_x, own_y = L['radar_xy_from_state'](st)
        ship = (st or {}).get('ship', {}) if isinstance(st, dict) else {}
        try:
            course_deg = float(ship.get('heading', 0.0) or 0.0)
        except Exception:
            course_deg = 0.0
        convoy = L.get('CONVOY')
        if convoy is not None:
            hx, hy, hermes_cell = convoy.escort_world_cell('hermes', own_x, own_y, course_deg)
        else:
            hx, hy = own_x, own_y
            hermes_cell = L['ship_cell_from_state'](st)
        dx = float(getattr(tgt, 'x', 0.0)) - float(hx)
        dy = float(getattr(tgt, 'y', 0.0)) - float(hy)
        rng_nm = (dx*dx + dy*dy) ** 0.5
        try:
            cell = L['world_to_cell'](float(getattr(tgt, 'x', 0.0)), float(getattr(tgt, 'y', 0.0)))
        except Exception:
            cell = "K13"
        # Policy: If an airborne pair can reach the primary before the target is within 5 nm of own ship,
        # vector that pair; otherwise, launch a fresh pair (if available).
        # -- Compute time window until target is 5 nm from ship (closing speed toward ship)
        def _closing_hours_to_5nm(_tgt) -> float:
            try:
                tx = float(getattr(_tgt,'x',0.0)); ty = float(getattr(_tgt,'y',0.0))
                spd = float(getattr(_tgt,'speed_kts',0.0) or getattr(_tgt,'speed',0.0))
                crs = float(getattr(_tgt,'course_deg', getattr(_tgt,'course',0.0)))
                rad = math.radians(crs % 360.0)
                vx, vy = math.sin(rad)*spd, -math.cos(rad)*spd  # kts ~ nm/h
                dx, dy = float(hx) - tx, float(hy) - ty
                rng = (dx*dx + dy*dy) ** 0.5
                if rng <= 5.0:
                    return 0.0
                if rng <= 1e-6:
                    return 0.0
                ux, uy = dx / rng, dy / rng
                closing = vx*ux + vy*uy  # nm/h toward ship
                if closing <= 0.0:
                    return 1e9  # moving away/tangential; effectively plenty of time
                return max(0.0, (rng - 5.0) / closing)
            except Exception:
                return 0.0

        # -- Can any airborne mission reach the target sooner than this window?
        can_vector = False
        chosen_mid = None
        try:
            snap = L['CAP'].snapshot()  # contains missions[] with timestamps
            now = time.time()
            t_window_h = _closing_hours_to_5nm(tgt)
            missions = snap.get('missions') or []
            # Helper: current mission position (linear along origin→old_target)
            def _mission_pos(m) -> tuple[float,float]:
                try:
                    mid = int(m.get('id'))
                    ts = m.get('timestamps') or {}
                    launch = float(ts.get('launch', now))
                    deck = float(getattr(L['CAP'], 'cfg', {}).get('deck_cycle_per_pair_s', 180))
                    outb = float(getattr(L['CAP'], 'cruise_speed_kts', 420.0))
                    outb = float(m.get('outbound_s', m.get('inbound_s', 600)) or 600)
                    prog = 0.0
                    try:
                        prog = max(0.0, min(1.0, (now - (launch + deck)) / max(1.0, outb)))
                    except Exception:
                        prog = 0.0
                    # origin from CAP_META if available; else Hermes current position
                    ox, oy = (hx, hy)
                    try:
                        o_local = m.get('origin_xy')
                        if isinstance(o_local, (list, tuple)) and len(o_local) == 2:
                            ox, oy = float(o_local[0]), float(o_local[1])
                    except Exception:
                        pass
                    try:
                        meta = L['CAP_META'].get(mid) or {}
                        o = meta.get('origin_xy')
                        if isinstance(o, (list, tuple)) and len(o) == 2:
                            ox, oy = float(o[0]), float(o[1])
                    except Exception:
                        pass
                    old_cell = str(m.get('target_cell') or '')
                    txo, tyo = L['cell_to_world'](old_cell) if old_cell else (ox, oy)
                    cx = float(ox) + (float(txo) - float(ox)) * prog
                    cy = float(oy) + (float(tyo) - float(oy)) * prog
                    return (cx, cy)
                except Exception:
                    return (own_x, own_y)

            # Evaluate airborne missions
            for m in missions:
                try:
                    if str(m.get('status','')) != 'airborne':
                        continue
                    cx, cy = _mission_pos(m)
                    dist_nm = ((float(getattr(tgt,'x',0.0)) - cx)**2 + (float(getattr(tgt,'y',0.0)) - cy)**2) ** 0.5
                    spd = float(getattr(L['CAP'], 'cruise_speed_kts', 420.0) or 420.0)
                    eta_h = dist_nm / max(1.0, spd)
                    if eta_h <= t_window_h:
                        can_vector = True
                        chosen_mid = int(m.get('id'))
                        break
                except Exception:
                    continue
        except Exception:
            can_vector = False

        if can_vector and chosen_mid is not None:
            # Retarget chosen airborne mission in-place
            try:
                tx, ty = float(getattr(tgt,'x',0.0)), float(getattr(tgt,'y',0.0))
                # Locate live mission object and update
                for m in getattr(L['CAP'], 'missions', []) or []:
                    if int(getattr(m,'id',-1)) == int(chosen_mid):
                        # estimate current pos again for remaining time
                        snap_m = {'id': chosen_mid, 'target_cell': getattr(m,'target_cell',''),
                                  'timestamps': getattr(m,'ts',{}), 'outbound_s': getattr(m,'outbound_s',600)}
                        cx, cy = _mission_pos(snap_m)
                        dist_nm = ((tx - cx)**2 + (ty - cy)**2) ** 0.5
                        spd = float(getattr(L['CAP'], 'cruise_speed_kts', 420.0) or 420.0)
                        eta_s = int(max(1.0, (dist_nm / max(1.0, spd)) * 3600.0))
                        setattr(m, 'target_cell', cell)
                        try:
                            m.ts['eta_onstation'] = time.time() + eta_s  # type: ignore[attr-defined]
                            m.ts['vector'] = True  # mark retarget for UI snapshot
                        except Exception:
                            pass
                        break
            except Exception:
                pass
            payload = {"ok": True, "message": f"Vectoring airborne pair to {cell}", "mission": {"id": chosen_mid, "target_cell": cell}}
            try:
                L['voice_emit']('pilot.vector', {'cell': cell}, fallback='Vectoring to %s.' % (cell,), role='Pilot')
            except Exception:
                pass
            try:
                L['record_event']('cap.intercept.launch', {
                    'id': tid,
                    'name': getattr(tgt, 'name', ''),
                    'cell': cell,
                    'from': hermes_cell,
                    'range_nm': round(rng_nm, 2)
                })
            except Exception:
                pass
            L['record_flight']({"route": route, "method": request.method, "status": 200,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": {"id": tid, "cell": cell, "range_nm": round(rng_nm, 2), "vector": True}, "response": payload})
            return jsonify(payload)

        # Fallback: launch a fresh pair
        res = L['CAP'].request_cap_to_cell(cell, distance_nm=float(rng_nm), origin_xy=(hx, hy), origin_cell=hermes_cell)
        status = 200 if res.get("ok") else 400
        payload = {"ok": bool(res.get("ok")), "message": res.get("message"), "mission": res.get("mission")}
        if res.get('ok'):
            try:
                L['stamp_cap_launch']()
            except Exception:
                pass
            try:
                # Intercept launch call
                L['voice_emit']('pilot.intercept.launch', {'cell': cell}, fallback='Hermes, intercept bogey, vector to %s.' % (cell,), role='Pilot')
                # Generic CAP launch fallback (kept for compatibility)
                L['voice_emit']('pilot.cap.launch', {'cell': cell}, fallback='Hermes, proceeding to CAP station at %s.' % (cell,), role='Pilot')
            except Exception:
                pass
            try:
                L['record_event']('cap.launch', {'cell': cell, 'from': hermes_cell, 'range_nm': round(rng_nm, 2)})
            except Exception:
                pass
            try:
                mid = int((res.get('mission') or {}).get('id'))
                meta = L['CAP_META'].get(mid) or {}
                meta['origin_xy'] = (hx, hy)
                meta['origin_cell'] = hermes_cell
                meta.setdefault('asked', False)
                meta.setdefault('authorized', False)
                L['CAP_META'][mid] = meta
            except Exception:
                pass
        L['record_flight']({"route": route, "method": request.method, "status": status,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {"id": tid, "cell": cell, "range_nm": round(rng_nm, 2)}, "response": payload})
        return jsonify(payload), status
    except Exception as e:
        logging.exception("/cap/request error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L = _lazy()
        L['record_flight']({"route": route, "method": request.method, "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.post("/cap/launch_to")
def cap_launch_to():
    L = _lazy(); t0 = time.time(); route = "/cap/launch_to"
    try:
        if L['CAP'] is None:
            payload = {"ok": False, "error": "CAP unavailable"}
            L['record_flight']({"route": route, "method": request.method, "status": 503,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": {}, "response": payload})
            return jsonify(payload), 503
        data = request.get_json(silent=True) or {}
        cell = str(data.get("cell") or "").strip().upper()
        if not cell:
            payload = {"ok": False, "error": "missing cell"}
            L['record_flight']({"route": route, "method": request.method, "status": 400,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": data, "response": payload})
            return jsonify(payload), 400
        st = L['ENG'].public_state() if hasattr(L['ENG'], "public_state") else {}
        own_x, own_y = L['radar_xy_from_state'](st)
        ship = (st or {}).get('ship', {}) if isinstance(st, dict) else {}
        try:
            course_deg = float(ship.get('heading', 0.0) or 0.0)
        except Exception:
            course_deg = 0.0
        convoy = L.get('CONVOY')
        if convoy is not None:
            hx, hy, hermes_cell = convoy.escort_world_cell('hermes', own_x, own_y, course_deg)
        else:
            hx, hy = own_x, own_y
            hermes_cell = L['ship_cell_from_state'](st)
        tx, ty = L['cell_to_world'](cell)
        dx, dy = float(tx) - float(hx), float(ty) - float(hy)
        rng_nm = (dx*dx + dy*dy) ** 0.5
        try:
            sm = data.get('station_minutes', None)
            rm = data.get('radius_nm', None)
        except Exception:
            sm = None; rm = None
        if sm is None: sm = 10
        if rm is None: rm = 10
        res = L['CAP'].request_cap_to_cell(cell, distance_nm=float(rng_nm), station_minutes=float(sm), radius_nm=float(rm), origin_xy=(hx, hy), origin_cell=hermes_cell)
        status = 200 if res.get("ok") else 400
        payload = {"ok": bool(res.get("ok")), "message": res.get("message"), "mission": res.get("mission")}
        if res.get('ok'):
            try:
                L['stamp_cap_launch']()
            except Exception:
                pass
            try:
                L['voice_emit']('pilot.cap.launch', {'cell': cell}, fallback='Hermes, proceeding to CAP station at %s.' % (cell,), role='Pilot')
            except Exception:
                pass
            try:
                L['record_event']('cap.launch', {'cell': cell})
            except Exception:
                pass
            try:
                mid = int((res.get('mission') or {}).get('id'))
                meta = L['CAP_META'].get(mid) or {}
                meta['origin_xy'] = (hx, hy)
                meta['origin_cell'] = hermes_cell
                meta.setdefault('asked', False)
                meta.setdefault('authorized', False)
                L['CAP_META'][mid] = meta
            except Exception:
                pass
        L['record_flight']({"route": route, "method": request.method, "status": status,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {"cell": cell, "range_nm": round(rng_nm, 2)}, "response": payload})
        return jsonify(payload), status
    except Exception as e:
        logging.exception("/cap/launch_to error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L = _lazy()
        L['record_flight']({"route": route, "method": request.method, "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500
