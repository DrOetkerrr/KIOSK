from __future__ import annotations

"""Simple watchdog to monitor front-end polling and runtime advancement.

The web UI should call :func:`record_frontend_poll` whenever `/api/status`
responds successfully. Runtime helpers call :func:`record_runtime_tick`
after advancing engine or radar state. A background thread checks the gaps
between heartbeats and triggers a soft reset callback if both front-end and
runtime have gone silent longer than configured thresholds.
"""

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    "frontend_last_poll_ts": 0.0,
    "frontend_poll_count": 0,
    "frontend_gap_s": 0.0,
    "runtime_last_tick_ts": 0.0,
    "runtime_tick_count": 0,
    "runtime_last_label": None,
    "runtime_gap_s": 0.0,
    "reset_count": 0,
    "last_reset_ts": 0.0,
    "last_reset_reason": None,
    "last_reset_success": None,
    "last_reset_error": None,
}

_CONFIG: Dict[str, float] = {
    "frontend_timeout_s": 18.0,
    "runtime_timeout_s": 25.0,
    "check_interval_s": 5.0,
    "reset_cooldown_s": 180.0,
}

_callback: Optional[Callable[[Dict[str, Any]], None]] = None
_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def configure(**kwargs: float) -> None:
    """Override watchdog thresholds. Safe to call before :func:`start`."""
    with _LOCK:
        for key, value in kwargs.items():
            if key in _CONFIG:
                try:
                    _CONFIG[key] = float(value)
                except Exception:
                    continue


def record_frontend_poll(ts: Optional[float] = None) -> None:
    """Stamp the time of the most recent successful `/api/status` poll."""
    if ts is None:
        ts = time.time()
    with _LOCK:
        _STATE["frontend_last_poll_ts"] = float(ts)
        _STATE["frontend_poll_count"] = int(_STATE["frontend_poll_count"] or 0) + 1
        _STATE["frontend_gap_s"] = 0.0


def record_runtime_tick(label: str, ts: Optional[float] = None) -> None:
    """Record that the backend advanced (e.g., radar/engine)."""
    if ts is None:
        ts = time.time()
    with _LOCK:
        _STATE["runtime_last_tick_ts"] = float(ts)
        _STATE["runtime_tick_count"] = int(_STATE["runtime_tick_count"] or 0) + 1
        _STATE["runtime_last_label"] = str(label or _STATE.get("runtime_last_label") or "")
        _STATE["runtime_gap_s"] = 0.0


def note_reset(reason: str, success: bool, *, error: Optional[str] = None) -> None:
    """Update reset metadata so diagnostics reflect the watchdog action."""
    now = time.time()
    with _LOCK:
        _STATE["reset_count"] = int(_STATE["reset_count"] or 0) + 1
        _STATE["last_reset_ts"] = now
        _STATE["last_reset_reason"] = reason
        _STATE["last_reset_success"] = bool(success)
        _STATE["last_reset_error"] = str(error) if error else None


def snapshot() -> Dict[str, Any]:
    """Return a copy of the watchdog state for diagnostic endpoints."""
    with _LOCK:
        snap = dict(_STATE)
        snap["config"] = dict(_CONFIG)
        snap["thread_alive"] = bool(_thread and _thread.is_alive())
        snap["now"] = time.time()
    return snap


def start(reset_callback: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
    """Start the watchdog thread if not already active."""
    global _thread, _callback
    with _LOCK:
        _callback = reset_callback
        if _thread and _thread.is_alive():
            return
        _stop_event.clear()
        # Seed timestamps so we do not immediately trigger a reset on start-up.
        now = time.time()
        if _STATE["frontend_last_poll_ts"] <= 0.0:
            _STATE["frontend_last_poll_ts"] = now
        if _STATE["runtime_last_tick_ts"] <= 0.0:
            _STATE["runtime_last_tick_ts"] = now
    _thread = threading.Thread(target=_watchdog_loop, name="FalklandsWatchdog", daemon=True)
    _thread.start()


def stop() -> None:
    """Stop the watchdog thread (mainly for tests)."""
    _stop_event.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=1.0)


def _watchdog_loop() -> None:
    while not _stop_event.wait(_CONFIG["check_interval_s"]):
        now = time.time()
        reasons = []
        front_gap = None
        runtime_gap = None
        with _LOCK:
            f_ts = _STATE.get("frontend_last_poll_ts", 0.0) or 0.0
            r_ts = _STATE.get("runtime_last_tick_ts", 0.0) or 0.0
            if f_ts > 0.0:
                front_gap = max(0.0, now - f_ts)
                _STATE["frontend_gap_s"] = front_gap
            if r_ts > 0.0:
                runtime_gap = max(0.0, now - r_ts)
                _STATE["runtime_gap_s"] = runtime_gap
        if front_gap is not None and front_gap > _CONFIG["frontend_timeout_s"]:
            reasons.append(f"frontend_gap>{_CONFIG['frontend_timeout_s']}s ({front_gap:.1f}s)")
        if runtime_gap is not None and runtime_gap > _CONFIG["runtime_timeout_s"]:
            reasons.append(f"runtime_gap>{_CONFIG['runtime_timeout_s']}s ({runtime_gap:.1f}s)")

        if not reasons:
            continue

        # Avoid triggering resets too frequently.
        with _LOCK:
            last_reset_ts = _STATE.get("last_reset_ts", 0.0) or 0.0
        if now - last_reset_ts < _CONFIG["reset_cooldown_s"]:
            continue

        logging.warning("Watchdog detected stalled state: %s", "; ".join(reasons))
        details = {
            "ts": now,
            "reasons": reasons,
            "frontend_gap_s": front_gap,
            "runtime_gap_s": runtime_gap,
            "config": dict(_CONFIG),
        }
        if _callback is None:
            continue
        try:
            _callback(details)
        except Exception:
            logging.exception("Watchdog reset callback failed")
