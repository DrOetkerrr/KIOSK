from __future__ import annotations

import logging
import json
from collections import deque
from flask import Blueprint, jsonify, request

bp = Blueprint("flight", __name__)


def _lazy():
    # Late import to avoid circular dependencies
    from ..webdash import FLIGHT_PATH
    return locals()


@bp.get("/flight/tail")
def flight_tail():
    L = _lazy()
    try:
        try:
            n = int(request.args.get("n", "50"))
        except Exception:
            n = 50
        n = max(5, min(200, n))
        if not L['FLIGHT_PATH'].exists():
            return jsonify({"ok": True, "lines": []})
        items = []
        with L['FLIGHT_PATH'].open("r", encoding="utf-8") as f:
            dq = deque(f, maxlen=n)
        for ln in reversed(list(dq)):
            try:
                items.append(json.loads(ln))
            except Exception:
                continue
        return jsonify({"ok": True, "lines": items})
    except Exception as e:
        logging.exception("/flight/tail error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/flight/errors")
def flight_errors():
    L = _lazy()
    try:
        try:
            n = int(request.args.get("n", "100"))
        except Exception:
            n = 100
        n = max(5, min(500, n))
        if not L['FLIGHT_PATH'].exists():
            return jsonify({"ok": True, "lines": []})
        items = []
        with L['FLIGHT_PATH'].open("r", encoding="utf-8") as f:
            dq = deque(f, maxlen=n * 5)
        for ln in reversed(list(dq)):
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            st = rec.get('status')
            ok = (rec.get('response') or {}).get('ok')
            if isinstance(st, int) and st >= 400:
                items.append(rec)
            elif ok is False:
                items.append(rec)
            if len(items) >= n:
                break
        return jsonify({"ok": True, "lines": items})
    except Exception as e:
        logging.exception("/flight/errors error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/flight/info")
def flight_info():
    L = _lazy()
    try:
        if not L['FLIGHT_PATH'].exists():
            return jsonify({"ok": True, "exists": False, "path": str(L['FLIGHT_PATH'])})
        st = L['FLIGHT_PATH'].stat()
        return jsonify({
            "ok": True,
            "exists": True,
            "path": str(L['FLIGHT_PATH']),
            "size_bytes": int(st.st_size),
            "modified_ts": int(st.st_mtime),
        })
    except Exception as e:
        logging.exception("/flight/info error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

