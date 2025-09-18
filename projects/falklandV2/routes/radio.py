from __future__ import annotations

import time
import logging
from flask import Blueprint, jsonify, request

bp = Blueprint("radio", __name__)


def _lazy():
    # Late import to avoid circular references
    from ..webdash import (
        record_flight, record_radio, record_officer,
        ENG, RADAR, CAP,
        get_own_xy, world_to_cell, cell_for_world,
        stamp_cap_launch, _arg_or_json
    )
    return locals()


@bp.route("/radio/say", methods=["GET", "POST"])
def radio_say():
    L = _lazy(); t0 = time.time(); route = "/radio/say"
    try:
        text = L['_arg_or_json'](request, 'text', '')
        kind = L['_arg_or_json'](request, 'kind', 'ENSIGN')
        if not text:
            payload = {"ok": False, "error": "missing text"}
            L['record_flight']({"route": route, "method": request.method, "status": 400,
                              "duration_ms": int((time.time()-t0)*1000),
                              "request": {"kind": kind, "text": text}, "response": payload})
            return jsonify(payload), 400
        L['record_radio'](kind or 'ENSIGN', text)
        payload = {"ok": True, "kind": kind or 'ENSIGN', "text": text}
        L['record_flight']({"route": route, "method": request.method, "status": 200,
                          "duration_ms": int((time.time()-t0)*1000),
                          "request": {"kind": kind, "text": text}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radio/say error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({"route": route, "method": request.method, "status": 500,
                          "duration_ms": int((time.time()-t0)*1000),
                          "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.route("/radio/ask", methods=["GET", "POST"])
def radio_ask():
    L = _lazy(); t0 = time.time(); route = "/radio/ask"
    try:
        txt = L['_arg_or_json'](request, 'text', '')
        if not txt:
            payload = {"ok": False, "error": "missing text"}
            L['record_flight']({"route": route, "method": request.method, "status": 400,
                              "duration_ms": int((time.time()-t0)*1000),
                              "request": {}, "response": payload})
            return jsonify(payload), 400
        s = str(txt).strip()
        head = s.split(':', 1)[0].split(',', 1)[0].strip().lower()
        role = 'Ensign'
        if head.startswith(('nav', 'navigation')):
            role = 'Navigation'
        elif head.startswith(('radar', 'search')):
            role = 'Radar'
        elif head.startswith(('weap', 'weapon')):
            role = 'Weapons'
        elif head.startswith(('fire control', 'fire', 'fc')):
            role = 'Fire Control'
        elif head.startswith(('eng', 'engineer', 'engineering')):
            role = 'Engineering'
        reply = 'Captain, acknowledged.'
        try:
            st = L['ENG'].public_state() if hasattr(L['ENG'], 'public_state') else {}
            ship = (st or {}).get('ship', {})
            hdg = int(float(ship.get('heading', 0)))
            spd = int(float(ship.get('speed', 0)))
            cell = L['cell_for_world'](float(ship.get('row', 50.0)), float(ship.get('col', 50.0)))
            low = s.lower()
            if role == 'Navigation' and ('course' in low or 'speed' in low or 'grid' in low):
                reply = f"Captain, ship steady on course {hdg}°, speed {spd} knots, grid {cell}."
            elif role == 'Radar' and ('nearest' in low or 'contacts' in low):
                own_x, own_y = L['get_own_xy'](st)
                if L['RADAR'].contacts:
                    c = min(L['RADAR'].contacts, key=lambda k: ((k.x-own_x)**2 + (k.y-own_y)**2))
                    rng = round(((c.x-own_x)**2 + (c.y-own_y)**2) ** 0.5, 2)
                    reply = f"Captain, nearest contact ID {c.id}, range {rng} nm."
                else:
                    reply = "Captain, no contacts on scope."
            elif role == 'Weapons' and ('status' in low or 'readiness' in low or 'ammo' in low):
                from ..webdash import load_arming, load_ammo  # late import
                arming = load_arming(); ammo = load_ammo()
                ready = [f"{k} {arming.get(k)} ({ammo.get(k,0)})" for k in ammo.keys()]
                reply = "Captain, weapons: " + "; ".join(ready[:3]) + ("…" if len(ready) > 3 else "")
            # CAP request via radio
            if ('cap' in low) and any(w in low for w in ('request', 'launch', 'vector')):
                tid = None
                try:
                    from .. import webdash as _wd
                    if hasattr(_wd, 'PRIMARY_ID') and _wd.PRIMARY_ID is not None:
                        tid = int(_wd.PRIMARY_ID)
                except Exception:
                    tid = None
                if tid is None:
                    tid = getattr(L['RADAR'], 'priority_id', None)
                tgt = next((c for c in L['RADAR'].contacts if int(getattr(c, 'id', -1)) == int(tid)), None) if tid is not None else None
                if tgt is None:
                    reply = "Captain, no locked or selected target for CAP."
                else:
                    own_x, own_y = L['get_own_xy'](st)
                    dx = float(getattr(tgt, 'x', 0.0)) - float(own_x)
                    dy = float(getattr(tgt, 'y', 0.0)) - float(own_y)
                    rng = (dx*dx + dy*dy) ** 0.5
                    try:
                        tcell = L['world_to_cell'](float(getattr(tgt, 'x', 0.0)), float(getattr(tgt, 'y', 0.0)))
                    except Exception:
                        tcell = cell
                    if L['CAP'] is None:
                        reply = "Captain, CAP unavailable."
                    else:
                        res = L['CAP'].request_cap_to_cell(tcell, distance_nm=float(rng))
                        if res.get('ok'):
                            reply = f"Hermes: CAP pair launching to {tcell}."
                            try:
                                L['stamp_cap_launch']()
                            except Exception:
                                pass
                            try:
                                mid = int((res.get('mission') or {}).get('id'))
                                meta = L['CAP_META'].get(mid) or {}
                                meta.setdefault('asked', False)
                                meta.setdefault('authorized', False)
                                meta.setdefault('last_request_ts', 0.0)
                                meta.setdefault('hold_since_ts', None)
                                L['CAP_META'][mid] = meta
                                L['CAP'].set_permission(mid, False)
                            except Exception:
                                pass
                        else:
                            reply = f"Hermes: unable to launch — {res.get('message','denied')}"
        except Exception:
            pass
        L['record_officer'](role, reply)
        payload = {"ok": True, "role": role, "reply": reply}
        L['record_flight']({"route": route, "method": request.method, "status": 200,
                          "duration_ms": int((time.time()-t0)*1000),
                          "request": {"text": txt}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radio/ask error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({"route": route, "method": request.method, "status": 500,
                          "duration_ms": int((time.time()-t0)*1000),
                          "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.post("/radio/ai")
def radio_ai():
    L = _lazy(); t0 = time.time(); route = "/radio/ai"
    try:
        from ..webdash import _ai_parse, _ai_exec, _arg_or_json  # reuse helpers
        txt = _arg_or_json(request, 'text', '')
        if not txt:
            payload = {"ok": False, "error": "missing text"}
            L['record_flight']({"route": route, "method": request.method, "status": 400,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": {}, "response": payload})
            return jsonify(payload), 400
        actions = _ai_parse(str(txt))
        if not actions:
            # Offer a brief help nudge
            L['record_officer']('Ensign', "Captain, say 'Scan radar', 'Lock <id>', 'Request CAP', or 'CAP to K13'.")
            payload = {"ok": True, "actions": [], "reply": "HELP"}
            L['record_flight']({"route": route, "method": request.method, "status": 200,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": {"text": txt}, "response": payload})
            return jsonify(payload)
        msgs = _ai_exec(actions)
        # Summarize as a single officer reply line
        if msgs:
            L['record_officer']('Ensign', f"Captain, { '; '.join(msgs) }.")
        payload = {"ok": True, "actions": actions, "messages": msgs}
        L['record_flight']({"route": route, "method": request.method, "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {"text": txt}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radio/ai error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({"route": route, "method": request.method, "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500
