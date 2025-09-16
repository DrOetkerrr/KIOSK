# falklands/systems/nav.py
from __future__ import annotations
import math

def _cap(v, lo, hi): return max(lo, min(hi, v))

def _grid_label_numeric(col_f: float, row_f: float, cols: int, rows: int) -> str:
    c = int(round(_cap(col_f, 1.0, float(cols))))
    r = int(round(_cap(row_f, 1.0, float(rows))))
    return f"{c}-{r:02d}"

class NavSystem:
    """
    Navigation: heading/speed, movement integration, and grid readout on numeric grid.
    0°=North (row decreases), 90°=East (col increases), 180°=South (row increases), 270°=West.
    """
    def __init__(self, st):
        self.st = st

    TURN_RATE_DEG_S = 1.0  # new rule: course change limited to 1 deg/s

    def set_heading_speed(self, heading_deg: float | None = None, speed_kn: float | None = None):
        # Update V2-compatible ship fields
        ship = self.st.data.setdefault("ship", {"col": 50, "row": 50, "heading": 270.0, "speed": 15.0, "max_speed": 32.0})
        if heading_deg is not None:
            hdg = float(heading_deg) % 360.0
            # Set desired course; actual heading will slew toward this in step()
            self.st.data["ship_course_deg"] = hdg
        if speed_kn is not None:
            ms = float(ship.get("max_speed", self.st.data.get("MAX_SPEED", 32.0)))
            spd = _cap(float(speed_kn), 0.0, ms)
            self.st.data["ship_speed_kn"] = spd
            ship["speed"] = spd
        self.st.data["ship"] = ship
        return self.show({})

    def step(self, dt_s: float):
        pos = self.st.data.get("ship_position", {})
        col_f = float(pos.get("col_f", 50.0))
        row_f = float(pos.get("row_f", 50.0))

        # Desired vs actual heading: slew at TURN_RATE_DEG_S toward target
        ship = self.st.data.setdefault("ship", {"col": 50, "row": 50, "heading": 270.0, "speed": 15.0, "max_speed": 32.0})
        target = float(self.st.data.get("ship_course_deg", ship.get("heading", 270.0))) % 360.0
        cur = float(ship.get("heading", target)) % 360.0
        # Shortest signed angular diff in [-180, 180]
        diff = ((target - cur + 540.0) % 360.0) - 180.0
        max_step = float(self.TURN_RATE_DEG_S) * float(dt_s)
        if abs(diff) <= max_step:
            cur = target
        else:
            cur = (cur + (max_step if diff > 0 else -max_step)) % 360.0
        ship["heading"] = cur

        course = cur
        speed  = float(self.st.data.get("ship_speed_kn", 15.0))
        cell_nm = float(self.st.data.get("CELL_NM", 4.0))

        # integrate
        d_cells = (speed * (dt_s/3600.0)) / cell_nm
        th = math.radians(course)
        col_f += math.sin(th) * d_cells           # +east
        row_f += -math.cos(th) * d_cells          # +south

        cols = int(self.st.data.get("MAP_COLS", 100))
        rows = int(self.st.data.get("MAP_ROWS", 100))
        col_f = _cap(col_f, 1.0, float(cols))
        row_f = _cap(row_f, 1.0, float(rows))

        self.st.data["ship_position"] = {"col_f": col_f, "row_f": row_f}
        # Persist updated instantaneous heading back to ship
        self.st.data["ship"] = ship

    # slash command adapters
    def cmd_set(self, args: dict):
        # /nav set heading=XXX speed=YY
        h = args.get("heading") or args.get("hdg")
        s = args.get("speed") or args.get("spd")
        try:    h = None if h is None else float(h)
        except: h = None
        try:    s = None if s is None else float(s)
        except: s = None
        return self.set_heading_speed(h, s)

    # Engine compatibility shim used by Engine._cmd_nav
    def set(self, payload: dict):  # payload may already be numeric
        h = payload.get("heading")
        s = payload.get("speed")
        try: h = None if h is None else float(h)
        except Exception: h = None
        try: s = None if s is None else float(s)
        except Exception: s = None
        return self.set_heading_speed(h, s)

    # Optional tick adapter to avoid attribute errors in Engine.tick
    def tick(self, dt: float):
        try:
            self.step(float(dt))
        except Exception:
            pass

    def cmd_show(self, args: dict):
        return self.show(args)

    def show(self, args: dict):
        cols = int(self.st.data.get("MAP_COLS", 100))
        rows = int(self.st.data.get("MAP_ROWS", 100))
        pos = self.st.data.get("ship_position", {})
        col_f = float(pos.get("col_f", 50.0))
        row_f = float(pos.get("row_f", 50.0))
        lab = _grid_label_numeric(col_f, row_f, cols, rows)
        # Prefer V2-compatible ship display values
        ship = self.st.data.get("ship", {})
        # Show the instantaneous heading (slewed), not the target
        hdg = float(ship.get("heading", self.st.data.get("ship_course_deg", 270.0)))
        spd = float(ship.get("speed", self.st.data.get("ship_speed_kn", 15.0)))
        return f"NAV: {lab} hdg {hdg:.0f} spd {spd:.1f} kn"
