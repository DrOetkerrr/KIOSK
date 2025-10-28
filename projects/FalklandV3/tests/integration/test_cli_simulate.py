import os
import subprocess
import sys
from pathlib import Path


def test_cli_simulate_runs(tmp_path):
    env = os.environ.copy()
    project_root = Path(__file__).resolve().parents[3]
    existing = env.get("PYTHONPATH")
    sep = os.pathsep
    env["PYTHONPATH"] = f"{project_root}{sep}{existing}" if existing else str(project_root)
    log_dir = tmp_path / "logs"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "falklandv3.cli.simulate",
            "--ticks",
            "1",
            "--dt",
            "0.1",
            "--log-dir",
            str(log_dir),
            "--summary",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        check=True,
    )
    assert "Simulation complete" in result.stdout
    assert "Weather:" in result.stdout
    assert "Radio:" in result.stdout


def _run_simulation(seed: int, log_dir: Path, env: dict) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "falklandv3.cli.simulate",
            "--ticks",
            "2",
            "--dt",
            "0.5",
            "--log-dir",
            str(log_dir),
            "--seed",
            str(seed),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        check=True,
    )
    snapshots = (log_dir / "snapshots.jsonl").read_text(encoding="utf-8").strip()
    assert snapshots, "expected snapshots output"
    return snapshots


def test_cli_simulate_seed_is_deterministic(tmp_path):
    env = os.environ.copy()
    project_root = Path(__file__).resolve().parents[3]
    existing = env.get("PYTHONPATH")
    sep = os.pathsep
    env["PYTHONPATH"] = f"{project_root}{sep}{existing}" if existing else str(project_root)

    first_dir = tmp_path / "run1"
    second_dir = tmp_path / "run2"
    first_dir.mkdir()
    second_dir.mkdir()

    snap_a = _run_simulation(999, first_dir, env)
    snap_b = _run_simulation(999, second_dir, env)

    assert snap_a == snap_b
