"""Load attack wave schedule from legacy V2 data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_DIRECTION_BEARINGS = {
  "N": 0.0,
  "NE": 45.0,
  "E": 90.0,
  "SE": 135.0,
  "S": 180.0,
  "SW": 225.0,
  "W": 270.0,
  "NW": 315.0,
}


@dataclass
class SpawnOption:
    name: str
    chance: float
    min_range_nm: Optional[float]


@dataclass
class AttackWave:
    label: str
    duration_s: float
    spawn_rate_per_min: float
    friendly_prob: float
    direction_bearing: float
    options: List[SpawnOption]


class WaveSchedule:
    def __init__(self, waves: List[AttackWave]) -> None:
        self._waves = waves
        self._durations = [wave.duration_s for wave in waves]
        self.total_duration_s = sum(self._durations)

    @property
    def waves(self) -> List[AttackWave]:
        return list(self._waves)

    def current(self, elapsed_s: float) -> Optional[AttackWave]:
        if not self._waves:
            return None
        remaining = elapsed_s
        for wave in self._waves:
            if remaining <= wave.duration_s:
                return wave
            remaining -= wave.duration_s
        return self._waves[-1]

    def progress(self, elapsed_s: float) -> Optional[tuple[AttackWave, float]]:
        if not self._waves:
            return None
        remaining = max(0.0, elapsed_s)
        for wave in self._waves:
            if remaining <= wave.duration_s:
                return wave, remaining
            remaining -= wave.duration_s
        last = self._waves[-1]
        return last, min(last.duration_s, remaining)


def load_wave_schedule(path: Path) -> WaveSchedule:
    data = json.loads(path.read_text(encoding="utf-8"))
    waves_src = data.get("waves") if isinstance(data, dict) else None
    waves: List[AttackWave] = []
    if isinstance(waves_src, list):
        for wave in waves_src:
            if not isinstance(wave, dict):
                continue
            label = str(wave.get("label", "Wave"))
            duration_min = float(wave.get("duration_min", 0.0) or 0.0)
            spawn_rate_per_min = float(wave.get("spawn_rate_per_min", 0.0) or 0.0)
            friendly_prob = float(wave.get("friendly_prob", 0.0) or 0.0)
            direction = str(wave.get("direction", "")).upper()
            bearing = _DIRECTION_BEARINGS.get(direction, 0.0)
            spawns = wave.get("spawns", {})
            options: List[SpawnOption] = []
            if isinstance(spawns, dict):
                for name, meta in spawns.items():
                    if not isinstance(meta, dict):
                        continue
                    chance = float(meta.get("chance", 0.0) or 0.0)
                    min_range_nm = meta.get("min_range_nm")
                    options.append(
                        SpawnOption(
                            name=str(name),
                            chance=chance,
                            min_range_nm=float(min_range_nm) if min_range_nm is not None else None,
                        )
                    )
            waves.append(
                AttackWave(
                    label=label,
                    duration_s=max(duration_min, 0.0) * 60.0,
                    spawn_rate_per_min=max(spawn_rate_per_min, 0.0),
                    friendly_prob=min(max(friendly_prob, 0.0), 1.0),
                    direction_bearing=bearing,
                    options=options,
                )
            )
    return WaveSchedule(waves)
