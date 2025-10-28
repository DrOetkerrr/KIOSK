"""Contact catalog loader for V3 radar."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class ContactType:
    name: str
    allegiance: str
    speed_kts: float
    weight: int
    klass: Optional[str]
    category: Optional[str]
    primary_weapon: Optional[str]
    min_range_nm: Optional[float]
    max_range_nm: Optional[float]
    primary_target: Optional[str]


class ContactCatalog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._items: List[ContactType] = []
        self._by_name: dict[str, ContactType] = {}
        self.reload()

    def items(self, allegiance: Optional[str] = None) -> List[ContactType]:
        if allegiance is None:
            return list(self._items)
        allegiance = allegiance.lower()
        return [item for item in self._items if item.allegiance.lower() == allegiance]

    def get(self, name: str) -> Optional[ContactType]:
        return self._by_name.get(name.lower())

    def reload(self) -> None:
        data = json.loads(self._path.read_text(encoding="utf-8"))
        items_source = data.get("items") if isinstance(data, dict) else data
        self._items = []
        self._by_name.clear()
        if isinstance(items_source, list):
            for item in items_source:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                allegiance = str(item.get("allegiance", "")).strip() or "Unknown"
                speed = float(item.get("speed_kts", 0.0) or 0.0)
                weight = int(item.get("weight", 1) or 1)
                klass = item.get("class")
                category = item.get("type") or item.get("category")
                primary_weapon = item.get("primary_weapon")
                min_range = item.get("min_range_nm")
                max_range = item.get("max_range_nm")
                primary_target = item.get("primary_target")
                contact = ContactType(
                    name=name,
                    allegiance=allegiance,
                    speed_kts=speed,
                    weight=max(weight, 1),
                    klass=str(klass) if klass else None,
                    category=str(category) if category else None,
                    primary_weapon=str(primary_weapon) if primary_weapon else None,
                    min_range_nm=float(min_range) if min_range is not None else None,
                    max_range_nm=float(max_range) if max_range is not None else None,
                    primary_target=(
                        primary_target[0]
                        if isinstance(primary_target, list) and primary_target
                        else str(primary_target)
                        if primary_target is not None
                        else None
                    ),
                )
                self._items.append(contact)
                if name:
                    self._by_name[name.lower()] = contact
