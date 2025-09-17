from __future__ import annotations

import logging
from flask import Blueprint, jsonify

bp = Blueprint("nav", __name__)


def _lazy():
    from ..webdash import (
        ENG, ship_cell_from_state, _load_json, DATA_DIR,
        radar_xy_from_state, board_to_cell, cell_to_world, clamp,
        BOARD_N, voice_emit, record_flight, record_event,
    )
    import math as _m  # local alias
    return locals()


@bp.get('/nav/hermes/close_in')
def nav_hermes_close_in():
    L = _lazy(); t0 = __import__('time').time(); route = '/nav/hermes/close_in'
    try:
        st = L['ENG'].public_state() if hasattr(L['ENG'], 'public_state') else {}
        own_cell = L['ship_cell_from_state'](st)
        j = 0
        while j < len(own_cell) and own_cell[j].isalpha():
            j += 1
        cletters = own_cell[:j] or 'A'; rstr = own_cell[j:] or '1'
        ci = 0
        for ch in cletters: ci = ci*26 + (ord(ch)-ord('A')+1)
        ri = int(rstr)
        convoy = L['_load_json'](L['DATA_DIR'] / 'convoy.json', {})
        escorts = convoy.get('escorts', []) if isinstance(convoy, dict) else []
        hermes = next((e for e in escorts if str(e.get('name','')).lower().find('hermes')>=0), None)
        if hermes:
            dx_cells, dy_cells = int(hermes.get('offset_cells',[-2,3])[0]), int(hermes.get('offset_cells',[-2,3])[1])
            hermes_cell = L['board_to_cell'](int(L['clamp'](ri+dy_cells,1,L['BOARD_N'])), int(L['clamp'](ci+dx_cells,1,L['BOARD_N'])))
        else:
            hermes_cell = 'L17'
        hx, hy = L['cell_to_world'](hermes_cell)
        ox, oy = L['radar_xy_from_state'](st)
        dx, dy = hx-ox, hy-oy
        rng = (dx*dx+dy*dy)**0.5
        brg = int(round((L['_m'].degrees(L['_m'].atan2(dx, -dy)) % 360.0)))
        rec_hdg = brg
        try:
            L['voice_emit']('nav.hermes.close_in.request', {'ref_brg': brg, 'ref_rng': round(rng,1), 'rec_hdg': rec_hdg}, fallback=f'Recommend closing on Hermes: bearing {brg}°, range {rng:.1f} nm. New course {rec_hdg}°.', role='Navigation')
        except Exception:
            pass
        try:
            L['record_event']('nav.hermes.close_in', {'cell': hermes_cell})
        except Exception:
            pass
        payload = {"ok": True, "bearing": brg, "range_nm": round(rng,1), "recommend_hdg": rec_hdg}
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
        own_cell = L['ship_cell_from_state'](st)
        j = 0
        while j < len(own_cell) and own_cell[j].isalpha():
            j += 1
        cletters = own_cell[:j] or 'A'; rstr = own_cell[j:] or '1'
        ci = 0
        for ch in cletters: ci = ci*26 + (ord(ch)-ord('A')+1)
        ri = int(rstr)
        convoy = L['_load_json'](L['DATA_DIR'] / 'convoy.json', {})
        escorts = convoy.get('escorts', []) if isinstance(convoy, dict) else []
        hermes = next((e for e in escorts if str(e.get('name','')).lower().find('hermes')>=0), None)
        if hermes:
            dx_cells, dy_cells = int(hermes.get('offset_cells',[-2,3])[0]), int(hermes.get('offset_cells',[-2,3])[1])
            hermes_cell = L['board_to_cell'](int(L['clamp'](ri+dy_cells,1,L['BOARD_N'])), int(L['clamp'](ci+dx_cells,1,L['BOARD_N'])))
        else:
            hermes_cell = 'L17'
        hx, hy = L['cell_to_world'](hermes_cell)
        ox, oy = L['radar_xy_from_state'](st)
        dx, dy = hx-ox, hy-oy
        rng = (dx*dx+dy*dy)**0.5
        brg = int(round((L['_m'].degrees(L['_m'].atan2(dx, -dy)) % 360.0)))
        standoff = 3
        try:
            L['voice_emit']('nav.hermes.stand_off.request', {'ref_brg': brg, 'ref_rng': round(rng,1), 'standoff_nm': standoff}, fallback=f'Recommend Hermes stand-off {standoff} nm; current bearing {brg}°, range {rng:.1f} nm.', role='Navigation')
        except Exception:
            pass
        try:
            L['record_event']('nav.hermes.stand_off', {'cell': hermes_cell})
        except Exception:
            pass
        payload = {"ok": True, "bearing": brg, "range_nm": round(rng,1), "standoff_nm": standoff}
        L['record_flight']({"route": route, "method": "GET", "status": 200, "duration_ms": int((__import__('time').time()-t0)*1000), "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/nav/hermes/stand_off error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500
