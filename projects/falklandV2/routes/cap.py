from __future__ import annotations

import time
import math
import logging
from pathlib import Path
from typing import Any, Dict, Tuple

from flask import Blueprint, jsonify, request

bp = Blueprint("cap", __name__)


def _lazy():
    # Import late from webdash to avoid circular imports
    from ..webdash import (
        CAP, CAP_META, RADAR, ENG, CONVOY, RUNTIME,
        voice_emit, officer_say, record_flight, record_event,
        radar_xy_from_state, world_to_cell, cell_to_world,
        stamp_cap_launch, ship_cell_from_state, TARGET_CLASS_BY_NAME
    )
    return locals()


def _contact_class_for_cap(contact: Any, mapping: Dict[str, Any] | None = None) -> str | None:
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
        if name and isinstance(mapping, dict):
            cls = mapping.get(str(name))
            if cls:
                return str(cls).title()
    except Exception:
        pass
    return None


def _normalize_loadout(value: Any) -> str:
    if not value:
        return ''
    v = str(value).strip().lower()
    if v in ('aim9', 'aim-9', 'sidewinder', 'missile'):
        return 'aim9'
    if v in ('bomb', 'bombs', 'mk82', 'iron'):
        return 'bombs'
    if v == 'auto':
        return ''
    return ''


def _resolve_hermes_origin(L: Dict[str, Any], state: Dict[str, Any] | None) -> Tuple[float, float, str]:
    invalid_cells = {'', 'AA00'}
    st = state if isinstance(state, dict) else {}
    try:
        own_x, own_y = L['radar_xy_from_state'](st)
    except Exception:
        own_x = own_y = 0.0

    ship = st.get('ship', {}) if isinstance(st, dict) else {}

    runtime = L.get('RUNTIME')
    if not ship:
        eng_obj = L.get('ENG')
        if eng_obj is not None and hasattr(eng_obj, '_ship_xy'):
            try:
                sx, sy = eng_obj._ship_xy()
                own_x, own_y = float(sx), float(sy)
                ship = {
                    'col': own_x,
                    'row': own_y,
                    'heading': 0.0,
                    'speed': 0.0,
                }
                if hasattr(eng_obj, '_ship_course_speed'):
                    try:
                        course_deg, spd = eng_obj._ship_course_speed()
                        ship['heading'] = course_deg
                        ship['speed'] = spd
                    except Exception:
                        pass
            except Exception:
                pass

    if (not ship) and runtime is not None:
        cache = getattr(runtime, '_engine_state_cache', None)
        if isinstance(cache, dict):
            cache_ship = cache.get('ship', {})
            pos = cache_ship.get('pos', {}) if isinstance(cache_ship, dict) else {}
            try:
                cx = float(pos.get('x'))
                cy = float(pos.get('y'))
                if math.isfinite(cx) and math.isfinite(cy):
                    own_x, own_y = cx, cy
                    ship = {
                        'col': cx,
                        'row': cy,
                        'heading': cache_ship.get('heading'),
                        'speed': cache_ship.get('speed'),
                    }
            except Exception:
                pass
            if not ship:
                try:
                    data = getattr(runtime, '_engine_state_cache', {}).get('ship_state', {})
                    if isinstance(data, dict):
                        cx = float(data.get('col', data.get('x', 0.0)))
                        cy = float(data.get('row', data.get('y', 0.0)))
                        own_x, own_y = cx, cy
                        ship = {'col': cx, 'row': cy, 'heading': data.get('heading'), 'speed': data.get('speed')}
                except Exception:
                    pass

    if not ship:
        runtime = L.get('RUNTIME')
        if runtime is not None:
            try:
                repo = getattr(runtime, 'state_repo', None)
                if repo is not None:
                    fallback = repo.load_json(repo.state_dir / 'falklands_state.json', {})
                    if isinstance(fallback, dict) and fallback:
                        st = {'ship': fallback}
                        ship = fallback
                        try:
                            own_x, own_y = L['radar_xy_from_state'](st)
                        except Exception:
                            pass
            except Exception:
                pass

    try:
        course_deg = float((ship or {}).get('heading', 0.0) or 0.0)
    except Exception:
        course_deg = 0.0
    try:
        ship_cell_label = L['ship_cell_from_state'](st)
    except Exception:
        ship_cell_label = None

    hx, hy = own_x, own_y
    hermes_cell: str | None = None

    convoy = L.get('CONVOY')
    if convoy is None:
        runtime = L.get('RUNTIME')
        try:
            from projects.falklandV2.subsystems.convoy import Convoy
            data_dir = None
            if runtime is not None:
                data_dir = getattr(runtime, 'data_dir', None)
            if data_dir is None and L.get('CAP') is not None:
                data_dir = getattr(L['CAP'], 'data_path', None)
            if data_dir is None:
                data_dir = Path(__file__).resolve().parents[1] / "data"
            convoy = Convoy.load(Path(data_dir))
        except Exception:
            convoy = None
    if convoy is not None:
        try:
            hx_candidate, hy_candidate, cell_candidate = convoy.escort_world_cell('hermes', own_x, own_y, course_deg)
            cel = str(cell_candidate or '').strip().upper()
            if cel and cel not in invalid_cells:
                hermes_cell = cel
                hx = float(hx_candidate)
                hy = float(hy_candidate)
        except Exception:
            pass

    if hermes_cell is None and ship_cell_label:
        cel = str(ship_cell_label).strip().upper()
        if cel and cel not in invalid_cells:
            hermes_cell = cel
            try:
                hx_cell, hy_cell = L['cell_to_world'](hermes_cell)
                hx, hy = float(hx_cell), float(hy_cell)
            except Exception:
                pass

    if hermes_cell is None:
        cap_obj = L.get('CAP')
        candidates = []
        if cap_obj is not None:
            candidates.append(getattr(cap_obj, '_last_origin_cell', None))
            try:
                candidates.append(cap_obj.cfg.get('default_origin_cell'))
            except Exception:
                pass
        candidates.append('AQ37')
        for candidate in candidates:
            if not candidate:
                continue
            cel = str(candidate).strip().upper()
            if cel in invalid_cells:
                continue
            try:
                hx_cell, hy_cell = L['cell_to_world'](cel)
                hx, hy = float(hx_cell), float(hy_cell)
                hermes_cell = cel
                break
            except Exception:
                continue

    if hermes_cell is None:
        hermes_cell = 'AQ37'
        try:
            hx_cell, hy_cell = L['cell_to_world'](hermes_cell)
            hx, hy = float(hx_cell), float(hy_cell)
        except Exception:
            pass

    return hx, hy, hermes_cell


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
        now = time.time()
        rec = L['CAP_META'].setdefault(mid, {})
        rec['authorized'] = auth
        rec['asked'] = True
        rec['last_request_ts'] = now
        if auth:
            rec['asked'] = False
            rec['hold_since_ts'] = None
        else:
            rec['hold_since_ts'] = now
        if auth:
            L['officer_say']('Pilot', 'cleared', {})
        else:
            L['officer_say']('Pilot', 'hold', {})
        try:
            L['CAP'].set_permission(mid, auth, now=now)
        except Exception:
            pass
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


@bp.post("/cap/rtb")
def cap_rtb():
    """Force a CAP mission to return to Hermes immediately."""
    L = _lazy(); t0 = time.time(); route = "/cap/rtb"
    try:
        if L['CAP'] is None:
            payload = {"ok": False, "error": "CAP unavailable"}
            L['record_flight']({"route": route, "method": request.method, "status": 503,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": {}, "response": payload})
            return jsonify(payload), 503
        data = request.get_json(silent=True) or {}
        try:
            mid = int(data.get('mission_id') or data.get('id'))
        except Exception:
            mid = 0
        if mid <= 0:
            payload = {"ok": False, "error": "missing mission_id"}
            L['record_flight']({"route": route, "method": request.method, "status": 400,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": data, "response": payload})
            return jsonify(payload), 400

        mission = next((m for m in getattr(L['CAP'], 'missions', []) if int(getattr(m, 'id', -1)) == int(mid)), None)
        if mission is None:
            payload = {"ok": False, "error": "mission not found"}
            L['record_flight']({"route": route, "method": request.method, "status": 404,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": data, "response": payload})
            return jsonify(payload), 404

        L['CAP'].force_rtb(mid, reason='manual_rtb')
        rec = L['CAP_META'].setdefault(mid, {})
        rec['rtb_requested_ts'] = time.time()

        try:
            L['voice_emit']('pilot.mission.rtb', {'id': mid}, fallback=f"SHAR {mid}, return to base.", role='Pilot')
        except Exception:
            pass

        message = f"SHAR {mid} ordered to RTB"
        payload = {"ok": True, "message": message, "mission": {"id": mid, "status": 'rtb'}}
        L['record_flight']({"route": route, "method": request.method, "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": data, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/cap/rtb error: %s", e)
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
        target_class = _contact_class_for_cap(tgt, L.get('TARGET_CLASS_BY_NAME'))
        requested_loadout = _normalize_loadout(data.get('loadout'))
        SURFACE_CLASSES = {
            'Ship', 'Surface', 'Carrier', 'Escort', 'Landing Craft', 'Merchant', 'Convoy',
            'Destroyer', 'Frigate', 'Corvette', 'Patrol Boat', 'Boat', 'Submarine'
        }
        AIR_CLASSES = {'Aircraft', 'Helicopter', 'Missile', 'Bomber', 'Fighter', 'Drone'}
        auto_default = 'aim9'
        if target_class and target_class in SURFACE_CLASSES:
            auto_default = 'bombs'
        loadout = requested_loadout or auto_default
        loadout_adjusted = None
        if loadout == 'bombs' and target_class and target_class in AIR_CLASSES:
            loadout_adjusted = {'from': requested_loadout or 'bombs', 'to': 'aim9', 'reason': 'air_target', 'target_class': target_class}
            loadout = 'aim9'
        elif loadout == 'aim9' and target_class and target_class in SURFACE_CLASSES and not requested_loadout:
            loadout_adjusted = {'from': 'auto', 'to': 'bombs', 'reason': 'surface_target', 'target_class': target_class}
            loadout = 'bombs'
        desired_loadout = loadout
        # Fallback: accept a grid cell from client when target object no longer exists locally
        fallback_cell = None
        try:
            cs = data.get('cell')
            fallback_cell = str(cs).strip().upper() if cs else None
        except Exception:
            fallback_cell = None
        if tgt is None and not fallback_cell:
            return jsonify({"ok": False, "error": "no locked/selected target"}), 400
        st = L['ENG'].public_state() if hasattr(L['ENG'], "public_state") else {}
        hx, hy, hermes_cell = _resolve_hermes_origin(L, st)
        if tgt is not None:
            dx = float(getattr(tgt, 'x', 0.0)) - float(hx)
            dy = float(getattr(tgt, 'y', 0.0)) - float(hy)
            rng_nm = (dx*dx + dy*dy) ** 0.5
            try:
                cell = L['world_to_cell'](float(getattr(tgt, 'x', 0.0)), float(getattr(tgt, 'y', 0.0)))
            except Exception:
                cell = "K13"
        else:
            # Use provided fallback cell
            tx, ty = L['cell_to_world'](fallback_cell)
            dx, dy = float(tx) - float(hx), float(ty) - float(hy)
            rng_nm = (dx*dx + dy*dy) ** 0.5
            cell = fallback_cell
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
                    deck = float(m.get('deck_cycle_s', getattr(L['CAP'], 'cfg', {}).get('deck_cycle_per_pair_s', 180)))
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

            # Evaluate airborne or on-station missions (both can be re-tasked quickly)
            for m in missions:
                try:
                    status_key = str(m.get('status', '')).lower()
                    if status_key not in ('airborne', 'onstation'):
                        continue
                    try:
                        if int(m.get('missiles_left', 0)) <= 0:
                            continue
                    except Exception:
                        pass
                    try:
                        if desired_loadout and str(m.get('loadout','aim9')).lower() != desired_loadout:
                            continue
                    except Exception:
                        pass
                    cx, cy = _mission_pos(m)
                    dist_nm = ((float(getattr(tgt,'x',0.0)) - cx)**2 + (float(getattr(tgt,'y',0.0)) - cy)**2) ** 0.5
                    mission_kind = str(m.get('kind', '')).lower()
                    if mission_kind == 'intercept':
                        spd_val = m.get('intercept_speed_kts')
                        if spd_val is None:
                            spd_val = getattr(L['CAP'], 'intercept_speed_kts', getattr(L['CAP'], 'cruise_speed_kts', 420.0))
                    else:
                        spd_val = m.get('cruise_speed_kts', getattr(L['CAP'], 'cruise_speed_kts', 420.0))
                    spd = float(spd_val or 420.0)
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
                        snap_m = {
                            'id': chosen_mid,
                            'target_cell': getattr(m,'target_cell',''),
                            'timestamps': getattr(m,'ts',{}),
                            'outbound_s': getattr(m,'outbound_s',600),
                            'deck_cycle_s': getattr(m, 'deck_cycle_s', getattr(L['CAP'], 'cfg', {}).get('deck_cycle_per_pair_s', 180)),
                            'intercept_speed_kts': getattr(m, 'intercept_speed_kts', getattr(L['CAP'], 'intercept_speed_kts', getattr(L['CAP'], 'cruise_speed_kts', 420.0))),
                            'cruise_speed_kts': getattr(m, 'cruise_speed_kts', getattr(L['CAP'], 'cruise_speed_kts', 420.0)),
                            'kind': getattr(m, 'kind', 'cap'),
                        }
                        cx, cy = _mission_pos(snap_m)
                        dist_nm = ((tx - cx)**2 + (ty - cy)**2) ** 0.5
                        base_spd = float(getattr(m, 'cruise_speed_kts', getattr(L['CAP'], 'cruise_speed_kts', 420.0)) or 420.0)
                        dash_spd = float(getattr(m, 'intercept_speed_kts', getattr(L['CAP'], 'intercept_speed_kts', base_spd)) or base_spd)
                        spd = dash_spd if getattr(m, 'kind', 'cap') == 'intercept' else base_spd
                        eta_s = int(max(1.0, (dist_nm / max(1.0, spd)) * 3600.0))
                        setattr(m, 'target_cell', cell)
                        try:
                            # Retask to intercept: leave onstation, go airborne with new ETA
                            m.ts['eta_onstation'] = time.time() + eta_s  # type: ignore[attr-defined]
                            m.ts['vector'] = True  # mark retarget for UI snapshot
                            # Ensure mission re-enters transit
                            try:
                                if str(getattr(m, 'status', '')) == 'onstation':
                                    m.ts.pop('onstation', None)
                                    m.ts['etd_rtb'] = None
                                setattr(m, 'status', 'airborne')
                            except Exception:
                                setattr(m, 'status', 'airborne')
                        except Exception:
                            pass
                        break
            except Exception:
                pass
            try:
                meta = L['CAP_META'].get(chosen_mid) or {}
                if tgt is not None:
                    meta['target_id'] = int(getattr(tgt, 'id', 0) or 0)
                    meta['target_name'] = getattr(tgt, 'name', '')
                meta['target_cell'] = cell
                meta['asked'] = False
                meta['authorized'] = False
                meta['last_request_ts'] = 0.0
                meta['hold_since_ts'] = None
                L['CAP_META'][chosen_mid] = meta
                L['CAP'].set_permission(chosen_mid, False)
            except Exception:
                pass
            payload = {"ok": True, "message": f"Vectoring airborne pair to {cell}", "mission": {"id": chosen_mid, "target_cell": cell}, "loadout": desired_loadout}
            try:
                L['voice_emit']('pilot.vector', {'cell': cell}, fallback='Vectoring to %s.' % (cell,), role='Pilot')
            except Exception:
                pass
            try:
                # Log explicit vector event for game console + audio cues
                L['record_event']('cap.vector', {
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
                               "request": {"id": tid, "cell": cell, "range_nm": round(rng_nm, 2), "vector": True, "loadout": desired_loadout}, "response": payload})
            return jsonify(payload)

        # Fallback: launch a fresh pair (honour optional loadout)
        res = L['CAP'].request_cap_to_cell(
            cell,
            distance_nm=float(rng_nm),
            origin_xy=(hx, hy),
            origin_cell=hermes_cell,
            mission_kind='intercept',
            loadout=loadout,
        )
        status = 200 if res.get("ok") else 400
        payload: Dict[str, Any] = {"ok": bool(res.get("ok")), "message": res.get("message"), "mission": res.get("mission"), "loadout": loadout}
        if loadout_adjusted:
            payload['loadout_adjusted'] = loadout_adjusted
            try:
                L['record_event']('cap.loadout.adjusted', {
                    'from': loadout_adjusted['from'],
                    'to': loadout_adjusted['to'],
                    'reason': loadout_adjusted['reason'],
                    'target_class': loadout_adjusted.get('target_class'),
                    'target_id': int(getattr(tgt, 'id', 0) or 0) if tgt is not None else None,
                })
            except Exception:
                pass
        if res.get('ok'):
            try:
                event_payload = {'cell': cell, 'from': hermes_cell, 'range_nm': round(rng_nm, 2), 'loadout': loadout}
                if target_class:
                    event_payload['target_class'] = target_class
                L['record_event']('cap.launch', event_payload)
            except Exception:
                pass
            try:
                mid = int((res.get('mission') or {}).get('id'))
                meta = L['CAP_META'].get(mid) or {}
                meta['origin_xy'] = (hx, hy)
                meta['origin_cell'] = hermes_cell
                if tgt is not None:
                    meta['target_id'] = int(getattr(tgt, 'id', 0) or 0)
                    meta['target_name'] = getattr(tgt, 'name', '')
                    meta['target_cell'] = L['world_to_cell'](float(getattr(tgt, 'x', 0.0)), float(getattr(tgt, 'y', 0.0)))
                else:
                    meta['target_cell'] = cell
                meta.setdefault('asked', False)
                meta.setdefault('authorized', False)
                meta.setdefault('last_request_ts', 0.0)
                meta.setdefault('hold_since_ts', None)
                L['CAP_META'][mid] = meta
                L['CAP'].set_permission(mid, False)
            except Exception:
                pass
            try:
                L['CAP'].tick(now=time.time())
            except Exception:
                pass
        L['record_flight']({"route": route, "method": request.method, "status": status,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {"id": tid, "cell": cell, "range_nm": round(rng_nm, 2), "loadout": loadout, "target_class": target_class}, "response": payload})
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
        hx, hy, hermes_cell = _resolve_hermes_origin(L, st)
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
        try:
            loadout_raw = str((data.get('loadout') or 'aim9')).lower()
        except Exception:
            loadout_raw = 'aim9'
        loadout = 'bombs' if loadout_raw in ('bomb', 'bombs') else 'aim9'
        follow = None
        try:
            f = data.get('follow')
            follow = str(f).strip().lower() if f else None
        except Exception:
            follow = None
        loadout_forced = None
        if follow == 'hermes' and loadout != 'aim9':
            loadout = 'aim9'
            loadout_forced = 'hermes_follow'
        res = L['CAP'].request_cap_to_cell(
            cell,
            distance_nm=float(rng_nm),
            station_minutes=float(sm),
            radius_nm=float(rm),
            origin_xy=(hx, hy),
            origin_cell=hermes_cell,
            loadout=loadout,
            follow=follow,
        )
        status = 200 if res.get("ok") else 400
        mission = res.get('mission') or {}
        actual_loadout = str(mission.get('loadout') or loadout)
        payload = {"ok": bool(res.get("ok")), "message": res.get("message"), "mission": mission, "loadout": actual_loadout}
        if loadout_forced and actual_loadout == 'aim9':
            payload['loadout_forced'] = loadout_forced
        if res.get('ok'):
            try:
                L['record_event']('cap.launch', {'cell': cell, 'loadout': actual_loadout, 'follow': follow})
            except Exception:
                pass
            try:
                mid = int((res.get('mission') or {}).get('id'))
                meta = L['CAP_META'].get(mid) or {}
                meta['origin_xy'] = (hx, hy)
                meta['origin_cell'] = hermes_cell
                meta.setdefault('asked', False)
                meta.setdefault('authorized', False)
                meta.setdefault('last_request_ts', 0.0)
                meta.setdefault('hold_since_ts', None)
                L['CAP_META'][mid] = meta
                L['CAP'].set_permission(mid, False)
            except Exception:
                pass
            try:
                L['CAP'].tick(now=time.time())
            except Exception:
                pass
        L['record_flight']({"route": route, "method": request.method, "status": status,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {"cell": cell, "range_nm": round(rng_nm, 2), "loadout": loadout, "follow": follow},
                           "response": payload})
        return jsonify(payload), status
    except Exception as e:
        logging.exception("/cap/launch_to error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L = _lazy()
        L['record_flight']({"route": route, "method": request.method, "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.post("/cap/convert_to_cap")
def cap_convert_to_cap():
    """Retask an airborne/onstation mission to hold CAP at a cell.

    JSON: { mission_id:int, cell:str, minutes?:float }
    Only works for AIM-9 loadout with missiles left.
    """
    L = _lazy(); t0 = time.time(); route = "/cap/convert_to_cap"
    try:
        if L['CAP'] is None:
            return jsonify({"ok": False, "error": "CAP unavailable"}), 503
        data = request.get_json(silent=True) or {}
        try:
            mid = int(data.get('mission_id') or data.get('id'))
        except Exception:
            mid = 0
        cell = str(data.get('cell') or '').strip().upper()
        minutes = data.get('minutes')
        follow = None
        try:
            f = data.get('follow')
            follow = str(f).lower() if f else None
        except Exception:
            follow = None
        if mid <= 0 or not cell:
            return jsonify({"ok": False, "error": "missing mission_id or cell"}), 400
        res = L['CAP'].convert_to_cap(mid, cell, minutes=(float(minutes) if minutes is not None else None), follow=follow)
        status = 200 if res.get('ok') else 400
        payload = {"ok": bool(res.get('ok')), "message": res.get('message'), "mission": res.get('mission')}
        if 'eta_seconds' in res:
            payload['eta_seconds'] = res['eta_seconds']
        L['record_flight']({"route": route, "method": request.method, "status": status,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": data, "response": payload})
        return jsonify(payload), status
    except Exception as e:
        logging.exception("/cap/convert_to_cap error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L = _lazy()
        L['record_flight']({"route": route, "method": request.method, "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.post("/cap/vector")
def cap_vector():
    """Retarget a specific airborne mission to the current primary lock target.

    Request JSON: { "mission_id": <int> }
    // Invariant guard: consistency suite — explicit vector endpoint for COMMS SHAR table
    """
    L = _lazy(); t0 = time.time(); route = "/cap/vector"
    try:
        if L['CAP'] is None:
            payload = {"ok": False, "error": "CAP unavailable"}
            L['record_flight']({"route": route, "method": request.method, "status": 503,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": {}, "response": payload})
            return jsonify(payload), 503
        data = request.get_json(silent=True) or {}
        try:
            mid = int(data.get('mission_id'))
        except Exception:
            mid = 0
        requested_loadout = _normalize_loadout(data.get('loadout'))
        if mid <= 0:
            payload = {"ok": False, "error": "missing mission_id"}
            L['record_flight']({"route": route, "method": request.method, "status": 400,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": data, "response": payload})
            return jsonify(payload), 400

        # Determine current primary lock target
        st = L['ENG'].public_state() if hasattr(L['ENG'], "public_state") else {}
        hx, hy, hermes_cell = _resolve_hermes_origin(L, st)
        pid = getattr(L['RADAR'], 'priority_id', None)
        tgt = next((c for c in L['RADAR'].contacts if int(getattr(c,'id',-1)) == int(pid)), None) if pid is not None else None
        if tgt is None:
            payload = {"ok": False, "error": "no locked target"}
            L['record_flight']({"route": route, "method": request.method, "status": 400,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": data, "response": payload})
            return jsonify(payload), 400
        cell = L['world_to_cell'](float(getattr(tgt,'x',0.0)), float(getattr(tgt,'y',0.0)))

        # Find the mission by id
        mission = next((m for m in getattr(L['CAP'], 'missions', []) if int(getattr(m,'id',-1)) == int(mid)), None)
        if mission is None:
            payload = {"ok": False, "error": "mission not found"}
            L['record_flight']({"route": route, "method": request.method, "status": 404,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": data, "response": payload})
            return jsonify(payload), 404

        # Determine desired loadout; default to current
        current_loadout = str(getattr(mission, 'loadout', 'aim9')).lower() or 'aim9'
        desired_loadout = requested_loadout or current_loadout
        if desired_loadout not in ('aim9', 'bombs'):
            desired_loadout = current_loadout

        # Apply loadout change if requested
        if desired_loadout != current_loadout:
            cfg = getattr(L['CAP'], 'cfg', {}) if isinstance(getattr(L['CAP'], 'cfg', None), dict) else {}
            weapons_cfg = cfg.get('weapons') or {}
            if desired_loadout == 'bombs':
                bombs_cfg = weapons_cfg.get('bombs') or {}
                total = int(bombs_cfg.get('bombs_total', bombs_cfg.get('missiles_total', getattr(mission, 'missiles_total', 4))))
                cooldown = int(bombs_cfg.get('engagement_cooldown_s', getattr(mission, 'engagement_cooldown_s', 5)))
            else:
                aim_cfg = weapons_cfg.get('aim9') or {}
                total = int(aim_cfg.get('missiles_total', getattr(mission, 'missiles_total', 2)))
                cooldown = int(aim_cfg.get('engagement_cooldown_s', getattr(mission, 'engagement_cooldown_s', 5)))
            setattr(mission, 'loadout', desired_loadout)
            setattr(mission, 'missiles_total', max(0, total))
            setattr(mission, 'engagement_cooldown_s', max(1, cooldown))
            if getattr(mission, 'missiles_left', total) > total:
                mission.missiles_left = total

        # Estimate mission current position along its existing leg
        def _mission_pos(m) -> tuple[float,float]:
            try:
                ts = getattr(m, 'ts', {}) or {}
                launch = float(ts.get('launch', time.time()))
                deck = float(getattr(L['CAP'], 'cfg', {}).get('deck_cycle_per_pair_s', 180))
                outb = float(getattr(m, 'outbound_s', getattr(m, 'inbound_s', 600)) or 600)
                prog = max(0.0, min(1.0, (time.time() - (launch + deck)) / max(1.0, outb)))
                ox, oy = (hx, hy)
                try:
                    meta = L['CAP_META'].get(int(getattr(m,'id',0))) or {}
                    o = meta.get('origin_xy')
                    if isinstance(o, (list, tuple)) and len(o) == 2:
                        ox, oy = float(o[0]), float(o[1])
                except Exception:
                    pass
                old_cell = str(getattr(m,'target_cell',''))
                txo, tyo = L['cell_to_world'](old_cell) if old_cell else (ox, oy)
                cx = float(ox) + (float(txo) - float(ox)) * prog
                cy = float(oy) + (float(tyo) - float(oy)) * prog
                return (cx, cy)
            except Exception:
                return (own_x, own_y)

        cx, cy = _mission_pos(mission)
        tx, ty = float(getattr(tgt,'x',0.0)), float(getattr(tgt,'y',0.0))
        dist_nm = ((tx - cx)**2 + (ty - cy)**2) ** 0.5
        base_spd = float(getattr(L['CAP'], 'cruise_speed_kts', 420.0) or 420.0)
        dash_spd = float(getattr(L['CAP'], 'intercept_speed_kts', base_spd))
        spd = dash_spd if getattr(mission, 'kind', 'cap') == 'intercept' else base_spd
        eta_s = int(max(1.0, (dist_nm / max(1.0, spd)) * 3600.0))

        # Retarget in place: ensure mission transitions back to airborne with new ETA
        setattr(mission, 'target_cell', cell)
        try:
            setattr(mission, 'follow', None)
        except Exception:
            pass
        try:
            setattr(mission, 'kind', 'intercept')
        except Exception:
            pass
        try:
            mission.ts['eta_onstation'] = time.time() + eta_s  # type: ignore[attr-defined]
            mission.ts['vector'] = True
            try:
                if str(getattr(mission, 'status', '')) == 'onstation':
                    mission.ts.pop('onstation', None)
                    mission.ts['etd_rtb'] = None
                setattr(mission, 'status', 'airborne')
            except Exception:
                setattr(mission, 'status', 'airborne')
        except Exception:
            pass

        # Clear any pending ROE prompt for this mission
        try:
            meta = L['CAP_META'].get(mid) or {}
            meta['target_id'] = int(getattr(tgt, 'id', 0) or 0)
            meta['target_name'] = getattr(tgt, 'name', '')
            meta['target_cell'] = cell
            meta['asked'] = False
            meta['authorized'] = False
            meta['last_request_ts'] = 0.0
            meta['hold_since_ts'] = None
            meta['follow'] = None
            meta['loadout'] = desired_loadout
            L['CAP_META'][mid] = meta
            L['CAP'].set_permission(mid, False)
        except Exception:
            pass

        payload = {
            "ok": True,
            "message": f"Vectoring SHAR {mid} to {cell}",
            "mission": {"id": mid, "target_cell": cell, "loadout": desired_loadout},
            "loadout": desired_loadout,
        }
        try:
            L['voice_emit']('pilot.vector', {'cell': cell}, fallback='Vectoring to %s.' % (cell,), role='Pilot')
        except Exception:
            pass
        try:
            L['record_event']('cap.vector', {'mission_id': mid, 'cell': cell})
        except Exception:
            pass
        L['record_flight']({"route": route, "method": request.method, "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {"mission_id": mid, "cell": cell, "loadout": desired_loadout}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/cap/vector error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L = _lazy()
        L['record_flight']({"route": route, "method": request.method, "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500
