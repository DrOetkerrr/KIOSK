from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Dict, Tuple
from types import MethodType
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import pytest

from projects.falklandV2.runtime_service import GameRuntime
from projects.falklandV2.radar import Contact, WORLD_N
from projects.falklandV2.subsystems import webcore as core


WEAPON_NAMES: Tuple[str, ...] = (
    "MM38 Exocet",
    "Sea Dart SAM",
    "4.5 inch Mk.8 gun",
    "20mm Oerlikon",
    "20mm GAM-BO1 (twin)",
    "Corvus chaff",
)


def _weapon_defaults() -> Dict[str, int]:
    defaults = copy.deepcopy(core.WEAP_DEFAULT_AMMO)
    # Ensure guns have plenty of ammunition for repeated firing in tests.
    defaults.setdefault("20mm Oerlikon", 500)
    defaults.setdefault("20mm GAM-BO1 (twin)", 500)
    return defaults


@pytest.fixture
def runtime(tmp_path: Path):
    state_dir = Path("projects") / "falklandV2" / "state"
    ammo_path = state_dir / "ammo.json"
    arming_path = state_dir / "arming.json"

    ammo_backup = ammo_path.read_text(encoding="utf-8")
    arming_backup = arming_path.read_text(encoding="utf-8")

    try:
        rt = GameRuntime()
        if not hasattr(rt.engine, "public_state"):
            def _public_state(_self):
                return getattr(rt, "_engine_state_cache", {})
            rt.engine.public_state = MethodType(_public_state, rt.engine)  # type: ignore[attr-defined]
        rt._update_engine_state_view()
        yield rt
    finally:
        ammo_path.write_text(ammo_backup, encoding="utf-8")
        arming_path.write_text(arming_backup, encoding="utf-8")


@pytest.fixture
def arming_file_backup():
    path = Path("projects") / "falklandV2" / "state" / "arming.json"
    backup = path.read_text(encoding="utf-8")
    try:
        yield path
    finally:
        path.write_text(backup, encoding="utf-8")


def _configure_armed(runtime: GameRuntime, ammo: Dict[str, int] | None = None) -> None:
    payload = ammo or _weapon_defaults()
    runtime.state_repo.save_ammo(payload)
    runtime.state_repo.save_arming({name: "Armed" for name in payload})
    runtime._update_engine_state_view()


def _add_hostile_contact(
    runtime: GameRuntime,
    *,
    name: str,
    klass: str,
    distance_nm: float,
) -> Contact:
    own_x, own_y = runtime._own_xy()
    clamped_distance = max(0.05, distance_nm)
    x = max(0.0, min(float(WORLD_N), own_x + clamped_distance))
    y = own_y
    contact = Contact(
        id=runtime.radar._next_id,
        name=name,
        allegiance="Hostile",
        x=float(x),
        y=float(y),
        course_deg=(math.degrees(math.atan2(own_x - x, -(own_y - y))) % 360.0),
        speed_kts=0.0,
        meta={
            "class": klass,
            "cap": {"class": klass},
        },
    )
    runtime.radar._next_id += 1
    runtime.radar.contacts.append(contact)
    try:
        runtime.radar._pending_detection.add(contact.id)
    except Exception:
        pass
    runtime.radar.priority_id = contact.id
    runtime._sync_engine_contacts()
    runtime._update_engine_state_view()
    return contact


def _clear_contacts(runtime: GameRuntime) -> None:
    runtime.radar.contacts = []
    try:
        runtime.radar._pending_detection.clear()
    except Exception:
        pass
    runtime.radar.priority_id = None
    runtime._sync_engine_contacts()
    runtime._update_engine_state_view()


def test_weapon_arm_transitions_to_ready(monkeypatch: pytest.MonkeyPatch, arming_file_backup: Path) -> None:
    from projects.falklandV2 import webdash
    from projects.falklandV2.routes import weapons as weapons_routes

    callbacks = []

    class TimerStub:
        def __init__(self, interval, func, *, daemon=False):
            self.func = func

        def start(self):
            callbacks.append(self.func)

    monkeypatch.setattr(weapons_routes.threading, "Timer", lambda interval, func, daemon=True: TimerStub(interval, func))

    client = webdash.app.test_client()
    resp = client.post("/weapons/arm", json={"name": "Sea Dart SAM", "state": "Armed"})
    assert resp.json["ok"] is True

    assert callbacks, "arming completion callback not scheduled"
    callbacks.pop()()

    raw = json.loads(arming_file_backup.read_text(encoding="utf-8"))
    rec = raw.get("Sea Dart SAM")
    assert isinstance(rec, dict)
    assert rec.get("armed") is True
    assert rec.get("arming_until") == 0.0

    with webdash.app.app_context():
        from projects.falklandV2.webdash import load_arming
        state = load_arming().get("Sea Dart SAM")
    assert state == "Armed"


@pytest.mark.parametrize("weapon", WEAPON_NAMES)
def test_weapons_support_test_fire(runtime: GameRuntime, weapon: str) -> None:
    _configure_armed(runtime)

    before = runtime.load_ammo()[weapon]
    result = runtime.fire_weapon(weapon, mode="test")
    assert result["ok"] is True
    assert result["result"] == "TEST"

    after = runtime.load_ammo()[weapon]
    expected_delta = 50 if weapon in ("20mm Oerlikon", "20mm GAM-BO1 (twin)") else 1
    assert before - after == expected_delta


REAL_FIRE_SCENARIOS: Tuple[Tuple[str, str, str, float], ...] = (
    ("MM38 Exocet", "ARA General Belgrano", "Ship", 12.0),
    ("Sea Dart SAM", "Mirage III", "Aircraft", 5.0),
    ("4.5 inch Mk.8 gun", "ARA Santísima Trinidad (Type 42 Destroyer)", "Ship", 5.0),
    ("20mm Oerlikon", "Pucara", "Aircraft", 0.4),
    ("20mm GAM-BO1 (twin)", "Super Étendard", "Aircraft", 1.0),
    ("Corvus chaff", "Mirage III", "Aircraft", 0.6),
)


@pytest.mark.parametrize("weapon,target_name,target_class,distance_nm", REAL_FIRE_SCENARIOS)
def test_weapons_support_real_fire(
    runtime: GameRuntime,
    weapon: str,
    target_name: str,
    target_class: str,
    distance_nm: float,
) -> None:
    _configure_armed(runtime)
    _clear_contacts(runtime)

    contact = _add_hostile_contact(
        runtime,
        name=target_name,
        klass=target_class,
        distance_nm=distance_nm,
    )

    before = runtime.load_ammo()[weapon]
    result = runtime.fire_weapon(weapon, mode="real")
    assert result["ok"] is True
    assert result["result"] == "FIRE"
    after = runtime.load_ammo()[weapon]

    expected_delta = 50 if weapon in ("20mm Oerlikon", "20mm GAM-BO1 (twin)") else 1
    assert before - after == expected_delta

    # Cooldown guard: immediate subsequent fire should fail with COOLDOWN
    cooldown_attempt = runtime.fire_weapon(weapon, mode="real")
    assert cooldown_attempt["ok"] is False
    assert cooldown_attempt["error"] == "COOLDOWN"

    _clear_contacts(runtime)


def test_weapon_fire_uses_cached_primary_when_lock_cleared(runtime: GameRuntime) -> None:
    _configure_armed(runtime)
    _clear_contacts(runtime)
    contact = _add_hostile_contact(
        runtime,
        name="ARA Hércules (Type 42 Destroyer)",
        klass="Ship",
        distance_nm=12.0,
    )
    runtime.radar.priority_id = contact.id
    runtime._sync_engine_contacts()
    # Build snapshot to cache the primary contact
    runtime.build_ui_snapshot()
    # Clear the priority to mimic lock loss
    runtime.radar.priority_id = None
    runtime._sync_engine_contacts()
    result = runtime.fire_weapon("MM38 Exocet", mode="real")
    assert result["ok"], result
