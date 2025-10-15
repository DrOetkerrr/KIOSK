from __future__ import annotations

import time
import logging
import threading
from flask import Blueprint, jsonify, request

bp = Blueprint("weapons", __name__)


def _lazy():
    from .. import webdash as _wd  # type: ignore
    from ..webdash import (
        WEAP_CATALOG, _load_json, _save_json, ARMING_PATH,
        RADAR, PENDING_EVENTS, STATE_LOCK, AUDIO_STATE,
        compute_in_range, get_own_xy, contact_to_ui, save_ammo,
        TARGET_CLASS_BY_NAME, ENG,
        load_ammo, load_arming, save_arming, voice_emit, officer_say,
        record_event, EVENT_QUEUE
    )
    try:
        _sound_key_for_weapon = getattr(_wd, '_sound_key_for_weapon')
    except AttributeError:
        from ..subsystems.webcore import _sound_key_for_weapon  # type: ignore
    mark_weapon_arming = getattr(_wd, 'mark_weapon_arming', lambda *args, **kwargs: None)
    clear_weapon_arming = getattr(_wd, 'clear_weapon_arming', lambda *args, **kwargs: None)
    pending_arming_left = getattr(_wd, 'pending_arming_left', lambda *_args, **_kwargs: 0)
    get_weapon_state = getattr(_wd, 'get_weapon_state', lambda name: {'state': 'Safe', 'armed': False, 'cooldown_until': 0.0, 'arming_until': 0.0})
    update_weapon_state = getattr(_wd, 'update_weapon_state', lambda name, **kwargs: kwargs)
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
        arm_delay = 10.0 if name == 'Sea Dart SAM' else 5.0
        now = time.time()
        rec = L['get_weapon_state'](name)
        disp_state = state
        if state == 'Armed':
            if str(rec.get('state', '')).lower() == 'armed' and rec.get('armed'):
                disp_state = 'Armed'
            else:
                due_ts = now + arm_delay
                L['update_weapon_state'](name, state='Arming', armed=False, arming_until=due_ts)
                try:
                    pending = L['PENDING_EVENTS']
                    if isinstance(pending, list):
                        with L['STATE_LOCK']:
                            pending[:] = [ev for ev in pending if not (str(ev.get('kind')) == 'arming_ready' and str(ev.get('weapon')) == name)]
                            pending.append({'due': due_ts, 'kind': 'arming_ready', 'weapon': name})
                except Exception:
                    pass

                def _complete() -> None:
                    try:
                        L['update_weapon_state'](name, state='Armed', armed=True, arming_until=0.0)
                    except Exception:
                        pass
                    try:
                        L['clear_weapon_arming'](name, target_state='Armed', armed=True)
                    except Exception:
                        pass
                    try:
                        L['record_event']('weapon.reload.complete', {'name': name, 'source': 'arming'})
                    except Exception:
                        pass

                try:
                    timer = threading.Timer(max(0.1, arm_delay), _complete)
                    timer.daemon = True
                    timer.start()
                except Exception:
                    _complete()
                try:
                    L['mark_weapon_arming'](name, due_ts)
                except Exception:
                    pass
                disp_state = 'Arming'
                try:
                    L['record_event']('weapon.arm', {'name': name})
                except Exception:
                    pass
        else:
            L['update_weapon_state'](name, state='Safe', armed=False, arming_until=0.0)
            try:
                L['clear_weapon_arming'](name)
            except Exception:
                pass
            try:
                pending = L['PENDING_EVENTS']
                if isinstance(pending, list):
                    with L['STATE_LOCK']:
                        pending[:] = [ev for ev in pending if not (str(ev.get('kind')) == 'arming_ready' and str(ev.get('weapon')) == name)]
            except Exception:
                pass
            try:
                L['record_event']('weapon.safe', {'name': name})
            except Exception:
                pass
        try:
            L['RADAR'].rec.log('weapons.arm', {'name': name, 'state': disp_state})
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
        # Helper: read/update cooldown using canonical state helpers
        def _get_cooldown_until(nm: str) -> float:
            try:
                rec = L['get_weapon_state'](nm)
                return float(rec.get('cooldown_until', 0.0) or 0.0)
            except Exception:
                return 0.0
        def _set_cooldown_until(nm: str, until: float) -> None:
            try:
                L['update_weapon_state'](nm, cooldown_until=float(until))
            except Exception:
                pass
        def _cooldown_seconds_by_class(nm: str) -> float:
            try:
                if nm == 'Sea Dart SAM':
                    return 10.0
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
            try:
                L['record_event']('weapon.fire.rejected', {'name': name, 'reason': 'COOLDOWN', 'mode': mode})
            except Exception:
                pass
            return jsonify({'ok': False, 'error': 'COOLDOWN'}), 400
        # Enforce ARMED state for both test and real
        try:
            pending_left = L['pending_arming_left'](name)
        except Exception:
            pending_left = 0
        state_rec = L['get_weapon_state'](name)
        state_label = str(state_rec.get('state', 'Safe')).lower()
        arming_until = float(state_rec.get('arming_until', 0.0) or 0.0)
        armed_flag = bool(state_rec.get('armed', False))
        if state_label == 'arming' and arming_until <= now:
            state_rec = L['update_weapon_state'](name, state='Armed', armed=True, arming_until=0.0)
            state_label = 'armed'
            armed_flag = True
            pending_left = 0
        if pending_left > 0 or (state_label != 'armed' and not armed_flag):
            try:
                reason = 'NOT_ARMED'
                if pending_left > 0:
                    L['record_event']('weapon.fire.rejected', {'name': name, 'reason': reason, 'mode': mode, 'pending_s': pending_left})
                else:
                    L['record_event']('weapon.fire.rejected', {'name': name, 'reason': reason, 'mode': mode})
            except Exception:
                pass
            return jsonify({'ok': False, 'error': 'NOT_ARMED', 'state': state_rec.get('state'), 'pending_s': pending_left}), 400
        if mode == 'test':
            if int(ammo.get(name, 0)) <= 0:
                try:
                    L['record_event']('weapon.out_of_ammo', {'name': name, 'mode': 'test'})
                    L['record_event']('weapon.fire.rejected', {'name': name, 'reason': 'NO_AMMO', 'mode': mode})
                except Exception:
                    pass
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
            # Radio cue handled via event feed; avoid duplicate lines with voice_emit
            try:
                L['record_event']('weapon.fire', {
                    'weapon': name,
                    'mode': 'test',
                    'shooter': 'Sheffield',
                    'target': 'Test Range'
                })
            except Exception:
                pass
            # Apply cooldown
            cooldown_s = _cooldown_seconds_by_class(name)
            _set_cooldown_until(name, now + cooldown_s)
            try:
                L['record_event']('weapon.reload.start', {'name': name, 'mode': mode, 'cooldown_s': cooldown_s})
            except Exception:
                pass
            try:
                pending = L['PENDING_EVENTS']
                if isinstance(pending, list):
                    pending[:] = [ev for ev in pending if not (str(ev.get('kind')) == 'weapon_reload_ready' and str(ev.get('weapon')) == name)]
                    pending.append({'due': now + cooldown_s, 'kind': 'weapon_reload_ready', 'weapon': name})
            except Exception:
                pass
            return jsonify({'ok': True, 'result': 'TEST', 'name': name, 'ammo': ammo[name]})

        # Real fire path
        if int(ammo.get(name, 0)) <= 0:
            try:
                L['record_event']('weapon.out_of_ammo', {'name': name, 'mode': 'real'})
                L['record_event']('weapon.fire.rejected', {'name': name, 'reason': 'NO_AMMO', 'mode': mode})
            except Exception:
                pass
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
            try:
                L['record_event']('weapon.fire.rejected', {'name': name, 'reason': 'NO_PRIMARY', 'mode': mode})
            except Exception:
                pass
            # Fallback to last primary cached by status builder (if any)
            try:
                fallback_primary = getattr(L.get('_wd'), 'LAST_PRIMARY_UI', None)
            except Exception:
                fallback_primary = None
            if fallback_primary:
                primary = fallback_primary
            else:
                try:
                    own = (own_x, own_y)
                    for c in reversed(L['RADAR'].contacts):
                        if getattr(c, 'allegiance', '') == 'Hostile':
                            primary = L['contact_to_ui'](c, own)
                            break
                except Exception:
                    primary = None
            if not primary:
                return jsonify({'ok': False, 'error': 'NO_PRIMARY'}), 400
        # Invariant guard: consistency suite — enforce in_range before any shot is created
        if not L['compute_in_range'](name, primary):
            try:
                rng = float(primary.get('range_nm', 0.0) or 0.0)
            except Exception:
                rng = 0.0
            try:
                L['record_event']('weapon.fire.blocked', {'name': name, 'reason': 'OUT_OF_RANGE', 'range_nm': rng})
                L['record_event']('weapon.fire.rejected', {'name': name, 'reason': 'OUT_OF_RANGE', 'mode': mode, 'range_nm': rng})
            except Exception:
                pass
            return jsonify({'ok': False, 'error': 'OUT_OF_RANGE', 'range_nm': rng}), 400
        # consume ammo
        try:
            dec = 50 if name in ("20mm Oerlikon", "20mm GAM-BO1 (twin)") else 1
        except Exception:
            dec = 1
        ammo[name] = max(0, int(ammo.get(name, 0)) - int(dec))
        L['save_ammo'](ammo)
        try:
            L['RADAR'].rec.log('weapons.fire', {'name': name, 'mode': 'real', 'ammo': ammo[name], 'range_ok': True})
            L['RADAR'].rec.log('radio.msg', {'kind': 'FIRE', 'text': f'{name} fired'})
        except Exception:
            pass
        try:
            with L['STATE_LOCK']:
                L['AUDIO_STATE']['last_launch'] = {'weapon': L['_sound_key_for_weapon'](name), 'ts': time.time()}
        except Exception:
            pass
        # Radio cue handled via event feed; avoid duplicate lines with voice_emit
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
        cooldown_s = _cooldown_seconds_by_class(name)
        _set_cooldown_until(name, now + cooldown_s)
        try:
            L['record_event']('weapon.reload.start', {'name': name, 'mode': mode, 'cooldown_s': cooldown_s})
        except Exception:
            pass
        try:
            pending = L['PENDING_EVENTS']
            if isinstance(pending, list):
                pending[:] = [ev for ev in pending if not (str(ev.get('kind')) == 'weapon_reload_ready' and str(ev.get('weapon')) == name)]
                pending.append({'due': now + cooldown_s, 'kind': 'weapon_reload_ready', 'weapon': name})
        except Exception:
            pass
        try:
            L['record_event']('weapon.fire', {
                'weapon': name,
                'mode': 'real',
                'shooter': 'Sheffield',
                'target': primary.get('name'),
                'target_id': primary.get('id'),
                'range_ok': True,
                'range_nm': primary.get('range_nm')
            })
        except Exception:
            pass
        return jsonify({'ok': True, 'result': 'FIRED', 'name': name, 'ammo': ammo[name], 'range_ok': True})
    except Exception as e:
        logging.exception("/weapons/fire error: %s", e)
        return jsonify({'ok': False, 'error': str(e)}), 500
