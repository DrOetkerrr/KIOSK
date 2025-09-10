#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Falklands V3 — Minimal Engine

Implements the minimal surfaces expected by utils.canary:
- WORLD_N == 40, BOARD_N == 26, BOARD_MIN_X/Y and project_edge_warning
- class Engine with: ship, tick(dt_s), hud_line(), contacts

Also wires in the Radar module (core.radar.Radar) and exposes its contacts.
Movement model: 0° = North, 90° = East; distance = kts * dt_s / 3600 nm.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple, List
import math

# World/board constants
WORLD_N = 40
BOARD_N = 26

# Place the 26x26 captain's board inside the 40x40 world with a 7 nm margin
BOARD_MIN_X = 7  # left edge x in world nm
BOARD_MIN_Y = 7  # top edge y in world nm
BOARD_MAX_X = BOARD_MIN_X + BOARD_N  # exclusive upper bound in nm
BOARD_MAX_Y = BOARD_MIN_Y + BOARD_N  # exclusive upper bound in nm


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def project_edge_warning(x: float, y: float, course_deg: float, speed_kts: float, dt_s: float = 60.0) -> bool:
    """
    Predict position after dt_s; return True if it would leave the 26x26 board window.
    Board coordinates: x in [BOARD_MIN_X, BOARD_MAX_X), y in [BOARD_MIN_Y, BOARD_MAX_Y);
    heading 0° is north (y decreasing), 90° east (x increasing).
    """
    if speed_kts <= 0 or dt_s <= 0:
        return False
    nm = speed_kts * (dt_s / 3600.0)
    rad = math.radians(course_deg % 360.0)
    dx = math.sin(rad) * nm
    dy = -math.cos(rad) * nm
    nx, ny = x + dx, y + dy
    return not (BOARD_MIN_X <= nx < BOARD_MAX_X and BOARD_MIN_Y <= ny < BOARD_MAX_Y)


@dataclass
class Ship:
    x: float
    y: float
    course_deg: float = 0.0
    speed_kts: float = 0.0

    def board_cell(self) -> Tuple[str, int]:
        """Return (col_letter, row_number) for current position snapped to nearest cell."""
        col_idx = int(round(self.x - BOARD_MIN_X))
        row_idx = int(round(self.y - BOARD_MIN_Y))
        col_idx = max(0, min(BOARD_N - 1, col_idx))
        row_idx = max(0, min(BOARD_N - 1, row_idx))
        col_letter = chr(ord('A') + col_idx)
        return col_letter, (row_idx + 1)


class Engine:
    """
    Minimal orchestrator for Falklands V3.
    Owns a `Ship`, integrates `Radar`, advances time via `tick(dt_s)`.
    """

    def __init__(self, rec: Optional[object] = None, cfg: Optional[dict] = None):
        # Ship starts at board cell K13 → indexes (K=10, row=12)
        x0 = BOARD_MIN_X + (ord('K') - ord('A'))
        y0 = BOARD_MIN_Y + (13 - 1)
        self.ship = Ship(x=float(x0), y=float(y0), course_deg=0.0, speed_kts=0.0)

        # Hook up radar
        try:
            from .radar import Radar
        except Exception:
            # Allow import even if radar is temporarily unavailable
            Radar = None  # type: ignore
        self.rec = rec
        self.cfg = cfg or {}
        self.radar = Radar(rec=self.rec) if 'Radar' in locals() and Radar is not None else None

        # Expose contacts list surface expected by canary and UI code
        self.contacts: List[object] = []

        # Provide a lightweight pool wrapper: exposes `.contacts` and `.grid` (cols/rows/cell_nm)
        class _Grid:
            cols = BOARD_N
            rows = BOARD_N
            cell_nm = 1.0
        class _Pool:
            def __init__(self, eng: 'Engine') -> None:
                self._eng = eng
                self.grid = _Grid()
            @property
            def contacts(self) -> List[object]:
                return self._eng.contacts
        self.pool = _Pool(self)

    # ----- controls -----
    def set_course(self, deg: float) -> None:
        self.ship.course_deg = float(deg) % 360.0

    def set_speed(self, kts: float) -> None:
        self.ship.speed_kts = max(0.0, float(kts))

    # ----- stepping -----
    def tick(self, dt_s: float) -> None:
        dt_s = float(dt_s)
        # Move ship in world NM coordinates
        if self.ship.speed_kts > 0 and dt_s > 0:
            nm = self.ship.speed_kts * (dt_s / 3600.0)
            rad = math.radians(self.ship.course_deg)
            dx = math.sin(rad) * nm
            dy = -math.cos(rad) * nm
            self.ship.x = clamp(self.ship.x + dx, 0.0, float(WORLD_N))
            self.ship.y = clamp(self.ship.y + dy, 0.0, float(WORLD_N))

        # Radar integration
        if self.radar is not None:
            self.radar.tick(dt_s, self.ship.x, self.ship.y)
            self.contacts = list(self.radar.contacts)
        else:
            self.contacts = []

    # ----- HUD -----
    def hud_line(self) -> str:
        col, row = self.ship.board_cell()
        return (
            f"Ship {col}{row} | hdg {self.ship.course_deg:.0f}° spd {self.ship.speed_kts:.0f} kn; "
            f"contacts: {len(self.contacts)}"
        )

    # ----- light V2 compatibility helpers -----
    def _ship_xy(self) -> Tuple[float, float]:
        return self.ship.x, self.ship.y

    def _ship_course_speed(self) -> Tuple[float, float]:
        return self.ship.course_deg, self.ship.speed_kts

    def _radar_scan(self) -> None:
        if self.radar is not None:
            self.radar.scan(self.ship.x, self.ship.y)
