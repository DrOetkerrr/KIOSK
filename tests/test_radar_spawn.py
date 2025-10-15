from __future__ import annotations

import math
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projects.falklandV2.radar import Radar, Contact  # noqa: E402


class _WaveStub:
    def __init__(self, enemies: tuple[str, ...] = ('bogey',)):
        self.enemies = enemies


class _ScheduleStub:
    def __init__(self, enemy_name: str):
        self._enemy_name = enemy_name

    def pick_enemy(self, wave, rng):  # pylint: disable=unused-argument
        return type('E', (), {'name': self._enemy_name, 'min_range_nm': None, 'max_range_nm': None})()


def _radar_with_stubbed_catalog() -> Radar:
    data_path = Path(__file__).resolve().parents[1] / "projects" / "falklandV2" / "data" / "contacts.json"
    radar = Radar(rec=None, catalog_path=str(data_path))
    radar.catalog.pick_friendly = lambda: ("Test Escort", 15.0, "Ship")  # type: ignore[attr-defined]
    radar.catalog.pick_hostile = lambda: ("Test Hostile", 300.0, "Aircraft")  # type: ignore[attr-defined]
    radar.catalog.pick_hostile_weighted = lambda _map=None: ("Test Hostile", 300.0, "Aircraft")  # type: ignore[attr-defined]
    return radar


def _make_stub_rng(values: list[float]):
    class _StubRng:
        def __init__(self, seq: list[float]):
            self._values = list(seq)

        def uniform(self, a: float, b: float) -> float:
            if not self._values:
                raise AssertionError("Stub RNG exhausted")
            return self._values.pop(0)

        def random(self) -> float:
            return 0.0

    return _StubRng(values)


def test_force_spawn_generates_unique_ids_under_concurrency():
    radar = _radar_with_stubbed_catalog()

    ids: list[int] = []

    def worker():
        for _ in range(8):
            c = radar.force_spawn(12.0, 18.0, "Friendly", 45.0, 6.0)
            ids.append(c.id)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ids) == len(set(ids)), "duplicate contact ids detected"
    assert len(radar.contacts) == len(ids)


def test_force_spawn_uses_last_known_origin_when_state_zero():
    radar = _radar_with_stubbed_catalog()

    first = radar.force_spawn(14.0, 9.0, "Friendly", 60.0, 5.0)
    assert (first.x, first.y) != (0.0, 0.0)

    second = radar.force_spawn(0.0, 0.0, "Friendly", 30.0, 4.0)
    assert (round(second.x, 3), round(second.y, 3)) != (0.0, 0.0)
    assert (round(second.x, 3), round(second.y, 3)) != (round(first.x, 3), round(first.y, 3)), "spawn should respect offset from previous origin"


def test_force_spawn_handles_zero_origin_on_initial_spawn():
    radar = _radar_with_stubbed_catalog()

    contact = radar.force_spawn(0.0, 0.0, "Friendly", 45.0, 6.0)
    assert (round(contact.x, 3), round(contact.y, 3)) != (0.0, 0.0)
    ox, oy = radar._last_own_xy  # type: ignore[attr-defined]
    assert (round(ox, 3), round(oy, 3)) != (0.0, 0.0), "fallback origin should not stick at zero"


def test_hostile_spawn_creates_pair_with_spacing():
    radar = _radar_with_stubbed_catalog()
    radar.wave_schedule = _ScheduleStub('Test Hostile')  # type: ignore[attr-defined]

    class PairRng:
        def __init__(self):
            self.uniform_calls = [25.0, 45.0, 60.0]

        def uniform(self, a: float, b: float) -> float:
            if self.uniform_calls:
                return self.uniform_calls.pop(0)
            return a

        def random(self) -> float:
            return 0.0

    radar.rng = PairRng()  # type: ignore[assignment]
    own_x = 20.0
    own_y = 20.0
    radar._spawn_attempt(own_x=own_x, own_y=own_y, friendly_prob=0.0, surprise=False, wave=_WaveStub(('pair',)))
    hostiles = [c for c in radar.contacts if c.allegiance == "Hostile"]
    assert len(hostiles) == 2, "hostile spawn should create a leader and wingman"
    roles = {c.meta.get('formation', {}).get('role') for c in hostiles}
    assert roles == {"leader", "wingman"}
    spacing_nm = radar._enemy_spacing_nm()
    leader = next(c for c in hostiles if c.meta.get('formation', {}).get('role') == 'leader')
    wingman = next(c for c in hostiles if c.meta.get('formation', {}).get('role') == 'wingman')
    dist = math.hypot(leader.x - wingman.x, leader.y - wingman.y)
    assert dist == pytest.approx(spacing_nm, rel=0.05)


def test_super_etendard_spawns_solo():
    radar = _radar_with_stubbed_catalog()
    radar.wave_schedule = _ScheduleStub('Super Etendard')  # type: ignore[attr-defined]
    radar.catalog.pick_hostile = lambda: ("Super Etendard", 400.0, "Aircraft")  # type: ignore[attr-defined]
    radar.catalog.pick_hostile_weighted = lambda _map=None: ("Super Etendard", 400.0, "Aircraft")  # type: ignore[attr-defined]
    radar.catalog.pick_hostile_by_name = lambda _name: ("Super Etendard", 400.0, "Aircraft")  # type: ignore[attr-defined]

    radar._maybe_spawn_hostile_wingman = lambda *args, **kwargs: None  # type: ignore[assignment]

    class ÉtendardRng:
        def __init__(self):
            self.uniform_calls = [18.0, 30.0, 30.0]

        def uniform(self, a: float, b: float) -> float:
            if self.uniform_calls:
                return self.uniform_calls.pop(0)
            return a

        def random(self) -> float:
            return 0.0

    radar.rng = ÉtendardRng()  # type: ignore[assignment]
    own_x = 20.0
    own_y = 20.0
    radar._spawn_attempt(own_x=own_x, own_y=own_y, friendly_prob=0.0, surprise=False, wave=_WaveStub(('enemy',)))
    hostiles = [c for c in radar.contacts if c.allegiance == "Hostile"]
    assert len(hostiles) == 1, "Super Étendard should operate solo"
    distance_nm = math.hypot(hostiles[0].x - own_x, hostiles[0].y - own_y)
    assert distance_nm >= 19.9


def test_hostile_pair_spacing_enforced_close_in():
    radar = _radar_with_stubbed_catalog()
    radar.wave_schedule = _ScheduleStub('Test Hostile')  # type: ignore[attr-defined]
    radar._spawn_attempt(own_x=20.0, own_y=20.0, friendly_prob=0.0, surprise=False, wave=_WaveStub())
    hostiles = [c for c in radar.contacts if c.allegiance == "Hostile"]
    assert len(hostiles) == 2
    formation_id = hostiles[0].meta.get('formation', {}).get('id')
    assert formation_id == hostiles[1].meta.get('formation', {}).get('id')
    leader = next(c for c in hostiles if c.meta.get('formation', {}).get('role') == 'leader')
    wingman = next(c for c in hostiles if c.meta.get('formation', {}).get('role') == 'wingman')
    # Compress spacing artificially, place leader close to Hermes
    wingman.x, wingman.y = leader.x, leader.y
    leader.x, leader.y = 21.5, 20.0
    radar._sync_hostile_formations(20.0, 20.0)
    spacing_nm = radar._enemy_spacing_nm()
    dist = ((leader.x - wingman.x) ** 2 + (leader.y - wingman.y) ** 2) ** 0.5
    assert dist >= spacing_nm * 0.95, "formation sync should restore minimum trail spacing near final attack"


def test_super_etendard_spawn_respects_minimum_range(monkeypatch: pytest.MonkeyPatch) -> None:
    radar = _radar_with_stubbed_catalog()
    radar.wave_schedule = _ScheduleStub('Super Etendard')  # type: ignore[attr-defined]

    class StubRng:
        def __init__(self):
            self.uniform_calls = [12.0, 90.0, 45.0]

        def uniform(self, a: float, b: float) -> float:
            if self.uniform_calls:
                return self.uniform_calls.pop(0)
            return a

        def random(self) -> float:
            return 0.0

    radar.rng = StubRng()  # type: ignore[assignment]
    radar.catalog.pick_hostile = lambda: ("Super Etendard", 400.0, "Aircraft")  # type: ignore[attr-defined]
    radar.catalog.pick_hostile_weighted = lambda _map=None: ("Super Etendard", 400.0, "Aircraft")  # type: ignore[attr-defined]
    radar.catalog.pick_hostile_by_name = lambda _name: ("Super Etendard", 400.0, "Aircraft")  # type: ignore[attr-defined]

    own_x = 15.0
    own_y = 15.0
    radar._spawn_attempt(own_x=own_x, own_y=own_y, friendly_prob=0.0, surprise=False, wave=_WaveStub(('enemy',)))
    hostiles = [c for c in radar.contacts if c.allegiance == "Hostile"]
    assert hostiles
    min_distance = min(math.hypot(c.x - own_x, c.y - own_y) for c in hostiles)
    assert min_distance >= 19.9


def test_surface_ship_spawn_minimum_distance(monkeypatch: pytest.MonkeyPatch) -> None:
    radar = _radar_with_stubbed_catalog()
    radar.catalog.pick_hostile = lambda: ("ARA Buenos Aires", 25.0, "Ship")  # type: ignore[attr-defined]
    radar.catalog.pick_hostile_weighted = lambda _map=None: ("ARA Buenos Aires", 25.0, "Ship")  # type: ignore[attr-defined]
    radar.catalog.pick_hostile_by_name = lambda _name: ("ARA Buenos Aires", 25.0, "Ship")  # type: ignore[attr-defined]

    class StubRng:
        def __init__(self):
            self.uniform_calls = [12.0, 45.0, 30.0]

        def uniform(self, a: float, b: float) -> float:
            if self.uniform_calls:
                return self.uniform_calls.pop(0)
            return a

        def random(self) -> float:
            return 0.0

    radar.rng = StubRng()  # type: ignore[assignment]
    own_x = 20.0
    own_y = 20.0
    radar.wave_schedule = _ScheduleStub('ARA Buenos Aires')  # type: ignore[attr-defined]

    radar._spawn_attempt(own_x=own_x, own_y=own_y, friendly_prob=0.0, surprise=False, wave=_WaveStub(('ship',)))
    hostiles = [c for c in radar.contacts if c.allegiance == "Hostile"]
    assert hostiles
    min_distance = min(math.hypot(c.x - own_x, c.y - own_y) for c in hostiles)
    assert min_distance >= 19.9
    distance_nm = math.hypot(hostiles[0].x - own_x, hostiles[0].y - own_y)
    assert distance_nm == pytest.approx(20.0, rel=0.01)


def test_radar_default_spawn_rates() -> None:
    radar = Radar()
    assert radar.cfg["spawn_rate_per_min"] == pytest.approx(0.12)
    assert radar.cfg["surprise_rate_per_min"] == pytest.approx(0.02)


def test_friendly_spawn_within_radius_and_avoids_occupied_cells_for_ships() -> None:
    radar = _radar_with_stubbed_catalog()
    radar.contacts.clear()
    radar._next_id = 1
    own_x = 20.0
    own_y = 20.0

    occ_x, occ_y = Radar._cell_to_world('AR20')
    radar.contacts.append(Contact(id=99, name='Existing Ship', allegiance='Friendly', x=occ_x, y=occ_y,
                                  course_deg=0.0, speed_kts=0.0, threat='low', meta={}))

    radar.catalog.pick_friendly = lambda: ('Stores Ship', 15.0, 'Ship')  # type: ignore[attr-defined]

    def _polar(x_val: float, y_val: float) -> tuple[float, float]:
        dx = x_val - own_x
        dy = y_val - own_y
        radius = math.hypot(dx, dy)
        bearing = (math.degrees(math.atan2(dx, -dy)) % 360.0)
        return radius, bearing

    r1, b1 = _polar(occ_x, occ_y)
    alt_x, alt_y = Radar._cell_to_world('AQ21')
    r2, b2 = _polar(alt_x, alt_y)
    values = [r1, b1, r2, b2, r2, b2]
    radar.rng = _make_stub_rng(values)  # type: ignore[assignment]

    radar._spawn_attempt(own_x=own_x, own_y=own_y, friendly_prob=1.0, surprise=False, wave=None)
    new_contact = radar.contacts[-1]
    new_cell = radar._cell_label_for_xy(new_contact.x, new_contact.y)
    assert new_cell != Radar._normalize_cell_label('AR20')
    distance_nm = math.hypot(new_contact.x - own_x, new_contact.y - own_y)
    assert distance_nm <= radar._nm_per_cell * 10.0 + 1e-6


def test_friendly_aircraft_can_share_occupied_cell() -> None:
    radar = _radar_with_stubbed_catalog()
    radar.contacts.clear()
    radar._next_id = 1
    own_x = 20.0
    own_y = 20.0

    occ_x, occ_y = Radar._cell_to_world('AR20')
    radar.contacts.append(Contact(id=42, name='Existing Ship', allegiance='Friendly', x=occ_x, y=occ_y,
                                  course_deg=0.0, speed_kts=0.0, threat='low', meta={}))

    radar.catalog.pick_friendly = lambda: ('Sea King', 90.0, 'Aircraft')  # type: ignore[attr-defined]

    dx = occ_x - own_x
    dy = occ_y - own_y
    radius = math.hypot(dx, dy)
    bearing = (math.degrees(math.atan2(dx, -dy)) % 360.0)
    values = [radius, bearing, radius, bearing, radius, bearing]
    radar.rng = _make_stub_rng(values)  # type: ignore[assignment]

    radar._spawn_attempt(own_x=own_x, own_y=own_y, friendly_prob=1.0, surprise=False, wave=None)
    new_contact = radar.contacts[-1]
    new_cell = radar._cell_label_for_xy(new_contact.x, new_contact.y)
    assert new_cell == Radar._normalize_cell_label('AR20')


def test_force_spawn_friendly_respects_radius_and_occupancy() -> None:
    radar = _radar_with_stubbed_catalog()
    radar.contacts.clear()
    radar._next_id = 1
    own_x = 20.0
    own_y = 20.0

    occ_x, occ_y = Radar._cell_to_world('AR20')
    radar.contacts.append(Contact(id=77, name='Existing Ship', allegiance='Friendly', x=occ_x, y=occ_y,
                                  course_deg=0.0, speed_kts=0.0, threat='low', meta={}))

    alt_x, alt_y = Radar._cell_to_world('AQ21')
    alt_radius = math.hypot(alt_x - own_x, alt_y - own_y)
    alt_bearing = (math.degrees(math.atan2(alt_x - own_x, -(alt_y - own_y))) % 360.0)
    radar.rng = _make_stub_rng([alt_radius, alt_bearing, alt_radius, alt_bearing])  # type: ignore[assignment]

    radar.force_spawn(own_x, own_y, 'Friendly', 0.0, 40.0)
    new_contact = radar.contacts[-1]
    distance_nm = math.hypot(new_contact.x - own_x, new_contact.y - own_y)
    assert distance_nm <= radar._nm_per_cell * 10.0 + 1e-6
    cell = radar._cell_label_for_xy(new_contact.x, new_contact.y)
    assert cell != Radar._normalize_cell_label('AR20')
