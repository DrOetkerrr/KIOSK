from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import json


DATA_DIR = Path(__file__).resolve().parents[1] / "schemas"


@dataclass(frozen=True)
class SchemaSpec:
    name: str
    version: str
    path: Path
    raw: Dict[str, Any]


def _load_schema(path: Path) -> Dict[str, Any]:
    with path.open('r', encoding='utf-8') as fh:
        return json.load(fh)


@lru_cache(maxsize=16)
def get_schema(name: str) -> Optional[SchemaSpec]:
    if not name:
        return None
    path = DATA_DIR / f"{name}.schema.json"
    if not path.exists():
        return None
    payload = _load_schema(path)
    version = str(payload.get('version') or payload.get('schemaVersion') or payload.get('title') or "0.0.0")
    return SchemaSpec(name=name, version=version, path=path, raw=payload)


def embed_schema_version(payload: Dict[str, Any], name: str) -> None:
    spec = get_schema(name)
    if not spec:
        return
    current = payload.get('schemaVersion')
    if isinstance(current, str) and current:
        return
    payload['schemaVersion'] = spec.version


try:
    import jsonschema  # type: ignore
except Exception:  # pragma: no cover
    jsonschema = None


def validate(name: str, payload: Dict[str, Any]) -> Optional[list[str]]:
    spec = get_schema(name)
    if not spec or not jsonschema:
        return None
    errors = []
    validator = jsonschema.Draft7Validator(spec.raw)
    for error in validator.iter_errors(payload):
        path = ".".join(str(p) for p in error.path)
        errors.append(f"{path}: {error.message}")
    return errors or None
