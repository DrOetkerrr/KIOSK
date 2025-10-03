from __future__ import annotations

import logging
import time
import math
import random
import json
from collections import deque
from flask import Blueprint, jsonify, request

from ..radar import Contact, WORLD_N  # types and constants

bp = Blueprint("radar_dev", __name__)


def _L():
    # Late import to avoid circular references and keep webdash as single source
    from ..webdash import (
        ENG, RADAR, RUNTIME,
        record_flight, contact_to_ui, world_to_cell, get_own_xy, radar_xy_from_state,
        _load_json, CONTACTS_PATH, officer_say,
        DEBUG_CONTACTS, _make_debug_contact,
    )
    return locals()


def _hostiles_allowed(runtime) -> bool:
    try:
        return bool(runtime.allow_hostile_contacts())
    except Exception:
        return True


@bp.get("/debug/spawn_contact")
def debug_spawn_contact():
    L = _L(); t0 = time.time(); route = "/debug/spawn_contact"
    try:
        args = request.args
        cell = args.get("cell") or None
        name = args.get("name") or None
        typ = args.get("type") or None
        try:
            rng = float(args.get("range", "")) if args.get("range") is not None else None
        except Exception:
            rng = None
        try:
            crs = int(float(args.get("course", ""))) if args.get("course") is not None else None
        except Exception:
            crs = None
        try:
            spd = int(float(args.get("speed", ""))) if args.get("speed") is not None else None
        except Exception:
            spd = None

        contact = L['_make_debug_contact'](cell=cell, name=name, typ=typ, range_nm=rng, course=crs, speed=spd)
        L['DEBUG_CONTACTS'].append(contact)
        payload = {"ok": True, "added": contact, "count": len(L['DEBUG_CONTACTS'])}
        L['record_flight']({
            "route": route, "method": "GET", "status": 200,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {k: args.get(k) for k in ("cell","name","type","range","course","speed")},
            "response": payload,
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/debug/spawn_contact error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({
            "route": route, "method": "GET", "status": 500,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload), 500


@bp.route("/debug/clear_contacts", methods=["POST", "GET"])
def debug_clear_contacts():
    L = _L(); t0 = time.time(); route = "/debug/clear_contacts"
    try:
        L['DEBUG_CONTACTS'].clear()
        payload = {"ok": True, "cleared": True}
        L['record_flight']({
            "route": route, "method": request.method, "status": 200,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/debug/clear_contacts error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({
            "route": route, "method": request.method, "status": 500,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload), 500


@bp.get("/debug/cellmap")
def debug_cellmap():
    L = _L()
    try:
        try:
            n = int(request.args.get("n", 8))
        except Exception:
            n = 8
        try:
            own_x, own_y = L['get_own_xy'](L['ENG'].state)
        except Exception:
            st = L['ENG'].public_state() if hasattr(L['ENG'], "public_state") else {}
            own_x, own_y = L['radar_xy_from_state'](st)
        out = []
        try:
            for c in L['RADAR'].contacts[:n]:
                out.append({
                    "id": c.id,
                    "name": c.name,
                    "type": c.allegiance,
                    "x": round(c.x, 2),
                    "y": round(c.y, 2),
                    "cell": L['world_to_cell'](c.x, c.y)
                })
        except Exception:
            pass
        return jsonify({"ok": True, "own": {"x": own_x, "y": own_y}, "contacts": out})
    except Exception as e:
        logging.exception("/debug/cellmap error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/radar/force_spawn")
def radar_force_spawn():
    L = _L(); t0 = time.time(); route = "/radar/force_spawn"
    try:
        if not _hostiles_allowed(L['RUNTIME']):
            payload = {"ok": False, "error": "hostile_spawns_disabled"}
            L['record_flight']({"route": route, "method": "GET", "status": 403,
                              "duration_ms": int((time.time()-t0)*1000),
                              "request": {}, "response": payload})
            return jsonify(payload), 403
        st = L['ENG'].public_state() if hasattr(L['ENG'], "public_state") else {}
        own_x, own_y = L['radar_xy_from_state'](st)
        c = L['RADAR'].force_spawn(own_x, own_y, "Hostile", bearing_deg=315.0, range_nm=random.uniform(8.0, 14.0))
        ui = L['contact_to_ui'](c, (own_x, own_y))
        try:
            ui['cell'] = L['world_to_cell'](c.x, c.y)
        except Exception:
            pass
        try:
            L['officer_say']('Radar','contact',{'type': ui.get('type'), 'bearing': 315, 'range_nm': ui.get('range_nm'), 'speed': ui.get('speed')})
        except Exception:
            pass
        payload = {"ok": True, "added": ui, "count": len(L['RADAR'].contacts)}
        L['record_flight']({"route": route, "method": "GET", "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radar/force_spawn error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({"route": route, "method": "GET", "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.get("/radar/force_spawn_hostile")
def radar_force_spawn_hostile():
    L = _L(); t0 = time.time(); route = "/radar/force_spawn_hostile"
    try:
        if not _hostiles_allowed(L['RUNTIME']):
            payload = {"ok": False, "error": "hostile_spawns_disabled"}
            L['record_flight']({"route": route, "method": "GET", "status": 403,
                              "duration_ms": int((time.time()-t0)*1000),
                              "request": {}, "response": payload})
            return jsonify(payload), 403
        st = L['ENG'].public_state() if hasattr(L['ENG'], "public_state") else {}
        own_x, own_y = L['get_own_xy'](st)
        c = L['RADAR'].force_spawn(own_x, own_y, "Hostile", 315.0, random.uniform(8.0, 14.0))
        ui = L['contact_to_ui'](c, (own_x, own_y))
        try:
            ui['cell'] = L['world_to_cell'](c.x, c.y)
        except Exception:
            pass
        try:
            L['officer_say']('Radar','contact',{'type': ui.get('type'), 'bearing': 315, 'range_nm': ui.get('range_nm'), 'speed': ui.get('speed')})
        except Exception:
            pass
        payload = {"ok": True, "added": ui, "count": len(L['RADAR'].contacts)}
        L['record_flight']({"route": route, "method": "GET", "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radar/force_spawn_hostile error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({"route": route, "method": "GET", "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.get("/radar/force_spawn_friendly")
def radar_force_spawn_friendly():
    L = _L(); t0 = time.time(); route = "/radar/force_spawn_friendly"
    try:
        st = L['ENG'].public_state() if hasattr(L['ENG'], "public_state") else {}
        own_x, own_y = L['get_own_xy'](st)
        c = L['RADAR'].force_spawn(own_x, own_y, "Friendly", 315.0, random.uniform(8.0, 14.0))
        ui = L['contact_to_ui'](c, (own_x, own_y))
        try:
            ui['cell'] = L['world_to_cell'](c.x, c.y)
        except Exception:
            pass
        try:
            L['officer_say']('Radar','contact',{'type': ui.get('type'), 'bearing': 315, 'range_nm': ui.get('range_nm'), 'speed': ui.get('speed')})
        except Exception:
            pass
        payload = {"ok": True, "added": ui, "count": len(L['RADAR'].contacts)}
        L['record_flight']({"route": route, "method": "GET", "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radar/force_spawn_friendly error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({"route": route, "method": "GET", "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.get("/radar/force_spawn_near")
def radar_force_spawn_near():
    L = _L(); t0 = time.time(); route = "/radar/force_spawn_near"
    try:
        if not _hostiles_allowed(L['RUNTIME']):
            payload = {"ok": False, "error": "hostile_spawns_disabled"}
            L['record_flight']({"route": route, "method": "GET", "status": 403,
                              "duration_ms": int((time.time()-t0)*1000),
                              "request": {}, "response": payload})
            return jsonify(payload), 403
        st = L['ENG'].public_state() if hasattr(L['ENG'], "public_state") else {}
        own_x, own_y = L['get_own_xy'](st)
        klass = (request.args.get('class') or 'Aircraft').title()
        try:
            rng = float(request.args.get('range') or (2.5 if klass == 'Aircraft' else 4.0))
        except Exception:
            rng = 2.5 if klass == 'Aircraft' else 4.0
        try:
            bearing_deg = float(request.args.get('bearing') or 315.0)
        except Exception:
            bearing_deg = 315.0
        rad = math.radians(bearing_deg)
        dx = math.sin(rad) * rng
        dy = -math.cos(rad) * rng
        x = max(0.0, min(float(WORLD_N), own_x + dx))
        y = max(0.0, min(float(WORLD_N), own_y + dy))
        try:
            data = L['_load_json'](L['CONTACTS_PATH'], [])
            items = data.get('items') if isinstance(data, dict) else data
            pool = [it for it in (items or []) if isinstance(it, dict) and str(it.get('allegiance','')).title()== 'Hostile' and str(it.get('type','')).title()==klass]
        except Exception:
            pool = []
        if not pool:
            name, speed = ('A-4 Skyhawk', 385.0) if klass=='Aircraft' else ('ARA General Belgrano', 22.0)
        else:
            it = random.choice(pool)
            name = str(it.get('name','Contact'))
            try:
                speed = float(it.get('speed_kts', 0.0))
            except Exception:
                speed = 0.0
        next_id = getattr(L['RADAR'], "_next_id", len(L['RADAR'].contacts) + 1)
        c = Contact(
            id=next_id,
            name=name,
            allegiance="Hostile",
            x=float(x),
            y=float(y),
            course_deg=(bearing_deg + 180.0) % 360.0,
            speed_kts=float(speed),
            threat="high" if klass=='Aircraft' else "medium",
            meta={"spawn": {"bearing_deg": round(bearing_deg,1), "range_nm": round(rng,2), "surprise": False, "forced": True, "class": klass}}
        )
        try:
            L['RADAR']._next_id = next_id + 1  # type: ignore[attr-defined]
        except Exception:
            pass
        L['RADAR'].contacts.append(c)
        ui = L['contact_to_ui'](c, (own_x, own_y))
        try:
            ui['cell'] = L['world_to_cell'](c.x, c.y)
        except Exception:
            pass
        try:
            L['RADAR'].rec.log("radar.force_spawn", {"name": name, "class": klass, "bearing_deg": round(bearing_deg,1), "range_nm": round(rng,2),
                            "target_world_xy": [round(x,2), round(y,2)], "ship_world_xy": [round(own_x,2), round(own_y,2)]})
        except Exception:
            pass
        try:
            L['officer_say']('Radar','contact',{'type': klass, 'bearing': round((bearing_deg)%360), 'range_nm': ui.get('range_nm'), 'speed': ui.get('speed')})
        except Exception:
            pass
        payload = {"ok": True, "added": ui, "count": len(L['RADAR'].contacts)}
        L['record_flight']({"route": route, "method": "GET", "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {"class": klass, "range": rng, "bearing": bearing_deg}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radar/force_spawn_near error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({"route": route, "method": "GET", "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.get("/radar/spawn_by_name")
def radar_spawn_by_name():
    """Debug: Spawn a specific hostile by exact name with catalog capabilities."""
    L = _L(); t0 = time.time(); route = "/radar/spawn_by_name"
    try:
        name = (request.args.get('name') or '').strip()
        if not name:
            payload = {"ok": False, "error": "missing name"}
            L['record_flight']({"route": route, "method": "GET", "status": 400,
                               "duration_ms": int((time.time()-t0)*1000),
                               "request": {}, "response": payload})
            return jsonify(payload), 400
        try:
            rng = float(request.args.get('range') or 18.0)
        except Exception:
            rng = 18.0
        try:
            bearing_deg = float(request.args.get('bearing') or 315.0)
        except Exception:
            bearing_deg = 315.0
        st = L['ENG'].public_state() if hasattr(L['ENG'], "public_state") else {}
        own_x, own_y = L['radar_xy_from_state'](st)
        # reuse helper from webdash to maintain identical behavior
        from ..webdash import _spawn_hostile_by_name as _spawn_by_name
        c = _spawn_by_name(own_x, own_y, name=name, range_nm=rng, bearing_deg=bearing_deg)
        ui = L['contact_to_ui'](c, (own_x, own_y))
        try:
            ui['cell'] = L['world_to_cell'](c.x, c.y)
        except Exception:
            pass
        payload = {"ok": True, "added": ui, "count": len(L['RADAR'].contacts)}
        L['record_flight']({"route": route, "method": "GET", "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {"name": name, "range": rng, "bearing": bearing_deg}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radar/spawn_by_name error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({"route": route, "method": "GET", "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.get("/radar/reload_catalog")
def radar_reload_catalog():
    L = _L(); t0 = time.time(); route = "/radar/reload_catalog"
    try:
        L['RADAR'].catalog.reload()
        h, f = L['RADAR'].catalog.counts()
        payload = {"ok": True, "counts": {"hostiles": h, "friendlies": f}}
        L['record_flight']({"route": route, "method": "GET", "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radar/reload_catalog error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({"route": route, "method": "GET", "status": 500,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload), 500
