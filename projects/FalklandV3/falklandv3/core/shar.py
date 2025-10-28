"""Sea Harrier (SHAR) models for Falkland V3."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SeaHarrierStatus(str, Enum):
    ON_DECK = "on_deck"
    LAUNCHED = "launched"
    RETURNING = "returning"
    REFUELING = "refueling"
    DESTROYED = "destroyed"


@dataclass
class SeaHarrier:
    ident: int
    callsign: str
    fuel_pct: float = 100.0
    status: SeaHarrierStatus = SeaHarrierStatus.ON_DECK
    fuel_bingo_pct: float = 35.0
    fuel_consumption_pct_per_min: float = 10.0
    refuel_rate_pct_per_min: float = 25.0
    time_in_status_s: float = 0.0

    def ready(self) -> bool:
        return self.status == SeaHarrierStatus.ON_DECK

    def launch(self) -> None:
        if self.status != SeaHarrierStatus.ON_DECK:
            return
        self.status = SeaHarrierStatus.LAUNCHED
        self.time_in_status_s = 0.0

    def begin_return(self) -> None:
        if self.status == SeaHarrierStatus.LAUNCHED:
            self.status = SeaHarrierStatus.RETURNING
            self.time_in_status_s = 0.0

    def destroy(self) -> None:
        self.status = SeaHarrierStatus.DESTROYED
        self.time_in_status_s = 0.0

    def tick(self, dt_seconds: float) -> None:
        dt = max(0.0, float(dt_seconds))
        if dt == 0.0 or self.status == SeaHarrierStatus.DESTROYED:
            return
        self.time_in_status_s += dt
        if self.status == SeaHarrierStatus.LAUNCHED:
            self._burn_fuel(dt)
            if self.fuel_pct <= self.fuel_bingo_pct:
                self.begin_return()
        elif self.status == SeaHarrierStatus.RETURNING:
            self._burn_fuel(dt, factor=0.5)
            if self.time_in_status_s >= 90 or self.fuel_pct <= 5.0:
                self.status = SeaHarrierStatus.REFUELING
                self.time_in_status_s = 0.0
        elif self.status == SeaHarrierStatus.REFUELING:
            self._refuel(dt)
            if self.fuel_pct >= 99.0:
                self.status = SeaHarrierStatus.ON_DECK
                self.time_in_status_s = 0.0

    # ----- helpers -------------------------------------------------
    def _burn_fuel(self, dt_seconds: float, *, factor: float = 1.0) -> None:
        burn = self.fuel_consumption_pct_per_min * factor * (dt_seconds / 60.0)
        self.fuel_pct = max(0.0, self.fuel_pct - burn)

    def _refuel(self, dt_seconds: float) -> None:
        gain = self.refuel_rate_pct_per_min * (dt_seconds / 60.0)
        self.fuel_pct = min(100.0, self.fuel_pct + gain)


@dataclass(frozen=True)
class SeaHarrierSnapshot:
    callsign: str
    status: SeaHarrierStatus
    fuel_pct: float
    time_in_status_s: float
