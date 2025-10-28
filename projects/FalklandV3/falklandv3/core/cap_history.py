"""CAP sortie logging."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List


@dataclass
class CapLogEntry:
    id: int
    ts: float
    action: str  # launch | reset | sync
    sorties: int
    mission_status: str


class CapLog:
    def __init__(self, *, max_entries: int = 100) -> None:
        self._entries: List[CapLogEntry] = []
        self._next_id = 1
        self._max_entries = max_entries

    def record(self, action: str, *, sorties: int, mission_status: str) -> CapLogEntry:
        entry = CapLogEntry(
            id=self._next_id,
            ts=time.time(),
            action=action,
            sorties=sorties,
            mission_status=mission_status,
        )
        self._next_id += 1
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries :]
        return entry

    def entries(self) -> List[CapLogEntry]:
        return list(self._entries)
