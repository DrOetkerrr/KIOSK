"""Ambient radio feed simulation."""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class RadioMessage:
    id: int
    text: str
    category: str
    ts: float


class RadioFeed:
    """Generates ambient radio chatter at a fixed cadence."""

    _SCRIPT = (
        "MAYDAY relay from Hermes flight control.",
        "Weather deck reports increasing swell.",
        "Radar contact bearing 210, range 45 NM.",
        "Bridge requests CAP status update.",
        "Engineering notes coolant temps within nominal band.",
    )

    def __init__(self, *, interval_s: float = 60.0, max_messages: int = 20) -> None:
        self.interval_s = interval_s
        self.max_messages = max_messages
        self._next_id = 1
        self._messages: List[RadioMessage] = []
        self._countdown = interval_s
        self._script_iter = itertools.cycle(self._SCRIPT)

    def tick(self, dt_seconds: float) -> None:
        dt = max(0.0, float(dt_seconds))
        self._countdown -= dt
        if self._countdown <= 0:
            self._countdown = self.interval_s
            self._append(next(self._script_iter))

    def push(self, text: str, *, category: str = "info") -> None:
        self._append(text, category=category)

    def snapshot(self) -> List[RadioMessage]:
        return list(self._messages)

    # ----- internals -----------------------------------------------------
    def _append(self, text: str, *, category: str = "info") -> None:
        msg = RadioMessage(id=self._next_id, text=text, category=category, ts=time.time())
        self._messages.append(msg)
        self._next_id += 1
        if len(self._messages) > self.max_messages:
            self._messages = self._messages[-self.max_messages :]
