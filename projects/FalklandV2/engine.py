"""
Compatibility shim: export Engine from canonical module using absolute import.
This avoids relative-import errors when run headless.
"""
from projects.falklandV2.core.engine import Engine  # noqa: F401

