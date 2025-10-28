"""Command-line driver for Falkland V3 runtime."""

from __future__ import annotations

import argparse
import sys
import time
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from falklandv3.config import Settings, load_settings
from falklandv3.services.persistence import PersistenceConfig, StateRepository
from falklandv3.services.runtime import GameRuntime
from falklandv3.services.runtime_loop import LoopConfig, RuntimeLoop


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Falkland V3 runtime simulation loop.")
    parser.add_argument("--ticks", type=int, default=10, help="Number of ticks to execute (default: 10)")
    parser.add_argument("--dt", type=float, default=None, help="Delta seconds per tick (default from settings)")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Optional directory for persistence output; uses a temp dir if omitted.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=None,
        help="Optional sleep between ticks to observe logs (seconds).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run ticks on a background loop until ^C instead of fixed tick count.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print weather/radio summary after each tick.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed the runtime RNG for deterministic output.",
    )
    return parser


def create_runtime(log_dir: Path | None, *, audio_max_events: int, settings: Settings) -> tuple[GameRuntime, object]:
    if log_dir is None:
        temp = TemporaryDirectory(prefix="falklandv3_cli_")
        repo_root = Path(temp.name)
        context: object = temp
    else:
        repo_root = log_dir
        repo_root.mkdir(parents=True, exist_ok=True)
        context = nullcontext()
    repo = StateRepository(PersistenceConfig(repo_root))
    runtime = GameRuntime(repository=repo, audio_max_events=audio_max_events, settings=settings)
    return runtime, context


def _print_summary(snapshot: dict) -> None:
    weather = snapshot.get("weather") or {}
    radio = snapshot.get("radio", {}).get("messages", [])
    latest = radio[-1]["text"] if radio else "(no messages)"
    print(
        "  Weather: "
        f"wind {weather.get('wind_dir_deg', 0):.0f}° @ {weather.get('wind_speed_kts', 0):.1f} kts, "
        f"sea state {weather.get('sea_state', 0):.1f}"
    )
    print(f"  Radio: {latest}")


def run_simulation(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.seed is not None:
        settings = replace(settings, rng_seed=int(args.seed))
    dt = args.dt if args.dt is not None else settings.tick_dt
    sleep = args.sleep if args.sleep is not None else settings.loop_sleep
    log_dir = args.log_dir if args.log_dir is not None else settings.log_dir
    runtime, ctx = create_runtime(log_dir, audio_max_events=settings.audio_max_events, settings=settings)
    with ctx:
        if args.live:
            loop = RuntimeLoop(runtime, LoopConfig(dt_seconds=dt))
            print(f"Starting live loop dt={dt:.2f}s. Ctrl+C to stop.")
            loop.start()
            try:
                while True:
                    time.sleep(max(0.5, dt))
                    snap = runtime.snapshot()
                    hud = snap["ship"]["hud"]
                    mission = snap["mission"]["status"]
                    cap = snap["cap"]["status"]
                    print(f"[tick {loop.ticks()}] {hud} | mission={mission} cap={cap}")
                    if args.summary:
                        _print_summary(snap)
            except KeyboardInterrupt:
                print("Stopping loop...")
                loop.stop()
        else:
            print(f"Starting Falkland V3 runtime: ticks={args.ticks} dt={dt}")
            loop = RuntimeLoop(runtime, LoopConfig(dt_seconds=dt, stop_after_ticks=args.ticks))
            loop.start()
            loop.stop()
            for tick_idx in range(1, loop.ticks() + 1):
                snap = runtime.snapshot()
                hud = snap["ship"]["hud"]
                mission = snap["mission"]["status"]
                cap = snap["cap"]["status"]
                print(f"[tick {tick_idx}] {hud} | mission={mission} cap={cap}")
                if args.summary:
                    _print_summary(snap)
                if sleep:
                    time.sleep(sleep)
            print("Simulation complete.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_simulation(args)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
