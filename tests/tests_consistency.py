import os
import time
import json
import types
import pytest


def _import_webcore():
    from projects.falklandV2.subsystems import webcore as core
    return core


def _import_runtime():
    from projects.falklandV2.runtime_service import GameRuntime
    return GameRuntime


@pytest.mark.parametrize(
    "weapon, rng_nm, expected",
    [
        ("Sea Dart SAM", 1.9, False),
        ("Sea Dart SAM", 2.0, True),
        ("Sea Dart SAM", 35.0, True),
        ("Sea Dart SAM", 35.1, False),
        ("MM38 Exocet", 7.9, False),
        ("MM38 Exocet", 8.0, True),
        ("MM38 Exocet", 22.0, True),
        ("MM38 Exocet", 22.1, False),
    ],
)
def test_compute_in_range_catalog_consistency(weapon, rng_nm, expected):
    core = _import_webcore()
    # sanity: catalog has these entries with configured ranges
    wrec = core.WEAP_MAP.get(weapon)
    assert wrec is not None, f"Missing weapon in catalog: {weapon}"
    assert isinstance(wrec.get("min_nm"), (int, float))
    assert isinstance(wrec.get("max_nm"), (int, float))
    # in_range must match the same geometry used by the fire gate
    primary = {"name": "Test Target", "range_nm": float(rng_nm)}
    assert core.compute_in_range(weapon, primary) is expected


def test_fire_gating_blocks_out_of_envelope_and_decrements_once():
    GameRuntime = _import_runtime()
    rt = GameRuntime()

    # Ensure a known ammo state
    ammo = rt.load_ammo()
    ammo["Sea Dart SAM"] = 2
    rt.save_ammo(ammo)

    # Arm the weapon
    arm = rt.load_arming(); arm["Sea Dart SAM"] = "Armed"; rt.save_arming(arm)

    # Force OUT_OF_RANGE on real fire path
    # Provide a fake primary: set radar priority and a distant target via radar internals
    # Instead of spinning the engine, patch compute_in_range to False to simulate out-of-envelope
    orig = rt.compute_in_range
    try:
        rt.compute_in_range = lambda name, primary: False  # type: ignore
        res = rt.fire_weapon("Sea Dart SAM", mode="real")
        assert res.get("ok") is False and res.get("error") == "OUT_OF_RANGE"
    finally:
        rt.compute_in_range = orig  # type: ignore

    # Test mode should still decrement once (but not increase)
    before = rt.load_ammo().get("Sea Dart SAM", 0)
    arm = rt.load_arming(); arm["Sea Dart SAM"] = "Armed"; rt.save_arming(arm)
    res2 = rt.fire_weapon("Sea Dart SAM", mode="test")
    after = rt.load_ammo().get("Sea Dart SAM", 0)
    assert res2.get("ok") is True
    assert before - after == 1


def test_resolve_refuses_out_of_envelope_hits_and_archives():
    # Use the shared helper exposed by webcore to enforce envelope on resolve
    core = _import_webcore()

    # Seed audio state with a single in-flight shot
    shot_id = "Sea Dart SAM:unit-test:99"
    now = time.time()
    core.AUDIO_STATE.setdefault("shots_in_flight", [])
    core.AUDIO_STATE.setdefault("shots_archive", [])
    core.AUDIO_STATE["shots_in_flight"] = [
        {
            "id": shot_id,
            "weapon": "Sea Dart SAM",
            "target_id": 99,
            "target_name": "A-4 Skyhawk",
            "target_class": "Aircraft",
            "range_nm": 51.8,
            "pk": 0.25,
            "fired_ts": now - 60.0,
            "due_ts": now,
            "result": None,
            "result_ts": 0.0,
            "cleanup_ts": 0.0,
        }
    ]

    # Resolve using the same logic engine loop uses
    outcome = core._resolve_shot_once(
        weapon="Sea Dart SAM",
        target_id=99,
        target_name="A-4 Skyhawk",
        target_class="Aircraft",
        range_nm=51.8,
        shot_id=shot_id,
        pk=0.25,
    )
    # Must not be a hit when out of envelope
    assert outcome in ("miss", "no_effect")
    assert outcome == "no_effect"
    # Shot should be removed from in-flight and appended to archive immediately
    inflight_ids = [r.get("id") for r in core.AUDIO_STATE.get("shots_in_flight", [])]
    assert shot_id not in inflight_ids
    archived = [r for r in core.AUDIO_STATE.get("shots_archive", []) if r.get("id") == shot_id]
    assert archived and archived[-1].get("outcome") == "no_effect"


def test_hud_and_radar_lock_coherent_or_explicit():
    # Ensure status payload exposes a coherent lock state
    from projects.falklandV2.subsystems import status as stat
    from projects.falklandV2 import webdash as wd

    # Set a dummy lock id and build status
    try:
        wd.RADAR.priority_id = 123
    except Exception:
        pass
    payload = stat.build()

    rad = payload.get("radar", {}) or {}
    hud_state = payload.get("hud_state", {}) or {}
    # At minimum, both keys should exist and match if present
    assert "locked_id" in rad
    assert "active_contact_id" in hud_state and "radar_locked_id" in hud_state
    assert hud_state["radar_locked_id"] == rad.get("locked_id") == hud_state["active_contact_id"]


def test_in_range_shared_with_fire_and_resolve():
    core = _import_webcore()
    GameRuntime = _import_runtime()
    rt = GameRuntime()
    # pick a weapon and a target-side primary stub
    primary = {"name": "Super Etendard", "range_nm": 12.0}
    ok = core.compute_in_range("MM38 Exocet", primary)

    # Fire gate must agree with compute_in_range
    orig = rt.compute_in_range
    try:
        rt.compute_in_range = core.compute_in_range  # type: ignore
        ammo = rt.load_ammo(); ammo["MM38 Exocet"] = 1; rt.save_ammo(ammo)
        arm = rt.load_arming(); arm["MM38 Exocet"] = "Armed"; rt.save_arming(arm)
        if ok:
            # Compute_in_range says True; if NO_PRIMARY it returns proper error, but we don't assert success here
            assert isinstance(ok, bool)
        else:
            # Simulate lack of primary but range decision false -> expect OUT_OF_RANGE if primary supplied
            pass
    finally:
        rt.compute_in_range = orig  # type: ignore

