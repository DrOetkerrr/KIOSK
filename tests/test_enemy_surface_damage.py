from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from projects.falklandV2.subsystems import webcore
from projects.falklandV2.subsystems import engage


class _StubContact:
    def __init__(self, cid: int, name: str = "ARA Test") -> None:
        self.id = cid
        self.name = name
        self.allegiance = 'Hostile'
        self.x = 30.0
        self.y = 30.0
        self.course_deg = 0.0
        self.speed_kts = 20.0
        self.threat = 'medium'
        self.meta: Dict[str, Any] = {
            'cap': {'class': 'Ship'},
            'surface_ship': {'hp': 4.0, 'max_hp': 4.0},
        }


class _StubRadar:
    def __init__(self, contact: _StubContact) -> None:
        self.contacts: List[_StubContact] = [contact]


class _StubENG:
    @staticmethod
    def public_state() -> Dict[str, Any]:
        return {'ship': {'col': 10.0, 'row': 10.0}}


class _StubWD:
    def __init__(self, contact: _StubContact) -> None:
        self.RADAR = _StubRadar(contact)
        self.ENG = _StubENG()
        self.STATE_LOCK = contextlib.nullcontext()
        self.ENEMY_SURFACE_STATE: Dict[int, Dict[str, Any]] = {}
        self._events: List[Tuple[str, Dict[str, Any]]] = []
        self._flights: List[Dict[str, Any]] = []

    def record_event(self, event_id: str, payload: Dict[str, Any]) -> None:  # pragma: no cover - debug helper
        self._events.append((event_id, dict(payload)))

    def record_officer(self, *_args: Any, **_kwargs: Any) -> None:  # pragma: no cover - unused stub
        pass

    def record_flight(self, payload: Dict[str, Any]) -> None:  # pragma: no cover - debug helper
        self._flights.append(dict(payload))


def test_sea_dart_damage_accumulates_and_sinks() -> None:
    contact = _StubContact(101, name="ARA Hércules")
    wd = _StubWD(contact)

    # First hit: handled, not sunk
    handled, sunk = webcore.apply_enemy_ship_damage(wd, 101, weapon='Sea Dart SAM', target_name='ARA Hércules', target_class='Ship')
    assert handled is True and sunk is False
    assert pytest.approx(wd.ENEMY_SURFACE_STATE[101]['hp']) == 3.0
    assert contact.meta['surface_ship']['hp'] == pytest.approx(3.0)

    # Second hit triggers retreat (≤50%)
    handled, sunk = webcore.apply_enemy_ship_damage(wd, 101, weapon='Sea Dart SAM', target_name='ARA Hércules', target_class='Ship')
    assert handled is True and sunk is False
    assert wd.ENEMY_SURFACE_STATE[101]['fleeing'] is True
    assert contact.meta.get('retreating') is True

    # Two more hits sink the ship
    webcore.apply_enemy_ship_damage(wd, 101, weapon='Sea Dart SAM', target_name='ARA Hércules', target_class='Ship')
    handled, sunk = webcore.apply_enemy_ship_damage(wd, 101, weapon='Sea Dart SAM', target_name='ARA Hércules', target_class='Ship')
    assert handled is True and sunk is True
    assert 101 not in wd.ENEMY_SURFACE_STATE
    assert not any(int(getattr(c, 'id', -1)) == 101 for c in wd.RADAR.contacts)

    # Ensure events captured
    event_ids = [evt for evt, _payload in wd._events]
    assert 'enemy.surface.flee' in event_ids
    assert 'enemy.surface.sunk' in event_ids


def test_weapon_matrix_allows_sea_dart_against_surface() -> None:
    assert engage.weapon_valid_for_target('seacat', 'Ship') is True
