from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projects.falklandV2.core.engine import (
    BOARD_MIN_X,
    BOARD_MIN_Y,
    BOARD_N,
    Engine,
    WORLD_N,
)
from projects.falklandV2.radar import HOSTILES, Radar


def test_engine_tick_moves_ship_eastward():
    eng = Engine()
    x0, y0 = eng._ship_xy()

    eng.set_course(90)  # east
    eng.set_speed(20)   # 20 kts
    eng.tick(3600)      # one hour

    x1, y1 = eng._ship_xy()

    assert pytest.approx(x1, rel=0.0, abs=1e-6) == min(x0 + 20.0, float(WORLD_N))
    assert pytest.approx(y1, rel=0.0, abs=1e-6) == y0

    col, row = eng.ship.board_cell()
    assert col == chr(ord("A") + min(BOARD_N - 1, round(x1 - BOARD_MIN_X)))
    assert 1 <= row <= BOARD_N


def test_engine_radar_contacts_synced():
    eng = Engine()
    assert isinstance(eng.radar, Radar) or eng.radar is None

    eng.tick(1.0)

    if eng.radar is not None:
        assert isinstance(eng.contacts, list)
        assert eng.contacts == eng.radar.contacts
        # pool exposes contacts view
        assert eng.pool.contacts == eng.contacts


def test_radar_spawn_weighting_uses_catalog(monkeypatch):
    # Force predictable RNG
    class DummyRandom:
        def __init__(self):
            self.calls = 0

        def uniform(self, a, b):
            self.calls += 1
            return a  # deterministic lower bound

        def randint(self, a, b):
            return a

    rng = DummyRandom()
    radar = Radar(rec=None, rng=rng, catalog_path=Path("projects/falklandV2/data/contacts.json"))
    own_x = own_y = float(WORLD_N / 2)

    # Drain initial cadence to trigger a scan
    radar.tick(radar.cfg["scan_interval_s"], own_x, own_y)
    assert len(radar.contacts) <= radar.cfg["max_contacts"]

    if radar.contacts:
        spawned = radar.contacts[0]
        names = [hostile[0] for hostile in HOSTILES]
        assert spawned.name in names
