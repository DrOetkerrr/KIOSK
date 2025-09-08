#!/usr/bin/env python3
from __future__ import annotations
import threading, time, json
from pathlib import Path
from typing import Any, Dict, List, Optional
from flask import Flask, jsonify, request, render_template, send_from_directory

import sys
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import Engine
from subsystems import radar as rdar
from subsystems import contacts as cons
from subsystems import nav as navi
from subsystems import engage as enga
from subsystems.hermes_cap import HermesCAP
from subsystems.audio import AudioManager

# Convoy class API
try:
    from subsystems.convoy import Convoy
except Exception:
    Convoy = None  # type: ignore

app = Flask(
    __name__,
    template_folder=str(ROOT / "templates"),
    static_folder=str(ROOT / "static"),
)

DATA = ROOT / "data"
STATE = ROOT / "state"
RUNTIME = STATE / "runtime.json"
AMMO_OVR = STATE / "ammo.json"
ARMING = STATE / "arming.json"
GAMECFG = DATA / "game.json"
SHIPCFG = DATA / "ship.json"
SOUNDS_DIR = DATA / "sounds"  # <-- your existing location

ENG_LOCK = threading.Lock()
ENG: Optional[Engine] = None
CAP: Optional[HermesCAP] = None
CONVOY: Optional["Convoy"] = None  # type: ignore
AUDIO: Optional[AudioManager] = None
RUN = True
PAUSED = False

# ---------- I/O helpers

def _read_json(p: Path, default: Any = None) -> Any:
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))

def _write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

def _fresh_runtime_state() -> Dict[str, Any]:
    game = _read_json(GAMECFG, {}) or {}
    start = game.get("start", {})
    cell = start.get("ship_cell", "K13")
    course = float(start.get("course_deg", 0.0))
    speed = float(start.get("speed_kts", 0.0))
    return {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "ship": {"cell": cell, "course_deg": course, "speed_kts": speed},
        "contacts": [],
        "radar": {"locked_contact_id": None}
    }

def _load_ship_cfg_with_overrides() -> Dict[str, Any]:
    base = _read_json(SHIPCFG, {}) or {}
    ovr = _read_json(AMMO_OVR, {}) or {}
    wbase = base.setdefault("weapons", {})
    wovr = (ovr or {}).get("weapons", {})
    for key, patch in (wovr or {}).items():
        if key not in wbase:
            continue
        for f, val in (patch or {}).items():
            if isinstance(val, (int, float)):
                wbase[key][f] = val
    return base

def _dec_ammo(wkey: str) -> None:
    ship = _read_json(SHIPCFG, {}) or {}
    w = (ship.get("weapons") or {}).get(wkey, {})
    ovr = _read_json(AMMO_OVR, {}) or {}
    o_w = ovr.setdefault("weapons", {}).setdefault(wkey, {})
    def setf(field: str, v: int) -> None:
        o_w[field] = max(0, int(v))
    if wkey == "gun_4_5in":
        he = int(w.get("ammo_he", 0)); setf("ammo_he", max(0, he-1))
    elif wkey in ("seacat","oerlikon_20mm","gam_bo1_20mm","exocet_mm38"):
        r = int(w.get("rounds", 0)); setf("rounds", max(0, r-1))
    elif wkey == "corvus_chaff":
        s = int(w.get("salvoes", 0)); setf("salvoes", max(0, s-1))
    _write_json(AMMO_OVR, ovr)

def _arming_now() -> Dict[str, Any]:
    return _read_json(ARMING, {"weapons": {}}) or {"weapons": {}}

def _save_arming(obj: Dict[str, Any]) -> None:
    _write_json(ARMING, obj)

def _ensure_armed_state_updated(now: float) -> Dict[str, Any]:
    st = _arming_now()
    changed = False
    for _, wst in list(st.get("weapons", {}).items()):
        arming_until = wst.get("arming_until")
        if arming_until and now >= float(arming_until):
            wst.pop("arming_until", None)
            wst["armed"] = True
            changed = True
    if changed:
        _save_arming(st)
    return st

def _start_arming(wkey: str, now: float, delay_s: int = 5) -> Dict[str, Any]:
    st = _arming_now()
    wst = st.setdefault("weapons", {}).setdefault(wkey, {})
    if not wst.get("armed") and not wst.get("arming_until"):
        wst["arming_until"] = now + delay_s
        _save_arming(st)
    return st

# ---------- engine thread

def engine_thread():
    global ENG, CAP, CONVOY, AUDIO, RUN, PAUSED
    if not RUNTIME.exists():
        _write_json(RUNTIME, _fresh_runtime_state())
    ENG = Engine()
    CAP = HermesCAP(DATA)
    if Convoy is not None:
        try:
            CONVOY = Convoy.load(DATA)
        except Exception:
            CONVOY = None
    AUDIO = AudioManager(DATA)

    # start ambience once (it loops client-side)
    AUDIO.play("bridge_ambience", replace=True)

    tick = float(ENG.game_cfg.get("tick_seconds", 1.0))
    while RUN:
        time.sleep(tick)
        with ENG_LOCK:
            if not PAUSED and ENG is not None:
                ENG.tick(tick)
                CAP.tick()
                _ensure_armed_state_updated(time.time())
                if AUDIO is not None:
                    AUDIO.tick()

# ---------- helpers

def _target_type_from_contact(t: Any) -> str:
    typ = str(getattr(t, "type","") or "").lower()
    if any(k in typ for k in ("air","jet","dagger","skyhawk","mirage","helo","helicopter")):
        return "air"
    return "ship"

def _contact_to_locked_obj(eng: Engine, locked_id: Optional[int], sx: float, sy: float) -> tuple[Optional[Dict[str, Any]], Optional[float], Optional[str]]:
    locked_rng = None; locked_obj = None; ttype = None
    if locked_id is not None:
        tgt = next((c for c in eng.pool.contacts if c.id == locked_id), None)
        if tgt is not None:
            locked_rng = float(cons.dist_nm_xy(tgt.x, tgt.y, sx, sy, eng.pool.grid))
            locked_rng = round(locked_rng, 2)
            ttype = _target_type_from_contact(tgt)
            locked_obj = {
                "id": tgt.id,
                "cell": cons.format_cell(int(round(tgt.x)), int(round(tgt.y))),
                "type": tgt.type,
                "name": tgt.name,
                "allegiance": tgt.allegiance,
                "range_nm": locked_rng,
                "course_deg": round(tgt.course_deg, 0),
                "speed_kts": round(tgt.speed_kts_game, 0)
            }
    return locked_obj, locked_rng, ttype

def _weapon_rows(ship_cfg: Dict[str, Any], locked_range_nm: Optional[float], target_type: Optional[str], arming: Dict[str, Any], now: float) -> Dict[str, Any]:
    table: List[Dict[str, Any]] = []
    name = ship_cfg.get("name","Own Ship"); klass = ship_cfg.get("class","")
    ship_name = f"{name} ({klass})" if klass else name

    W = ship_cfg.get("weapons", {})
    order = ["gun_4_5in","seacat","oerlikon_20mm","gam_bo1_20mm","exocet_mm38","corvus_chaff"]
    seen=set()

    def push(key: str, wdef: Dict[str, Any]) -> None:
        if key == "gun_4_5in":
            ammo = f"HE={int(wdef.get('ammo_he',0))} ILLUM={int(wdef.get('ammo_illum',0))}"
            ammo_ok = int(wdef.get("ammo_he",0)) > 0
            rdef = wdef.get("effective_max_nm", wdef.get("range_nm"))
        elif key in ("seacat","oerlikon_20mm","gam_bo1_20mm","exocet_mm38"):
            rounds=int(wdef.get("rounds",0)); ammo=f"{rounds}"; ammo_ok=rounds>0; rdef=wdef.get("range_nm")
        elif key == "corvus_chaff":
            s=int(wdef.get("salvoes",0)); ammo=f"{s}"; ammo_ok=s>0; rdef=None
        else:
            ammo="?"; ammo_ok=False; rdef=None

        valid = enga.weapon_valid_for_target(key, wdef, target_type)  # True/False/None
        if valid is True and locked_range_nm is not None:
            in_range = enga.in_range_flag(ship_cfg, key, locked_range_nm, target_type)
        elif valid is False:
            in_range = None
        else:
            in_range = None

        wst = arming.get("weapons", {}).get(key, {})
        armed = bool(wst.get("armed", False))
        arming_until = wst.get("arming_until")
        arming_s = int(max(0, float(arming_until) - now)) if arming_until else 0

        table.append({
            "key": key,
            "name": {
                "gun_4_5in": '4.5in Mk.8',
                "seacat": 'Sea Cat',
                "oerlikon_20mm": '20mm Oerlikon',
                "gam_bo1_20mm": 'GAM-BO1 20mm',
                "exocet_mm38": 'Exocet MM38',
                "corvus_chaff": 'Corvus chaff'
            }.get(key, key),
            "ammo": ammo,
            "range": ( "—" if rdef is None else
                       (f"≤{float(rdef):.1f} nm" if isinstance(rdef,(int,float)) else
                        f"{'≥'+str(rdef[0]) if rdef and rdef[0] is not None else ''}"
                        f"{'–' if rdef and (rdef[0] is not None or rdef[1] is not None) else ''}"
                        f"{'≤'+str(rdef[1]) if rdef and rdef[1] is not None else ''} nm") ),
            "ready": (in_range is True and ammo_ok),
            "in_range": in_range,
            "armed": armed,
            "arming_s": arming_s
        })

    for k in order:
        if k in W: push(k, W[k]); seen.add(k)
    for k,v in W.items():
        if k not in seen: push(k, v)

    return {"ship_name": ship_name, "table": table}

def _escorts_snapshot(eng: Engine, sx: float, sy: float, course: float, speed: float) -> List[Dict[str, Any]]:
    if CONVOY is None:
        return []
    try:
        snaps = CONVOY.update(sx, sy, course, speed, eng.pool.grid)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for e in snaps or []:
        out.append({
            "name": e.name,
            "cell": e.cell,
            "course_deg": float(e.course_deg),
            "speed_kts": float(e.speed_kts),
        })
    return out

# ---------- snapshot

def snapshot() -> Dict[str, Any]:
    with ENG_LOCK:
        eng = ENG; cap = CAP; audio = AUDIO
        assert eng is not None and cap is not None and audio is not None
        sx, sy = eng._ship_xy()
        locked_id = eng.state.get("radar", {}).get("locked_contact_id")

        # hostiles only
        nearest = [
            c for c in eng.pool.contacts
            if str(c.allegiance).lower().startswith("host")
        ]
        nearest.sort(key=lambda c: cons.dist_nm_xy(c.x, c.y, sx, sy, eng.pool.grid))
        nearest = nearest[:10]
        contacts = [{
            "id": c.id,
            "cell": cons.format_cell(int(round(c.x)), int(round(c.y))),
            "type": c.type,
            "name": c.name,
            "allegiance": c.allegiance,
            "range_nm": round(cons.dist_nm_xy(c.x, c.y, sx, sy, eng.pool.grid), 1),
            "course_deg": round(c.course_deg, 0),
            "speed_kts": round(c.speed_kts_game, 0)
        } for c in nearest]

        course, speed = eng._ship_course_speed()
        locked_obj, locked_rng, locked_ttype = _contact_to_locked_obj(eng, locked_id, sx, sy)

        ship_cfg = _load_ship_cfg_with_overrides()
        now = time.time()
        arming = _ensure_armed_state_updated(now)
        weapons = _weapon_rows(ship_cfg, locked_rng, locked_ttype, arming, now)
        escorts = _escorts_snapshot(eng, sx, sy, course, speed)

        cap_snap = cap.snapshot()
        audio_snap = audio.snapshot()

        return {
            "hud": eng.hud(),
            "ship": {
                "cell": navi.format_cell(*navi.snapped_cell(
                    navi.NavState(eng.state["ship"]["pos"]["x"], eng.state["ship"]["pos"]["y"])
                )),
                "course_deg": round(course, 1),
                "speed_kts": round(speed, 1)
            },
            "locked_target": locked_obj,
            "contacts": contacts,
            "weapons": weapons,
            "escorts": escorts,
            "cap": cap_snap,
            "audio": audio_snap,
            "paused": PAUSED,
        }

# ---------- routes

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
        if AMMO_OVR.exists(): AMMO_OVR.unlink()
        if ARMING.exists(): ARMING.unlink()
        with ENG_LOCK:
            global ENG, CAP, CONVOY, AUDIO
            ENG = Engine()
            CAP = HermesCAP(DATA)
            if Convoy is not None:
                CONVOY = Convoy.load(DATA)
            if AUDIO is None:
                AUDIO = AudioManager(DATA)
            else:
                AUDIO.clear()
                AUDIO.play("bridge_ambience", replace=True)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/pause")
def api_pause():
    global PAUSED
    with ENG_LOCK:
        PAUSED = not PAUSED
        return jsonify({"ok": True, "paused": PAUSED})

# ---- arming / fire

@app.post("/api/arm")
def api_arm():
    data = request.get_json(silent=True) or {}
    wkey = str(data.get("weapon") or "").strip()
    if not wkey:
        return jsonify({"ok": False, "error": "weapon key required"}), 400

    ship_cfg = _load_ship_cfg_with_overrides()
    wdef = (ship_cfg.get("weapons") or {}).get(wkey)
    if not wdef:
        return jsonify({"ok": False, "error": "unknown weapon"}), 400
    ammo_ok = False
    if wkey == "gun_4_5in":
        ammo_ok = int(wdef.get("ammo_he",0)) > 0
    elif wkey in ("seacat","oerlikon_20mm","gam_bo1_20mm","exocet_mm38"):
        ammo_ok = int(wdef.get("rounds",0)) > 0
    elif wkey == "corvus_chaff":
        ammo_ok = int(wdef.get("salvoes",0)) > 0
    if not ammo_ok:
        return jsonify({"ok": False, "error": "no ammo"}), 400

    now = time.time()
    st = _start_arming(wkey, now, delay_s=5)
    wst = st["weapons"].get(wkey, {})
    arming_until = wst.get("arming_until")
    armed = bool(wst.get("armed"))
    secs = int(max(0, float(arming_until)-now)) if arming_until else 0
    # audio cue for arming start (soft)
    if AUDIO is not None and not armed and arming_until:
        AUDIO.play("weapon_ready", cooldown_s=1.0, gain=0.4)  # quiet cue
    return jsonify({"ok": True, "weapon": wkey, "armed": armed, "arming_seconds": secs})

@app.post("/api/fire")
def api_fire():
    data = request.get_json(silent=True) or {}
    wkey = str(data.get("weapon") or "").strip()
    mode = str(data.get("mode") or "fire").strip().lower()
    if not wkey:
        return jsonify({"ok": False, "error": "weapon key required"}), 400

    now = time.time()
    st = _ensure_armed_state_updated(now)
    wst = st.get("weapons", {}).get(wkey, {})
    armed = bool(wst.get("armed", False))
    arming_until = wst.get("arming_until")

    if not armed:
        return jsonify({"ok": False, "error": "weapon not armed", "arming_seconds": int(max(0, (arming_until or now)-now))}), 400

    ship_cfg = _load_ship_cfg_with_overrides()

    with ENG_LOCK:
        eng = ENG; assert eng is not None
        if mode != "test":
            locked_id = eng.state.get("radar", {}).get("locked_contact_id")
            if locked_id is None:
                return jsonify({"ok": False, "error": "no target locked"}), 400
            sx, sy = eng._ship_xy()
            tgt = next((c for c in eng.pool.contacts if c.id == locked_id), None)
            if tgt is None:
                return jsonify({"ok": False, "error": "locked target not found"}), 400
            rng_nm = float(cons.dist_nm_xy(tgt.x, tgt.y, sx, sy, eng.pool.grid))
            ttype = _target_type_from_contact(tgt)

            # launch cue
            if AUDIO is not None:
                if wkey == "gun_4_5in":
                    AUDIO.play("gun_fire", cooldown_s=0.2)
                elif wkey == "seacat":
                    AUDIO.play("seacat_launch", cooldown_s=0.2)
                    AUDIO.schedule("missile_track", 1.0)
                elif wkey == "exocet_mm38":
                    AUDIO.play("exocet_launch", cooldown_s=0.2)
                    AUDIO.schedule("missile_track", 2.0)
                    AUDIO.schedule("exocet_terminal", 10.0)

            outcome = enga.fire_once(ship_cfg, enga.FireRequest(weapon=wkey, target_range_nm=rng_nm, target_type=ttype))
            if not outcome.ok:
                return jsonify({"ok": False, "error": outcome.reason, "in_range": outcome.in_range, "pk": outcome.pk_used}), 400

            _dec_ammo(wkey)

            destroyed = False
            if outcome.hit:
                if AUDIO is not None:
                    AUDIO.play("hit", cooldown_s=0.5)
                eng.pool.contacts = [c for c in eng.pool.contacts if c.id != tgt.id]
                if eng.state.get("radar", {}).get("locked_contact_id") == tgt.id:
                    rdar.unlock_contact(eng.state)
                eng._autosave()
                destroyed = True
            else:
                if AUDIO is not None:
                    AUDIO.play("splash", cooldown_s=0.5)

            st["weapons"][wkey] = {"armed": False}
            _save_arming(st)

            return jsonify({
                "ok": True,
                "mode": "fire",
                "weapon": wkey,
                "target": {"id": tgt.id, "name": tgt.name, "type": tgt.type, "cell": cons.format_cell(int(round(tgt.x)), int(round(tgt.y)))},
                "range_nm": round(rng_nm,2),
                "pk": outcome.pk_used,
                "hit": outcome.hit,
                "destroyed": destroyed,
            })
        else:
            # TEST FIRE path
            if AUDIO is not None:
                if wkey == "gun_4_5in":
                    AUDIO.play("gun_fire", cooldown_s=0.2)
                elif wkey == "seacat":
                    AUDIO.play("seacat_launch", cooldown_s=0.2)
                elif wkey == "exocet_mm38":
                    AUDIO.play("exocet_launch", cooldown_s=0.2)

            _dec_ammo(wkey)
            st["weapons"][wkey] = {"armed": False}
            _save_arming(st)
            return jsonify({"ok": True, "mode": "test", "weapon": wkey, "note": "test round expended"})

# ---------- CAP

@app.get("/api/cap/status")
def api_cap_status():
    with ENG_LOCK:
        assert CAP is not None
        return jsonify(CAP.snapshot())

@app.post("/api/cap/request")
def api_cap_request():
    with ENG_LOCK:
        eng = ENG; cap = CAP
        assert eng is not None and cap is not None
        locked_id = eng.state.get("radar", {}).get("locked_contact_id")
        if locked_id is None:
            return jsonify({"ok": False, "error": "no target locked"}), 400
        sx, sy = eng._ship_xy()
        tgt = next((c for c in eng.pool.contacts if c.id == locked_id), None)
        if tgt is None:
            return jsonify({"ok": False, "error": "locked target not found"}), 400
        dist_nm = float(cons.dist_nm_xy(tgt.x, tgt.y, sx, sy, eng.pool.grid))
        cell = cons.format_cell(int(round(tgt.x)), int(round(tgt.y)))
        result = cap.request_cap_to_cell(cell, distance_nm=dist_nm)
        return jsonify(result)

# ---------- serve sounds from data/sounds

@app.get("/sounds/<path:fname>")
def serve_sound(fname: str):
    # Browser will request /sounds/<file> and we send from data/sounds
    return send_from_directory(SOUNDS_DIR, fname, as_attachment=False)

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