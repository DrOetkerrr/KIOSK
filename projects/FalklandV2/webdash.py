#!/usr/bin/env python3
"""
Falkland V2 — Web API facade (serves /web UI) + Engine loop

Static:
  /                    -> web/index.html
  /app.js              -> web/app.js
  /assets/sounds/<f>   -> data/sounds/<f>

APIs:
  GET  /api/status
  POST /api/scan         (report-only, never spawns)
  POST /api/unlock
  POST /api/lock         {id}
  POST /api/helm         {course_deg?, speed_kts?}
  POST /api/reset
  POST /api/pause
  POST /api/refit
  POST /api/sfx_test
  POST /api/fire         {weapon, mode?} -> uses subsystems.fire_control.fire
"""

from __future__ import annotations
import json, random, threading, time, sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from flask import Flask, Response, jsonify, request, send_from_directory

# ---- project paths ----------------------------------------------------------
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---- engine + subsystems ----------------------------------------------------
from engine import Engine
from subsystems import contacts as cons
from subsystems import nav as navi
from subsystems import radar as rdar
from subsystems import engage
from subsystems import weapons as weap

# ---- flask ------------------------------------------------------------------
app = Flask(__name__)

DATA = ROOT / "data"
STATE = ROOT / "state"
WEB_DIR = ROOT / "web"

RUNTIME = STATE / "runtime.json"
GAMECFG = DATA / "game.json"
SHIP_FILE = DATA / "ship.json"
LOADOUT_FILE = DATA / "ship_loadout.json"

AUDIO_FILE = DATA / "audio.json"
PROFILES_FILE = DATA / "weapon_profiles.json"
SOUNDS_DIR = DATA / "sounds"

# ---- runtime globals --------------------------------------------------------
ENG_LOCK = threading.Lock()
ENG: Optional[Engine] = None
THREAD_STARTED = False
RUN = True
PAUSED = False

# Logs, cooldowns, in-flight events
ENG_LOG: List[str] = []
COOLDOWNS: Dict[str, float] = {}          # weapon_key -> unix_ts_ready
EVENTS: List[Dict[str, Any]] = []         # pending resolution shots
EVENT_SEQ = 1

# Config caches
AUDIO: Dict[str, Any] = {}
PROFILES: Dict[str, Any] = {}
SFX_QUEUE: List[str] = []                  # absolute (served) URLs to play

# ---- helpers ----------------------------------------------------------------
def _read_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

def _fresh_runtime_state() -> Dict[str, Any]:
    game = _read_json(GAMECFG)
    start = (game.get("start") or {})
    cell = start.get("ship_cell", "K13")
    course = float(start.get("course_deg", 0.0))
    speed = float(start.get("speed_kts", 0.0))
    return {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ship": {"cell": cell, "course_deg": course, "speed_kts": speed, "pos": {}},
        "contacts": [],
        "radar": {"locked_contact_id": None},
    }

def _load_configs() -> None:
    global AUDIO, PROFILES
    AUDIO = _read_json(AUDIO_FILE) or {}
    PROFILES = _read_json(PROFILES_FILE) or {}

def _sfx_push(key: str, event: str) -> None:
    try:
        base = AUDIO.get("base_url", "/assets/sounds/")
        fname = (AUDIO.get(key) or {}).get(event)
        if fname:
            SFX_QUEUE.append(base + fname)
    except Exception:
        pass

def _weapon_profile(key: str) -> Dict[str, Any]:
    """Return callable travel_s(range_nm) and cooldown_s."""
    defaults = {
        "cooldown_s": 5.0,
        "travel_base_s": 0.0,
        "travel_per_nm_s": 2.0,
        "travel_min_s": 0.0,
        "hit_p": 0.25,
    }
    cfg = dict(defaults)
    cfg.update(PROFILES.get(key, {}))

    def travel_s(rnm: float) -> float:
        base = float(cfg.get("travel_base_s", 0.0))
        per = float(cfg.get("travel_per_nm_s", 0.0))
        min_s = float(cfg.get("travel_min_s", 0.0))
        return max(min_s, base + per * float(rnm))

    return {"cooldown_s": float(cfg["cooldown_s"]), "travel_s": travel_s, "hit_p": cfg.get("hit_p", 0.25)}

# ---- engine lifecycle -------------------------------------------------------
def _engine_loop():
    global RUN
    if not RUNTIME.exists():
        _write_json(RUNTIME, _fresh_runtime_state())
    with ENG_LOCK:
        global ENG
        if ENG is None:
            ENG = Engine()

    tick = float((ENG.game_cfg or {}).get("tick_seconds", 1.0))  # type: ignore
    while RUN:
        time.sleep(tick)
        now = time.time()
        with ENG_LOCK:
            if not PAUSED and ENG is not None:
                ENG.tick(tick)
                # background spawn cadence + fleet maintenance
                for line in rdar.auto_tick(ENG, now):
                    ENG_LOG.append(f"[{time.strftime('%H:%M:%S')}] {line}")
        _resolve_due_events(now)

def _ensure_engine() -> None:
    global THREAD_STARTED, ENG
    with ENG_LOCK:
        if ENG is None:
            if not RUNTIME.exists():
                _write_json(RUNTIME, _fresh_runtime_state())
            ENG = Engine()
        if not THREAD_STARTED:
            t = threading.Thread(target=_engine_loop, daemon=True)
            t.start()
            THREAD_STARTED = True

# ---- shot resolution --------------------------------------------------------
def _resolve_due_events(now: float) -> None:
    global EVENTS
    if not EVENTS:
        return
    due = [e for e in EVENTS if e["ts_resolve"] <= now]
    if not due:
        return
    EVENTS[:] = [e for e in EVENTS if e["ts_resolve"] > now]

    with ENG_LOCK:
        eng = ENG
        if eng is None:
            return

        for e in due:
            key = e["weapon"]
            prof = _weapon_profile(key)
            hit_p = float(prof.get("hit_p", 0.25))
            tgt = next((c for c in eng.pool.contacts if c.id == e["target_id"]), None)
            if tgt is None:
                ENG_LOG.append(f"[{time.strftime('%H:%M:%S')}] {weap.display_name(key)} resolve: target lost.")
                continue

            if random.random() < hit_p:
                cell = cons.format_cell(int(round(tgt.x)), int(round(tgt.y)))
                eng.pool.contacts = [c for c in eng.pool.contacts if c.id != tgt.id]
                ENG_LOG.append(f"[{time.strftime('%H:%M:%S')}] HIT: {weap.display_name(key)} destroyed #{tgt.id} {tgt.type} at {cell}")
                _sfx_push(key, "hit")
                if eng.state.get("radar", {}).get("locked_contact_id") == tgt.id:
                    rdar.unlock_contact(eng.state)
                try:
                    eng._autosave()
                except Exception:
                    pass
            else:
                ENG_LOG.append(f"[{time.strftime('%H:%M:%S')}] MISS: {weap.display_name(key)} vs #{tgt.id} {tgt.type}")
                _sfx_push(key, "miss")

# ---- snapshots --------------------------------------------------------------
def _locked_snapshot(eng: Engine, sx: float, sy: float) -> Dict[str, Any]:
    locked_id = eng.state.get("radar", {}).get("locked_contact_id")
    info = {"id": None, "range_nm": None, "cell": None}
    if locked_id is None:
        return info
    tgt = next((c for c in eng.pool.contacts if c.id == locked_id), None)
    if tgt is None:
        return info
    rng = round(cons.dist_nm_xy(tgt.x, tgt.y, sx, sy, eng.pool.grid), 2)
    cell = cons.format_cell(int(round(tgt.x)), int(round(tgt.y)))
    return {"id": tgt.id, "range_nm": rng, "cell": cell}

def _weapons_snapshot(locked_range_nm: Optional[float]) -> Dict[str, Any]:
    ship = _read_json(SHIP_FILE)
    ship_name = "Own Ship"
    if ship:
        nm = ship.get("name")
        cl = ship.get("class")
        if nm and cl:
            ship_name = f"{nm} ({cl})"
        elif nm:
            ship_name = nm
    status_line = weap.weapons_status(ship or {})

    results = engage.assess_all(ship or {}, locked_range_nm)
    table: List[Dict[str, Any]] = []
    now = time.time()
    for r in results:
        cd_left = 0.0
        if r.key in COOLDOWNS:
            t_ready = COOLDOWNS[r.key]
            if t_ready > now:
                cd_left = round(t_ready - now, 1)
        table.append({
            "key": r.key,
            "name": r.name,
            "ammo": r.ammo_text,
            "range": r.range_text,
            "ready": (r.ready if cd_left == 0 else False),
            "reason": ("cooldown" if cd_left > 0 else r.reason),
            "cooldown_s": cd_left,
        })
    return {"ship_name": ship_name, "status_line": status_line, "table": table}

def snapshot() -> Dict[str, Any]:
    _ensure_engine()
    with ENG_LOCK:
        eng = ENG
        if eng is None:
            return {
                "hud": "HUD: (engine starting…)",
                "ship": {"cell": "—", "course_deg": 0, "speed_kts": 0},
                "radar": {"locked_contact_id": None, "locked_cell": None, "locked_range_nm": None, "status_line": "RADAR: 0 contact(s)"},
                "contacts": [], "weapons": {"ship_name": "Own Ship", "status_line": "", "table": []},
                "paused": PAUSED, "engagements": [], "inflight": [], "sfx": []
            }

        sx, sy = eng._ship_xy()
        nearest = sorted(eng.pool.contacts, key=lambda c: cons.dist_nm_xy(c.x, c.y, sx, sy, eng.pool.grid))[:10]
        contacts_view = [{
            "id": c.id,
            "cell": cons.format_cell(int(round(c.x)), int(round(c.y))),
            "type": c.type,
            "name": getattr(c, "name", ""),
            "allegiance": c.allegiance,
            "range_nm": round(cons.dist_nm_xy(c.x, c.y, sx, sy, eng.pool.grid), 1),
            "course_deg": round(c.course_deg, 0),
            "speed_kts": round(c.speed_kts_game, 0),
        } for c in nearest]

        course, speed = eng._ship_course_speed()
        lock = _locked_snapshot(eng, sx, sy)
        weapons = _weapons_snapshot(lock["range_nm"])

        inflight = []
        now = time.time()
        for e in sorted(EVENTS, key=lambda x: x["ts_resolve"])[:6]:
            inflight.append({
                "id": e["id"], "weapon": e["weapon"], "target_id": e["target_id"],
                "cell": e["target_cell"], "range_nm": e["range_nm"],
                "eta_s": max(0, int(round(e["ts_resolve"] - now))),
            })

        global SFX_QUEUE
        sfx = list(SFX_QUEUE)
        SFX_QUEUE.clear()

        return {
            "hud": eng.hud(),
            "ship": {
                "cell": navi.format_cell(*navi.snapped_cell(
                    navi.NavState(eng.state["ship"]["pos"]["x"], eng.state["ship"]["pos"]["y"])
                )),
                "course_deg": round(course, 1),
                "speed_kts": round(speed, 1),
            },
            "radar": {
                "locked_contact_id": lock["id"],
                "locked_cell": lock["cell"],
                "locked_range_nm": lock["range_nm"],
                "status_line": rdar.status_line(eng.pool, (sx, sy), locked_id=lock["id"], max_list=3),
            },
            "contacts": contacts_view,
            "weapons": weapons,
            "paused": PAUSED,
            "engagements": ENG_LOG[-8:],
            "inflight": inflight,
            "sfx": sfx,
        }

# ---- static routes ----------------------------------------------------------
@app.get("/")
def index() -> Response:
    return send_from_directory(WEB_DIR, "index.html", conditional=True)

@app.get("/app.js")
def app_js() -> Response:
    return send_from_directory(WEB_DIR, "app.js", conditional=True)

@app.get("/assets/sounds/<path:fname>")
def sounds_asset(fname: str):
    return send_from_directory(SOUNDS_DIR, fname, conditional=True)

# ---- api routes -------------------------------------------------------------
@app.get("/api/status")
def api_status():
    return jsonify(snapshot())

@app.post("/api/scan")
def api_scan():
    _ensure_engine()
    with ENG_LOCK:
        if ENG is None:
            return jsonify({"ok": False, "error": "engine not ready"}), 503
        line = rdar.manual_scan(ENG)
        ENG_LOG.append(f"[{time.strftime('%H:%M:%S')}] {line}")
    return jsonify({"ok": True})

@app.post("/api/unlock")
def api_unlock():
    _ensure_engine()
    with ENG_LOCK:
        if ENG is None:
            return jsonify({"ok": False, "error": "engine not ready"}), 503
        rdar.unlock_contact(ENG.state)  # type: ignore
    return jsonify({"ok": True})

@app.post("/api/lock")
def api_lock():
    _ensure_engine()
    data = request.get_json(silent=True) or {}
    cid = int(data.get("id", 0))
    with ENG_LOCK:
        if ENG is None:
            return jsonify({"ok": False, "error": "engine not ready"}), 503
        if cid not in [c.id for c in ENG.pool.contacts]:
            return jsonify({"ok": False, "error": f"contact #{cid} not found"}), 400
        rdar.lock_contact(ENG.state, cid)  # type: ignore
    return jsonify({"ok": True})

@app.post("/api/helm")
def api_helm():
    _ensure_engine()
    data = request.get_json(silent=True) or {}
    with ENG_LOCK:
        if ENG is None:
            return jsonify({"ok": False, "error": "engine not ready"}), 503
        ship = ENG.state.setdefault("ship", {})  # type: ignore
        if "course_deg" in data:
            ship["course_deg"] = float(data["course_deg"]) % 360.0
        if "speed_kts" in data:
            ship["speed_kts"] = max(0.0, float(data["speed_kts"]))
        ENG._autosave()  # type: ignore
    return jsonify({"ok": True})

@app.post("/api/reset")
def api_reset():
    _ensure_engine()
    try:
        fresh = _fresh_runtime_state()
        _write_json(RUNTIME, fresh)
        with ENG_LOCK:
            global ENG, ENG_LOG, EVENTS, COOLDOWNS, EVENT_SEQ
            ENG = Engine()
            ENG_LOG = []
            EVENTS = []
            COOLDOWNS = {}
            EVENT_SEQ = 1
        _load_configs()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/pause")
def api_pause():
    _ensure_engine()
    global PAUSED
    with ENG_LOCK:
        PAUSED = not PAUSED
    return jsonify({"ok": True, "paused": PAUSED})

@app.post("/api/refit")
def api_refit():
    _ensure_engine()
    ship = _read_json(SHIP_FILE)
    load = _read_json(LOADOUT_FILE)
    if not ship or "weapons" not in load:
        return jsonify({"ok": False, "error": "loadout missing"}), 500
    w = ship.setdefault("weapons", {})
    for k, v in load["weapons"].items():
        d = w.setdefault(k, {})
        for field, val in v.items():
            d[field] = val
    _write_json(SHIP_FILE, ship)
    ENG_LOG.append(f"[{time.strftime('%H:%M:%S')}] Refit complete (ammo restored).")
    return jsonify({"ok": True})

@app.post("/api/sfx_test")
def api_sfx_test():
    _ensure_engine()
    _sfx_push("ui", "radio_on")
    try:
        base = AUDIO.get("base_url", "/assets/sounds/")
        fname = (AUDIO.get("ui") or {}).get("radio_on")
        url = base + fname if fname else None
    except Exception:
        url = None
    return jsonify({"ok": True, "url": url})

@app.post("/api/fire")
def api_fire():
    global EVENT_SEQ  # must be first line inside function BEFORE any use
    _ensure_engine()

    data = request.get_json(silent=True) or {}
    key = str(data.get("weapon", "")).strip()
    mode = str(data.get("mode", "he")).lower() if data.get("mode") else None
    if not key:
        return jsonify({"ok": False, "error": "no weapon provided"}), 400

    with ENG_LOCK:
        eng = ENG
    if eng is None:
        return jsonify({"ok": False, "error": "engine not ready"}), 503

    # Delegate to centralized fire control for gating/ammo/log/SFX
    from subsystems.fire_control import fire as fc_fire
    result = fc_fire(eng, SHIP_FILE, key, mode, _sfx_push, COOLDOWNS, EVENTS, {"seq": EVENT_SEQ}, ENG_LOG)
    if not result.get("ok"):
        return jsonify(result), 400

    # Schedule resolution using weapon profiles (ETA)
    rng = float(result["range_nm"])
    tgt_id = int(result["target_id"])
    cell = result["target_cell"]

    prof = _weapon_profile(key)
    travel = float(prof["travel_s"](rng))
    now = time.time()

    with ENG_LOCK:
        EVENTS.append({
            "id": EVENT_SEQ,
            "ts_fire": now,
            "ts_resolve": now + travel,
            "weapon": key,
            "target_id": tgt_id,
            "target_cell": cell,
            "range_nm": rng,
        })
        EVENT_SEQ += 1

    ENG_LOG.append(f"[{time.strftime('%H:%M:%S')}] Missile away — ETA {int(travel)}s")
    return jsonify({"ok": True})

# ---- main -------------------------------------------------------------------
def main():
    _load_configs()
    _ensure_engine()
    try:
        app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)
    finally:
        global RUN
        RUN = False

if __name__ == "__main__":
    main()