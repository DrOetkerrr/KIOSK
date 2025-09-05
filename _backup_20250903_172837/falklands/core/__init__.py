# falklands/core/__init__.py

from .engine import Engine
from .state import GameState, FalklandsState
from .router import Router  # primary export

# Optional compatibility alias if anything still imports CommandRouter
CommandRouter = Router

__all__ = [
    "Engine",
    "GameState",
    "FalklandsState",
    "Router",
    "CommandRouter",
]