"""Track navigation command history."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List


@dataclass
class NavCommand:
    id: int
    ts: float
    action: str
    value: float


class NavHistory:
    def __init__(self, *, max_entries: int = 50) -> None:
        self._entries: List[NavCommand] = []
        self._max = max_entries
        self._next_id = 1

    def record_course(self, heading_deg: float) -> NavCommand:
        return self._append("course", heading_deg)

    def record_speed(self, speed_kts: float) -> NavCommand:
        return self._append("speed", speed_kts)

    def entries(self) -> List[NavCommand]:
        return list(self._entries)

    def _append(self, action: str, value: float) -> NavCommand:
        cmd = NavCommand(id=self._next_id, ts=time.time(), action=action, value=float(value))
        self._next_id += 1
        self._entries.append(cmd)
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max :]
        return cmd
