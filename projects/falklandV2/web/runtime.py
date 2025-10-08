from __future__ import annotations

from typing import Optional

try:
    from flask import Flask
except Exception:  # pragma: no cover - flask not required for type checking
    Flask = object  # type: ignore

from projects.falklandV2.runtime_service import GameRuntime

_RUNTIME: Optional[GameRuntime] = None
_EXT_KEY = "falkland.runtime"


def _safe_reset(runtime: GameRuntime) -> None:
    try:
        runtime.reset_state()
    except Exception:
        pass


def init_runtime(*, port: Optional[int] = None, reset: bool = True) -> GameRuntime:
    """Create or return the singleton GameRuntime instance."""
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = GameRuntime(port=port)
    if reset:
        _safe_reset(_RUNTIME)
    return _RUNTIME


def set_runtime(runtime: GameRuntime) -> None:
    """Force the global runtime reference (used by tests)."""
    global _RUNTIME
    _RUNTIME = runtime


def get_runtime() -> GameRuntime:
    if _RUNTIME is None:
        raise RuntimeError("GameRuntime has not been initialised yet")
    return _RUNTIME


def attach_runtime(app: "Flask", runtime: Optional[GameRuntime] = None) -> GameRuntime:
    """Attach the runtime to a Flask application (stored in app.extensions)."""
    rt = runtime or init_runtime()
    try:
        app.extensions[_EXT_KEY] = rt  # type: ignore[attr-defined]
    except Exception:
        # app may be a proxy during tests; ignore failures
        pass
    return rt


def from_app(app: "Flask") -> GameRuntime:
    """Fetch the runtime from a Flask application, initialising if absent."""
    runtime = None
    try:
        runtime = app.extensions.get(_EXT_KEY)  # type: ignore[attr-defined]
    except Exception:
        runtime = None
    if runtime is None:
        runtime = attach_runtime(app)
    return runtime
