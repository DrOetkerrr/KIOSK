from __future__ import annotations

import contextlib
import logging
from flask import Blueprint, jsonify, render_template

bp = Blueprint("pages", __name__)


def _lazy():
    from .. import webdash as wd  # type: ignore
    return {
        'APP_VERSION': wd.APP_VERSION,
        'AUDIO_STATE': wd.AUDIO_STATE,
        'STATE_LOCK': getattr(wd, 'STATE_LOCK', None),
    }


@bp.get("/")
def index():
    L = _lazy()
    try:
        lock = L.get('STATE_LOCK')
        audio_state = L.get('AUDIO_STATE') or {}
        with (lock if lock is not None else contextlib.nullcontext()):
            intro_active = bool((audio_state or {}).get('intro'))
        return render_template("index.html", app_version=L['APP_VERSION'], intro_active=intro_active)
    except Exception as e:
        logging.exception("/ index error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/radio/test")
def radio_test_page():
    L = _lazy()
    try:
        return render_template("radio_test.html", app_version=L['APP_VERSION'])
    except Exception as e:
        logging.exception("/radio/test page error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/menu")
def menu_page():
    try:
        return render_template('menu.html')
    except Exception as e:
        logging.exception("/menu page error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/skirmish")
def skirmish_page():
    try:
        return render_template('skirmish.html')
    except Exception as e:
        logging.exception("/skirmish page error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500
