from __future__ import annotations

import logging
from flask import Blueprint, jsonify, render_template

bp = Blueprint("pages", __name__)


def _lazy():
    from ..webdash import APP_VERSION
    return locals()


@bp.get("/")
def index():
    L = _lazy()
    try:
        return render_template("index.html", app_version=L['APP_VERSION'])
    except Exception as e:
        logging.exception("/ index error: %s", e)
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

