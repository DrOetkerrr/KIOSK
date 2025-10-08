from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projects.falklandV2.radar import Radar  # noqa: E402


def _radar_with_stubbed_catalog() -> Radar:
    data_path = Path(__file__).resolve().parents[1] / "projects" / "falklandV2" / "data" / "contacts.json"
    radar = Radar(rec=None, catalog_path=str(data_path))
    radar.catalog.pick_friendly = lambda: ("Test Escort", 15.0, "Ship")  # type: ignore[attr-defined]
    radar.catalog.pick_hostile = lambda: ("Test Hostile", 300.0, "Aircraft")  # type: ignore[attr-defined]
    radar.catalog.pick_hostile_weighted = lambda _map=None: ("Test Hostile", 300.0, "Aircraft")  # type: ignore[attr-defined]
    return radar


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
