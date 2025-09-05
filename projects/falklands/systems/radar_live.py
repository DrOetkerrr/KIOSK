from __future__ import annotations
import math, random, time
from typing import List
from falklands.data.contacts_catalog import CATALOG as CONTACT_CATALOG

# ---------- TUNING ----------
SPAWN_MIN_NM = 22.0
SPAWN_MAX_NM = 48.0

DETECT_MAX_NM = {"air":80.0, "surface":28.0, "missile":40.0, "unknown":30.0}
ALERT_CLOSE_NM = 10.0

# Instead of per-tick spam, use a cooldown + cap:
SPAWN_COOLDOWN_S      = 20.0    # min seconds between spawns
MAX_ACTIVE_CONTACTS   = 6       # total (detected or not)

GROUP_SPEED = {"air":(240,420), "surface":(8,24), "missile":(500,650), "unknown":(8,24)}

def _cap(v, lo, hi): return max(lo, min(hi, v))

def _bearing_deg_ship_to(ship_col, ship_row, tgt_col, tgt_row) -> float:
    dcol = tgt_col - ship_col; drow = tgt_row - ship_row
    north = -drow; east = dcol
    return math.degrees(math.atan2(east, north)) % 360.0

def _clock_from(abs_brg: float, ship_hdg: float) -> str:
    rel = (abs_brg - ship_hdg) % 360.0
    o = int((rel + 15) // 30) % 12
    return "12" if o == 0 else str(o)

def _range_nm(ship_col, ship_row, tgt_col, tgt_row, cell_nm: float) -> float:
    return math.hypot((tgt_col - ship_col)*cell_nm, (tgt_row - ship_row)*cell_nm)

def _grid_numeric(col_f: float, row_f: float, cols: int, rows: int) -> str:
    c = int(round(_cap(col_f, 1.0, float(cols))))
    r = int(round(_cap(row_f, 1.0, float(rows))))
    return f"{c}-{r:02d}"

def _step_motion(c: dict, dt_s: float, cols: int, rows: int):
    d_cells = (c["speed_kn"] * (dt_s/3600.0)) / c["CELL_NM"]
    th = math.radians(c["course_deg"])
    c["col_f"] += math.sin(th) * d_cells
    c["row_f"] += -math.cos(th) * d_cells
    c["col_f"] = _cap(c["col_f"], 1.0, float(cols))
    c["row_f"] = _cap(c["row_f"], 1.0, float(rows))

def _choose_catalog_entry():
    weights = [e.get("weight", 1) for e in CONTACT_CATALOG]
    return random.choices(CONTACT_CATALOG, weights=weights, k=1)[0]

class RadarLive:
    """ Live radar with numeric grid output + controlled spawn rate. """
    def __init__(self, st):
        self.st = st
        self._ensure_state()
        self._next_spawn_ts = 0.0

    def _ensure_state(self):
        d = self.st.data
        d.setdefault("CELL_NM", 4.0)
        d.setdefault("contacts", {})
        d.setdefault("primary_id", None)
        d.setdefault("MAP_COLS", 100)
        d.setdefault("MAP_ROWS", 100)

    def step(self, dt_s: float):
        d = self.st.data
        cell_nm = float(d.get("CELL_NM", 4.0))
        cols = int(d.get("MAP_COLS", 100)); rows = int(d.get("MAP_ROWS", 100))
        ship_col = float(d.get("ship_position", {}).get("col_f", 50.0))
        ship_row = float(d.get("ship_position", {}).get("row_f", 50.0))
        ship_hdg = float(d.get("ship_course_deg", 270.0))

        # maybe spawn (cooldown + cap)
        now = time.time()
        if now >= self._next_spawn_ts and len(d["contacts"]) < MAX_ACTIVE_CONTACTS:
            self._spawn_from_catalog(ship_col, ship_row, cell_nm, cols, rows)
            self._next_spawn_ts = now + SPAWN_COOLDOWN_S

        # move & recompute geometry
        for c in list(d["contacts"].values()):
            _step_motion(c, dt_s, cols, rows)
            rng = _range_nm(ship_col, ship_row, c["col_f"], c["row_f"], cell_nm)
            brg = _bearing_deg_ship_to(ship_col, ship_row, c["col_f"], c["row_f"])
            clk = _clock_from(brg, ship_hdg)
            c["range_nm"]    = rng
            c["bearing_deg"] = brg
            c["clock"]       = clk
            c["grid"]        = _grid_numeric(c["col_f"], c["row_f"], cols, rows)

            det_max = DETECT_MAX_NM.get(c["group"], DETECT_MAX_NM["unknown"])
            prev = c.get("_detected", False)
            nowd = (rng <= det_max)
            c["_first_detect"] = (nowd and not prev)
            c["_detected"] = nowd

            was_close = c.get("_was_close", False)
            is_close  = (rng <= ALERT_CLOSE_NM)
            c["_entered_close"] = (is_close and not was_close)
            c["_was_close"] = is_close

        # choose primary (closest detected)
        detected = [x for x in d["contacts"].values() if x.get("_detected")]
        d["primary_id"] = (min(detected, key=lambda x: x.get("range_nm", 9e9))["id"]
                           if detected else None)

    def check_alerts(self) -> List[str]:
        alerts: List[str] = []
        # We still build all alerts (engine/bridge will rate-limit speech),
        # but you can switch to "emit only one" by returning after first append.
        for c in self.st.data["contacts"].values():
            if c.get("_first_detect"):
                alerts.append(self._fmt_detect(c))
                c["_first_detect"] = False
            elif c.get("_entered_close"):
                alerts.append(self._fmt_close(c))
                c["_entered_close"] = False
        return alerts

    # internals
    def _spawn_from_catalog(self, ship_col: float, ship_row: float, cell_nm: float, cols: int, rows: int):
        entry = _choose_catalog_entry()
        group = entry.get("group", "unknown")
        gmin, gmax = GROUP_SPEED.get(group, GROUP_SPEED["unknown"])
        speed = random.uniform(gmin, gmax)

        rng_nm = random.uniform(SPAWN_MIN_NM, SPAWN_MAX_NM)
        brg = random.uniform(0, 360)
        d_cells = rng_nm / cell_nm
        th = math.radians(brg)
        col = _cap(ship_col + math.sin(th) * d_cells, 1.0, float(cols))
        row = _cap(ship_row + -math.cos(th) * d_cells, 1.0, float(rows))

        if group == "missile":
            to_ship = _bearing_deg_ship_to(col, row, ship_col, ship_row)
            course = (to_ship + random.uniform(-10, 10)) % 360.0
        elif group == "air":
            course = (brg + random.uniform(-35, 35)) % 360.0
        else:
            course = (brg + random.uniform(-12, 12)) % 360.0

        cid = f"{group}-{random.randint(100,999)}"
        self.st.data["contacts"][cid] = {
            "id": cid, "group": group,
            "name": entry.get("name", "contact"),
            "status": entry.get("status", "Unknown"),
            "armament": entry.get("armament", ""),
            "col_f": col, "row_f": row,
            "course_deg": course, "speed_kn": speed,
            "CELL_NM": cell_nm,
            "range_nm": rng_nm, "bearing_deg": brg, "clock": None, "grid": None,
            "_detected": False, "_first_detect": False,
            "_was_close": (rng_nm <= ALERT_CLOSE_NM), "_entered_close": False,
            "active": True,
        }

    def _fmt_detect(self, c: dict) -> str:
        nm = max(0.1, round(c.get("range_nm", 0.0), 1))
        clk = c.get("clock") or "?"
        grid = c.get("grid") or "??-??"
        name = c.get("name", "contact")
        status = c.get("status", "Unknown")
        if status and status not in ("Neutral", "Friendly"):
            return f"Captain, new {status.lower()} {name} at {clk} o'clock, {nm} NM, grid {grid}."
        return f"Captain, new {name} at {clk} o'clock, {nm} NM, grid {grid}."

    def _fmt_close(self, c: dict) -> str:
        nm = max(0.1, round(c.get("range_nm", 0.0), 1))
        clk = c.get("clock") or "?"
        grid = c.get("grid") or "??-??"
        name = c.get("name", "contact")
        return f"Captain, {name} now inside {ALERT_CLOSE_NM:.0f} NM at {clk} o'clock, {nm} NM, grid {grid}."