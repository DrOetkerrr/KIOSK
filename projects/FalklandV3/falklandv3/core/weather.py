"""Weather simulation helpers."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class WeatherState:
    wind_dir_deg: float
    wind_speed_kts: float
    sea_state: float  # 0 calm, 12 extreme


class WeatherSimulator:
    def __init__(self, *, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self._state = WeatherState(
            wind_dir_deg=self._rng.uniform(0, 360),
            wind_speed_kts=self._rng.uniform(5, 25),
            sea_state=self._rng.uniform(1, 4),
        )

    def tick(self, dt_seconds: float) -> WeatherState:
        jitter = min(1.0, max(0.1, dt_seconds / 60.0))
        self._state.wind_dir_deg = (self._state.wind_dir_deg + self._rng.uniform(-3, 3)) % 360
        self._state.wind_speed_kts = max(0.0, self._state.wind_speed_kts + self._rng.uniform(-1, 1) * jitter)
        target_sea = min(12.0, max(0.0, self._state.wind_speed_kts / 5.0))
        self._state.sea_state += (target_sea - self._state.sea_state) * 0.1
        return self._state

    def snapshot(self) -> WeatherState:
        return self._state
