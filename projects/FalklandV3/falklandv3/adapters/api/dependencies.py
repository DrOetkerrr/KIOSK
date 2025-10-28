"""Shared FastAPI dependencies for Falkland V3 adapters."""

from __future__ import annotations

from functools import lru_cache
from threading import Lock

from falklandv3.config import load_settings
from falklandv3.services.runtime import GameRuntime
from falklandv3.services.runtime_loop import LoopConfig, RuntimeLoop


class RuntimeDepends:
    """Provides a long-lived runtime with background ticking."""

    def __init__(self) -> None:
        settings = load_settings()
        self._runtime = GameRuntime(settings=settings)
        self._loop = RuntimeLoop(self._runtime, LoopConfig(dt_seconds=settings.tick_dt))
        self._lock = Lock()
        self._started = False

    def __call__(self) -> GameRuntime:
        self.startup()
        return self._runtime
    
    @property
    def runtime(self) -> GameRuntime:
        return self.__call__()

    def startup(self) -> None:
        with self._lock:
            if self._started:
                return
            self._loop.start()
            self._started = True

    def shutdown(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._loop.stop(timeout=1.0)
            self._started = False


@lru_cache(maxsize=1)
def get_runtime_dep() -> RuntimeDepends:
    return RuntimeDepends()


runtime_dep = get_runtime_dep()
