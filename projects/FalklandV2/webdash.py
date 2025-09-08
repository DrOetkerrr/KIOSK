#!/usr/bin/env python3
"""
Falklands V2 — Web Dashboard (stable + radar-any list)

- Golden UI served from templates/index.html (no HTML here).
- Weapons logic via subsystems/engage.py.
- Arming computed via engage.arm_status.
- Firing mutates data/ship.json and stamps audio cue.
- Sounds served from /data/sounds/<file>.
- Radar card now receives a "radar_list" (nearest 5 ANY allegiance);
  bottom table remains nearest hostiles (top 10).
"""

from __future__ import annotations
import threading, time, json, sys
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple
from flask import Flask, jsonify, request, render_template, send_from_directory

# ----- paths / sys
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
STATE = ROOT / "state"
RUNTIME = STATE / "runtime.json"

# ----- project imports
from engine import Engine
from subsystems import contacts as cons
from subsystems import nav as navi
from subsystems import radar as rdar
from subsystems import convoy as convoy_mod
from subsystems import engage as enga
from subsystems.hermes_cap import HermesCAP

app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))

# =========================
# Utilities
# =========================

def _read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))

def _write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

def _fresh_runtime_state() -> Dict[str, Any]:
    game = _read_json(DATA / "game.json")
    start = game.get("start", {})
    return {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "ship": {
            "cell": start.get("ship_cell", "K13"),
            "course_deg": float(start.get("course_deg", 0.0)),
            "speed_kts": float(start.get("speed_kts", 0.0)),
        },
        "contacts": [],
        "radar": {"locked_contact_id": None},
        "arming": {},
        "audio": {},
    }

def _load_ship_cfg() -> Dict[str, Any]:
    p = DATA / "ship.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"weapons": {}}

def _save_ship_cfg(cfg: Dict[str, Any]) -> None:
    p = DATA / "ship.json"
    p.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

# =========================
# Engine thread
# =========================

ENG_LOCK = threading.Lock()
ENG: Optional[Engine] = None
RUN = True
PAUSED = False

def engine_thread():
    """Background tick loop for the simulation engine."""
    global ENG, RUN, PAUSED
    if not RUNTIME.exists():
        _write_json(RUNTIME, _fresh_runtime_state())
    ENG = Engine()
    tick = float(ENG.game_cfg.get("tick_seconds", 1.0))
    while RUN:
        time.sleep(tick)
        with ENG_LOCK:
            if not PAUSED and ENG is not None:
                ENG.tick(tick)

# =========================
# Subsystems
# =========================

CONVOY = convoy_mod.Convoy.load(DATA)
CAP = HermesCAP(DATA)

# =========================
# Snapshot helpers
# =========================

def _ship_xy(eng: Engine) -> Tuple[float, float]:
    return eng._ship_xy()

def _ship_course_speed(eng: Engine) -> Tuple[float, float]:
    return eng._ship_course_speed()

def _locked_target(eng: Engine, sx: float, sy: float) -> Optional[Dict[str, Any]]:
    locked_id = eng.state.get("radar", {}).get("locked_contact_id")
    if locked_id is None:
        return None
    tgt = next((c for c in eng.pool.contacts if c.id == locked_id), None)
    if not tgt:
        return None
    rng = round(cons.dist_nm_xy(tgt.x, tgt.y, sx, sy, eng.pool.grid), 2)
    return {
        "id": tgt.id,
        "cell": cons.format_cell(int(round(tgt.x)), int(round(tgt.y))),
        "type": getattr(tgt, "type", None),
        "name": getattr(tgt, "name", ""),
        "allegiance": getattr(tgt, "allegiance", ""),
        "range_nm": rng,
        "course_deg": round(getattr(tgt, "course_deg", 0.0), 0),
        "speed_kts": round(getattr(tgt, "speed_kts_game", 0.0), 0),
    }

def _escorts_snaps(eng: Engine, sx: float, sy: float, course: float, speed: float) -> List[Dict[str, Any]]:
    snaps = CONVOY.update(sx, sy, course, speed, eng.pool.grid)
    return [
        dict(
            id=e.id,
            name=e.name,
            klass=e.klass,
            type=e.type,
            allegiance=e.allegiance,
            cell=e.cell,
            course_deg=e.course_deg,
            speed_kts=e.speed_kts,
        )
        for e in snaps
    ]

def _weapons_rows(eng: Engine, locked: Optional[Dict[str, Any]], now: float) -> List[Dict[str, Any]]:
    ship_cfg = _load_ship_cfg()
    target = {"range_nm": locked.get("range_nm"), "type": locked.get("type")} if locked else None
    base = enga.summarize(ship_cfg, target)

    rows: List[Dict[str, Any]] = []
    for r in base:
        st = enga.arm_status(eng.state, r["key"], now)
        rows.append({
            "key": r["key"],
            "name": r["name"],
            "ammo": r["ammo_text"],
            "range": r["range_text"],
            "valid": r["valid"],
            "in_range": r["in_range"],
            "ready": True if r["ready"] is True else (False if r["ready"] is False else None),
            "reason": r["reason"],
            "armed": bool(st["armed"]),
            "arming_s": int(st["arming_s"]),
        })
    return rows

def snapshot() -> Dict[str, Any]:
    with ENG_LOCK:
        eng = ENG
        assert eng is not None

        sx, sy = _ship_xy(eng)
        course, speed = _ship_course_speed(eng)

        # Locked primary target
        locked = _locked_target(eng, sx, sy)

        # Radar card: nearest 5 ANY allegiance
        nearest_all = sorted(
            eng.pool.contacts,
            key=lambda c: cons.dist_nm_xy(c.x, c.y, sx, sy, eng.pool.grid)
        )[:5]
        radar_list = [{
            "id": c.id,
            "cell": cons.format_cell(int(round(c.x)), int(round(c.y))),
            "type": getattr(c, "type", None),
            "name": getattr(c, "name", ""),
            "allegiance": getattr(c, "allegiance", ""),
            "range_nm": round(cons.dist_nm_xy(c.x, c.y, sx, sy, eng.pool.grid), 2),
            "course_deg": round(getattr(c, "course_deg", 0.0), 0),
            "speed_kts": round(getattr(c, "speed_kts_game", 0.0), 0),
        } for c in nearest_all]

        # Bottom table: hostiles only, nearest 10
        nearest = sorted(eng.pool.contacts, key=lambda c: cons.dist_nm_xy(c.x, c.y, sx, sy, eng.pool.grid))
        hostiles = [c for c in nearest if str(getattr(c, "allegiance", "")).lower().startswith("hostile")][:10]
        contacts = [{
            "id": c.id,
            "cell": cons.format_cell(int(round(c.x)), int(round(c.y))),
            "type": getattr(c, "type", None),
            "name": getattr(c, "name", ""),
            "allegiance": getattr(c, "allegiance", ""),
            "range_nm": round(cons.dist_nm_xy(c.x, c.y, sx, sy, eng.pool.grid), 1),
            "course_deg": round(getattr(c, "course_deg", 0.0), 0),
            "speed_kts": round(getattr(c, "speed_kts_game", 0.0), 0),
        } for c in hostiles]

        weapons = _weapons_rows(eng, locked, time.time())
        escorts = _escorts_snaps(eng, sx, sy, course, speed)

        return {
            "hud": eng.hud(),
            "ship": {
                "cell": navi.format_cell(*navi.snapped_cell(
                    navi.NavState(eng.state["ship"]["pos"]["x"], eng.state["ship"]["pos"]["y"])
                )),
                "course_deg": round(course, 1),
                "speed_kts": round(speed, 1),
            },
            "escorts": escorts,
            "radar": {
                "locked_contact_id": eng.state.get("radar", {}).get("locked_contact_id"),
                "status_line": rdar.status_line(eng.pool, (sx, sy),
                                                locked_id=eng.state.get("radar", {}).get("locked_contact_id"),
                                                max_list=3),
            },
            "locked_target": locked,
            "radar_list": radar_list,      # <— new: nearest ANY for Radar card
            "contacts": contacts,          # bottom table: hostiles only
            "weapons": {"table": weapons},
            "cap": CAP.snapshot(now=time.time()),
            "paused": PAUSED,
            "audio": eng.state.get("audio", {}),
        }

# =========================
# Routes
# =========================

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/status")
def api_status():
    return jsonify(snapshot())

@app.post("/api/scan")
def api_scan():
    with ENG_LOCK:
        ENG._radar_scan()  # type: ignore
    return jsonify({"ok": True})

@app.post("/api/unlock")
def api_unlock():
    with ENG_LOCK:
        rdar.unlock_contact(ENG.state)  # type: ignore
    return jsonify({"ok": True})

@app.post("/api/lock")
def api_lock():
    data = request.get_json(silent=True) or {}
    cid = int(data.get("id", 0))
    with ENG_LOCK:
        pool_ids = [c.id for c in ENG.pool.contacts]  # type: ignore
        if cid not in pool_ids:
            return jsonify({"ok": False, "error": f"contact #{cid} not found"}), 400
        rdar.lock_contact(ENG.state, cid)  # type: ignore
    return jsonify({"ok": True})

@app.post("/api/helm")
def api_helm():
    data = request.get_json(silent=True) or {}
    with ENG_LOCK:
        ship = ENG.state.setdefault("ship", {})  # type: ignore
        if "course_deg" in data:
            ship["course_deg"] = float(data["course_deg"]) % 360.0
        if "speed_kts" in data:
            ship["speed_kts"] = max(0.0, float(data["speed_kts"]))
        ENG._autosave()  # type: ignore
    return jsonify({"ok": True})

@app.post("/api/reset")
def api_reset():
    try:
        _write_json(RUNTIME, _fresh_runtime_state())
        with ENG_LOCK:
            global ENG
            ENG = Engine()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/pause")
def api_pause():
    global PAUSED
    with ENG_LOCK:
        PAUSED = not PAUSED
        return jsonify({"ok": True, "paused": PAUSED})

# ----- CAP

@app.post("/api/cap/request")
def api_cap_request():
    with ENG_LOCK:
        eng = ENG  # type: ignore
        sx, sy = _ship_xy(eng)
        locked = _locked_target(eng, sx, sy)
        if not locked:
            return jsonify({"ok": False, "message": "No locked target"}), 400
        dist = float(locked["range_nm"])
        out = CAP.request_cap_to_cell(locked["cell"], distance_nm=dist, now=time.time())
        return jsonify(out)

@app.get("/api/cap/status")
def api_cap_status():
    return jsonify(CAP.snapshot(now=time.time()))

# ----- Weapons (arm + fire)

@app.post("/api/arm")
def api_arm():
    data = request.get_json(silent=True) or {}
    wkey = str(data.get("weapon", "")).strip()
    if not wkey:
        return jsonify({"ok": False, "error": "missing weapon"}), 400
    with ENG_LOCK:
        enga.arm_start(ENG.state, wkey, time.time())  # type: ignore
        st = enga.arm_status(ENG.state, wkey, time.time())  # type: ignore
        ENG._autosave()  # type: ignore
    return jsonify({"ok": True, "armed": st["armed"], "arming_s": st["arming_s"]})

@app.post("/api/fire")
def api_fire():
    data = request.get_json(silent=True) or {}
    wkey = str(data.get("weapon", "")).strip()
    mode = str(data.get("mode", "fire")).strip().lower()
    if mode not in ("fire", "test"):
        mode = "fire"
    if not wkey:
        return jsonify({"ok": False, "error": "missing weapon"}), 400

    with ENG_LOCK:
        eng = ENG  # type: ignore
        st = enga.arm_status(eng.state, wkey, time.time())
        if not st["armed"]:
            return jsonify({"ok": False, "error": f"{wkey} is not armed"}), 400

        sx, sy = _ship_xy(eng)
        locked = _locked_target(eng, sx, sy)
        rng_nm = locked["range_nm"] if (locked and mode == "fire") else None
        ttype = locked["type"] if (locked and mode == "fire") else None

        ship_cfg = _load_ship_cfg()
        outcome = enga.fire_once(
            ship_cfg,
            enga.FireRequest(weapon=wkey, target_range_nm=rng_nm, target_type=ttype, mode=mode)
        )
        if not outcome.get("ok"):
            return jsonify({"ok": False, "error": outcome.get("message", "blocked")}), 400

        _save_ship_cfg(ship_cfg)

        # consume arming (single-shot)
        arming = eng.state.setdefault("arming", {})
        rec = arming.get(wkey, {"armed": False, "arming_until": 0})
        rec["armed"] = False
        rec["arming_until"] = 0
        arming[wkey] = rec

        # audio cue
        audio = eng.state.setdefault("audio", {})
        audio["last_launch"] = {"weapon": wkey, "ts": time.time()}

        eng._autosave()  # type: ignore

        return jsonify({
            "ok": True,
            "message": outcome.get("message", "FIRED"),
            "weapon": wkey,
            "ammo_after": outcome.get("ammo_after")
        })

# ----- Serve sound files

@app.get("/data/sounds/<path:fname>")
def data_sounds(fname: str):
    return send_from_directory(str(DATA / "sounds"), fname, as_attachment=False)

# =========================
# Main
# =========================

def main():
    t = threading.Thread(target=engine_thread, daemon=True)
    t.start()
    try:
        app.run(host="127.0.0.1", port=8080, debug=False, threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        global RUN
        RUN = False
        t.join(timeout=2)

if __name__ == "__main__":
    main()