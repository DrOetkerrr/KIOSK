import random
from pathlib import Path

from falklandv3.core.radar import RadarSimulator
from falklandv3.data.catalog import ContactCatalog
from falklandv3.data.waves import AttackWave, SpawnOption, WaveSchedule


def test_radar_uses_wave_schedule(tmp_path: Path):
    catalog_path = tmp_path / "contacts.json"
    catalog_path.write_text(
        """
        {"items": [
          {"name": "Mirage", "allegiance": "Hostile", "speed_kts": 400, "weight": 1},
          {"name": "Sea Harrier", "allegiance": "Friendly", "speed_kts": 350, "weight": 1}
        ]}
        """
    )
    catalog = ContactCatalog(catalog_path)
    wave = AttackWave(
        label="Aggressive",
        duration_s=120.0,
        spawn_rate_per_min=60.0,
        friendly_prob=0.0,
        direction_bearing=90.0,
        options=[SpawnOption(name="Mirage", chance=1.0, min_range_nm=20.0)],
    )
    schedule = WaveSchedule([wave])
    radar = RadarSimulator(rng=random.Random(42), catalog=catalog, wave_schedule=schedule)
    radar.tick(1.0, own_x=10.0, own_y=10.0)
    assert radar.contacts
    contact = radar.contacts[0]
    assert contact.name == "Mirage"
    assert contact.allegiance == "Hostile"
