from __future__ import annotations

import time
import logging
from flask import Blueprint, jsonify, render_template, request

bp = Blueprint("roadmap", __name__)


def _lazy():
    # Import helpers from webdash to avoid duplication and maintain single source of truth
    from ..webdash import (
        _init_roadmap_if_missing, _load_roadmap, _save_roadmap, _skirmish_now_iso,
        record_flight,
    )
    return locals()


@bp.get('/roadmap')
def roadmap_page():
    try:
        return render_template('roadmap.html')
    except Exception as e:
        logging.exception("/roadmap page error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get('/roadmap/list')
def roadmap_list():
    L = _lazy(); t0 = time.time(); route = '/roadmap/list'
    try:
        L['_init_roadmap_if_missing']()
        db = L['_load_roadmap']()
        items = db.get('items') or []
        items = sorted(items, key=lambda it: it.get('order', 0))
        payload = {"ok": True, "items": items, "updated": db.get('updated')}
        L['record_flight']({"route": route, "method": "GET", "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/roadmap/list error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post('/roadmap/set_status')
def roadmap_set_status():
    L = _lazy(); t0 = time.time(); route = '/roadmap/set_status'
    try:
        data = request.get_json(silent=True) or {}
        iid = int(data.get('id'))
        status = str(data.get('status')).strip().lower()
        if status not in ('pending','in_progress','done'):
            return jsonify({"ok": False, "error": "bad status"}), 400
    except Exception:
        return jsonify({"ok": False, "error": "bad params"}), 400
    try:
        L['_init_roadmap_if_missing']()
        db = L['_load_roadmap'](); items = list(db.get('items') or [])
        changed = False
        # enforce single in_progress
        if status == 'in_progress':
            for it in items:
                if it.get('status') == 'in_progress' and int(it.get('id',0)) != iid:
                    it['status'] = 'pending'; changed = True
        for it in items:
            if int(it.get('id',0)) == iid:
                it['status'] = status; changed = True
                break
        if changed:
            db['items'] = items; db['updated'] = L['_skirmish_now_iso'](); L['_save_roadmap'](db)
        payload = {"ok": True, "items": items}
        L['record_flight']({"route": route, "method": "POST", "status": 200,
                           "duration_ms": int((time.time()-t0)*1000),
                           "request": {"id": iid, "status": status}, "response": payload})
        return jsonify(payload)
    except Exception as e:
        logging.exception("/roadmap/set_status error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

