from __future__ import annotations
import threading
import time
from typing import Callable, Optional

class GameTimer:
    """Calls a callback every `interval_s` seconds, passing dt_s each time."""
    def __init__(self, callback: Callable[[float], None]):
        self._cb = callback
        self._thr: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._interval = 1.0
        self._dt = 1.0
        self._lock = threading.Lock()

    def start(self, interval_s: float, dt_s: float):
        self.stop()
        with self._lock:
            self._interval = max(0.1, float(interval_s))
            self._dt = max(0.1, float(dt_s))
        self._stop.clear()
        self._thr = threading.Thread(target=self._run, daemon=True)
        self._thr.start()

    def _run(self):
        next_t = time.time() + self._interval
        while not self._stop.is_set():
            now = time.time()
            if now >= next_t:
                with self._lock:
                    dt = self._dt
                    interval = self._interval
                try:
                    self._cb(dt)
                except Exception as e:
                    print(f"[TIMER] callback error: {e}")
                next_t += interval
            time.sleep(0.05)

    def stop(self):
        self._stop.set()
        if self._thr and self._thr.is_alive():
            self._thr.join(timeout=1.0)
        self._thr = None

    def is_running(self) -> bool:
        return self._thr is not None and self._thr.is_alive()

    def config(self):
        with self._lock:
            return {"interval": self._interval, "dt": self._dt}