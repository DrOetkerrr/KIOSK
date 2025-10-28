from pathlib import Path

from falklandv3.data.waves import AttackWave, SpawnOption, WaveSchedule, load_wave_schedule


def test_wave_schedule_current():
    waves = WaveSchedule([
        AttackWave("A", 60.0, 1.0, 0.1, 0.0, []),
        AttackWave("B", 120.0, 2.0, 0.2, 90.0, []),
    ])
    assert waves.current(30).label == "A"
    assert waves.current(90).label == "B"
    assert waves.current(1000).label == "B"
    progress = waves.progress(30)
    assert progress is not None
    wave, elapsed = progress
    assert wave.label == "A"
    assert elapsed == 30

    progress = waves.progress(75)
    assert progress is not None
    wave, elapsed = progress
    assert wave.label == "B"
    assert elapsed == 15


def test_load_wave_schedule(tmp_path: Path):
    data = {
        "waves": [
            {
                "label": "Test",
                "duration_min": 1,
                "spawn_rate_per_min": 2,
                "friendly_prob": 0.5,
                "direction": "E",
                "spawns": {"Mirage": {"chance": 0.7, "min_range_nm": 15}}
            }
        ]
    }
    path = tmp_path / "waves.json"
    path.write_text(__import__("json").dumps(data))
    schedule = load_wave_schedule(path)
    wave = schedule.current(0)
    assert wave is not None
    assert wave.direction_bearing == 90.0
    assert wave.options[0].name == "Mirage"
