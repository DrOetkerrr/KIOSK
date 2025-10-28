"""Minimal Combat Air Patrol (CAP) manager for Falkland V3."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from falklandv3.core.shar import SeaHarrier, SeaHarrierSnapshot, SeaHarrierStatus


class CAPStatus(str, Enum):
    READY = "ready"
    LAUNCHED = "launched"
    RETURNING = "returning"


@dataclass(frozen=True)
class CAPSnapshot:
    status: CAPStatus
    sorties: int
    time_in_status_s: float
    harriers: tuple[SeaHarrierSnapshot, ...]


class CAPManager:
    """Tracks CAP launch/recovery cycle with simple timers."""

    def __init__(self, launch_duration_s: float = 90.0, cycle_duration_s: float = 300.0) -> None:
        self.launch_duration_s = launch_duration_s
        self.cycle_duration_s = cycle_duration_s
        self._status = CAPStatus.READY
        self._sorties = 0
        self._time_in_status = 0.0
        self._harriers: list[SeaHarrier] = [
            SeaHarrier(1, "SHAR-1"),
            SeaHarrier(2, "SHAR-2"),
        ]

    def launch(self) -> None:
        if self._status == CAPStatus.READY:
            launched = False
            for harrier in self._harriers:
                if harrier.ready():
                    harrier.launch()
                    launched = True
                    break
            if not launched:
                return
            self._status = CAPStatus.LAUNCHED
            self._time_in_status = 0.0
            self._sorties += 1

    def tick(self, dt_seconds: float) -> None:
        dt = max(0.0, float(dt_seconds))
        if dt == 0.0:
            return
        self._time_in_status += dt
        for harrier in self._harriers:
            harrier.tick(dt)
        if self._status == CAPStatus.LAUNCHED and self._time_in_status >= self.launch_duration_s:
            self._begin_return()
        elif self._status == CAPStatus.RETURNING and self._time_in_status >= (self.cycle_duration_s - self.launch_duration_s):
            self._status = CAPStatus.READY
            self._time_in_status = 0.0
        if self._status == CAPStatus.READY and any(h.status == SeaHarrierStatus.LAUNCHED for h in self._harriers):
            self._status = CAPStatus.LAUNCHED
            self._time_in_status = 0.0

    def snapshot(self) -> CAPSnapshot:
        return CAPSnapshot(
            status=self._status,
            sorties=self._sorties,
            time_in_status_s=self._time_in_status,
            harriers=tuple(
                SeaHarrierSnapshot(
                    callsign=harrier.callsign,
                    status=harrier.status,
                    fuel_pct=round(harrier.fuel_pct, 1),
                    time_in_status_s=harrier.time_in_status_s,
                )
                for harrier in self._harriers
            ),
        )

    def record_intercept(self) -> None:
        self._begin_return()

    def _begin_return(self) -> None:
        self._status = CAPStatus.RETURNING
        self._time_in_status = 0.0
        for harrier in self._harriers:
            harrier.begin_return()
