from __future__ import annotations

# Falklands V2 — Web Dashboard (robust server)
# Repairs:
# - Avoid reliance on ENG.game_cfg; uses a safe tick helper.
# - Adds /health endpoint that always returns JSON.
# - Hardens /api/status to never return an empty body.
# - Configurable port via PORT env (default 5000).
# - Engine background thread is resilient and runs as a daemon.

# ---- stdlib imports and repo path setup ----
import os, sys, time, threading, logging, hashlib, pathlib
from pathlib import Path
from typing import Any, Dict
import json
from datetime import datetime, timezone

# Compute repo root so `projects.*` absolute imports resolve
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[2]  # .../kiosk
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Server port (default 5000)
try:
    PORT = int(os.environ.get("PORT", "5000"))
except Exception:
    PORT = 5000

# ---- third-party ----
from flask import Flask, jsonify, render_template  # type: ignore

# ---- engine import (absolute) ----
from projects.falklands.core.engine import Engine


# ---- Flask app ----
TPL_DIR = Path(__file__).parent / "templates"
app = Flask(__name__, template_folder=str(TPL_DIR))


# ---- Engine instance and helpers ----
ENG = Engine(state_path=Path.home() / "Documents" / "kiosk" / "falklands_state.json")


def get_tick_seconds() -> float:
    """Return engine tick seconds from best available source (default 1.0)."""
    # Common patterns we tolerate
    v = getattr(ENG, "tick_seconds", None)
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    cfg = getattr(ENG, "config", None)
    if isinstance(cfg, dict):
        try:
            w = float(cfg.get("tick_seconds", 0))
            if w > 0:
                return w
        except Exception:
            pass
    settings = getattr(ENG, "settings", None)
    if isinstance(settings, dict):
        try:
            w = float(settings.get("tick_seconds", 0))
            if w > 0:
                return w
        except Exception:
            pass
    return 1.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def engine_thread() -> None:
    """Background ticking loop; resilient to transient errors."""
    while True:
        try:
            dt = _clamp(float(get_tick_seconds()), 0.05, 1.0)
            ENG.tick(dt)
            time.sleep(dt)
        except Exception as e:
            logging.exception("engine_thread: tick failed: %s", e)
            time.sleep(0.5)


# Start the background thread (daemon)
_t = threading.Thread(target=engine_thread, daemon=True)
_t.start()


# ---- Flight recorder (lightweight) ----
LOG_DIR = REPO_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
FLIGHT_PATH = LOG_DIR / "flight.jsonl"

def _truncate(val: Any, max_len: int = 400) -> Any:
    if isinstance(val, str) and len(val) > max_len:
        return val[:max_len] + "…"
    return val

def record_flight(ev: Dict[str, Any]) -> None:
    try:
        base = {"ts": datetime.now(timezone.utc).isoformat(), "hud": None}
        try:
            base["hud"] = ENG.hud_line() if hasattr(ENG, "hud_line") else None
        except Exception:
            base["hud"] = None
        rec = {**base, **ev}
        # truncate long response values
        if isinstance(rec.get("response"), dict):
            rec["response"] = {k: _truncate(v) for k, v in rec["response"].items()}
        with FLIGHT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---- Template diagnostics ----
def _template_info(name: str = "index.html") -> Dict[str, Any]:
    p = (pathlib.Path(app.template_folder) / name).resolve()
    info: Dict[str, Any] = {
        "template_folder": str(pathlib.Path(app.template_folder).resolve()),
        "path": str(p),
        "exists": p.exists(),
    }
    if p.exists():
        b = p.read_bytes()
        info.update({
            "size": len(b),
            "mtime": os.path.getmtime(p),
            "sha1": hashlib.sha1(b).hexdigest(),
        })
    return info


# ---- Routes ----
@app.get("/debug/template")
def debug_template():
    return {"ok": True, "index": _template_info("index.html")}, 200


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    try:
        hud = ENG.hud_line() if hasattr(ENG, "hud_line") else "OK"
        return jsonify({"ok": True, "hud": hud})
    except Exception as e:
        logging.exception("/health error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/status")
def api_status():
    t0 = time.time()
    route = "/api/status"
    try:
        payload: Dict[str, Any] = {"ok": True}
        if hasattr(ENG, "public_state"):
            try:
                payload["state"] = ENG.public_state()  # type: ignore
            except Exception:
                payload["hud"] = ENG.hud_line() if hasattr(ENG, "hud_line") else "OK"
        else:
            payload["hud"] = ENG.hud_line() if hasattr(ENG, "hud_line") else "OK"
        # include any debug contacts (dev-only injection)
        try:
            payload["contacts"] = list(DEBUG_CONTACTS)
        except Exception:
            payload["contacts"] = []
        record_flight({
            "route": route, "method": "GET", "status": 200,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/api/status error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({
            "route": route, "method": "GET", "status": 500,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload), 500

@app.route("/api/command", methods=["GET", "POST"])
def api_command():
    from flask import request
    t0 = time.time()
    route = "/api/command"
    # Extract cmd from query or JSON body
    cmd = ""
    try:
        if request.method == "GET":
            cmd = (request.args.get("cmd") or "").strip()
        else:
            if request.is_json:
                data = request.get_json(silent=True) or {}
                cmd = str(data.get("cmd", "")).strip()
            if not cmd:
                cmd = (request.args.get("cmd") or "").strip()
    except Exception:
        cmd = ""

    if not cmd:
        payload = {"ok": False, "error": "missing cmd"}
        record_flight({
            "route": route, "method": request.method, "status": 400,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {"cmd": cmd}, "response": payload,
        })
        return jsonify(payload), 400

    try:
        if hasattr(ENG, "exec_slash"):
            try:
                result = ENG.exec_slash(cmd)  # type: ignore
            except Exception as ee:
                result = f"ERR: {ee}"
        else:
            result = "ERR: command interface unavailable"

        payload = {"ok": True, "result": result}
        record_flight({
            "route": route, "method": request.method, "status": 200,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {"cmd": cmd}, "response": payload,
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/api/command error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({
            "route": route, "method": request.method, "status": 500,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {"cmd": cmd}, "response": payload,
        })
        return jsonify(payload), 500

# ---- Dev-only debug contacts injection ----
# In-memory store of contacts for UI testing (cleared on process restart)
DEBUG_CONTACTS: list[dict] = []
DEBUG_NEXT_ID: int = 1

def _default_cell_from_engine() -> str:
    try:
        # Prefer public_state to avoid reaching into internals
        if hasattr(ENG, "public_state"):
            st = ENG.public_state()  # type: ignore
            ship = st.get("ship", {}) if isinstance(st, dict) else {}
            col = int(ship.get("col", 50))
            row = int(ship.get("row", 50))
            return f"{col}-{row+1}"
    except Exception:
        pass
    return "50-51"

def _make_debug_contact(cell: str | None = None,
                        name: str | None = None,
                        typ: str | None = None,
                        range_nm: float | None = None,
                        course: int | None = None,
                        speed: int | None = None) -> dict:
    global DEBUG_NEXT_ID
    cid = int(DEBUG_NEXT_ID)
    DEBUG_NEXT_ID += 1
    c = {
        "id": cid,
        "cell": cell or _default_cell_from_engine(),
        "name": name or "Contact",
        "type": typ or "Unknown",
        "range_nm": float(range_nm if range_nm is not None else 10.0),
        "course": int(course if course is not None else 90),
        "speed": int(speed if speed is not None else 300),
    }
    return c

@app.get("/debug/spawn_contact")
def debug_spawn_contact():
    from flask import request
    t0 = time.time()
    route = "/debug/spawn_contact"
    try:
        args = request.args
        cell = args.get("cell") or None
        name = args.get("name") or None
        typ = args.get("type") or None
        try:
            rng = float(args.get("range", "")) if args.get("range") is not None else None
        except Exception:
            rng = None
        try:
            crs = int(float(args.get("course", ""))) if args.get("course") is not None else None
        except Exception:
            crs = None
        try:
            spd = int(float(args.get("speed", ""))) if args.get("speed") is not None else None
        except Exception:
            spd = None

        contact = _make_debug_contact(cell=cell, name=name, typ=typ, range_nm=rng, course=crs, speed=spd)
        DEBUG_CONTACTS.append(contact)
        payload = {"ok": True, "added": contact, "count": len(DEBUG_CONTACTS)}
        record_flight({
            "route": route, "method": "GET", "status": 200,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {k: args.get(k) for k in ("cell","name","type","range","course","speed")},
            "response": payload,
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/debug/spawn_contact error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({
            "route": route, "method": "GET", "status": 500,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload), 500

@app.route("/debug/clear_contacts", methods=["POST", "GET"])
def debug_clear_contacts():
    t0 = time.time()
    route = "/debug/clear_contacts"
    try:
        DEBUG_CONTACTS.clear()
        payload = {"ok": True, "cleared": True}
        record_flight({
            "route": route, "method": "POST", "status": 200,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload)
    except Exception as e:
        logging.exception("/debug/clear_contacts error: %s", e)
        payload = {"ok": False, "error": str(e)}
        record_flight({
            "route": route, "method": "POST", "status": 500,
            "duration_ms": int((time.time()-t0)*1000),
            "request": {}, "response": payload,
        })
        return jsonify(payload), 500

@app.get("/flight/tail")
def flight_tail():
    try:
        from flask import request
        try:
            n = int(request.args.get("n", "50"))
        except Exception:
            n = 50
        n = max(5, min(200, n))
        if not FLIGHT_PATH.exists():
            return jsonify({"ok": True, "lines": []})
        with FLIGHT_PATH.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        tail = list(reversed(lines[-n:]))
        items = []
        for ln in tail:
            try:
                items.append(json.loads(ln))
            except Exception:
                continue
        return jsonify({"ok": True, "lines": items})
    except Exception as e:
        logging.exception("/flight/tail error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ---- Main ----
if __name__ == "__main__":
    # Keep logs concise
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    info = _template_info("index.html")
    print(f"[webdash] templates -> {info['template_folder']} | index -> {info.get('path')} | sha1={info.get('sha1')}")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
