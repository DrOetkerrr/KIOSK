from __future__ import annotations

import time
import logging
from typing import Any, Dict
from flask import Blueprint, jsonify

bp = Blueprint("skirmish", __name__)


def _lazy():
    from ..webdash import (
        _load_skirmishes, _save_skirmishes, _skirmish_next_id, _skirmish_now_iso,
        _skirmish_apply_config, _skirmish_summarize,
        STATE_LOCK, SKIRMISH_ACTIVE, record_flight,
    )
    return locals()


@bp.get('/skirmish/list')
def skirmish_list():
    L = _lazy()
    try:
        db = L['_load_skirmishes']()
        items = db.get('items') or {}
        lst = []
        for k, v in items.items():
            try:
                it = dict(v); it['id'] = int(k); lst.append(it)
            except Exception:
                continue
        lst.sort(key=lambda x: x.get('id', 0))
        return jsonify({"ok": True, "items": lst, "active": L['SKIRMISH_ACTIVE'].get('id')})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get('/skirmish/get')
def skirmish_get():
    L = _lazy()
    try:
        from flask import request
        sid = int(request.args.get('id', '0'))
    except Exception:
        return jsonify({"ok": False, "error": "missing id"}), 400
    db = L['_load_skirmishes'](); items = db.get('items') or {}
    it = items.get(str(sid))
    if not it:
        return jsonify({"ok": False, "error": "not found"}), 404
    obj = dict(it); obj['id'] = sid
    return jsonify({"ok": True, "item": obj, "active": L['SKIRMISH_ACTIVE'].get('id')})


@bp.post('/skirmish/create')
def skirmish_create():
    L = _lazy(); from flask import request
    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        data = {}
    db = L['_load_skirmishes'](); items = db.get('items') or {}
    sid = L['_skirmish_next_id'](db)
    name = str(data.get('name') or f'Skirmish {sid}')
    notes = str(data.get('notes') or '')
    cfg = data.get('config') or {
        'own': {'heading_deg': 120.0, 'speed_kts': 32.0},
        'arm': {'Sea Dart SAM': 'Armed', '20mm GAM-BO1 (twin)': 'Armed', '20mm Oerlikon': 'Armed'},
        'hostiles': [{'name': 'Super Étendard', 'range_nm': 12.0, 'bearing_deg': 315.0}],
    }
    rec = {'name': name, 'notes': notes, 'created_ts': L['_skirmish_now_iso'](), 'config': cfg, 'status': 'ready', 'outcomes': [], 'run': None}
    items[str(sid)] = rec
    db['items'] = items
    L['_save_skirmishes'](db)
    return jsonify({"ok": True, "id": sid, "item": {**rec, 'id': sid}})


@bp.post('/skirmish/start')
def skirmish_start():
    L = _lazy(); from flask import request
    try:
        sid = int((request.get_json(silent=True) or {}).get('id', 0) or (request.args.get('id') or 0))
    except Exception:
        return jsonify({"ok": False, "error": "missing id"}), 400
    if not sid:
        return jsonify({"ok": False, "error": "missing id"}), 400
    with L['STATE_LOCK']:
        if L['SKIRMISH_ACTIVE'].get('id') not in (None, 0):
            return jsonify({"ok": False, "error": "skirmish already running", "active": L['SKIRMISH_ACTIVE'].get('id')}), 409
    db = L['_load_skirmishes'](); items = db.get('items') or {}
    it = items.get(str(sid))
    if not it:
        return jsonify({"ok": False, "error": "not found"}), 404
    cfg = it.get('config') or {}
    applied = L['_skirmish_apply_config'](cfg if isinstance(cfg, dict) else {})
    now_ep = time.time(); now_iso = L['_skirmish_now_iso']()
    it['status'] = 'running'; it['run'] = {'started_ts': now_iso, 'started_epoch': now_ep}
    items[str(sid)] = it; db['items'] = items; L['_save_skirmishes'](db)
    with L['STATE_LOCK']:
        L['SKIRMISH_ACTIVE']['id'] = sid; L['SKIRMISH_ACTIVE']['started_ts'] = now_iso
    L['record_flight']({"route": "/skirmish.start", "method": "INT", "status": 200, "duration_ms": 0, "request": {"id": sid}, "response": {"ok": True}})
    return jsonify({"ok": True, "id": sid, "applied": applied})


@bp.post('/skirmish/stop')
def skirmish_stop():
    L = _lazy(); from flask import request
    try:
        sid = int((request.get_json(silent=True) or {}).get('id', 0) or (request.args.get('id') or 0))
    except Exception:
        sid = None
    db = L['_load_skirmishes'](); items = db.get('items') or {}
    if not sid:
        sid = (L['SKIRMISH_ACTIVE'].get('id') or 0)
    it = items.get(str(sid))
    if not it:
        return jsonify({"ok": False, "error": "not found or not running"}), 404
    run = it.get('run') or {}
    start_ep = float(run.get('started_epoch', 0.0))
    stop_ep = time.time(); stop_iso = L['_skirmish_now_iso']()
    summary = L['_skirmish_summarize'](start_ep, stop_ep)
    it['status'] = 'stopped'; it['run'] = {**run, 'stopped_ts': stop_iso, 'stopped_epoch': stop_ep}
    it.setdefault('outcomes', []).append({'started_ts': run.get('started_ts'), 'stopped_ts': stop_iso, 'summary': summary})
    items[str(sid)] = it; db['items'] = items; L['_save_skirmishes'](db)
    with L['STATE_LOCK']:
        L['SKIRMISH_ACTIVE']['id'] = None; L['SKIRMISH_ACTIVE']['started_ts'] = None
    L['record_flight']({"route": "/skirmish.stop", "method": "INT", "status": 200, "duration_ms": 0, "request": {"id": sid}, "response": {"ok": True, "summary": summary}})
    return jsonify({"ok": True, "id": sid, "summary": summary})

