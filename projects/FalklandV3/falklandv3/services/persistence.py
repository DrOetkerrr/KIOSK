"""Persistence helpers for Falkland V3 runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


@dataclass
class PersistenceConfig:
    root: Path
    ensure_dirs: bool = True


class StateRepository:
    """Stores runtime snapshots and events under a filesystem root."""

    def __init__(self, config: PersistenceConfig) -> None:
        self._root = config.root
        if config.ensure_dirs:
            self._root.mkdir(parents=True, exist_ok=True)

    def write_json(self, relative: str, payload: Any) -> Path:
        path = self._resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def append_jsonl(self, relative: str, entries: Iterable[Any]) -> Path:
        path = self._resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry) + "\n")
        return path

    def read_json(self, relative: str, default: Optional[Any] = None) -> Any:
        path = self._resolve(relative)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default

    def _resolve(self, relative: str) -> Path:
        return self._root / relative
