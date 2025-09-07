#!/usr/bin/env python3
"""
Falklands V2 — Text Dashboard (curses)
Shows a stable, auto-refreshing view instead of scroll spam.

Panels:
  - HUD (grid / own cell / COG / SOG / contact count)
  - Ship (course/speed)
  - Radar status (cell-first formatting)
  - Locked target (if any)
  - Nearest contacts table (cell, type/name, range, course, speed, id)
  - Footer with keybinds

Keys:
  q        quit
  space    pause/resume simulation
  s        radar scan now
  u        radar unlock
  c/C      decrease/increase course by 5°  (hold Shift = increase)
  v/V      decrease/increase speed by 1 kt (hold Shift = increase)

Notes:
- Uses your existing engine; no engine edits required.
- Respects game.json tick_seconds for pacing.
"""

from __future__ import annotations
import curses, time, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import Engine
from subsystems import radar as rdar
from subsystems import contacts as cons

def _range_nm(pool, sx, sy, c) -> float:
    return cons.dist_nm_xy(c.x, c.y, sx, sy, pool.grid)

def _fmt_cell(c) -> str:
    return cons.format_cell(int(round(c.x)), int(round(c.y)))

def _draw_box(win, title: str):
    h, w = win.getmaxyx()
    win.box()
    if title:
        win.addstr(0, 2, f" {title} ")

def dashboard(stdscr):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_GREEN, -1)
    curses.init_pair(4, curses.COLOR_RED, -1)
    accent = curses.color_pair(1)
    warn   = curses.color_pair(4)
    ok     = curses.color_pair(3)
    info   = curses.color_pair(2)

    eng = Engine()
    tick = float(eng.game_cfg.get("tick_seconds", 1.0))
    paused = False

    stdscr.nodelay(True)  # non-blocking getch
    stdscr.timeout(int(tick * 1000))  # refresh cadence

    while True:
        # --- Input
        ch = stdscr.getch()
        if ch != -1:
            if ch in (ord('q'), ord('Q')):
                break
            elif ch == ord(' '):
                paused = not paused
            elif ch in (ord('s'), ord('S')):
                eng._radar_scan()  # immediate sweep
            elif ch in (ord('u'), ord('U')):
                # unlock
                rdar.unlock_contact(eng.state)
            elif ch == ord('c'):
                # course -5
                ship = eng.state.setdefault("ship", {})
                ship["course_deg"] = (float(ship.get("course_deg", 0.0)) - 5.0) % 360.0
            elif ch == ord('C'):
                # course +5
                ship = eng.state.setdefault("ship", {})
                ship["course_deg"] = (float(ship.get("course_deg", 0.0)) + 5.0) % 360.0
            elif ch == ord('v'):
                # speed -1
                ship = eng.state.setdefault("ship", {})
                ship["speed_kts"] = max(0.0, float(ship.get("speed_kts", 0.0)) - 1.0)
            elif ch == ord('V'):
                # speed +1
                ship = eng.state.setdefault("ship", {})
                ship["speed_kts"] = max(0.0, float(ship.get("speed_kts", 0.0)) + 1.0)

        # --- Sim tick
        if not paused:
            eng.tick(tick)

        # --- Layout
        stdscr.erase()
        H, W = stdscr.getmaxyx()
        # Minimal sizes guard
        minW, minH = 80, 24
        if W < minW or H < minH:
            stdscr.addstr(0, 0, f"Resize terminal to at least {minW}x{minH}. Now: {W}x{H}")
            stdscr.refresh()
            continue

        # Regions
        hud_h = 3
        ship_h = 3
        radar_h = 4
        locked_h = 3
        table_h = H - (hud_h + ship_h + radar_h + locked_h + 3)  # +3 for footer and borders
        table_h = max(6, table_h)

        hud_win    = stdscr.derwin(hud_h, W, 0, 0)
        ship_win   = stdscr.derwin(ship_h, W, hud_h, 0)
        radar_win  = stdscr.derwin(radar_h, W, hud_h+ship_h, 0)
        locked_win = stdscr.derwin(locked_h, W, hud_h+ship_h+radar_h, 0)
        table_win  = stdscr.derwin(table_h, W, hud_h+ship_h+radar_h+locked_h, 0)
        foot_win   = stdscr.derwin(1, W, H-1, 0)

        # --- HUD
        _draw_box(hud_win, " HUD ")
        hud_win.attron(accent)
        hud_win.addstr(1, 2, eng.hud())
        hud_win.attroff(accent)

        # --- Ship panel
        _draw_box(ship_win, " SHIP ")
        course, speed = eng._ship_course_speed()
        ship_win.addstr(1, 2, f"Course: {course:5.1f}°   Speed: {speed:4.1f} kts   "
                               f"Contacts: {len(eng.pool.contacts)}   "
                               f"{'PAUSED' if paused else 'RUNNING'}",
                        ok if not paused else warn)

        # --- Radar status
        _draw_box(radar_win, " RADAR ")
        sx, sy = eng._ship_xy()
        locked_id = eng.state.get("radar", {}).get("locked_contact_id")
        status_line = rdar.status_line(eng.pool, (sx, sy), locked_id=locked_id, max_list=3)
        radar_win.addstr(1, 2, status_line[:W-4])

        # --- Locked target details
        _draw_box(locked_win, " LOCKED TARGET ")
        if locked_id is not None and any(c.id == locked_id for c in eng.pool.contacts):
            c = next(c for c in eng.pool.contacts if c.id == locked_id)
            rng = _range_nm(eng.pool, sx, sy, c)
            cell = _fmt_cell(c)
            locked_win.addstr(1, 2, f"{cell} {c.name} {c.allegiance}  d={rng:0.1f} nm  crs={c.course_deg:0.0f}°  "
                                    f"spd={c.speed_kts_game:0.0f} kts  (#{c.id})",
                              info)
        else:
            locked_win.addstr(1, 2, "None")

        # --- Nearest contacts table
        _draw_box(table_win, " CONTACTS (nearest first) ")
        table_win.attron(accent)
        header = "CELL  TYPE        NAME                      RANGE  CRS  SPD   ID"
        table_win.addstr(1, 2, header)
        table_win.attroff(accent)

        if eng.pool.contacts:
            sorted_cs = sorted(eng.pool.contacts, key=lambda c: _range_nm(eng.pool, sx, sy, c))
            rows = min(table_h - 3, len(sorted_cs))
            for i in range(rows):
                c = sorted_cs[i]
                cell = _fmt_cell(c)
                rng = _range_nm(eng.pool, sx, sy, c)
                line = f"{cell:<4} {c.type:<10} {c.name:<24} {rng:>5.1f}  {c.course_deg:>3.0f}°  {c.speed_kts_game:>3.0f}  #{c.id:02d}"
                table_win.addstr(2+i, 2, line[:W-4])
        else:
            table_win.addstr(2, 2, "No contacts.")

        # --- Footer
        foot = ("q=quit  space=pause/resume  s=scan  u=unlock  c/C=course -/+5°  v/V=speed -/+1 kt   "
                "Tip: resize terminal for more rows")
        foot_win.addstr(0, 0, foot[:W-1], accent)

        # Render
        stdscr.refresh()

def main():
    curses.wrapper(dashboard)

if __name__ == "__main__":
    main()