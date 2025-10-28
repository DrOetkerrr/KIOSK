#!/usr/bin/env python3
"""Generate JSON schema artefacts for Falkland V3 APIs."""

from __future__ import annotations

import json
from pathlib import Path

try:
    from falklandv3.api.schemas.status import StatusSnapshot
except ModuleNotFoundError as exc:  # pragma: no cover - dev convenience
    raise SystemExit(
        "Missing dependency for schema generation: install project deps (e.g. `uv sync`) "
        f"[{exc}]"
    )


def main() -> None:
    schema = StatusSnapshot.model_json_schema()
    out_dir = Path(__file__).resolve().parents[2] / "docs" / "contracts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "status.schema.json"
    out_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
