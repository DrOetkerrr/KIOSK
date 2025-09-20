from __future__ import annotations

import time
import logging
from flask import Blueprint, jsonify, request
import os
import json
import math
import pathlib
from typing import Any, Dict, List
import re
from flask import current_app
try:
    import requests  # uses same requests lib as elsewhere
except Exception:  # pragma: no cover
    requests = None  # type: ignore

bp = Blueprint("interpreter", __name__)


def _lazy():
    # Late import to avoid circular references and keep webdash slim
    from ..webdash import (
        ENG, RADAR, CAP,
        record_flight, _arg_or_json,
        radar_xy_from_state, world_to_cell,
        load_ammo, load_arming, WEAP_CATALOG,
    )
    return locals()


def _openai_key_ok() -> tuple[bool, str | None]:
    """Basic sanity check for OPENAI_API_KEY. Returns (ok, error_message)."""
    key = os.environ.get('OPENAI_API_KEY') or ''
    if not key:
        return False, 'missing OPENAI_API_KEY'
    try:
        key.encode('ascii')
    except Exception:
        return False, 'OPENAI_API_KEY contains non-ASCII characters (e.g., … “ ”). Paste the exact key from your provider.'
    if '…' in key or '—' in key or ' ' in key:
        return False, 'OPENAI_API_KEY looks invalid (contains spaces or placeholder …). Paste the exact key from your provider.'
    return True, None

def _context_pack() -> dict:
    L = _lazy()
    # Ship snapshot
    try:
        st = L['ENG'].public_state() if hasattr(L['ENG'], 'public_state') else {}
        ship = (st or {}).get('ship', {}) if isinstance(st, dict) else {}
    except Exception:
        st, ship = {}, {}
    try:
        sx, sy = L['radar_xy_from_state'](st)
    except Exception:
        sx, sy = 50.0, 50.0
    try:
        cell = L['world_to_cell'](sx, sy)
    except Exception:
        cell = 'K13'

    # Radar snapshot (trim to 5 nearest)
    try:
        contacts = []
        for c in getattr(L['RADAR'], 'contacts', [])[:]:
            try:
                d = {
                    'id': int(getattr(c, 'id', -1)),
                    'name': str(getattr(c, 'name', '')),
                    'type': str(getattr(c, 'typ', getattr(c, 'type', ''))),
                    'cell': L['world_to_cell'](float(getattr(c, 'x', 0.0)), float(getattr(c, 'y', 0.0))),
                    'range_nm': round(((float(getattr(c, 'x', 0.0))-sx)**2 + (float(getattr(c, 'y', 0.0))-sy)**2) ** 0.5, 2),
                    'course': int(getattr(c, 'course_deg', getattr(c, 'course', 0)) or 0),
                    'speed': int(getattr(c, 'speed_kts', getattr(c, 'speed', 0)) or 0),
                }
                contacts.append(d)
            except Exception:
                continue
        contacts.sort(key=lambda d: float(d.get('range_nm', 1e9)))
        contacts = contacts[:5]
        locked_id = int(getattr(L['RADAR'], 'priority_id', -1)) if getattr(L['RADAR'], 'priority_id', None) is not None else None
        top_threat_id = next((int(d['id']) for d in contacts if str(d.get('type','')).lower() == 'hostile'), None)
        radar = {'locked_id': locked_id, 'top_threat_id': top_threat_id, 'contacts': contacts}
    except Exception:
        radar = {'locked_id': None, 'top_threat_id': None, 'contacts': []}

    # Weapons snapshot (compact)
    weaps = []
    try:
        ammo = L['load_ammo']()
        arming = L['load_arming']()
        for w in L['WEAP_CATALOG']:
            nm = w.get('name'); cls = w.get('class')
            weaps.append({
                'name': nm,
                'class': cls,
                'ammo': int(ammo.get(nm, 0)),
                'armed': arming.get(nm, 'Safe'),
                'min_nm': w.get('min_nm'),
                'max_nm': w.get('max_nm'),
            })
    except Exception:
        pass

    # CAP snapshot (very compact)
    try:
        cap_ready = 0; airframes = 0; cooldown = 0
        missions = []
        if L['CAP'] is not None:
            snap = L['CAP'].snapshot()
            try:
                r = (snap.get('readiness') or {})
                cap_ready = int(r.get('ready_pairs', r.get('pairs', 0)) or 0)
                airframes = int(r.get('airframes', r.get('airframe_pool_total', 0)) or 0)
                cooldown = int(r.get('cooldown_s', 0) or 0)
            except Exception:
                pass
            try:
                missions = [{'id': int(m.get('id')), 'status': m.get('status')} for m in (snap.get('missions') or []) if m.get('id') is not None]
            except Exception:
                missions = []
        cap = {'ready_pairs': cap_ready, 'airframes': airframes, 'cooldown_s': cooldown, 'missions': missions}
    except Exception:
        cap = {'ready_pairs': 0, 'airframes': 0, 'cooldown_s': 0, 'missions': []}

    # Recent history (radio + events) and doctrine
    recent_radio = []
    recent_events = []
    try:
        from .. import webdash as wd  # type: ignore
        try:
            with wd.STATE_LOCK:
                hist = list(getattr(wd, 'RADIO_HISTORY', []))
            for r in hist[-5:]:
                recent_radio.append({'role': str(r.get('role') or ''), 'text': str(r.get('text') or '')})
        except Exception:
            pass
        try:
            evs = list(getattr(wd, 'EVENT_QUEUE', []))
            for ev in evs[-5:]:
                recent_events.append({'id': ev.get('id'), 'text': ev.get('text'), 'data': ev.get('data')})
        except Exception:
            pass
    except Exception:
        pass

    doctrine = {
        'standing_orders': [
            'Offer brief recommendations when safety or mission success warrants it; never execute without order.',
            'Use clipped RN radio; address Captain; ≤ 2 short lines.'
        ],
        'recommendations': {
            'arm_sam_if_hostile_within_nm': 25
        }
    }

    # World/Era card to steer style and terminology
    world = {
        'year': 1982,
        'theater': 'San Carlos Water',
        'side': 'Royal Navy',
        'adversary': 'Argentina',
        'ship': 'HMS Sheffield',
        'ship_class': 'Type 42',
        'carrier': 'HMS Hermes'
    }

    # Style and variety knobs to humanize output
    try:
        # Cheap changing seed based on time; caller may override in future
        style_seed = int(time.time()) % 1000
    except Exception:
        style_seed = 0
    style = {
        'tone': 'calm',
        'role_styles': {
            'Radar': 'clipped',
            'Fire Control': 'firm',
            'Weapons': 'brisk',
            'Engineering': 'gruff',
            'Pilot': 'airwing'
        },
        'style_seed': style_seed
    }
    variety_policy = {
        'avoid_repeats_window_s': 45
    }

    return {
        'who': {'role': 'Captain', 'ship': 'HMS Sheffield', 'class': 'Type 42'},
        'ship': {'cell': cell, 'heading': int(float(ship.get('heading', 0) or 0)), 'speed': int(float(ship.get('speed', 0) or 0))},
        'radar': radar,
        'weapons': weaps,
        'cap': cap,
        'capabilities': ['RADAR.SCAN', 'RADAR.LOCK', 'WEAPON.ARM', 'WEAPON.FIRE', 'CAP.LAUNCH', 'CAP.AUTHORIZE'],
        'policy': {'always_confirm': ['WEAPON.FIRE', 'CAP.AUTHORIZE', 'CAP.LAUNCH']},
        'history': {'recent_radio': recent_radio, 'recent_events': recent_events},
        'world': world,
        'style': style,
        'variety_policy': variety_policy,
        'doctrine': doctrine,
    }


@bp.post("/radio/interp")
def radio_interpreter_preview():
    """Preview endpoint: returns the context_pack and the strict response schema.
    No OpenAI call, no action execution.
    Body: {"text": "..."}
    """
    L = _lazy(); t0 = time.time(); route = "/radio/interp"
    try:
        user_text = L['_arg_or_json'](request, 'text', '') or ''
        ctx = _context_pack()
        schema = {
            'radio': 'string',
            'actions': [
                {'type': 'RADAR.SCAN'},
                {'type': 'RADAR.LOCK', 'params': {'id': 4}},
                {'type': 'WEAPON.ARM', 'params': {'name': 'Sea Dart SAM', 'state': 'Armed'}},
                {'type': 'WEAPON.FIRE', 'params': {'weapon': 'Sea Dart SAM', 'target_id': 4, 'mode': 'real'}},
                {'type': 'CAP.LAUNCH', 'params': {'cell': 'K13'}},
                {'type': 'CAP.AUTHORIZE', 'params': {'mission_id': 12, 'authorize': True}},
            ],
            'advisories': [
                {
                    'advice': 'Bring Sea Dart online.',
                    'reason': 'Nearest hostile within 25 nm and closing.',
                    'recommended_action': {'type': 'WEAPON.ARM', 'params': {'name': 'Sea Dart SAM', 'state': 'Armed'}},
                    'priority': 'info|warning|critical',
                    'ttl_s': 30,
                }
            ],
            'needs_confirm': True,
            'clarifying_question': 'string|null',
        }
        example = {
            'radio': 'Captain, scanning radar. Over.',
            'actions': [{'type': 'RADAR.SCAN'}],
            'advisories': [],
            'needs_confirm': False,
            'clarifying_question': None,
        }
        example_adv = {
            'radio': 'Captain, A-4 inbound 24.8 nm, bearing 315°. Recommend bring Sea Dart online.',
            'actions': [],
            'advisories': [
                {
                    'advice': 'Bring Sea Dart online.',
                    'reason': 'Nearest hostile within 25 nm and closing.',
                    'recommended_action': {'type': 'WEAPON.ARM', 'params': {'name': 'Sea Dart SAM', 'state': 'Armed'}},
                    'priority': 'warning',
                    'ttl_s': 30,
                }
            ],
            'needs_confirm': False,
            'clarifying_question': None,
        }
        payload = {'ok': True, 'context_pack': ctx, 'schema': schema, 'examples': {'basic': example, 'advisory': example_adv}, 'echo_text': user_text}
        L['record_flight']({
            'route': route, 'method': request.method, 'status': 200,
            'duration_ms': int((time.time()-t0)*1000),
            'request': {'text': user_text}, 'response': {'ok': True}
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/radio/interp error: %s", e)
        L = _lazy()
        payload = {'ok': False, 'error': str(e)}
        L['record_flight']({
            'route': route, 'method': request.method, 'status': 500,
            'duration_ms': int((time.time()-t0)*1000),
            'request': {}, 'response': payload
        })
        return jsonify(payload), 500


def _system_prompt_for(ctx: Dict[str, Any]) -> str:
    world = ctx.get('world', {})
    year = world.get('year', 1982)
    ship = world.get('ship', 'HMS Sheffield')
    theater = world.get('theater', 'San Carlos Water')
    return (
        f"You are the Operations/Radio Officer aboard {ship} (Type 42), {year}, Falklands campaign at {theater}.\n"
        "Follow 1982 Royal Navy radio discipline: clipped, precise, address the CO as 'Captain'.\n"
        "Keep to at most two short lines. Include bearing and range when relevant.\n"
        "You must return ONLY a single JSON object with fields: radio, actions, advisories, needs_confirm, clarifying_question.\n"
        "- actions must only use known capabilities: RADAR.SCAN, RADAR.LOCK{id}, WEAPON.ARM{name,state}, WEAPON.FIRE{weapon,target_id,mode}, CAP.LAUNCH{cell}, CAP.AUTHORIZE{mission_id,authorize}.\n"
        "- advisories are suggestions only, never auto-executed: {advice, reason, recommended_action?, priority, ttl_s}.\n"
        "- Never invent contacts, weapons, or missions not present in context_pack. If unknown, say so briefly.\n"
        "- Offer brief recommendations when safety warrants; you may echo a recommendation in radio.\n"
        "- Always set needs_confirm=true for actions that change state or carry risk.\n"
        "- Vary phrasing modestly and avoid repeating the same opener/verb as in recent_radio.\n"
    )


@bp.post("/radio/interp_ai")
def radio_interpreter_ai():
    """Call OpenAI in JSON mode to interpret the Captain's utterance.
    Returns ONLY the model's structured plan, no execution.

    Body: {"text": "..."}
    Env: OPENAI_API_KEY, OPENAI_INTERP_MODEL (default gpt-4o-mini)
    """
    L = _lazy(); t0 = time.time(); route = "/radio/interp_ai"
    try:
        user_text = L['_arg_or_json'](request, 'text', '') or ''
        if not user_text:
            return jsonify({'ok': False, 'error': 'missing text'}), 400
        ctx = _context_pack()
        ok_key, err_key = _openai_key_ok()
        key = os.environ.get('OPENAI_API_KEY')
        model = os.environ.get('OPENAI_INTERP_MODEL', 'gpt-4o-mini')
        if not ok_key or not key or requests is None:
            return jsonify({'ok': False, 'error': 'OPENAI unavailable', 'detail': err_key}), 503
        url = 'https://api.openai.com/v1/chat/completions'
        headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
        system = _system_prompt_for(ctx)
        # Send compact context and the captain's words together to minimize tokens
        payload = {
            'model': model,
            'response_format': {'type': 'json_object'},
            'temperature': 0.6,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps({'context_pack': ctx, 'captain': user_text})},
            ]
        }
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code != 200:
            return jsonify({'ok': False, 'error': f'OpenAI {r.status_code}', 'body': r.text[:400]}), 502
        data = r.json()
        content = (data.get('choices') or [{}])[0].get('message', {}).get('content', '')
        parsed = None
        try:
            parsed = json.loads(content) if content else None
        except Exception:
            parsed = None
        # Optionally validate the parsed plan against live state (non-executing)
        validation = None
        try:
            if isinstance(parsed, dict):
                validation = _validate_plan(ctx, parsed)
        except Exception:
            validation = None
        out = {'ok': True, 'model': model, 'raw': content, 'parsed': parsed, 'validation': validation}
        try:
            L['record_flight']({
                'route': route, 'method': request.method, 'status': 200,
                'duration_ms': int((time.time()-t0)*1000),
                'request': {'text': user_text},
                'response': {'ok': True, 'parsed_keys': list(parsed.keys()) if isinstance(parsed, dict) else None}
            })
        except Exception:
            pass
        return jsonify(out)
    except Exception as e:
        logging.exception("/radio/interp_ai error: %s", e)
        L = _lazy()
        payload = {'ok': False, 'error': str(e)}
        L['record_flight']({
            'route': route, 'method': request.method, 'status': 500,
            'duration_ms': int((time.time()-t0)*1000),
            'request': {}, 'response': payload
        })
        return jsonify(payload), 500


@bp.post("/radio/ptt")
def radio_ptt_upload():
    """Accept a PTT audio upload (for push-to-talk hardware).
    No ASR here yet; stores the file and returns a reference.
    Form-Data: field 'file' as audio/wav or audio/mpeg
    Returns: { ok, path }
    """
    L = _lazy(); t0 = time.time(); route = "/radio/ptt"
    try:
        if 'file' not in request.files:
            return jsonify({'ok': False, 'error': 'missing file'}), 400
        f = request.files['file']
        if not f.filename:
            return jsonify({'ok': False, 'error': 'empty filename'}), 400
        from .. import webdash as wd  # type: ignore
        base = pathlib.Path(wd.LOG_DIR) / 'ptt'
        base.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        safe = ''.join(c for c in f.filename if c.isalnum() or c in ('.','-','_')) or 'ptt.wav'
        path = base / f"{ts}_{safe}"
        f.save(str(path))
        try:
            L['record_flight']({
                'route': route, 'method': request.method, 'status': 200,
                'duration_ms': int((time.time()-t0)*1000),
                'request': {'filename': f.filename},
                'response': {'ok': True, 'path': str(path)}
            })
        except Exception:
            pass
        return jsonify({'ok': True, 'path': str(path)})
    except Exception as e:
        logging.exception("/radio/ptt error: %s", e)
        L = _lazy()
        payload = {'ok': False, 'error': str(e)}
        L['record_flight']({
            'route': route, 'method': request.method, 'status': 500,
            'duration_ms': int((time.time()-t0)*1000),
            'request': {}, 'response': payload
        })
        return jsonify(payload), 500


# ---- Validation (non-executing) ----
def _is_valid_cell(cell: str, board_n: int = 26) -> bool:
    try:
        if not cell: return False
        s = str(cell).strip().upper()
        m = re.match(r"^([A-Z]+)([0-9]{1,2})$", s)
        if not m: return False
        letters, digits = m.group(1), m.group(2)
        # Convert letters to 1-based index (A=1 .. Z=26, AA=27 ...)
        col = 0
        for ch in letters:
            col = col * 26 + (ord(ch) - ord('A') + 1)
        row = int(digits)
        return 1 <= col <= board_n and 1 <= row <= board_n
    except Exception:
        return False


def _validate_plan(ctx: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a parsed plan (radio/actions/advisories...). Never executes.
    Returns a structure with per-action validity and an execution hint (route/method/body) for later use.
    """
    L = _lazy()
    caps: List[str] = list(ctx.get('capabilities') or [])
    policy = ctx.get('policy') or {}
    always_confirm: List[str] = list(policy.get('always_confirm') or [])
    radar = ctx.get('radar') or {}
    contacts: List[Dict[str, Any]] = list(radar.get('contacts') or [])
    locked_id = radar.get('locked_id')
    weapons_def = {w.get('name'): w for w in (ctx.get('weapons') or [])}
    weap_catalog = list(L['WEAP_CATALOG']) if 'WEAP_CATALOG' in L else []
    known_weaps = {w.get('name'): w for w in weap_catalog}

    def _contact_by_id(cid: int) -> Dict[str, Any] | None:
        for d in contacts:
            try:
                if int(d.get('id')) == int(cid):
                    return d
            except Exception:
                continue
        return None

    results: List[Dict[str, Any]] = []
    issues: List[str] = []
    requires_confirmation = False

    for a in (plan.get('actions') or []):
        a_type = str(a.get('type') or '').strip()
        entry = {'type': a_type, 'valid': False, 'reason': None, 'exec': None}
        if not a_type:
            entry['reason'] = 'missing type'
            results.append(entry); continue
        if a_type not in caps:
            entry['reason'] = f'not in capabilities: {a_type}'
            results.append(entry); continue

        # Build execution hint per type
        if a_type == 'RADAR.SCAN':
            entry['valid'] = True
            entry['exec'] = {'method': 'GET', 'url': '/api/command', 'params': {'cmd': '/radar scan'}}
        elif a_type == 'RADAR.LOCK':
            pid = None
            try:
                pid = int(((a.get('params') or {}).get('id')))
            except Exception:
                pid = None
            if pid is None:
                entry['reason'] = 'missing params.id'
            elif _contact_by_id(pid) is None:
                entry['reason'] = f'unknown contact id {pid}'
            else:
                entry['valid'] = True
                entry['exec'] = {'method': 'GET', 'url': '/api/command', 'params': {'cmd': f'/radar lock {pid}'}}
        elif a_type == 'WEAPON.ARM':
            p = a.get('params') or {}
            name = str(p.get('name') or '')
            state = str(p.get('state') or '')
            if not name or state not in ('Armed','Safe'):
                entry['reason'] = 'bad params (name/state)'
            elif name not in known_weaps:
                entry['reason'] = f'unknown weapon {name}'
            else:
                entry['valid'] = True
                entry['exec'] = {'method': 'POST', 'url': '/weapons/arm', 'json': {'name': name, 'state': state}}
        elif a_type == 'WEAPON.FIRE':
            p = a.get('params') or {}
            weapon = str(p.get('weapon') or '')
            mode = str(p.get('mode') or 'real')
            tid = None
            try:
                tid = int(p.get('target_id'))
            except Exception:
                tid = None
            wdef = weapons_def.get(weapon) or {}
            if not weapon or tid is None:
                entry['reason'] = 'missing weapon/target_id'
            elif weapon not in known_weaps:
                entry['reason'] = f'unknown weapon {weapon}'
            elif wdef.get('ammo', 0) <= 0:
                entry['reason'] = 'NO_AMMO'
            elif wdef.get('armed') != 'Armed':
                entry['reason'] = 'NOT_ARMED'
            elif locked_id is None or int(locked_id) != int(tid):
                entry['reason'] = 'NO_PRIMARY (lock target first)'
            else:
                target = _contact_by_id(tid)
                rng = float((target or {}).get('range_nm', 1e9))
                min_nm = float((known_weaps.get(weapon) or {}).get('min_nm') or 0.0)
                max_nm = float((known_weaps.get(weapon) or {}).get('max_nm') or 1e9)
                if not (min_nm <= rng <= max_nm):
                    entry['reason'] = f'OUT_OF_RANGE ({rng:.1f} nm not in {min_nm}-{max_nm})'
                else:
                    entry['valid'] = True
                    entry['exec'] = {'method': 'POST', 'url': '/weapons/fire', 'json': {'name': weapon, 'mode': mode}}
                    requires_confirmation = True
        elif a_type == 'CAP.LAUNCH':
            p = a.get('params') or {}
            cell = str(p.get('cell') or '')
            cap = ctx.get('cap') or {}
            ready = int(cap.get('ready_pairs') or 0)
            if not cell:
                entry['reason'] = 'missing cell'
            elif ready <= 0:
                entry['reason'] = 'CAP not ready'
            elif not _is_valid_cell(cell):
                entry['reason'] = f'invalid cell {cell}'
            else:
                entry['valid'] = True
                entry['exec'] = {'method': 'POST', 'url': '/cap/launch_to', 'json': {'cell': cell}}
                requires_confirmation = True
        elif a_type == 'CAP.AUTHORIZE':
            p = a.get('params') or {}
            try:
                mid = int(p.get('mission_id'))
            except Exception:
                mid = 0
            # Validate using CAP snapshot missions
            cap = ctx.get('cap') or {}
            mission_ok = any(int(m.get('id', -1)) == mid for m in (cap.get('missions') or []))
            if mid <= 0:
                entry['reason'] = 'missing mission_id'
            elif not mission_ok:
                entry['reason'] = f'unknown mission id {mid}'
            else:
                entry['valid'] = True
                entry['exec'] = {'method': 'POST', 'url': '/cap/authorize', 'json': {'id': mid, 'authorize': bool(p.get('authorize', True))}}
                requires_confirmation = True
        else:
            entry['reason'] = f'unsupported type {a_type}'
        results.append(entry)

    # Aggregate issues (invalid actions)
    for r in results:
        if not r.get('valid') and r.get('reason'):
            issues.append(f"{r.get('type')}: {r.get('reason')}")

    # Enforce confirmation policy
    for r in results:
        t = str(r.get('type') or '')
        if any(t == x for x in always_confirm):
            requires_confirmation = True

    return {
        'ok': len(issues) == 0,
        'requires_confirmation': bool(requires_confirmation),
        'issues': issues,
        'actions': results,
        'advisories': plan.get('advisories') or [],
        'radio': plan.get('radio') or '',
        'clarifying_question': plan.get('clarifying_question')
    }


@bp.post("/radio/exec")
def radio_execute_plan():
    """Confirm and execute a validated plan. This calls internal routes only.

    Body options:
      - { "plan": { ...parsed LLM JSON... }, "confirm": true }
      - { "actions": [ {type, params} ], "confirm": true }  # will be wrapped into a plan
      - speak: bool (optional) — if true, enqueue plan.radio for TTS after execution
      - voice_role: str (optional) — crew role for TTS (default 'Bridge')

    Returns per-action results; refuses to execute without confirm when required.
    """
    L = _lazy(); t0 = time.time(); route = "/radio/exec"
    try:
        body = request.get_json(silent=True) or {}
        plan = body.get('plan') if isinstance(body.get('plan'), dict) else None
        actions_in = body.get('actions') if isinstance(body.get('actions'), list) else None
        confirm = bool(body.get('confirm', False))
        if plan is None and actions_in is not None:
            plan = {'radio': '', 'actions': actions_in, 'advisories': [], 'needs_confirm': True}
        if plan is None:
            return jsonify({'ok': False, 'error': 'missing plan/actions'}), 400
        ctx = _context_pack()
        validation = _validate_plan(ctx, plan)
        if not validation.get('ok'):
            return jsonify({'ok': False, 'error': 'invalid plan', 'validation': validation}), 400
        if validation.get('requires_confirmation') and not confirm:
            return jsonify({'ok': False, 'error': 'confirmation required', 'validation': validation}), 403
        # Execute only whitelisted internal routes
        ALLOW = {'/api/command', '/weapons/arm', '/weapons/fire', '/cap/launch_to', '/cap/authorize'}
        execs: List[Dict[str, Any]] = []
        for a in (validation.get('actions') or []):
            if a.get('valid') and a.get('exec'):
                e = dict(a.get('exec') or {})
                if str(e.get('url') or '') in ALLOW:
                    execs.append(e)
        results: List[Dict[str, Any]] = []
        ok_all = True
        # Dispatch via Flask test client to avoid external HTTP
        with current_app.test_client() as c:
            for e in execs:
                method = (e.get('method') or 'GET').upper()
                url = str(e.get('url') or '')
                try:
                    if method == 'GET':
                        resp = c.get(url, query_string=e.get('params') or {})
                    elif method == 'POST':
                        js = e.get('json')
                        resp = c.post(url, json=js if js is not None else {})
                    else:
                        results.append({'url': url, 'status': 'SKIP', 'error': f'unsupported method {method}'})
                        ok_all = False
                        continue
                    try:
                        j = resp.get_json(silent=True)
                    except Exception:
                        j = None
                    results.append({'url': url, 'status_code': resp.status_code, 'ok': 200 <= resp.status_code < 300, 'body': j})
                    if not (200 <= resp.status_code < 300):
                        ok_all = False
                except Exception as ex:
                    results.append({'url': url, 'status': 'ERR', 'error': str(ex)})
                    ok_all = False
        # Optional: speak radio line after execution when requested
        spoken = None
        try:
            speak = bool((body or {}).get('speak', False))
            # Allow query-string override
            qs_speak = request.args.get('speak')
            if qs_speak is not None:
                speak = (str(qs_speak).strip().lower() in ('1','true','yes','on'))
            role = str((body or {}).get('voice_role') or request.args.get('voice_role') or 'Bridge')
            radio_line = ''
            try:
                if isinstance(plan, dict):
                    radio_line = str(plan.get('radio') or '')
            except Exception:
                radio_line = ''
            if speak and radio_line and ok_all:
                from .. import webdash as wd  # type: ignore
                wd.record_officer(role or 'Bridge', radio_line)
                spoken = {'role': role or 'Bridge', 'text': radio_line}
        except Exception:
            spoken = None

        payload = {'ok': bool(ok_all), 'results': results, 'validation': validation, 'spoken': spoken}
        try:
            L['record_flight']({
                'route': route, 'method': request.method, 'status': 200 if ok_all else 400,
                'duration_ms': int((time.time()-t0)*1000),
                'request': {'confirm': confirm, 'speak': bool((body or {}).get('speak', False))},
                'response': {'ok': bool(ok_all), 'n': len(results), 'spoken': bool(spoken)}
            })
        except Exception:
            pass
        return jsonify(payload), (200 if ok_all else 400)
    except Exception as e:
        logging.exception("/radio/exec error: %s", e)
        L = _lazy()
        payload = {'ok': False, 'error': str(e)}
        L['record_flight']({
            'route': route, 'method': request.method, 'status': 500,
            'duration_ms': int((time.time()-t0)*1000),
            'request': {}, 'response': payload
        })
        return jsonify(payload), 500


def _asr_transcribe_bytes(data: bytes, filename: str | None = None, mime: str | None = None) -> str:
    ok_key, err_key = _openai_key_ok()
    key = os.environ.get('OPENAI_API_KEY')
    model = os.environ.get('OPENAI_ASR_MODEL', 'whisper-1')
    if not ok_key or not key or requests is None:
        raise RuntimeError(f'ASR unavailable ({err_key or "missing OPENAI_API_KEY"})')
    url = 'https://api.openai.com/v1/audio/transcriptions'
    headers = {'Authorization': f'Bearer {key}'}
    # Normalize common browser MIME with codecs param
    try:
        mm = (mime or 'audio/webm').split(';', 1)[0].strip().lower()
    except Exception:
        mm = mime or 'audio/webm'
    files = {
        'file': (filename or 'ptt.webm', data, mm or 'audio/webm'),
    }
    data_form = {
        'model': model,
    }
    r = requests.post(url, headers=headers, files=files, data=data_form, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f'ASR failed {r.status_code}: {r.text[:200]}')
    j = r.json()
    # Whisper returns {'text': '...'}; newer models may return a different shape
    txt = j.get('text') if isinstance(j, dict) else None
    if not txt:
        # Try common alternative
        try:
            txt = (j.get('results') or [{}])[0].get('transcript')
        except Exception:
            txt = None
    return str(txt or '').strip()


@bp.post("/radio/voice")
def radio_voice_chain():
    """Convenience chain: (optional) audio → ASR → AI plan → validation.

    Accepts either text or an audio file. Does not execute actions.
    Body:
      - multipart/form-data with 'file' (audio)
      - or JSON {"text": "..."}
    Optional:
      - speak: bool (query or JSON) — if true, enqueue parsed.radio via server-side TTS
      - voice_role: str (query or JSON) — crew role to use for TTS (default 'Bridge')

    Returns transcript (if any), parsed AI plan, validation, 'affirm' helper for /radio/exec,
    and 'spoken' info when speak=true.
    """
    L = _lazy(); t0 = time.time(); route = "/radio/voice"
    try:
        user_text = None
        if request.is_json:
            try:
                user_text = (request.get_json(silent=True) or {}).get('text')
            except Exception:
                user_text = None
        if not user_text and 'file' in request.files:
            f = request.files['file']
            data = f.read()
            try:
                user_text = _asr_transcribe_bytes(data, f.filename, f.mimetype)
            except Exception as ex:
                # Fail graceful for client; surface diagnostic
                return jsonify({'ok': False, 'error': 'ASR unavailable', 'detail': str(ex)}), 503
        if not user_text:
            return jsonify({'ok': False, 'error': 'missing text or file'}), 400

        # AI interpret (reuse the same logic as /radio/interp_ai)
        ctx = _context_pack()
        ok_key, err_key = _openai_key_ok()
        key = os.environ.get('OPENAI_API_KEY')
        model = os.environ.get('OPENAI_INTERP_MODEL', 'gpt-4o-mini')
        if not ok_key or not key or requests is None:
            return jsonify({'ok': False, 'error': 'OPENAI unavailable', 'detail': err_key}), 503
        url = 'https://api.openai.com/v1/chat/completions'
        headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
        system = _system_prompt_for(ctx)
        payload = {
            'model': model,
            'response_format': {'type': 'json_object'},
            'temperature': 0.6,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps({'context_pack': ctx, 'captain': user_text})},
            ]
        }
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code != 200:
            return jsonify({'ok': False, 'error': f'OpenAI {r.status_code}', 'body': r.text[:400]}), 502
        data_out = r.json()
        content = (data_out.get('choices') or [{}])[0].get('message', {}).get('content', '')
        try:
            parsed = json.loads(content) if content else None
        except Exception:
            parsed = None
        validation = None
        try:
            if isinstance(parsed, dict):
                validation = _validate_plan(ctx, parsed)
        except Exception:
            validation = None
        affirm = None
        if isinstance(parsed, dict):
            affirm = {'endpoint': '/radio/exec', 'body': {'plan': parsed, 'confirm': True}}
        # Optional: speak the radio line now
        spoken = None
        try:
            # Accept speak and voice_role from either query string or JSON body
            speak = False
            role = 'Bridge'
            if request.is_json:
                body = request.get_json(silent=True) or {}
                speak = bool(body.get('speak', False))
                role = str(body.get('voice_role') or role)
            # Query string overrides if present
            qs_speak = request.args.get('speak')
            if qs_speak is not None:
                speak = (str(qs_speak).strip().lower() in ('1','true','yes','on'))
            qs_role = request.args.get('voice_role')
            if qs_role:
                role = str(qs_role)
            if speak and isinstance(parsed, dict):
                radio_line = str((parsed.get('radio') or '')).strip()
                if radio_line:
                    from .. import webdash as wd  # type: ignore
                    wd.record_officer(role or 'Bridge', radio_line)
                    spoken = {'role': role or 'Bridge', 'text': radio_line}
        except Exception:
            spoken = None

        out = {
            'ok': True,
            'transcript': user_text,
            'ai': {'model': model, 'parsed': parsed, 'validation': validation},
            'affirm': affirm,
            'spoken': spoken,
        }
        try:
            L['record_flight']({
                'route': route, 'method': request.method, 'status': 200,
                'duration_ms': int((time.time()-t0)*1000),
                'request': {'has_file': 'file' in request.files},
                'response': {'ok': True, 'has_parsed': bool(parsed), 'spoken': bool(spoken)}
            })
        except Exception:
            pass
        return jsonify(out)
    except Exception as e:
        logging.exception("/radio/voice error: %s", e)
        L = _lazy()
        payload = {'ok': False, 'error': str(e)}
        L['record_flight']({
            'route': route, 'method': request.method, 'status': 500,
            'duration_ms': int((time.time()-t0)*1000),
            'request': {}, 'response': payload
        })
        return jsonify(payload), 500
