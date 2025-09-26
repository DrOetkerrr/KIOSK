from __future__ import annotations

import logging
from flask import Blueprint, jsonify

bp = Blueprint("nav", __name__)


def _lazy():
    from ..webdash import (
        ENG, ship_cell_from_state,
        radar_xy_from_state, cell_to_world, clamp,
        BOARD_N, voice_emit, record_flight, record_event, CONVOY
    )
    from ..engine_adapter import world_to_cell
    import math as _m  # local alias
    return locals()


@bp.get('/nav/hermes/close_in')
def nav_hermes_close_in():
    L = _lazy(); t0 = __import__('time').time(); route = '/nav/hermes/close_in'
    try:
        st = L['ENG'].public_state() if hasattr(L['ENG'], 'public_state') else {}
        ship = (st or {}).get('ship', {}) if isinstance(st, dict) else {}
        ox, oy = L['radar_xy_from_state'](st)
        course = float(ship.get('heading', 0.0) or 0.0)
        rng_nm = None
        brg = None
        hermes_cell = 'L17'
        distance_cells = None
        convoy = L.get('CONVOY')
        changed = True
        if convoy is not None:
            distance_cells, changed = convoy.adjust_distance('hermes', -1.0)
            hx, hy = convoy.escort_world_position('hermes', float(ox), float(oy), course)
            hermes_cell = L['world_to_cell'](hx, hy)
            dx = hx - float(ox)
            dy = hy - float(oy)
            rng_nm = (dx*dx + dy*dy) ** 0.5
            brg = int(round((L['_m'].degrees(L['_m'].atan2(dx, -dy)) % 360.0)))
        else:
            own_cell = L['ship_cell_from_state'](st)
            j = 0
            while j < len(own_cell) and own_cell[j].isalpha():
                j += 1
            cletters = own_cell[:j] or 'A'; rstr = own_cell[j:] or '1'
            ri = int(rstr)
            adj_row = int(L['clamp'](ri - 2, 1, L['BOARD_N']))
            col_label = cletters or 'A'
            hermes_cell = f"{col_label}{adj_row}"
            hx, hy = L['cell_to_world'](hermes_cell)
            dx = hx - float(ox)
            dy = hy - float(oy)
            rng_nm = (dx*dx + dy*dy) ** 0.5
            brg = int(round((L['_m'].degrees(L['_m'].atan2(dx, -dy)) % 360.0)))
            distance_cells = 2.0

        rec_hdg = brg if brg is not None else 0
        try:
            if rng_nm is not None and brg is not None:
                L['voice_emit']('nav.hermes.close_in.request', {'ref_brg': brg, 'ref_rng': round(rng_nm,1), 'rec_hdg': rec_hdg}, fallback=f'Recommend closing on Hermes: bearing {brg}°, range {rng_nm:.1f} nm. New course {rec_hdg}°.', role='Navigation')
        except Exception:
            pass
        try:
            L['record_event']('nav.hermes.close_in', {'cell': hermes_cell, 'distance_cells': round(distance_cells,1) if distance_cells is not None else None})
        except Exception:
            pass
        msg = None
        if convoy is not None and not changed:
            msg = 'Hermes already at minimum separation.'
        payload = {"ok": True, "bearing": brg or 0, "range_nm": round(rng_nm,1) if rng_nm is not None else None, "recommend_hdg": rec_hdg, "distance_cells": round(distance_cells,1) if distance_cells is not None else None}
        if msg:
            payload['message'] = msg
        L['record_flight']({"route": route, "method": "GET", "status": 200, "duration_ms": int((__import__('time').time()-t0)*1000), "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/nav/hermes/close_in error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get('/nav/hermes/stand_off')
def nav_hermes_stand_off():
    L = _lazy(); t0 = __import__('time').time(); route = '/nav/hermes/stand_off'
    try:
        st = L['ENG'].public_state() if hasattr(L['ENG'], 'public_state') else {}
        ship = (st or {}).get('ship', {}) if isinstance(st, dict) else {}
        ox, oy = L['radar_xy_from_state'](st)
        course = float(ship.get('heading', 0.0) or 0.0)
        convoy = L.get('CONVOY')
        distance_cells = None
        changed = True
        if convoy is not None:
            distance_cells, changed = convoy.adjust_distance('hermes', +1.0)
            hx, hy = convoy.escort_world_position('hermes', float(ox), float(oy), course)
            hermes_cell = L['world_to_cell'](hx, hy)
            dx = hx - float(ox)
            dy = hy - float(oy)
            rng = (dx*dx + dy*dy) ** 0.5
            brg = int(round((L['_m'].degrees(L['_m'].atan2(dx, -dy)) % 360.0)))
        else:
            own_cell = L['ship_cell_from_state'](st)
            j = 0
            while j < len(own_cell) and own_cell[j].isalpha():
                j += 1
            cletters = own_cell[:j] or 'A'; rstr = own_cell[j:] or '1'
            ri = int(rstr)
            adj_row = int(L['clamp'](ri - 3, 1, L['BOARD_N']))
            col_label = cletters or 'A'
            hermes_cell = f"{col_label}{adj_row}"
            hx, hy = L['cell_to_world'](hermes_cell)
            dx = hx - float(ox)
            dy = hy - float(oy)
            rng = (dx*dx + dy*dy) ** 0.5
            brg = int(round((L['_m'].degrees(L['_m'].atan2(dx, -dy)) % 360.0)))
            distance_cells = 3.0
        standoff = round(distance_cells,1) if distance_cells is not None else 3
        try:
            L['voice_emit']('nav.hermes.stand_off.request', {'ref_brg': brg, 'ref_rng': round(rng,1), 'standoff_nm': standoff}, fallback=f'Recommend Hermes stand-off {standoff} nm; current bearing {brg}°, range {rng:.1f} nm.', role='Navigation')
        except Exception:
            pass
        try:
            L['record_event']('nav.hermes.stand_off', {'cell': hermes_cell, 'distance_cells': round(distance_cells,1) if distance_cells is not None else None})
        except Exception:
            pass
        msg = None
        if convoy is not None and not changed:
            msg = 'Hermes already at maximum separation.'
        payload = {"ok": True, "bearing": brg, "range_nm": round(rng,1), "standoff_nm": standoff, "distance_cells": round(distance_cells,1) if distance_cells is not None else None}
        if msg:
            payload['message'] = msg
        L['record_flight']({"route": route, "method": "GET", "status": 200, "duration_ms": int((__import__('time').time()-t0)*1000), "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/nav/hermes/stand_off error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500
