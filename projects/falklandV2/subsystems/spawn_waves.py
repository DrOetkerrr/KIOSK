from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import json
import math
import random

DIRECTION_SECTORS: Dict[str, Tuple[Tuple[float, float], ...]] = {
    'N': ((315.0, 360.0), (0.0, 45.0)),
    'NE': ((0.0, 90.0),),
    'E': ((45.0, 135.0),),
    'SE': ((90.0, 180.0),),
    'S': ((135.0, 225.0),),
    'SW': ((180.0, 270.0),),
    'W': ((225.0, 315.0),),
    'NW': ((270.0, 360.0),),
    'ALL': ((0.0, 360.0),),
}


@dataclass(frozen=True)
class WaveEnemy:
    name: str
    chance: float
    min_range_nm: Optional[float] = None
    max_range_nm: Optional[float] = None


@dataclass(frozen=True)
class WaveDefinition:
    label: str
    start_s: float
    end_s: float
    direction: str
    direction_segments: Tuple[Tuple[float, float], ...]
    enemies: Tuple[WaveEnemy, ...]
    spawn_rate_per_min: Optional[float] = None
    surprise_rate_per_min: Optional[float] = None
    friendly_prob: Optional[float] = None

    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


class WaveSchedule:
    def __init__(self, total_duration_s: float, waves: Sequence[WaveDefinition], *, start_wave_index: int = 0) -> None:
        self.total_duration_s = max(0.0, float(total_duration_s))
        self._waves: Tuple[WaveDefinition, ...] = tuple(waves)
        if not self._waves:
            raise ValueError("WaveSchedule requires at least one wave definition")
        self.start_wave_index = max(0, min(int(start_wave_index), len(self._waves) - 1))
        self.start_elapsed_s = self._waves[self.start_wave_index].start_s

    @property
    def waves(self) -> Tuple[WaveDefinition, ...]:
        return self._waves

    def current(self, elapsed_s: float) -> WaveDefinition:
        """Return the wave active at elapsed_s (seconds)."""
        t = max(0.0, float(elapsed_s))
        for wave in self._waves:
            if t < wave.end_s:
                return wave
        return self._waves[-1]

    def sample_bearing(self, wave: WaveDefinition, rng: random.Random) -> float:
        segments = wave.direction_segments or DIRECTION_SECTORS['ALL']
        if not segments:
            return rng.uniform(0.0, 360.0)
        widths = []
        total = 0.0
        for seg in segments:
            start, end = seg
            width = (end - start) if end >= start else (end + 360.0 - start)
            width = max(1e-6, width)
            widths.append(width)
            total += width
        pick = rng.uniform(0.0, total)
        acc = 0.0
        for (start, end), width in zip(segments, widths):
            acc += width
            if pick <= acc:
                span = width
                if end >= start:
                    return rng.uniform(start, end)
                # wrap-around segment
                value = start + rng.uniform(0.0, span)
                return value % 360.0
        start, end = segments[-1]
        if end >= start:
            return rng.uniform(start, end)
        value = start + rng.uniform(0.0, (end + 360.0) - start)
        return value % 360.0

    def pick_enemy(self, wave: WaveDefinition, rng: random.Random) -> Optional[WaveEnemy]:
        if not wave.enemies:
            return None
        indices = list(range(len(wave.enemies)))
        rng.shuffle(indices)
        for idx in indices:
            enemy = wave.enemies[idx]
            if enemy.chance <= 0.0:
                continue
            if rng.random() <= enemy.chance:
                return enemy
        return None


def _coerce_float(raw: Any, default: Optional[float] = None) -> Optional[float]:
    if raw is None:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _parse_direction(value: Any) -> Tuple[Tuple[float, float], ...]:
    if not value:
        return DIRECTION_SECTORS['ALL']
    key = str(value).strip().upper()
    return DIRECTION_SECTORS.get(key, DIRECTION_SECTORS['ALL'])


def _normalize_duration(waves: List[Dict[str, Any]], total_min: float) -> List[float]:
    explicit = []
    unspecified_indices = []
    for idx, wave in enumerate(waves):
        dur = _coerce_float(wave.get('duration_min'))
        if dur is None or dur <= 0.0:
            unspecified_indices.append(idx)
        else:
            explicit.append((idx, dur))
    total_explicit = sum(d for _, d in explicit)
    remaining = max(0.0, total_min - total_explicit)
    result = [0.0] * len(waves)
    for idx, dur in explicit:
        result[idx] = float(dur)
    if unspecified_indices:
        share = remaining / len(unspecified_indices) if unspecified_indices else 0.0
        share = max(0.0, share)
        for idx in unspecified_indices:
            result[idx] = share
    # If we still have leftover due to rounding, add to last wave
    total_assigned = sum(result)
    if total_assigned < total_min and result:
        result[-1] += (total_min - total_assigned)
    return result


def load_wave_schedule(path: Path | str, *, fallback_total_min: float = 45.0) -> Optional[WaveSchedule]:
    try:
        text = Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    except Exception:
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    total_min = _coerce_float(data.get('total_game_time_min'), fallback_total_min) or fallback_total_min
    waves_raw = data.get('waves')
    if not isinstance(waves_raw, list) or not waves_raw:
        return None

    durations_min = _normalize_duration(waves_raw, total_min)
    total_s = total_min * 60.0

    start_wave_raw = data.get('start_wave')
    try:
        start_wave_index = int(start_wave_raw) - 1
    except Exception:
        start_wave_index = 0

    waves: List[WaveDefinition] = []
    cursor = 0.0
    for raw, dur_min in zip(waves_raw, durations_min):
        if not isinstance(raw, dict):
            continue
        label = str(raw.get('label') or f"Wave {len(waves)+1}")
        dur_s = max(1.0, float(dur_min) * 60.0)
        start_s = cursor
        end_s = min(total_s, cursor + dur_s)
        cursor = end_s
        direction_segments = _parse_direction(raw.get('direction'))
        spawn_rate_per_min = _coerce_float(raw.get('spawn_rate_per_min'))
        if spawn_rate_per_min is not None and spawn_rate_per_min < 0.0:
            spawn_rate_per_min = 0.0
        surprise_rate_per_min = _coerce_float(raw.get('surprise_rate_per_min'))
        if surprise_rate_per_min is not None and surprise_rate_per_min < 0.0:
            surprise_rate_per_min = 0.0
        friendly_prob = _coerce_float(raw.get('friendly_prob'))
        if friendly_prob is not None:
            try:
                friendly_prob = max(0.0, min(1.0, float(friendly_prob)))
            except Exception:
                friendly_prob = None
        spawns = raw.get('spawns') if isinstance(raw.get('spawns'), dict) else {}
        enemies: List[WaveEnemy] = []
        for name, cfg in spawns.items():
            try:
                chance = float(cfg.get('chance', cfg.get('prob', 0.0))) if isinstance(cfg, dict) else float(cfg)
            except Exception:
                chance = 0.0
            chance = max(0.0, min(1.0, chance))
            min_nm = _coerce_float(cfg.get('min_range_nm')) if isinstance(cfg, dict) else None
            max_nm = _coerce_float(cfg.get('max_range_nm')) if isinstance(cfg, dict) else None
            if chance <= 0.0:
                continue
            enemies.append(WaveEnemy(name=str(name), chance=chance, min_range_nm=min_nm, max_range_nm=max_nm))
        waves.append(WaveDefinition(
            label=label,
            start_s=start_s,
            end_s=end_s,
            direction=str(raw.get('direction') or 'ALL').upper(),
            direction_segments=direction_segments,
            enemies=tuple(enemies),
            spawn_rate_per_min=spawn_rate_per_min,
            surprise_rate_per_min=surprise_rate_per_min,
            friendly_prob=friendly_prob,
        ))

    if not waves:
        return None

    # Ensure final wave reaches total duration
    last = waves[-1]
    if last.end_s < total_s:
        waves[-1] = WaveDefinition(
            label=last.label,
            start_s=last.start_s,
            end_s=total_s,
            direction=last.direction,
            direction_segments=last.direction_segments,
            enemies=last.enemies,
            spawn_rate_per_min=last.spawn_rate_per_min,
            surprise_rate_per_min=last.surprise_rate_per_min,
            friendly_prob=last.friendly_prob,
        )

    if start_wave_index < 0:
        start_wave_index = 0
    if start_wave_index >= len(waves):
        start_wave_index = len(waves) - 1

    return WaveSchedule(total_duration_s=total_s, waves=waves, start_wave_index=start_wave_index)
