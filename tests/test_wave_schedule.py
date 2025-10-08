from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from projects.falklandV2.runtime_service import GameRuntime


DATA_PATH = Path("projects/falklandV2/data/attack_waves.json")


def test_start_wave_respected_and_reload() -> None:
    original_text = DATA_PATH.read_text(encoding="utf-8")
    original_data = json.loads(original_text)
    original_index = int(original_data.get("start_wave", 1)) - 1

    modified = json.loads(original_text)
    modified["start_wave"] = 2
    DATA_PATH.write_text(json.dumps(modified, indent=2), encoding="utf-8")

    try:
        runtime = GameRuntime()
        assert runtime.wave_schedule is not None
        assert runtime.wave_schedule.start_wave_index == 1
        start_elapsed = runtime.wave_schedule.waves[1].start_s
        assert abs(runtime.radar._wave_elapsed - start_elapsed) < 1e-6

        DATA_PATH.write_text(original_text, encoding="utf-8")
        runtime.reset_state()
        assert runtime.wave_schedule is not None
        assert runtime.wave_schedule.start_wave_index == max(0, original_index)
    finally:
        DATA_PATH.write_text(original_text, encoding="utf-8")
