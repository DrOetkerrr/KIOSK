from __future__ import annotations

import time
import logging
from flask import Blueprint, jsonify, request

bp = Blueprint("weapons", __name__)


def _lazy():
    from ..webdash import (
        WEAP_CATALOG, _load_json, _save_json, ARMING_PATH,
        RADAR, PENDING_EVENTS, STATE_LOCK, AUDIO_STATE,
        compute_in_range, get_own_xy, contact_to_ui, save_ammo,
        TARGET_CLASS_BY_NAME, _sound_key_for_weapon, ENG
    )
    return locals()


@bp.get("/weapons/catalog")
def weapons_catalog():
    L = _lazy(); t0 = time.time(); route = "/weapons/catalog"
    try:
        payload = {'ok': True, 'catalog': L['WEAP_CATALOG']}
        L['RADAR'].rec.log('weapons.catalog', {}) if hasattr(L['RADAR'], 'rec') else None
        return jsonify(payload)
    except Exception as e:
        logging.exception("/weapons/catalog error: %s", e)
        return jsonify({'ok': False, 'error': str(e)}), 500


def _arg_or_json(req, key: str, default: str | None = None) -> str | None:
    v = req.args.get(key)
    if v is None and req.is_json:
        try:
            body = req.get_json(silent=True) or {}
            v = body.get(key)
        except Exception:
            v = None
    return v if v is not None else default


@bp.post("/weapons/arm")
def weapons_arm():
    L = _lazy(); t0 = time.time(); route = "/weapons/arm"
    try:
        name = _arg_or_json(request, 'name', '')
        state = _arg_or_json(request, 'state', '')
        if not name or state not in ("Armed","Safe"):
            return jsonify({'ok': False, 'error': 'bad params'}), 400
        raw = L['_load_json'](L['ARMING_PATH'], {})
        if not isinstance(raw, dict):
            raw = {}
        if state == 'Armed':
            rec = {'armed': False, 'arming_until': time.time() + 5.0}
            disp_state = 'Arming'
        else:
            rec = {'armed': False, 'arming_until': 0}
            disp_state = 'Safe'
        raw[name] = rec
        L['_save_json'](L['ARMING_PATH'], raw)
        try:
            L['RADAR'].rec.log('weapons.arm', {'name': name, 'state': state})
        except Exception:
            pass
        if state == 'Armed':
            try:
                L['PENDING_EVENTS'].append({'due': time.time()+5.0, 'kind': 'arming_ready', 'weapon': name})
            except Exception:
                pass
        return jsonify({'ok': True, 'name': name, 'state': disp_state})
    except Exception as e:
        logging.exception("/weapons/arm error: %s", e)
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.post("/weapons/fire")
def weapons_fire():
    L = _lazy(); t0 = time.time(); route = "/weapons/fire"
    try:
        name = _arg_or_json(request, 'name', '')
        mode = (_arg_or_json(request, 'mode', 'real') or 'real').lower()
        if not name or mode not in ('real','test'):
            return jsonify({'ok': False, 'error': 'bad params'}), 400
        # Load & update ammo
        def _ammo():
            try:
                d = L['_load_json'](L['ARMING_PATH'], {})
                return d if isinstance(d, dict) else {}
            except Exception:
                return {}
        raw = _ammo()
        ammo = raw.get('weapons') if isinstance(raw, dict) else None
        if not isinstance(ammo, dict):
            ammo = raw
        if not isinstance(ammo, dict):
            ammo = {}
        ammo.setdefault(name, 0)
        if mode == 'test':
            if int(ammo.get(name, 0)) <= 0:
                return jsonify({'ok': False, 'error': 'NO_AMMO'}), 400
            try:
                if mode == 'test':
                    L['RADAR'].rec.log('weapons.fire', {'name': name, 'mode': 'test', 'ammo': ammo[name]})
            except Exception:
                pass
            try:
                with L['STATE_LOCK']:
                    L['AUDIO_STATE']['last_launch'] = {'weapon': L['_sound_key_for_weapon'](name), 'ts': time.time()}
            except Exception:
                pass
            return jsonify({'ok': True, 'result': 'TEST', 'name': name, 'ammo': ammo[name]})

        # Real fire path
        if int(ammo.get(name, 0)) <= 0:
            return jsonify({'ok': False, 'error': 'NO_AMMO'}), 400
        # Compute range gate with current primary
        primary = None
        try:
            st = (L['ENG'].public_state() if hasattr(L['ENG'], 'public_state') else {})
            own_x, own_y = L['get_own_xy'](st)
            pid = getattr(L['RADAR'], 'priority_id', None)
            if pid is not None:
                for c in L['RADAR'].contacts:
                    if int(getattr(c, 'id', -1)) == int(pid):
                        primary = L['contact_to_ui'](c, (own_x, own_y))
                        break
        except Exception:
            primary = None
        if not primary:
            return jsonify({'ok': False, 'error': 'NO_PRIMARY'}), 400
        if not L['compute_in_range'](name, primary):
            return jsonify({'ok': False, 'error': 'OUT_OF_RANGE'}), 400
        # consume ammo
        try:
            dec = 50 if name in ("20mm Oerlikon", "20mm GAM-BO1 (twin)") else 1
        except Exception:
            dec = 1
        ammo[name] = max(0, int(ammo.get(name, 0)) - int(dec))
        L['save_ammo'](ammo)
        try:
            L['RADAR'].rec.log('weapons.fire', {'name': name, 'mode': 'real', 'ammo': ammo[name]})
            L['RADAR'].rec.log('radio.msg', {'kind': 'FIRE', 'text': f'{name} fired'})
        except Exception:
            pass
        try:
            with L['STATE_LOCK']:
                L['AUDIO_STATE']['last_launch'] = {'weapon': L['_sound_key_for_weapon'](name), 'ts': time.time()}
        except Exception:
            pass
        # Chaff special case
        try:
            if name.lower().find('chaff') >= 0:
                with L['STATE_LOCK']:
                    from ..webdash import DEFENSE_STATE  # late import
                    DEFENSE_STATE['chaff_until'] = time.time() + 60.0
        except Exception:
            pass
        # Schedule result
        try:
            tid = int(primary.get('id'))
            rng = float(primary.get('range_nm', 0.0))
            tname = str(primary.get('name', 'Target'))
            tclass = L['TARGET_CLASS_BY_NAME'].get(tname) or 'Ship'
            from ..webdash import _schedule_shot_result  # late import
            _schedule_shot_result(name, tid, tname, tclass, rng)
        except Exception:
            pass
        return jsonify({'ok': True, 'result': 'FIRED', 'name': name, 'ammo': ammo[name]})
    except Exception as e:
        logging.exception("/weapons/fire error: %s", e)
        return jsonify({'ok': False, 'error': str(e)}), 500

