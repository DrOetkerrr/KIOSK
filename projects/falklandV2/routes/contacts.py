from __future__ import annotations

import logging
from flask import Blueprint, jsonify, request

bp = Blueprint("contacts_bp", __name__)


def _lazy():
    from ..webdash import CONTACTS_PATH, _load_json
    return locals()


@bp.get('/contacts/catalog')
def contacts_catalog():
    """Return contacts catalog (filterable by ?hostile=1 or ?friendly=1)."""
    L = _lazy()
    try:
        data = L['_load_json'](L['CONTACTS_PATH'], [])
        items = data.get('items') if isinstance(data, dict) else data
        arr = []
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            name = str(it.get('name',''))
            if not name:
                continue
            arr.append({'name': name, 'type': it.get('type') or it.get('class') or '', 'allegiance': it.get('allegiance') or ''})
        if request.args.get('hostile'):
            arr = [x for x in arr if str(x.get('allegiance','')).title() == 'Hostile']
        if request.args.get('friendly'):
            arr = [x for x in arr if str(x.get('allegiance','')).title() == 'Friendly']
        return jsonify({"ok": True, "items": arr})
    except Exception as e:
        logging.exception("/contacts/catalog error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

