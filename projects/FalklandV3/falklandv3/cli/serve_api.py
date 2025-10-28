"""CLI for launching the Falkland V3 FastAPI application."""

from __future__ import annotations

import argparse
import importlib
import os
import sys

from falklandv3.config import load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the Falkland V3 API via uvicorn.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (uvicorn)")
    parser.add_argument("--seed", type=int, default=None, help="Seed runtime RNG for deterministic state.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.seed is not None:
        os.environ["FALKLANDV3_RNG_SEED"] = str(args.seed)

    settings = load_settings()

    try:
        uvicorn = importlib.import_module("uvicorn")
    except ImportError as exc:
        print("uvicorn is required to serve the API; install project dependencies.", file=sys.stderr)
        return 1

    from falklandv3.adapters.api.server import create_app

    app = create_app()

    log_config = {
        "version": 1,
        "formatters": {
            "default": {"format": "%(levelname)s %(name)s %(message)s"}
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO"},
        },
    }

    config = uvicorn.Config(  # type: ignore[attr-defined]
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_config=log_config,
        env_file=None,
    )
    server = uvicorn.Server(config)  # type: ignore[attr-defined]

    print(
        "Serving Falkland V3 API",
        f"host={args.host}",
        f"port={args.port}",
        f"tick_dt={settings.tick_dt}",
    )
    return server.run()


if __name__ == "__main__":  # pragma: no cover - manual execution
    raise SystemExit(main())
