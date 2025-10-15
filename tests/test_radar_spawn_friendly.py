from __future__ import annotations

from projects.falklandV2.radar import Radar


def test_force_spawn_never_returns_sea_harrier():
    radar = Radar()
    for _ in range(12):
        c = radar.force_spawn(20.0, 20.0, 'Friendly', bearing_deg=45.0, range_nm=10.0)
        assert 'harrier' not in str(c.name).lower()
