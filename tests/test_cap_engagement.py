from __future__ import annotations

import random
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from projects.falklandV2.subsystems.hermes_cap import HermesCAP


DATA_DIR = Path(__file__).resolve().parents[1] / "projects" / "falklandV2" / "data"


class _Target:
    def __init__(self, name: str = "Bandit", klass: str = "Aircraft") -> None:
        self.name = name
        setattr(self, "class", klass)
        self.type = klass


def _make_cap() -> HermesCAP:
    return HermesCAP(DATA_DIR)


def test_auto_engage_hits_air_target(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _make_cap()

    res = cap.request_cap_to_cell(
        target_cell="K10",
        distance_nm=8.0,
        mission_kind="intercept",
        loadout="aim9",
    )
    assert res["ok"] is True

    mission = cap.missions[-1]
    mission.status = "onstation"
    mission.ts["onstation"] = time.time() - 5
    mission.ts["etd_rtb"] = time.time() + 300
    mission.missiles_left = 4
    cap.set_permission(mission.id, True)

    target = _Target()
    cap.bind_target_resolver(lambda cid: target if cid == 42 else None)

    hits: list[tuple[int, str]] = []
    cap.bind_hit_callback(lambda cid, _name, klass, ctx=None: hits.append((cid, klass)))

    monkeypatch.setattr(random, "random", lambda: 0.0)

    result = cap.auto_engage(3.0, 42, now=time.time())

    assert result is not None
    assert result["hit"] is True
    assert result["shots"] == 1
    assert mission.missiles_left == 3
    assert hits == [(42, "Aircraft")]
