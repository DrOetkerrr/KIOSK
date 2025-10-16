#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from projects.falklandV2.subsystems.status import build as build_status
from projects.falklandV2.radar_snapshot import build_radar_view
from projects.falklandV2.radar_render import render_radar_png


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render an 800x480 Hermes radar snapshot to PNG.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tmp/trmnl/radar_snapshot.png"),
        help="Path for the generated PNG (default: tmp/trmnl/radar_snapshot.png).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    payload = build_status()
    context = build_radar_view(payload)
    output = render_radar_png(context, args.output)
    print(f"[render_radar_snapshot] wrote {output}")


if __name__ == "__main__":
    main()
