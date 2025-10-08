import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projects.falklandV2.subsystems import webcore as core


def test_load_eng_sys_survives_corrupted_file(tmp_path, monkeypatch):
    # Point ENG_SYS_PATH to a temp file and reset cache
    eng_path = tmp_path / "eng_systems.json"
    monkeypatch.setattr(core, "ENG_SYS_PATH", eng_path)
    monkeypatch.setattr(core, "_ENG_SYS_CACHE", None)

    core.reset_eng_state()

    state = core.load_eng_sys()
    # Flip Radar offline and persist
    for sys in state.get("systems", []):
        if sys.get("id") == "Radar":
            sys["status"] = "Offline"
            sys["team_assigned"] = True
            sys["timer_s"] = 90
            break
    core.save_eng_sys(state)

    loaded = core.load_eng_sys()
    radar = next((s for s in loaded.get("systems", []) if s.get("id") == "Radar"), {})
    assert radar.get("status") == "Offline"

    # Corrupt the JSON file mid-write to simulate a concurrent truncation.
    eng_path.write_text('{"teams_total": 4,\n', encoding="utf-8")

    recovered = core.load_eng_sys()
    radar_recovered = next((s for s in recovered.get("systems", []) if s.get("id") == "Radar"), {})
    assert radar_recovered.get("status") == "Offline"


def test_repair_timer_counts_down_with_subsecond_tick():
    now = 1_000.0
    eng = {
        'teams_total': 1,
        'teams_free': 0,
        'systems': [
            {
                'id': 'COMMS',
                'name': 'COMMS station',
                'status': 'Damaged',
                'team_assigned': True,
                'timer_s': 2.0,
                'last_damaged_ts': now - 30.0,
                'response_deadline_ts': 0.0,
            }
        ],
    }

    # Four 0.25s ticks should reduce the timer by 1 second overall.
    for _ in range(4):
        assert core._advance_eng_repairs(eng, 0.25, now) is True
        now += 0.25

    sys = eng['systems'][0]
    assert math.isclose(sys['timer_s'], 1.0, rel_tol=1e-6, abs_tol=1e-6)
    assert sys['status'] == 'Damaged'
    assert sys['team_assigned'] is True
    assert eng['teams_free'] == 0

    # Another four ticks should finish the repair and free the team.
    for _ in range(4):
        assert core._advance_eng_repairs(eng, 0.25, now) is True
        now += 0.25

    sys = eng['systems'][0]
    assert sys['status'] == 'OK'
    assert sys['team_assigned'] is False
    assert sys['timer_s'] == 0.0
    assert eng['teams_free'] == 1
