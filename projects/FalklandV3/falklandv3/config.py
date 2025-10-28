"""Configuration helpers for Falkland V3."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

ENV_PREFIX = "FALKLANDV3_"


@dataclass(frozen=True)
class Settings:
    tick_dt: float = 1.0
    log_dir: Optional[Path] = None
    loop_sleep: float = 0.0
    audio_max_events: int = 10
    build_label: str = "dev"
    api_key: Optional[str] = None
    rng_seed: Optional[int] = None


def _read_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(f"{ENV_PREFIX}{key}")
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _read_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(f"{ENV_PREFIX}{key}")
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = env or os.environ
    tick_dt = _read_float(env, "TICK_DT", 1.0)
    loop_sleep = _read_float(env, "LOOP_SLEEP", 0.0)
    audio_max = _read_int(env, "AUDIO_MAX_EVENTS", 10)
    log_dir_raw = env.get(f"{ENV_PREFIX}LOG_DIR")
    log_dir = Path(log_dir_raw).expanduser() if log_dir_raw else None
    build_label = env.get(f"{ENV_PREFIX}BUILD", "dev")
    api_key = env.get(f"{ENV_PREFIX}API_KEY")
    rng_seed = env.get(f"{ENV_PREFIX}RNG_SEED")
    try:
        rng_seed_val = int(rng_seed) if rng_seed is not None else None
    except (TypeError, ValueError):
        rng_seed_val = None
    return Settings(
        tick_dt=tick_dt,
        log_dir=log_dir,
        loop_sleep=loop_sleep,
        audio_max_events=audio_max,
        build_label=build_label,
        api_key=api_key,
        rng_seed=rng_seed_val,
    )
