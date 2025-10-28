"""Background loop for ticking the Falkland V3 runtime."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from falklandv3.services.runtime import GameRuntime


@dataclass
class LoopConfig:
    dt_seconds: float = 1.0
    stop_after_ticks: Optional[int] = None


class RuntimeLoop:
    """Runs GameRuntime.tick() on a background thread until stopped."""

    def __init__(self, runtime: GameRuntime, config: LoopConfig) -> None:
        self._runtime = runtime
        self._config = config
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._ticks = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, *, timeout: Optional[float] = None) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def ticks(self) -> int:
        return self._ticks

    def _run(self) -> None:
        dt = self._config.dt_seconds
        target_ticks = self._config.stop_after_ticks
        while not self._stop.is_set():
            self._runtime.tick(dt)
            self._ticks += 1
            if target_ticks is not None and self._ticks >= target_ticks:
                break
            time.sleep(max(0.0, dt))
        self._stop.set()
