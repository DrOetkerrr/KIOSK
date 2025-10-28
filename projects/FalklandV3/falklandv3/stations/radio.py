"""Radio station projection utilities."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence


@dataclass(frozen=True)
class RadioMessageEntry:
    """Single radio message ordered by newest first."""

    id: int
    text: str
    category: str
    ts: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "category": self.category,
            "ts": self.ts,
        }


@dataclass(frozen=True)
class RadioCategorySummary:
    category: str
    count: int

    def as_dict(self) -> Dict[str, Any]:
        return {"category": self.category, "count": self.count}


@dataclass(frozen=True)
class RadioStationView:
    """Projection for the radio/announcer station."""

    messages: Sequence[RadioMessageEntry]
    summaries: Sequence[RadioCategorySummary]
    total_messages: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "messages": [message.as_dict() for message in self.messages],
            "summaries": [summary.as_dict() for summary in self.summaries],
            "total_messages": self.total_messages,
        }


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _normalise_messages(entries: Iterable[Mapping[str, Any]], limit: int) -> List[RadioMessageEntry]:
    normalised: List[RadioMessageEntry] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        normalised.append(
            RadioMessageEntry(
                id=_coerce_int(entry.get("id")),
                text=_coerce_str(entry.get("text")),
                category=_coerce_str(entry.get("category"), "general"),
                ts=_coerce_float(entry.get("ts")),
            )
        )
    normalised.sort(key=lambda message: message.ts, reverse=True)
    return normalised[: max(0, limit)]


def _summaries(messages: Sequence[RadioMessageEntry]) -> List[RadioCategorySummary]:
    counts = Counter(message.category for message in messages)
    return [
        RadioCategorySummary(category=category, count=count)
        for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def build_radio_station_view(snapshot: Mapping[str, Any], *, limit: int = 20) -> RadioStationView:
    """Project runtime snapshot to radio station view."""

    payload = snapshot.get("radio") if isinstance(snapshot, Mapping) else None
    entries = payload.get("messages", []) if isinstance(payload, Mapping) else []
    limited = _normalise_messages(entries, limit)
    total = len(entries) if isinstance(entries, list) else len(limited)
    return RadioStationView(
        messages=tuple(limited),
        summaries=tuple(_summaries(limited)),
        total_messages=total,
    )
