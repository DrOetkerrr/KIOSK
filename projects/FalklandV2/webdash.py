#!/usr/bin/env python3
"""
Falklands V2 — Web Dashboard (runtime + snapshot offloaded)
- Serves templates/index.html
- Uses subsystems.runtime for Engine/CAP/Convoy + tick thread
- Uses subsystems.ui_snapshot for building the JSON snapshot
"""

import json, sys, time
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request, Response, render_template

# --- Local imports -----------------------------------------------------------
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Runtime owns the engine thread and globals
from subsystems import runtime as rt
# Snapshot builder
from subsystems.ui_snapshot import build_snapshot
# For hot-reset we re-instantiate subsystems inside the runtime lock
from engine import Engine
from subsystems import radar as rdar
from subsystems import contacts as cons
from subsystems.hermes_cap import HermesCAP
from subsystems.convoy import Convoy

# --- Flask app ---------------------------------------------------------------
app = Flask(__name__, template_folder=str(ROOT / "templates"))

# --- Tiny local JSON helpers (used only for reset) --------------------------
def _write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

# --- Routes ------------------------------------------------------------------

@app.get("/")
def index() -> Response:
    return render_template("index.html")

@app.get("/api/status")
def api_status():
    with rt.ENG_LOCK:
        return jsonify(build_snapshot(rt.ENG, rt.CAP, rt.CONVOY, rt.PAUSED, rt.DATA))  # type: ignore

@app.post("/api/scan")
def api_scan():
    with rt.ENG_LOCK:
        rt.ENG._radar_scan()  # type: ignore
    return jsonify({"ok": True})

@app.post("/api/unlock")
def api_unlock():
    with rt.ENG_LOCK:
        rdar.unlock_contact(rt.ENG.state)  # type: ignore
    return jsonify({"ok": True})

@app.post("/api/lock")
def api_lock():
    data = request.get_json(silent=True) or {}
    cid = int(data.get("id", 0))
    with rt.ENG_LOCK:
        pool_ids = [c.id for c in rt.ENG.pool.contacts]  # type: ignore
        if cid not in pool_ids:
            return jsonify({"ok": False, "error": f"contact #{cid} not found"}), 400
        rdar.lock_contact(rt.ENG.state, cid)  # type: ignore
    return jsonify({"ok": True})

@app.post("/api/helm")
def api_helm():
    data = request.get_json(silent=True) or {}
    with rt.ENG_LOCK:
        ship = rt.ENG.state.setdefault("ship", {})  # type: ignore
        if "course_deg" in data:
            ship["course_deg"] = float(data["course_deg"]) % 360.0
        if "speed_kts" in data:
            ship["speed_kts"] = max(0.0, float(data["speed_kts"]))
        rt.ENG._autosave()  # type: ignore
    return jsonify({"ok": True})

@app.post("/api/reset")
def api_reset():
    """Reset runtime.json and hot-swap Engine/CAP/Convoy inside runtime."""
    try:
        fresh = rt.fresh_state()
        _write_json(rt.RUNTIME, fresh)
        with rt.ENG_LOCK:
            rt.ENG = Engine()
            rt.CAP = HermesCAP(rt.DATA)
            rt.CONVOY = Convoy.load(rt.DATA)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/pause")
def api_pause():
    """Toggle paused flag in runtime; return new state."""
    with rt.ENG_LOCK:
        rt.PAUSED = not rt.PAUSED
        return jsonify({"ok": True, "paused": rt.PAUSED})

# ---- CAP endpoints ----------------------------------------------------------

@app.get("/api/cap/status")
def api_cap_status():
    with rt.ENG_LOCK:
        if rt.CAP is None:
            return jsonify({"readiness": {}, "missions": []})
        return jsonify(rt.CAP.snapshot())

@app.post("/api/cap/request_locked")
def api_cap_request_locked():
    """Request CAP for the currently locked target (distance via own ship for now)."""
    with rt.ENG_LOCK:
        eng, cap = rt.ENG, rt.CAP
        if eng is None or cap is None:
            return jsonify({"ok": False, "message": "CAP unavailable"}), 503

        locked_id = eng.state.get("radar", {}).get("locked_contact_id")
        if locked_id is None:
            return jsonify({"ok": False, "message": "No locked target"}), 400

        tgt = next((c for c in eng.pool.contacts if c.id == locked_id), None)
        if tgt is None:
            return jsonify({"ok": False, "message": "Locked target not found"}), 404

        sx, sy = eng._ship_xy()
        dist_nm = cons.dist_nm_xy(tgt.x, tgt.y, sx, sy, eng.pool.grid)
        cell = cons.format_cell(int(round(tgt.x)), int(round(tgt.y)))
        res = cap.request_cap_to_cell(cell, distance_nm=float(dist_nm))
        return jsonify(res), (200 if res.get("ok") else 400)

# --- Entrypoint --------------------------------------------------------------

def main():
    # start runtime thread
    t = rt.start()
    try:
        app.run(host="127.0.0.1", port=8080, debug=False, threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        rt.stop(t)

if __name__ == "__main__":
    main()