"""
Runtime manager for Falklands V2.
- Owns Engine, CAP, Convoy instances
- Runs the background tick thread
- Provides start()/stop() and fresh_state()
"""

import threading, time, json
from pathlib import Path
from typing import Any, Dict, Optional

import sys
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import Engine
from subsystems.hermes_cap import HermesCAP
from subsystems.convoy import Convoy

# Globals
ENG: Optional[Engine] = None
CAP: Optional[HermesCAP] = None
CONVOY: Optional[Convoy] = None
ENG_LOCK = threading.Lock()
RUN = True
PAUSED = False

DATA = Path(__file__).resolve().parent.parent / "data"
STATE = Path(__file__).resolve().parent.parent / "state"
RUNTIME = STATE / "runtime.json"
GAMECFG = DATA / "game.json"

def _read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))

def _write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

def fresh_state() -> Dict[str, Any]:
    """Build a fresh runtime.json using game.json start values."""
    game = _read_json(GAMECFG)
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

def engine_thread():
    global ENG, CAP, CONVOY, RUN, PAUSED
    if not RUNTIME.exists():
        _write_json(RUNTIME, fresh_state())
    ENG = Engine()
    CAP = HermesCAP(DATA)
    CONVOY = Convoy.load(DATA)
    tick = float(ENG.game_cfg.get("tick_seconds", 1.0))
    while RUN:
        time.sleep(tick)
        with ENG_LOCK:
            if not PAUSED and ENG is not None:
                ENG.tick(tick)
                if CAP: CAP.tick()

def start() -> threading.Thread:
    """Start the background engine thread."""
    t = threading.Thread(target=engine_thread, daemon=True)
    t.start()
    return t

def stop(t: threading.Thread) -> None:
    """Stop the background thread cleanly."""
    global RUN
    RUN = False
    t.join(timeout=2)