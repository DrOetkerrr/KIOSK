from __future__ import annotations

from flask import Blueprint, jsonify

bp = Blueprint("audio", __name__)


def _clear_intro_state() -> None:
    try:
        from .. import webdash as wd  # type: ignore
    except Exception:
        return
    try:
        with wd.STATE_LOCK:
            wd.AUDIO_STATE.pop('intro', None)
    except Exception:
        pass


@bp.post("/audio/intro_complete")
def audio_intro_complete():
    try:
        _clear_intro_state()
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
