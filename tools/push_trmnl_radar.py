#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from projects.falklandV2.subsystems.status import build as build_status
from projects.falklandV2.radar_snapshot import build_radar_view
from projects.falklandV2.radar_render import render_radar_png


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the Hermes radar snapshot and push it to a TRMNL Screens API endpoint."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tmp/trmnl/radar_snapshot.png"),
        help="Where to write the rendered PNG before upload.",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("TRMNL_ENDPOINT", "https://usetrmnl.com/api/display"),
        help="TRMNL Screens API endpoint (default: https://usetrmnl.com/api/display).",
    )
    parser.add_argument(
        "--access-token",
        default=os.environ.get("TRMNL_ACCESS_TOKEN"),
        help="TRMNL access token (falls back to TRMNL_ACCESS_TOKEN env var).",
    )
    parser.add_argument(
        "--device-id",
        help="Optional device identifier to include in the payload (if required by your account).",
    )
    parser.add_argument(
        "--field-name",
        default="screen[image]",
        help="Multipart field name for the uploaded image (default: screen[image]).",
    )
    parser.add_argument(
        "--filename-field",
        default="screen[filename]",
        help="Form field used to send the filename (set blank to skip).",
    )
    parser.add_argument(
        "--json-payload",
        action="store_true",
        help="Send JSON with base64 image data instead of multipart form-data.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render the snapshot but skip the upload step.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds for the upload request (default: 30).",
    )
    return parser.parse_args()


def _create_payload(args: argparse.Namespace) -> Dict[str, Any]:
    payload = build_status()
    context = build_radar_view(payload)
    rendered_path = render_radar_png(context, args.output)
    return {"context": context, "rendered_path": rendered_path}


def _upload_multipart(image_path: Path, args: argparse.Namespace) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if args.filename_field:
        data[args.filename_field] = image_path.name
    if args.device_id:
        data["device_id"] = args.device_id

    with image_path.open("rb") as fh:
        files = {
            args.field_name: (image_path.name, fh, "image/png"),
        }
        headers = {"access-token": args.access_token}
        response = requests.post(
            args.endpoint,
            headers=headers,
            data=data,
            files=files,
            timeout=args.timeout,
        )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"ok": True, "raw": response.text}


def _upload_json(image_path: Path, args: argparse.Namespace) -> Dict[str, Any]:
    with image_path.open("rb") as fh:
        encoded = base64.b64encode(fh.read()).decode("ascii")
    payload: Dict[str, Any] = {
        "filename": image_path.name,
        "image_base64": encoded,
    }
    if args.device_id:
        payload["device_id"] = args.device_id
    headers = {
        "access-token": args.access_token,
        "Content-Type": "application/json",
    }
    response = requests.post(
        args.endpoint,
        headers=headers,
        data=json.dumps(payload),
        timeout=args.timeout,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"ok": True, "raw": response.text}


def main() -> None:
    args = parse_args()
    if not args.access_token:
        raise SystemExit("TRMNL access token is required (use --access-token or set TRMNL_ACCESS_TOKEN).")

    result = _create_payload(args)
    image_path: Path = result["rendered_path"]
    print(f"[push_trmnl_radar] rendered snapshot to {image_path}")

    if args.dry_run:
        print("[push_trmnl_radar] dry-run enabled, skipping upload.")
        return

    try:
        if args.json_payload:
            response_data = _upload_json(image_path, args)
        else:
            response_data = _upload_multipart(image_path, args)
    except requests.HTTPError as exc:
        raise SystemExit(f"Upload failed: {exc.response.status_code} {exc.response.text}") from exc
    except requests.RequestException as exc:
        raise SystemExit(f"Upload failed: {exc}") from exc

    print(f"[push_trmnl_radar] upload completed: {response_data}")


if __name__ == "__main__":
    main()
