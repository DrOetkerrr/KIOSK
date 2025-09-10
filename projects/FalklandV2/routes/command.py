from __future__ import annotations

import logging
import time
from flask import Blueprint, jsonify, request

bp = Blueprint("command", __name__)


@bp.route("/api/command", methods=["GET", "POST"])
def api_command():
    """Handle slash-style commands used by the dashboard.

    This blueprint imports runtime objects lazily from webdash to avoid
    circular imports during app startup.
    """
    # Late imports to avoid cycles at import time
    from ..webdash import (
        ENG,
        RADAR,
        STATE_LOCK,
        NAV_STATE,
        record_flight,
        voice_emit,
        officer_say,
        ship_cell_from_state,
        _load_json,
        DATA_DIR,
        get_own_xy,
        contact_to_ui,
        radar_xy_from_state,
        _radar_summary_ctx,
    )

    t0 = time.time()
    route = "/api/command"

    # Extract cmd from query or JSON body
    cmd = ""
    try:
        if request.method == "GET":
            cmd = (request.args.get("cmd") or "").strip()
        else:
            if request.is_json:
                data = request.get_json(silent=True) or {}
                cmd = str(data.get("cmd", "")).strip()
            if not cmd:
                cmd = (request.args.get("cmd") or "").strip()
    except Exception:
        cmd = ""

    if not cmd:
        payload = {"ok": False, "error": "missing cmd"}
        record_flight({
            "route": route, "method": request.method, "status": 400,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {"cmd": cmd}, "response": payload,
        })
        return jsonify(payload), 400

    try:
        s = cmd.strip()
        # NAV set intercept: acknowledge course/speed and set turn target
        if s.lower().startswith('/nav set'):
            # naive kv parse
            parts = s.split()
            kv = {}
            for p in parts[2:]:
                if '=' in p:
                    k, v = p.split('=', 1)
                    kv[k.strip().lower()] = v.strip()
            # Execute command via engine
            try:
                result = ENG.exec_slash(cmd)  # type: ignore
            except Exception as ee:
                result = f"ERR: {ee}"
            # Voice acks
            try:
                st2 = ENG.public_state() if hasattr(ENG, 'public_state') else {}
                ship = (st2 or {}).get('ship', {}) if isinstance(st2, dict) else {}
                spd = float(ship.get('speed', 0.0))
            except Exception:
                spd = 0.0
            if 'heading' in kv:
                try:
                    hdg = float(kv['heading'])
                    with STATE_LOCK:
                        NAV_STATE['turn_target'] = float(hdg)
                        NAV_STATE['turn_hold_since'] = 0.0
                    voice_emit('nav.set.course.ack', {'hdg': round(hdg), 'spd': round(spd)}, fallback=f'Course set {round(hdg)}°, making {round(spd)} knots.', role='Navigation')
                except Exception:
                    pass
            if 'speed' in kv:
                try:
                    spd_new = float(kv['speed'])
                    # get current heading for context
                    st3 = ENG.public_state() if hasattr(ENG, 'public_state') else {}
                    ship3 = (st3 or {}).get('ship', {}) if isinstance(st3, dict) else {}
                    hdg3 = float(ship3.get('heading', 0.0))
                    voice_emit('nav.set.speed.ack', {'spd': round(spd_new), 'hdg': round(hdg3)}, fallback=f'Speed now {round(spd_new)} knots; heading {round(hdg3)}°.', role='Navigation')
                except Exception:
                    pass
            payload = {"ok": True, "result": result}
            record_flight({
                "route": route, "method": request.method, "status": 200,
                "duration_ms": int((time.time()-t0)*1000),
                "request": {"cmd": cmd}, "response": payload,
            })
            return jsonify(payload)

        # NAV Hermes helper commands: /nav hermes close_in | stand_off
        if s.lower().startswith('/nav hermes'):
            import math as _m
            try:
                st2 = ENG.public_state() if hasattr(ENG, 'public_state') else {}
                own_cell = ship_cell_from_state(st2)
                j = 0
                while j < len(own_cell) and own_cell[j].isalpha():
                    j += 1
                cletters = own_cell[:j] or 'A'; rstr = own_cell[j:] or '1'
                ci = 0
                for ch in cletters: ci = ci*26 + (ord(ch)-ord('A')+1)
                ri = int(rstr)
                convoy = _load_json(DATA_DIR / 'convoy.json', {})
                escorts = convoy.get('escorts', []) if isinstance(convoy, dict) else []
                hermes = next((e for e in escorts if str(e.get('name','')).lower().find('hermes') >= 0), None)
                if hermes:
                    dx_cells, dy_cells = (-2, 2) if 'close_in' in s else (-6, 6)
                    # No-op for now; stub acknowledgement
                    voice_emit('nav.hermes.ack', {'mode': ('close in' if 'close_in' in s else 'stand off')}, fallback='Hermes navigation command acknowledged.', role='Navigation')
                    payload = {"ok": True, "result": "Hermes nav ack"}
                else:
                    payload = {"ok": False, "error": "Hermes not in convoy"}
                record_flight({"route": route, "method": request.method, "status": (200 if payload.get('ok') else 404),
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": {"cmd": cmd}, "response": payload})
                return jsonify(payload), (200 if payload.get('ok') else 404)
            except Exception as e:
                logging.exception("/api/command hermes error: %s", e)
                payload = {"ok": False, "error": str(e)}
                record_flight({"route": route, "method": request.method, "status": 500,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": {"cmd": cmd}, "response": payload})
                return jsonify(payload), 500

        # Radar lock/unlock helpers
        if s.lower().startswith("/radar unlock"):
            try:
                RADAR.priority_id = None  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                officer_say('Fire Control', 'unlocked', {})
            except Exception:
                pass
            payload = {"ok": True, "result": "UNLOCKED"}
            record_flight({"route": route, "method": request.method, "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {"cmd": cmd}, "response": payload})
            return jsonify(payload)

        if s.lower().startswith("/radar lock"):
            parts = s.split()
            arg = parts[-1] if len(parts) >= 3 else ""
            # helper to find by id
            def _radar_find_by_id(cid_val):
                try:
                    cid_i = int(str(cid_val))
                except Exception:
                    return None
                for c in RADAR.contacts:
                    if int(getattr(c, "id", -1)) == cid_i:
                        return c
                return None
            # allow '/radar lock nearest' or 'primary'
            if str(arg).lower() in ("nearest","primary"):
                tid = getattr(RADAR, 'priority_id', None)
                target = next((c for c in RADAR.contacts if int(getattr(c,'id',-1)) == int(tid)), None) if tid is not None else None
            else:
                target = _radar_find_by_id(arg)
            if target is None:
                try:
                    avail = [int(getattr(c,'id',-1)) for c in RADAR.contacts]
                except Exception:
                    avail = []
                payload = {"ok": False, "error": "contact not found", "available_ids": avail[:10]}
                record_flight({
                    "route": route, "method": request.method, "status": 404,
                    "duration_ms": int((time.time()-t0)*1000),
                    "request": {"cmd": cmd}, "response": payload,
                })
                return jsonify(payload), 404
            tid = int(getattr(target, 'id', 0))
            try:
                globals()['PRIMARY_ID'] = tid
            except Exception:
                pass
            try:
                RADAR.priority_id = tid  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                RADAR.rec.log("radar.lock", {"id": tid})
            except Exception:
                pass
            # Officer radio (Fire Control) — best-effort, never fail the command
            try:
                st = ENG.public_state() if hasattr(ENG, 'public_state') else {}
                own_x, own_y = get_own_xy(st)
                ui = contact_to_ui(target, (own_x, own_y))
                try:
                    officer_say('Fire Control','locked',{'name': ui.get('name'), 'id': tid, 'range_nm': ui.get('range_nm')})
                except Exception:
                    pass
            except Exception:
                try:
                    officer_say('Fire Control','locked',{'id': tid})
                except Exception:
                    pass
            payload = {"ok": True, "result": f"LOCKED id={tid}"}
            record_flight({
                "route": route, "method": request.method, "status": 200,
                "duration_ms": int((time.time()-t0)*1000),
                "request": {"cmd": cmd}, "response": payload,
            })
            return jsonify(payload), 200

        # Special-case radar scan to use RADAR directly
        elif s.lower().startswith("/radar scan"):
            # compute own_xy
            st = ENG.public_state() if hasattr(ENG, "public_state") else {}
            own_x, own_y = radar_xy_from_state(st)
            # Radio: confirmation + summary
            try:
                officer_say('Radar', 'scanning', {})
            except Exception:
                pass
            try:
                RADAR.scan(own_x, own_y)
            except Exception:
                pass
            # Build a short summary for the crew
            try:
                ctx = _radar_summary_ctx(own_x, own_y)
                officer_say('Radar', 'scan_report', ctx, fallback=f"Captain, radar scan complete: {ctx['contacts']} contact(s), hostiles {ctx['hostiles']}, friendlies {ctx['friendlies']}.")
            except Exception:
                pass
            result = f"RADAR: scanned, {len(RADAR.contacts)} contact(s)"
        elif hasattr(ENG, "exec_slash"):
            try:
                result = ENG.exec_slash(cmd)  # type: ignore
            except Exception as ee:
                result = f"ERR: {ee}"
        else:
            result = "ERR: command interface unavailable"

        payload = {"ok": True, "result": result}
        record_flight({
            "route": route, "method": request.method, "status": 200,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {"cmd": cmd}, "response": payload,
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/api/command error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({
            "route": route, "method": request.method, "status": 500,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {"cmd": cmd}, "response": payload,
        })
        return jsonify(payload), 500

