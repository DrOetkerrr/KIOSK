import random

from falklandv3.core.radar import RadarSimulator, RadarContact


def test_radar_contact_strike_range_exocet_prioritises_max_distance():
    contact = RadarContact(
        id=1,
        name="AM39 Exocet",
        allegiance="Hostile",
        x_nm=0.0,
        y_nm=0.0,
        heading_deg=0.0,
        speed_kts=500.0,
        category="Missile",
        primary_weapon="Exocet AM39",
        min_range_nm=5.0,
        max_range_nm=35.0,
    )
    strike = contact.strike_range_nm(3.5)
    assert strike >= 5.0


def test_radar_spawns_and_moves_contacts():
    rng = random.Random(42)
    radar = RadarSimulator(rng=rng, max_contacts=4, spawn_interval_s=1.0)
    radar.ensure_seed_contacts(20.0, 20.0)
    assert len(radar.contacts) == 3  # three friendlies seeded

    radar.tick(60.0, 20.0, 20.0)
    assert len(radar.contacts) >= 3

    views = radar.views(20.0, 20.0)
    assert views
    nearest = views[0]
    assert 0.0 <= nearest.range_nm <= 30.0
