"""Ship kinematics and world grid helpers for Falkland V3."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

from falklandv3.utils.grid import world_to_label

WORLD_SIZE_NM = 40.0
BOARD_SIZE_NM = 26.0
BOARD_MIN_X = 7.0
BOARD_MIN_Y = 7.0


def clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


@dataclass
class ShipState:
    """Mutable ship state tracked by the engine."""

    x_nm: float
    y_nm: float
    heading_deg: float
    speed_kts: float

    def board_cell(self) -> str:
        return world_to_label(self.x_nm, self.y_nm)


class Engine:
    """Deterministic ship integrator; radar/CAP attach via higher-level services."""

    def __init__(self) -> None:
        self.ship = ShipState(
            x_nm=BOARD_MIN_X + 10.0,  # aligns with historical K13 start
            y_nm=BOARD_MIN_Y + 12.0,
            heading_deg=0.0,
            speed_kts=0.0,
        )

    # ----- controls ---------------------------------------------------------
    def set_course(self, heading_deg: float) -> None:
        self.ship.heading_deg = float(heading_deg) % 360.0

    def set_speed(self, speed_kts: float) -> None:
        self.ship.speed_kts = max(0.0, float(speed_kts))

    # ----- stepping ---------------------------------------------------------
    def tick(self, dt_seconds: float) -> None:
        dt = float(dt_seconds)
        if dt <= 0 or self.ship.speed_kts <= 0:
            return
        distance_nm = self.ship.speed_kts * (dt / 3600.0)
        radians = math.radians(self.ship.heading_deg)
        dx = math.sin(radians) * distance_nm
        dy = -math.cos(radians) * distance_nm
        self.ship.x_nm = clamp(self.ship.x_nm + dx, 0.0, WORLD_SIZE_NM)
        self.ship.y_nm = clamp(self.ship.y_nm + dy, 0.0, WORLD_SIZE_NM)

    # ----- projections ------------------------------------------------------
    def hud_line(self) -> str:
        cell = self.ship.board_cell()
        return f"Ship {cell} | hdg {self.ship.heading_deg:.0f}° spd {self.ship.speed_kts:.0f} kn"
