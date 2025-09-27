from __future__ import annotations

from typing import Any, Dict, List, Tuple

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from projects.falklandV2.subsystems import webcore


class _StubWD:
    def __init__(self) -> None:
        self.events: List[Tuple[str, Dict[str, Any]]] = []

    def record_event(self, event_id: str, payload: Dict[str, Any] | None = None) -> None:  # pragma: no cover - simple stub
        self.events.append((event_id, dict(payload or {})))


def test_enemy_bomb_records_special_event() -> None:
    wd = _StubWD()
    payload = {
        'contact_id': 7,
        'target': 'Hermes',
        'name': 'Canberra bomber',
        'weapon': 'Bombs',
    }
    webcore._record_enemy_attack_event(wd, 'bomb', 'hit', payload, context={'source': 'enemy_attack', 'contact_id': 7})

    event_ids = [evt for evt, _ in wd.events]
    assert 'enemy.attack.hit' in event_ids
    assert 'enemy.bomb.hit' in event_ids


def test_enemy_non_bomb_does_not_duplicate() -> None:
    wd = _StubWD()
    payload = {
        'contact_id': 8,
        'target': 'Sheffield',
        'name': 'Skyhawk',
        'weapon': 'Rockets',
    }
    webcore._record_enemy_attack_event(wd, 'rocket', 'miss', payload, context={'source': 'enemy_attack', 'contact_id': 8})

    event_ids = [evt for evt, _ in wd.events]
    assert event_ids == ['enemy.attack.miss']


@pytest.mark.parametrize("attack_kind", ["attack", "gun"])
def test_enemy_surface_event_duplicates(attack_kind: str) -> None:
    wd = _StubWD()
    payload = {
        'contact_id': 9,
        'target': 'Sheffield',
        'name': 'ARA General Belgrano',
        'weapon': '6-inch main battery',
    }
    webcore._record_enemy_attack_event(wd, attack_kind, 'hit', payload, context={'source': 'enemy_attack', 'contact_id': 9})

    event_ids = [evt for evt, _ in wd.events]
    assert 'enemy.attack.hit' in event_ids
    assert 'enemy.surface.hit' in event_ids
