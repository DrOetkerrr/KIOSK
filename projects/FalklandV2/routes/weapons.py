from __future__ import annotations

import time
import logging
from flask import Blueprint, jsonify, request

bp = Blueprint("weapons", __name__)


def _lazy():
    from .. import webdash as _wd  # type: ignore
    from ..webdash import (
        WEAP_CATALOG, _load_json, _save_json, ARMING_PATH,
        RADAR, PENDING_EVENTS, STATE_LOCK, AUDIO_STATE,
        compute_in_range, get_own_xy, contact_to_ui, save_ammo,
        TARGET_CLASS_BY_NAME, ENG,
        load_ammo, load_arming, voice_emit, officer_say,
        record_event
    )
    try:
        _sound_key_for_weapon = getattr(_wd, '_sound_key_for_weapon')
    except AttributeError:
        from ..subsystems.webcore import _sound_key_for_weapon  # type: ignore
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
        try:
            if state == 'Armed':
                L['record_event']('weapon.arm', {'name': name})
            else:
                L['record_event']('weapon.safe', {'name': name})
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
        # Load & update ammo using canonical store
        ammo = L['load_ammo']() if 'load_ammo' in L else {}
        ammo.setdefault(name, 0)
        # Helper: read/update cooldown in arming state file
        def _get_cooldown_until(nm: str) -> float:
            try:
                raw = L['_load_json'](L['ARMING_PATH'], {})
                rec = (raw or {}).get(nm) if isinstance(raw, dict) else None
                if isinstance(rec, dict):
                    return float(rec.get('cooldown_until', 0.0) or 0.0)
            except Exception:
                pass
            return 0.0
        def _set_cooldown_until(nm: str, until: float) -> None:
            try:
                raw = L['_load_json'](L['ARMING_PATH'], {})
                if not isinstance(raw, dict): raw = {}
                rec = raw.get(nm)
                if not isinstance(rec, dict): rec = {'armed': False, 'arming_until': 0}
                rec['cooldown_until'] = float(until)
                raw[nm] = rec
                L['_save_json'](L['ARMING_PATH'], raw)
            except Exception:
                pass
        def _cooldown_seconds_by_class(nm: str) -> float:
            try:
                wrec = next((w for w in L['WEAP_CATALOG'] if w.get('name') == nm), None)
                cls = (wrec or {}).get('class', 'Other')
                if (wrec or {}).get('cooldown_s') is not None:
                    return float(wrec['cooldown_s'])
                if cls == 'Missile':
                    return 8.0
                if cls == 'SAM':
                    return 6.0
                if cls == 'Decoy':
                    return 5.0
                # Guns & other
                return 2.0
            except Exception:
                return 3.0
        # Enforce cooldown
        now = time.time()
        if _get_cooldown_until(name) > now:
            return jsonify({'ok': False, 'error': 'COOLDOWN'}), 400
        # Enforce ARMED state for both test and real
        arming_state = L['load_arming']() if 'load_arming' in L else {}
        if arming_state.get(name) != 'Armed':
            return jsonify({'ok': False, 'error': 'NOT_ARMED'}), 400
        if mode == 'test':
            if int(ammo.get(name, 0)) <= 0:
                return jsonify({'ok': False, 'error': 'NO_AMMO'}), 400
            # Consume ammo in test as a live drill (no range gating)
            try:
                dec = 50 if name in ("20mm Oerlikon", "20mm GAM-BO1 (twin)") else 1
            except Exception:
                dec = 1
            ammo[name] = max(0, int(ammo.get(name, 0)) - int(dec))
            L['save_ammo'](ammo)
            try:
                L['RADAR'].rec.log('weapons.fire', {'name': name, 'mode': 'test', 'ammo': ammo[name]})
            except Exception:
                pass
            # Stamp audio launch so frontend plays the sound
            try:
                with L['STATE_LOCK']:
                    L['AUDIO_STATE']['last_launch'] = {'weapon': L['_sound_key_for_weapon'](name), 'ts': time.time()}
            except Exception:
                pass
            # Radio cue (Weapons) — best-effort; ensures at least beeps even without TTS
            try:
                L['voice_emit']('weapons.launch', {'weapon': name}, fallback=f"{name} away.", role='Weapons')
            except Exception:
                try:
                    L['officer_say']('Weapons', f"{name} fired (test).", {})
                except Exception:
                    pass
            # Apply cooldown
            _set_cooldown_until(name, now + _cooldown_seconds_by_class(name))
            try:
                L['record_event']('weapon.fire', {'name': name, 'mode': 'test'})
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
        range_ok = True
        if not L['compute_in_range'](name, primary):
            range_ok = False
            try:
                rng = float(primary.get('range_nm', 0.0) or 0.0)
            except Exception:
                rng = 0.0
            relax = False
            try:
                wrec = next((w for w in L['WEAP_CATALOG'] if w.get('name') == name), None)
                if wrec:
                    try:
                        mn = float(wrec.get('min_nm', 0.0) or 0.0)
                    except Exception:
                        mn = 0.0
                    try:
                        mx = float(wrec.get('max_nm', 0.0) or 0.0)
                    except Exception:
                        mx = 0.0
                    buffer = max(1.5, 0.12 * max(mx, 1.0))
                    if mn - buffer <= rng <= mx + buffer:
                        relax = True
            except Exception:
                relax = False
            if not relax:
                logging.warning("weapons.fire OUT_OF_RANGE forcing fire name=%s range=%.2f primary=%s", name, rng, primary)
            primary['range_nm'] = rng
        # consume ammo
        try:
            dec = 50 if name in ("20mm Oerlikon", "20mm GAM-BO1 (twin)") else 1
        except Exception:
            dec = 1
        ammo[name] = max(0, int(ammo.get(name, 0)) - int(dec))
        L['save_ammo'](ammo)
        try:
            L['RADAR'].rec.log('weapons.fire', {'name': name, 'mode': 'real', 'ammo': ammo[name], 'range_ok': range_ok})
            L['RADAR'].rec.log('radio.msg', {'kind': 'FIRE', 'text': f'{name} fired'})
        except Exception:
            pass
        try:
            with L['STATE_LOCK']:
                L['AUDIO_STATE']['last_launch'] = {'weapon': L['_sound_key_for_weapon'](name), 'ts': time.time()}
        except Exception:
            pass
        # Radio cue (Weapons)
        try:
            L['voice_emit']('weapons.launch', {'weapon': name}, fallback=f"{name} away.", role='Weapons')
        except Exception:
            try:
                L['officer_say']('Weapons', f"{name} fired.", {})
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
            tcell = primary.get('cell')
            from ..webdash import _schedule_shot_result  # late import
            _schedule_shot_result(name, tid, tname, tclass, rng, tcell)
        except Exception:
            pass
        # Apply cooldown
        _set_cooldown_until(name, now + _cooldown_seconds_by_class(name))
        try:
            L['record_event']('weapon.fire', {
                'name': name,
                'mode': 'real',
                'target': primary.get('name'),
                'target_id': primary.get('id'),
                'range_ok': range_ok,
                'range_nm': primary.get('range_nm')
            })
        except Exception:
            pass
        return jsonify({'ok': True, 'result': 'FIRED', 'name': name, 'ammo': ammo[name], 'range_ok': range_ok})
    except Exception as e:
        logging.exception("/weapons/fire error: %s", e)
        return jsonify({'ok': False, 'error': str(e)}), 500
