#!/usr/bin/env python3
"""
Falklands V2 — Web API + Engine (UI served from /web)
- Serves:   /            -> web/index.html
            /app.js      -> web/app.js
            /assets/sounds/<file> from data/sounds/
- APIs:     /api/status, /api/scan, /api/unlock, /api/lock, /api/helm,
            /api/reset, /api/pause, /api/refit, /api/sfx_test, /api/fire
- Features: cooldowns, in-flight events with travel time, HIT/MISS resolution,
            JSON-driven weapon profiles & sounds, 20mm burst (2×50),
            SFX queue drained to client each poll + immediate sound-test URL,
            wave-based hostile spawns with lull periods (see game.json: "radar").
"""

from __future__ import annotations
import json, random, threading, time
from pathlib import Path
from typing import Any, Dict, List, Optional
from flask import Flask, Response, jsonify, request, send_from_directory

# ---- Local imports ----------------------------------------------------------
import sys
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import Engine
from subsystems import contacts as cons
from subsystems import engage
from subsystems import nav as navi
from subsystems import radar as rdar
from subsystems import weapons as weap

# ---- App / paths ------------------------------------------------------------
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

# ---- Engine / runtime -------------------------------------------------------
ENG_LOCK = threading.Lock()
ENG: Optional[Engine] = None
RUN = True
PAUSED = False

ENG_LOG: List[str] = []
COOLDOWNS: Dict[str, float] = {}      # weapon_key -> unix_ts_ready
EVENTS: List[Dict[str, Any]] = []     # in-flight shots
EVENT_SEQ = 1

AUDIO: Dict[str, Any] = {}            # audio.json
PROFILES: Dict[str, Any] = {}         # weapon_profiles.json
SFX_QUEUE: List[str] = []             # URLs to play on next poll

# 20mm burst logic
BURST_ROUNDS = {"oerlikon_20mm": 50, "gam_bo1_20mm": 50}
BURSTS_PER_ENGAGEMENT = 2  # => consume 100 per engagement


# ---- Utils ------------------------------------------------------------------
def _read_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def _write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

def _load_configs() -> None:
    global AUDIO, PROFILES
    AUDIO = _read_json(AUDIO_FILE) or {}
    PROFILES = _read_json(PROFILES_FILE) or {}

def _fresh_runtime_state() -> Dict[str, Any]:
    game = _read_json(GAMECFG) or {}
    start = game.get("start", {})
    cell = start.get("ship_cell", "K13")
    course = float(start.get("course_deg", 0.0))
    speed = float(start.get("speed_kts", 0.0))
    return {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "ship": {"cell": cell, "course_deg": course, "speed_kts": speed},
        "contacts": [],
        "radar": {"locked_contact_id": None},
    }

def _sfx_push(key: str, event: str) -> None:
    """Queue a sound URL mapped in audio.json."""
    try:
        m = AUDIO.get(key, {})
        fname = m.get(event)
        base = AUDIO.get("base_url", "/assets/sounds/")
        if fname:
            SFX_QUEUE.append(base + fname)
    except Exception:
        pass

def _rounds_required_for_fire(key: str) -> int:
    if key in BURST_ROUNDS:
        return BURSTS_PER_ENGAGEMENT * BURST_ROUNDS[key]  # 2*50 = 100
    return 1

# ---- Profiles (timing / odds) ----------------------------------------------
def _weapon_profile(key: str) -> Dict[str, Any]:
    defaults = {
        "cooldown_s": 5.0,
        "travel_base_s": 0.0,
        "travel_per_nm_s": 2.0,
        "travel_min_s": 0.0,
        "hit_p": 0.25,
    }
    p = dict(defaults)
    p.update(PROFILES.get(key, {}))

    def travel_s(rnm: float) -> float:
        base = float(p.get("travel_base_s", 0.0))
        per = float(p.get("travel_per_nm_s", 0.0))
        min_s = float(p.get("travel_min_s", 0.0))
        return max(min_s, base + per * float(rnm))

    return {"cooldown_s": float(p["cooldown_s"]), "travel_s": travel_s, "hit_p": p.get("hit_p")}

# ---- Event resolution -------------------------------------------------------
def _resolve_due_events(now: float) -> None:
    global EVENTS, ENG_LOG
    if not EVENTS:
        return

    due = [e for e in EVENTS if e["ts_resolve"] <= now]
    if not due:
        return
    EVENTS = [e for e in EVENTS if e["ts_resolve"] > now]

    with ENG_LOCK:
        eng = ENG
        assert eng is not None

        for e in due:
            key = e["weapon"]
            prof = _weapon_profile(key)
            hit_p = prof.get("hit_p", 0.0) or 0.0

            if key == "corvus_chaff":
                ENG_LOG.append(f"[{time.strftime('%H:%M:%S')}] CHAFF cloud active.")
                _sfx_push("corvus_chaff", "deploy")
                continue

            tgt = next((c for c in eng.pool.contacts if c.id == e["target_id"]), None)
            if tgt is None:
                ENG_LOG.append(f"[{time.strftime('%H:%M:%S')}] {weap.display_name(key)} resolve: target lost.")
                continue

            roll = random.random()
            if roll < hit_p:
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

# ---- Engine thread ----------------------------------------------------------
def engine_thread():
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
                # Wave-based hostile spawns with lull periods
                newlogs = rdar.auto_tick(ENG, time.time())
                for line in newlogs:
                    ENG_LOG.append(f"[{time.strftime('%H:%M:%S')}] {line}")
        _resolve_due_events(time.time())

# ---- Snapshots --------------------------------------------------------------
def _locked_snapshot(eng: Engine, sx: float, sy: float) -> Dict[str, Any]:
    locked_id = eng.state.get("radar", {}).get("locked_contact_id")
    locked_rng = None
    locked_cell = None
    if locked_id is not None:
        tgt = next((c for c in eng.pool.contacts if c.id == locked_id), None)
        if tgt is not None:
            locked_rng = round(cons.dist_nm_xy(tgt.x, tgt.y, sx, sy, eng.pool.grid), 2)
            locked_cell = cons.format_cell(int(round(tgt.x)), int(round(tgt.y)))
    return {"id": locked_id, "range_nm": locked_rng, "cell": locked_cell}

def _weapons_snapshot(locked_range_nm: Optional[float]) -> Dict[str, Any]:
    ship_name = "Own Ship"
    if not SHIP_FILE.exists():
        return {"ship_name": ship_name, "status_line": "WEAPONS: (no ship.json)", "table": []}

    ship = _read_json(SHIP_FILE)
    name = ship.get("name", ship_name); klass = ship.get("class", "")
    ship_name = f"{name} ({klass})" if klass else name
    status_line = weap.weapons_status(ship)

    results = engage.assess_all(ship, locked_range_nm)
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
    with ENG_LOCK:
        eng = ENG
        assert eng is not None
        sx, sy = eng._ship_xy()

        nearest = sorted(eng.pool.contacts, key=lambda c: cons.dist_nm_xy(c.x, c.y, sx, sy, eng.pool.grid))[:10]
        contacts = [{
            "id": c.id,
            "cell": cons.format_cell(int(round(c.x)), int(round(c.y))),
            "type": c.type,
            "name": c.name,
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
            "contacts": contacts,
            "weapons": weapons,
            "paused": PAUSED,
            "engagements": ENG_LOG[-8:],
            "inflight": inflight,
            "sfx": sfx,
        }

# ---- Static routes ----------------------------------------------------------
@app.get("/")
def index() -> Response:
    return send_from_directory(WEB_DIR, "index.html", conditional=True)

@app.get("/app.js")
def app_js() -> Response:
    return send_from_directory(WEB_DIR, "app.js", conditional=True)

@app.get("/assets/sounds/<path:fname>")
def sounds_asset(fname: str):
    return send_from_directory(SOUNDS_DIR, fname, conditional=True)

# ---- API routes -------------------------------------------------------------
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
        fresh = _fresh_runtime_state()
        _write_json(RUNTIME, fresh)
        with ENG_LOCK:
            global ENG, ENG_LOG, EVENTS, COOLDOWNS
            ENG = Engine()
            ENG_LOG = []
            EVENTS = []
            COOLDOWNS = {}
        _load_configs()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/pause")
def api_pause():
    global PAUSED
    with ENG_LOCK:
        PAUSED = not PAUSED
    return jsonify({"ok": True, "paused": PAUSED})

@app.post("/api/refit")
def api_refit():
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
    """Queue UI sound and also return URL for immediate in-gesture playback."""
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
    global EVENT_SEQ
    data = request.get_json(silent=True) or {}
    key = str(data.get("weapon", "")).strip()
    mode = str(data.get("mode", "he")).lower()  # "he" or "illum" for 4.5in
    if not key:
        return jsonify({"ok": False, "error": "no weapon provided"}), 400

    ship = _read_json(SHIP_FILE)
    if not ship:
        return jsonify({"ok": False, "error": "ship.json missing"}), 500

    now = time.time()
    t_ready = COOLDOWNS.get(key, 0.0)
    if t_ready > now:
        return jsonify({"ok": False, "error": f"cooldown {round(t_ready - now, 1)}s"}), 400

    with ENG_LOCK:
        eng = ENG
        assert eng is not None
        sx, sy = eng._ship_xy()
        lock = _locked_snapshot(eng, sx, sy)

    rng = lock["range_nm"]
    cell = lock["cell"]
    if key != "corvus_chaff" and (lock["id"] is None or rng is None):
        return jsonify({"ok": False, "error": "no locked target"}), 400

    wdefs = ship.get("weapons", {})
    if key not in wdefs:
        return jsonify({"ok": False, "error": f"unknown weapon '{key}'"}), 400

    res_list = engage.assess_all(ship, rng if key != "corvus_chaff" else None)
    res = next((r for r in res_list if r.key == key), None)
    if res is None:
        return jsonify({"ok": False, "error": "internal assessment error"}), 500

    # ammo checks
    primary, secondary = weap.get_ammo(ship, key)
    if key == "gun_4_5in":
        need = 1
        have = (secondary if mode == "illum" else primary) or 0
        if have < need:
            return jsonify({"ok": False, "error": f"no {'ILLUM' if mode=='illum' else 'HE'} ammo"}), 400
    elif key == "corvus_chaff":
        need = 1
        if (primary or 0) < need:
            return jsonify({"ok": False, "error": "no chaff remaining"}), 400
    else:
        need = _rounds_required_for_fire(key)  # 100 for 20mm
        if (primary or 0) < need:
            return jsonify({"ok": False, "error": f"need {need} rounds, have {(primary or 0)}"}), 400

    # final gate (chaff is ammo-only)
    if key != "corvus_chaff" and res.ready is not True:
        why = res.reason or "blocked"
        ENG_LOG.append(f"[{time.strftime('%H:%M:%S')}] FIRE {res.name}: BLOCKED — {why}")
        return jsonify({"ok": False, "error": why}), 400

    # consume ammo + persist
    if key == "gun_4_5in":
        ok = weap.consume_ammo(ship, key, 1, illum=(mode == "illum"))
    elif key == "corvus_chaff":
        ok = weap.consume_ammo(ship, key, 1)
    else:
        ok = weap.consume_ammo(ship, key, need)
    if not ok:
        return jsonify({"ok": False, "error": "ammo depletion race"}), 409
    _write_json(SHIP_FILE, ship)

    # cooldown + SFX
    prof = _weapon_profile(key)
    COOLDOWNS[key] = now + float(prof["cooldown_s"])
    stamp = time.strftime("%H:%M:%S")

    if key == "corvus_chaff":
        ENG_LOG.append(f"[{stamp}] DEPLOY Chaff — OK")
        _sfx_push("corvus_chaff", "deploy")
        return jsonify({"ok": True})

    # create in-flight event
    travel = float(prof["travel_s"](float(rng)))
    with ENG_LOCK:
        EVENTS.append({
            "id": EVENT_SEQ,
            "ts_fire": now,
            "ts_resolve": now + travel,
            "weapon": key,
            "target_id": lock["id"],
            "target_cell": cell,
            "range_nm": float(rng),
        })
        EVENT_SEQ += 1
    ENG_LOG.append(f"[{stamp}] LAUNCH {weap.display_name(key)} → #{lock['id']} @ {cell} ({rng} nm), T+{int(travel)}s")
    _sfx_push(key, "fire")
    return jsonify({"ok": True})

# ---- Main -------------------------------------------------------------------
def main():
    _load_configs()
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