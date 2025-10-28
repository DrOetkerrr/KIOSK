"""Simplified audio event queue for Falkland V3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class AudioEvent:
    kind: str  # e.g. "radio", "alert", "voice"
    message: str
    ts: float


class AudioQueue:
    """Tracks the most recent audio events for UI consumption."""

    def __init__(self, max_events: int = 10) -> None:
        self._max_events = max_events
        self._events: List[AudioEvent] = []

    def push(self, event: AudioEvent) -> None:
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events :]

    def latest(self, kind: str) -> Optional[AudioEvent]:
        for event in reversed(self._events):
            if event.kind == kind:
                return event
        return None

    def events(self) -> List[AudioEvent]:
        return list(self._events)
