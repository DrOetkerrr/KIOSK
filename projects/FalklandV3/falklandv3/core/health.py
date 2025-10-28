"""Asset health tracking for Falkland V3."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class AssetHealth:
    name: str
    max_lives: int
    lives: int

    def as_dict(self) -> Dict[str, int]:
        return {"max_lives": self.max_lives, "lives": self.lives}


class HealthManager:
    """Tracks health for key assets (e.g., Hermes) and exposes mutations."""

    def __init__(self, assets: Dict[str, AssetHealth]) -> None:
        self._assets: Dict[str, AssetHealth] = assets

    @classmethod
    def from_json(cls, path: Path) -> "HealthManager":
        data = json.loads(path.read_text(encoding="utf-8"))
        assets: Dict[str, AssetHealth] = {}
        for key, value in data.items():
            if key.endswith("_max_lives"):
                asset = key[: -len("_max_lives")]
                max_lives = int(value)
                lives = int(data.get(f"{asset}_lives", max_lives))
                assets[asset] = AssetHealth(name=asset, max_lives=max_lives, lives=lives)
            elif key == "max_lives":  # legacy aggregate
                assets["ship"] = AssetHealth(name="ship", max_lives=int(value), lives=int(data.get("lives", value)))
        return cls(assets)

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        return {name: asset.as_dict() for name, asset in self._assets.items()}

    def assets(self) -> tuple[AssetHealth, ...]:
        return tuple(self._assets.values())

    def lives(self, asset: str) -> Optional[int]:
        entry = self._assets.get(asset)
        return entry.lives if entry else None

    def asset(self, asset: str) -> Optional[AssetHealth]:
        return self._assets.get(asset)

    def damage(self, asset: str, amount: int = 1) -> None:
        if amount <= 0:
            return
        if asset not in self._assets:
            return
        entry = self._assets[asset]
        new_lives = max(0, entry.lives - amount)
        self._assets[asset] = AssetHealth(name=entry.name, max_lives=entry.max_lives, lives=new_lives)

    def repair(self, asset: str, amount: int = 1) -> None:
        if amount <= 0:
            return
        if asset not in self._assets:
            return
        entry = self._assets[asset]
        new_lives = min(entry.max_lives, entry.lives + amount)
        self._assets[asset] = AssetHealth(name=entry.name, max_lives=entry.max_lives, lives=new_lives)
