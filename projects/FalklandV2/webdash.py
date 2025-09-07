#!/usr/bin/env python3
"""
Falklands V2 — Web Dashboard (Step 13d fixed)
- Pause/Resume
- Click a contact row to lock
- Weapons panel (per-weapon table + READY vs locked)
- Reset game
- Runs on 127.0.0.1:8080 to avoid macOS AirPlay conflict on 5000
"""

from __future__ import annotations
import threading, time, json
from pathlib import Path
from typing import Any, Dict, List, Optional
from flask import Flask, jsonify, request, Response

# --- Local imports
import sys
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import Engine
from subsystems import radar as rdar
from subsystems import contacts as cons
from subsystems import nav as navi
from subsystems import weapons as weap

app = Flask(__name__)

# --- Paths
DATA = ROOT / "data"
STATE = ROOT / "state"
RUNTIME = STATE / "runtime.json"
GAMECFG = DATA / "game.json"

# --- Engine runner in a background thread
ENG_LOCK = threading.Lock()
ENG: Optional[Engine] = None
RUN = True
PAUSED = False  # toggled by /api/pause

def _read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))

def _write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

def _fresh_runtime_state() -> Dict[str, Any]:
    """Build a fresh runtime.json using game.json start values."""
    game = _read_json(GAMECFG)
    start = game.get("start", {})
    cell = start.get("ship_cell", "K13")
    course = float(start.get("course_deg", 0.0))
    speed = float(start.get("speed_kts", 0.0))
    return {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "ship": {
            "cell": cell,
            "course_deg": course,
            "speed_kts": speed
        },
        "contacts": [],
        "radar": {"locked_contact_id": None}
    }

def engine_thread():
    """Background tick loop. Respects PAUSED, reuses ENG; reset hot-swaps it."""
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

# ---------- Snapshots / helpers

def _weapons_snapshot(locked_range_nm: Optional[float]) -> Dict[str, Any]:
    """
    Build weapons info:
      - status_line (legacy single-line summary)
      - table: list of { name, ammo, range, ready } (ready requires locked target distance within range and ammo > 0)
    """
    path = DATA / "ship.json"
    ship_name = "Own Ship"
    status_line = "WEAPONS: (no ship.json found)"
    table: List[Dict[str, Any]] = []
    if not path.exists():
        return {"ship_name": ship_name, "status_line": status_line, "table": table}

    try:
        ship = _read_json(path)
        # Header
        name = ship.get("name", ship_name); klass = ship.get("class", "")
        ship_name = f"{name} ({klass})" if klass else name
        # Legacy summary
        status_line = weap.weapons_status(ship)

        # Table
        w = ship.get("weapons", {})
        def in_range(rdef, rng_nm: float) -> Optional[bool]:
            if rng_nm is None:
                return None
            if rdef is None:
                return None
            if isinstance(rdef, (int, float)):
                return rng_nm <= float(rdef)
            if isinstance(rdef, list) and len(rdef) == 2:
                lo = float(rdef[0]) if rdef[0] is not None else None
                hi = float(rdef[1]) if rdef[1] is not None else None
                if lo is not None and rng_nm < lo: return False
                if hi is not None and rng_nm > hi: return False
                return True
            return None

        # Gun 4.5"
        if "gun_4_5in" in w:
            g = w["gun_4_5in"]
            ammo_he = int(g.get("ammo_he", 0))
            ammo_il = int(g.get("ammo_illum", 0))
            rng_def = g.get("effective_max_nm", g.get("range_nm"))
            ready = (in_range(rng_def, locked_range_nm) if locked_range_nm is not None else None)
            table.append({
                "name": "4.5in Mk.8",
                "ammo": f"HE={ammo_he} ILLUM={ammo_il}",
                "range": (f"≤{float(rng_def):.1f} nm" if isinstance(rng_def, (int,float))
                          else f"{('≥'+str(rng_def[0])) if rng_def and rng_def[0] else ''}"
                               f"{'–' if rng_def and (rng_def[0] or rng_def[1]) else ''}"
                               f"{('≤'+str(rng_def[1])) if rng_def and rng_def[1] else ''} nm"),
                "ready": (ready and ammo_he > 0)
            })

        # Sea Cat
        if "seacat" in w:
            sc = w["seacat"]; rounds = int(sc.get("rounds", 0)); rng_def = sc.get("range_nm")
            rd = in_range(rng_def, locked_range_nm) if locked_range_nm is not None else None
            table.append({
                "name": "Sea Cat",
                "ammo": f"{rounds}",
                "range": f"{('≥'+str(rng_def[0])) if rng_def and rng_def[0] else ''}"
                         f"{'–' if rng_def and (rng_def[0] or rng_def[1]) else ''}"
                         f"{('≤'+str(rng_def[1])) if rng_def and rng_def[1] else ''} nm",
                "ready": (rd and rounds > 0)
            })

        # 20mm Oerlikon
        if "oerlikon_20mm" in w:
            o = w["oerlikon_20mm"]; rounds = int(o.get("rounds", 0)); rng_def = o.get("range_nm")
            rd = in_range(rng_def, locked_range_nm) if locked_range_nm is not None else None
            table.append({
                "name": "20mm Oerlikon",
                "ammo": f"{rounds}",
                "range": f"{('≥'+str(rng_def[0])) if rng_def and rng_def[0] else ''}"
                         f"{'–' if rng_def and (rng_def[0] or rng_def[1]) else ''}"
                         f"{('≤'+str(rng_def[1])) if rng_def and rng_def[1] else ''} nm",
                "ready": (rd and rounds > 0)
            })

        # GAM-BO1 20mm
        if "gam_bo1_20mm" in w:
            g2 = w["gam_bo1_20mm"]; rounds = int(g2.get("rounds", 0)); rng_def = g2.get("range_nm")
            rd = in_range(rng_def, locked_range_nm) if locked_range_nm is not None else None
            table.append({
                "name": "GAM-BO1 20mm",
                "ammo": f"{rounds}",
                "range": f"{('≥'+str(rng_def[0])) if rng_def and rng_def[0] else ''}"
                         f"{'–' if rng_def and (rng_def[0] or rng_def[1]) else ''}"
                         f"{('≤'+str(rng_def[1])) if rng_def and rng_def[1] else ''} nm",
                "ready": (rd and rounds > 0)
            })

        # Exocet
        if "exocet_mm38" in w:
            ex = w["exocet_mm38"]; rounds = int(ex.get("rounds", 0)); rng_def = ex.get("range_nm")
            rd = in_range(rng_def, locked_range_nm) if locked_range_nm is not None else None
            table.append({
                "name": "Exocet MM38",
                "ammo": f"{rounds}",
                "range": f"{('≥'+str(rng_def[0])) if rng_def and rng_def[0] else ''}"
                         f"{'–' if rng_def and (rng_def[0] or rng_def[1]) else ''}"
                         f"{('≤'+str(rng_def[1])) if rng_def and rng_def[1] else ''} nm",
                "ready": (rd and rounds > 0)
            })

        # Corvus chaff
        if "corvus_chaff" in w:
            ch = w["corvus_chaff"]; salvoes = int(ch.get("salvoes", 0))
            table.append({
                "name": "Corvus chaff",
                "ammo": f"{salvoes}",
                "range": "—",
                "ready": None
            })

        return {"ship_name": ship_name, "status_line": status_line, "table": table}
    except Exception as e:
        return {"ship_name": ship_name, "status_line": f"WEAPONS: (error reading ship.json: {e})", "table": table}

def snapshot() -> Dict[str, Any]:
    """Build a JSON picture for the UI."""
    with ENG_LOCK:
        eng = ENG
        assert eng is not None
        sx, sy = eng._ship_xy()
        locked_id = eng.state.get("radar", {}).get("locked_contact_id")
        nearest = sorted(eng.pool.contacts, key=lambda c: cons.dist_nm_xy(c.x, c.y, sx, sy, eng.pool.grid))[:10]
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
        # locked distance (if any)
        locked_rng = None
        if locked_id is not None:
            tgt = next((c for c in eng.pool.contacts if c.id == locked_id), None)
            if tgt is not None:
                locked_rng = round(cons.dist_nm_xy(tgt.x, tgt.y, sx, sy, eng.pool.grid), 2)
        weapons = _weapons_snapshot(locked_rng)
        return {
            "hud": eng.hud(),
            "ship": {
                "cell": navi.format_cell(*navi.snapped_cell(
                    navi.NavState(eng.state["ship"]["pos"]["x"], eng.state["ship"]["pos"]["y"])
                )),
                "course_deg": round(course, 1),
                "speed_kts": round(speed, 1)
            },
            "radar": {
                "locked_contact_id": locked_id,
                "locked_range_nm": locked_rng,
                "status_line": rdar.status_line(eng.pool, (sx, sy), locked_id=locked_id, max_list=3)
            },
            "contacts": contacts,
            "weapons": weapons,
            "paused": PAUSED,
        }

# --- Routes

@app.get("/")
def index() -> Response:
    html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Falklands V2 — Web Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 20px; }
    h1 { margin: 0 0 8px; }
    .row { display: flex; gap: 16px; flex-wrap: wrap; }
    .card { border: 1px solid #ddd; border-radius: 12px; padding: 12px 14px; box-shadow: 0 1px 4px rgba(0,0,0,.05); }
    .card h2 { margin: 0 0 8px; font-size: 16px; }
    #contacts table { border-collapse: collapse; width: 100%; }
    #contacts th, #contacts td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; font-variant-numeric: tabular-nums; }
    .controls label { display: inline-block; margin-right: 8px; }
    input[type=number] { width: 90px; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .pill { display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; background:#f2f2f2; }
    .badge { padding:2px 8px; border-radius:999px; background:#eee; font-size:12px; }
    .ready { background:#d9f2d9; }
    .notready { background:#f7d9d9; }
    tr.clickable { cursor: pointer; }
  </style>
</head>
<body>
  <h1>Falklands V2 — Web Dashboard</h1>
  <div class="row">
    <div id="hud" class="card" style="flex:1 1 460px;">
      <h2>HUD</h2>
      <div class="mono" id="hud_line">loading…</div>
    </div>
    <div id="ship" class="card" style="flex:1 1 360px;">
      <h2>Own Ship</h2>
      <div>Cell: <span id="ship_cell" class="pill">—</span></div>
      <div>Course: <span id="ship_course" class="mono">—</span>°</div>
      <div>Speed: <span id="ship_speed" class="mono">—</span> kts</div>
      <div class="controls" style="margin-top:10px;">
        <label>Set course <input type="number" id="set_course" min="0" max="359" step="1"></label>
        <label>Set speed <input type="number" id="set_speed" min="0" step="1"></label>
        <button onclick="helm()">Apply</button>
        <button style="margin-left:8px;" onclick="resetGame()">Reset</button>
        <button style="margin-left:8px;" id="pauseBtn" onclick="togglePause()">Pause</button>
      </div>
    </div>
    <div id="radar" class="card" style="flex:1 1 520px;">
      <h2>Radar</h2>
      <div class="mono" id="radar_line">loading…</div>
      <div style="margin-top:8px;">
        <button onclick="scan()">Scan now</button>
        <button onclick="unlock()">Unlock</button>
        <label>Lock #<input type="number" id="lock_id" min="1" step="1" style="width:80px;"> <button onclick="lock()">Go</button></label>
      </div>
    </div>
  </div>

  <div class="row" style="margin-top:16px;">
    <div id="weapons" class="card" style="flex:1 1 100%;">
      <h2>Weapons</h2>
      <div class="mono" id="weapons_ship">—</div>
      <div class="mono" id="weapons_line" style="margin-top:6px;">loading…</div>
      <div style="margin-top:8px; overflow-x:auto;">
        <table>
          <thead><tr><th>Weapon</th><th>Ammo</th><th>Range</th><th>READY vs locked</th></tr></thead>
          <tbody id="weapons_table"><tr><td colspan="4">—</td></tr></tbody>
        </table>
      </div>
      <div id="weapons_hint" class="mono" style="margin-top:6px; font-size:12px; color:#666;"></div>
    </div>
  </div>

  <div id="contacts" class="card" style="margin-top:16px;">
    <h2>Nearest Contacts (click a row to lock)</h2>
    <table>
      <thead><tr><th>CELL</th><th>TYPE</th><th>NAME</th><th>RANGE</th><th>CRS</th><th>SPD</th><th>ID</th></tr></thead>
      <tbody id="contacts_body"><tr><td colspan="7">loading…</td></tr></tbody>
    </table>
  </div>

<script>
async function load() {
  const r = await fetch('/api/status');
  const j = await r.json();

  // HUD + ship
  document.getElementById('hud_line').textContent = j.hud;
  document.getElementById('ship_cell').textContent = j.ship.cell;
  document.getElementById('ship_course').textContent = j.ship.course_deg;
  document.getElementById('ship_speed').textContent = j.ship.speed_kts;

  // Pause button label
  document.getElementById('pauseBtn').textContent = j.paused ? 'Resume' : 'Pause';

  // Radar
  document.getElementById('radar_line').textContent = j.radar.status_line;

  // Weapons
  document.getElementById('weapons_ship').textContent = j.weapons.ship_name;
  document.getElementById('weapons_line').textContent = j.weapons.status_line;

  const wt = document.getElementById('weapons_table');
  wt.innerHTML = '';
  for (const row of (j.weapons.table || [])) {
    const tr = document.createElement('tr');
    const badge = (row.ready === true) ? '<span class="badge ready">READY</span>'
                : (row.ready === false) ? '<span class="badge notready">OUT</span>'
                : '<span class="badge">N/A</span>';
    tr.innerHTML = `<td>${row.name}</td><td class="mono">${row.ammo}</td><td class="mono">${row.range}</td><td>${badge}</td>`;
    wt.appendChild(tr);
  }
  const hint = (j.radar.locked_contact_id && j.radar.locked_range_nm !== null)
      ? `Locked target range: ${j.radar.locked_range_nm.toFixed(2)} nm`
      : `Lock a target to evaluate weapon ranges.`;
  document.getElementById('weapons_hint').textContent = hint;

  // Contacts table (click to lock)
  const tb = document.getElementById('contacts_body');
  tb.innerHTML = '';
  if (j.contacts.length === 0) {
    tb.innerHTML = '<tr><td colspan="7">No contacts.</td></tr>';
  } else {
    for (const c of j.contacts) {
      const tr = document.createElement('tr');
      tr.className = 'clickable';
      tr.onclick = () => lockById(c.id);
      tr.innerHTML = `
        <td class="mono">${c.cell}</td>
        <td>${c.type}</td>
        <td>${c.name} <span class="pill">${c.allegiance}</span></td>
        <td class="mono">${c.range_nm.toFixed(1)} nm</td>
        <td class="mono">${c.course_deg}°</td>
        <td class="mono">${c.speed_kts}</td>
        <td class="mono">#${String(c.id).padStart(2,'0')}</td>`;
      tb.appendChild(tr);
    }
  }
}

async function scan() { await fetch('/api/scan', {method:'POST'}); await load(); }
async function unlock() { await fetch('/api/unlock', {method:'POST'}); await load(); }
async function lock() {
  const id = parseInt(document.getElementById('lock_id').value);
  if (!id) return;
  await fetch('/api/lock', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
  await load();
}
async function lockById(id) {
  await fetch('/api/lock', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
  await load();
}
async function helm() {
  const c = document.getElementById('set_course').value;
  const s = document.getElementById('set_speed').value;
  const payload = {};
  if (c !== '') payload.course_deg = parseFloat(c);
  if (s !== '') payload.speed_kts = parseFloat(s);
  if (Object.keys(payload).length === 0) return;
  await fetch('/api/helm', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  await load();
}
async function resetGame() {
  const ok = confirm('Reset game to fresh state? Contacts will be cleared.');
  if (!ok) return;
  const r = await fetch('/api/reset', {method:'POST'});
  const j = await r.json();
  if (!j.ok) { alert('Reset failed: ' + (j.error || 'unknown error')); }
  await load();
}
async function togglePause() {
  const r = await fetch('/api/pause', {method:'POST'});
  const j = await r.json();
  document.getElementById('pauseBtn').textContent = j.paused ? 'Resume' : 'Pause';
}

load();
setInterval(load, 1000);
</script>
</body>
</html>"""
    return Response(html, mimetype="text/html")

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
    """Reset runtime state and hot-swap the Engine."""
    try:
        fresh = _fresh_runtime_state()
        _write_json(RUNTIME, fresh)
        with ENG_LOCK:
            global ENG
            ENG = Engine()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/api/pause")
def api_pause():
    """Toggle paused state server-side; return new state."""
    global PAUSED
    with ENG_LOCK:
        PAUSED = not PAUSED
        return jsonify({"ok": True, "paused": PAUSED})

def main():
    # start engine tick thread
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