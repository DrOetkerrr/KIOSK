"""Structured logging utilities."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass
class LogEvent:
    level: str
    event: str
    ts: str
    data: Mapping[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "level": self.level,
                "event": self.event,
                "ts": self.ts,
                "data": self.data,
            }
        )


def log(event: str, *, level: str = "info", stream=None, **data: Any) -> None:
    stream = stream or sys.stdout
    ts = datetime.now(timezone.utc).isoformat()
    stream.write(LogEvent(level=level, event=event, ts=ts, data=data).to_json() + "\n")
