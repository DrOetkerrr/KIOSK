"""
API blueprint for Falklands V2 dashboard.
Holds all /api/* routes; uses subsystems.runtime and ui_snapshot.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict

from flask import Blueprint, jsonify, request

# Local imports
import sys
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from subsystems import runtime as rt
from subsystems.ui_snapshot import build_snapshot
from engine import Engine
from subsystems import radar as rdar
from subsystems import contacts as cons
from subsystems.hermes_cap import HermesCAP
from subsystems.convoy import Convoy

api = Blueprint("api", __name__, url_prefix="/api")

# --- tiny helper for reset
def _write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

# ----- Routes ----------------------------------------------------------------

@api.get("/status")
def status():
    with rt.ENG_LOCK:
        return jsonify(build_snapshot(rt.ENG, rt.CAP, rt.CONVOY, rt.PAUSED, rt.DATA))  # type: ignore

@api.post("/scan")
def scan():
    with rt.ENG_LOCK:
        rt.ENG._radar_scan()  # type: ignore
    return jsonify({"ok": True})

@api.post("/unlock")
def unlock():
    with rt.ENG_LOCK:
        rdar.unlock_contact(rt.ENG.state)  # type: ignore
    return jsonify({"ok": True})

@api.post("/lock")
def lock():
    data = request.get_json(silent=True) or {}
    cid = int(data.get("id", 0))
    with rt.ENG_LOCK:
        pool_ids = [c.id for c in rt.ENG.pool.contacts]  # type: ignore
        if cid not in pool_ids:
            return jsonify({"ok": False, "error": f"contact #{cid} not found"}), 400
        rdar.lock_contact(rt.ENG.state, cid)  # type: ignore
    return jsonify({"ok": True})

@api.post("/helm")
def helm():
    data = request.get_json(silent=True) or {}
    with rt.ENG_LOCK:
        ship = rt.ENG.state.setdefault("ship", {})  # type: ignore
        if "course_deg" in data:
            ship["course_deg"] = float(data["course_deg"]) % 360.0
        if "speed_kts" in data:
            ship["speed_kts"] = max(0.0, float(data["speed_kts"]))
        rt.ENG._autosave()  # type: ignore
    return jsonify({"ok": True})

@api.post("/reset")
def reset():
    """Reset runtime.json and hot-swap Engine/CAP/Convoy inside runtime."""
    try:
        fresh = rt.fresh_state()