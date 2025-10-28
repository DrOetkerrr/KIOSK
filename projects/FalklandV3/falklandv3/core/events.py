"""Domain event definitions for Falkland V3."""

from __future__ import annotations

from dataclasses import dataclass

from falklandv3.core.radar import RadarContactView


@dataclass(frozen=True)
class ShipMoved:
    heading_deg: float
    speed_kts: float
    x_nm: float
    y_nm: float


@dataclass(frozen=True)
class RadarContactsUpdated:
    contacts: tuple[RadarContactView, ...]
