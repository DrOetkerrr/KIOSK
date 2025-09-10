from __future__ import annotations

import logging
import math
import random
import time
from flask import Blueprint, jsonify, request

bp = Blueprint("radar", __name__)


def _lazy():
    # Late imports from webdash to avoid circular imports
    from ..webdash import (
        ENG, RADAR, Contact,
        record_flight, officer_say,
        world_to_cell, contact_to_ui, get_own_xy,
        radar_xy_from_state,
        _load_json, DATA_DIR, HOSTILES, WORLD_N
    )
    return locals()


@bp.get("/debug/cellmap")
def debug_cellmap():
    L = _lazy()
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
    L = _lazy(); t0 = time.time(); route = "/radar/force_spawn"
    try:
        st = L['ENG'].public_state() if hasattr(L['ENG'], "public_state") else {}
        own_x, own_y = L['radar_xy_from_state'](st)
        c = L['RADAR'].force_spawn(own_x, own_y, "Hostile", bearing_deg=315.0, range_nm=random.uniform(8.0, 14.0))
        ui = L['contact_to_ui'](c, (own_x, own_y))
        try:
            ui['cell'] = L['world_to_cell'](c.x, c.y)
        except Exception:
            pass
        try:
            L['officer_say']('Radar','contact',{'type': ui.get('type'), 'bearing': round((315.0)%360), 'range_nm': ui.get('range_nm'), 'speed': ui.get('speed')})
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
    L = _lazy(); t0 = time.time(); route = "/radar/force_spawn_hostile"
    try:
        st = L['ENG'].public_state() if hasattr(L['ENG'], "public_state") else {}
        own_x, own_y = L['get_own_xy'](st)
        c = L['RADAR'].force_spawn(own_x, own_y, "Hostile", 315.0, random.uniform(8.0, 14.0))
        ui = L['contact_to_ui'](c, (own_x, own_y))
        try:
            ui['cell'] = L['world_to_cell'](c.x, c.y)
        except Exception:
            pass
        try:
            L['officer_say']('Radar','contact',{'type': ui.get('type'), 'bearing': round((315.0)%360), 'range_nm': ui.get('range_nm'), 'speed': ui.get('speed')})
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
    L = _lazy(); t0 = time.time(); route = "/radar/force_spawn_friendly"
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
            L['officer_say']('Radar','contact',{'type': ui.get('type'), 'bearing': round((315.0)%360), 'range_nm': ui.get('range_nm'), 'speed': ui.get('speed')})
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
    """Spawn a forced Hostile contact nearby for weapons testing."""
    L = _lazy(); t0 = time.time(); route = "/radar/force_spawn_near"
    try:
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
        x = max(0.0, min(float(L['WORLD_N']), own_x + dx))
        y = max(0.0, min(float(L['WORLD_N']), own_y + dy))
        try:
            data = L['_load_json'](L['DATA_DIR'] / 'contacts.json', {})
            items = data.get("items") if isinstance(data, dict) else data
        except Exception:
            items = []
        cand = [it for it in (items or []) if isinstance(it, dict) and str(it.get('class','')).title() == klass and str(it.get('allegiance',''))=='Hostile']
        name = str(random.choice(cand).get('name')) if cand else (klass if klass in ('Aircraft','Ship') else 'Hostile')
        speed = float(next((it.get('speed_kts', 300.0) for it in (cand or []) if str(it.get('name',''))==name), 300.0))
        next_id = getattr(L['RADAR'], "_next_id", len(L['RADAR'].contacts) + 1)
        c = L['Contact'](id=int(next_id), name=str(name), allegiance="Hostile", x=float(x), y=float(y), course_deg=(float(bearing_deg)+180.0)%360.0, speed_kts=speed, threat="high")
        try:
            L['RADAR']._next_id = int(next_id) + 1  # type: ignore[attr-defined]
        except Exception:
            pass
        L['RADAR'].contacts.append(c)
        ui = L['contact_to_ui'](c, (own_x, own_y))
        try:
            ui['cell'] = L['world_to_cell'](c.x, c.y)
        except Exception:
            pass
        try:
            L['officer_say']('Radar','contact',{'type': klass, 'bearing': round((bearing_deg)%360), 'range_nm': ui.get('range_nm'), 'speed': ui.get('speed')})
        except Exception:
            pass
        payload = {"ok": True, "added": ui, "count": len(L['RADAR'].contacts)}
        L['record_flight']({"route": route, "method": "GET", "status": 200,
                          "duration_ms": int((time.time()-t0)*1000),
                          "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radar/force_spawn_near error: %s", e)
        payload = {"ok": False, "error": str(e)}
        L['record_flight']({"route": route, "method": "GET", "status": 500,
                          "duration_ms": int((time.time()-t0)*1000),
                          "request": {}, "response": payload})
        return jsonify(payload), 500


@bp.get("/radar/reload_catalog")
def radar_reload_catalog():
    L = _lazy(); t0 = time.time(); route = "/radar/reload_catalog"
    try:
        try:
            L['RADAR'].catalog.reload()
        except Exception:
            pass
        payload = {"ok": True}
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

