from pathlib import Path

from falklandv3.config import Settings
from falklandv3.core.mission import MissionLoader, MissionManager
from falklandv3.services.persistence import PersistenceConfig, StateRepository
from falklandv3.services.runtime import GameRuntime


def test_set_course_updates_snapshot_immediately(tmp_path):
    tmp_repo = StateRepository(PersistenceConfig(tmp_path / "repo"))
    runtime = GameRuntime(repository=tmp_repo)

    runtime.set_course(180.0)
    snap = runtime.snapshot()

    assert snap["ship"]["heading_deg"] == 180.0
    assert "hdg 180°" in snap["ship"]["hud"]
    assert "radar" in snap
    assert isinstance(snap["radar"]["contacts"], list)
    assert "mission" in snap
    assert snap["mission"]["status"] in {"in_progress", "success", "inactive"}
    assert "cap" in snap
    baseline_sorties = snap["cap"]["sorties"]
    assert "weapons" in snap
    assert isinstance(snap["weapons"]["slots"], list)
    first_slot = snap["weapons"]["slots"][0]
    assert "ammo" in first_slot and "max_ammo" in first_slot
    assert "weather" in snap
    assert "radio" in snap

    runtime.arm_weapon(first_slot["name"])
    snap_armed = runtime.snapshot()
    armed_slot = next(slot for slot in snap_armed["weapons"]["slots"] if slot["name"] == first_slot["name"])
    assert armed_slot["state"] == "Armed"
    assert snap_armed["audio"]["events"], "arming should emit audio event"

    runtime.safe_weapon(first_slot["name"])
    snap_safe = runtime.snapshot()
    safe_slot = next(slot for slot in snap_safe["weapons"]["slots"] if slot["name"] == first_slot["name"])
    assert safe_slot["state"] == "Safe"
    assert snap_safe["audio"]["events"], "safing should maintain audio history"

    runtime.launch_cap()
    snap_after = runtime.snapshot()
    assert snap_after["cap"]["status"] == "launched"
    assert snap_after["cap"]["sorties"] == baseline_sorties + 1


def test_runtime_persists_snapshots(tmp_path: Path):
    repo = StateRepository(PersistenceConfig(tmp_path))
    runtime = GameRuntime(repository=repo)

    runtime.radio.push("Test transmission")
    runtime.set_course(90.0)
    runtime.launch_cap()
    runtime.tick(0.0)

    snapshots_file = tmp_path / "snapshots.jsonl"
    assert snapshots_file.exists()
    lines = snapshots_file.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "expected at least one snapshot record"

    weather_file = tmp_path / "weather.jsonl"
    assert weather_file.exists()
    assert weather_file.read_text(encoding="utf-8").strip()

    radio_file = tmp_path / "radio.jsonl"
    assert radio_file.exists()
    radio_lines = radio_file.read_text(encoding="utf-8").strip().splitlines()
    assert radio_lines, "expected radio log entry"

    nav_file = tmp_path / "nav_history.jsonl"
    assert nav_file.exists()
    assert nav_file.read_text(encoding="utf-8").strip()

    cap_file = tmp_path / "cap_history.jsonl"
    assert cap_file.exists()
    assert cap_file.read_text(encoding="utf-8").strip()


def test_runtime_loads_attack_wave_schedule(tmp_path: Path):
    repo = StateRepository(PersistenceConfig(tmp_path))
    runtime = GameRuntime(repository=repo)

    summary = runtime.radar.wave_summary()
    assert summary is not None, "expected radar to expose wave summary"
    assert summary["label"] == "Calm Seas"


def test_runtime_radar_is_deterministic_with_seed(tmp_path: Path):
    repo_a = StateRepository(PersistenceConfig(tmp_path / "a"))
    repo_b = StateRepository(PersistenceConfig(tmp_path / "b"))
    settings_a = Settings(rng_seed=123)
    settings_b = Settings(rng_seed=123)

    runtime_a = GameRuntime(repository=repo_a, settings=settings_a)
    runtime_b = GameRuntime(repository=repo_b, settings=settings_b)

    runtime_a.tick(30.0)
    runtime_b.tick(30.0)

    snap_a = runtime_a.snapshot()
    snap_b = runtime_b.snapshot()

    assert snap_a["radar"]["contacts"] == snap_b["radar"]["contacts"]
    assert snap_a["wave"] == snap_b["wave"]
    assert snap_a["weather"] == snap_b["weather"]


def test_cap_launch_intercepts_hostile(tmp_path: Path):
    settings = Settings(rng_seed=123)
    runtime = GameRuntime(repository=StateRepository(PersistenceConfig(tmp_path)), settings=settings)
    hostile = runtime.radar.force_spawn(
        name="Bogey",
        allegiance="Hostile",
        x_nm=runtime.engine.ship.x_nm + 5.0,
        y_nm=runtime.engine.ship.y_nm + 5.0,
        heading_deg=90.0,
        speed_kts=250.0,
    )
    initial_contacts = len(runtime.radar.contacts)

    runtime.launch_cap()

    runtime.tick(95.0)

    assert all(contact.id != hostile.id for contact in runtime.radar.contacts)

    radio_messages = runtime.snapshot()["radio"]["messages"]
    assert any("CAP reports hostile" in msg["text"] for msg in radio_messages)
    assert any("Mission accomplished" in msg["text"] for msg in radio_messages)

    audio_events = runtime.snapshot()["audio"]["events"]
    assert any(event["kind"] == "cap" for event in audio_events)
    assert any(event["kind"] == "mission" for event in audio_events)

    mission_status = runtime.snapshot()["mission"]["status"]
    assert mission_status == "success"


def test_cap_intercepts_exocet_announces_specific_callout(tmp_path: Path):
    settings = Settings(rng_seed=42)
    runtime = GameRuntime(repository=StateRepository(PersistenceConfig(tmp_path)), settings=settings)
    runtime.radar.force_spawn(
        name="Super Étendard",
        allegiance="Hostile",
        x_nm=runtime.engine.ship.x_nm + 15.0,
        y_nm=runtime.engine.ship.y_nm,
        heading_deg=180.0,
        speed_kts=450.0,
        primary_weapon="Exocet AM39",
        category="Aircraft",
    )

    runtime.launch_cap()
    runtime.tick(95.0)

    radio_messages = runtime.snapshot()["radio"]["messages"]
    assert any("Exocet threat neutralised" in event["message"] for event in runtime.snapshot()["audio"]["events"])
    assert any("Exocet" in msg["text"] for msg in radio_messages)


def test_fire_weapon_consumes_ammo_and_sets_cooldown(tmp_path: Path):
    settings = Settings(rng_seed=7)
    runtime = GameRuntime(repository=StateRepository(PersistenceConfig(tmp_path)), settings=settings)
    runtime.arm_weapon("Sea Dart Fwd")
    runtime.radar.force_spawn(
        name="Mirage III",
        allegiance="Hostile",
        x_nm=runtime.engine.ship.x_nm + 6.0,
        y_nm=runtime.engine.ship.y_nm,
        heading_deg=90.0,
        speed_kts=400.0,
        category="Aircraft",
    )

    before = runtime.weapons.ammo("Sea Dart Fwd")
    result = runtime.fire_weapon("Sea Dart Fwd")
    assert result["ok"]
    after = runtime.weapons.ammo("Sea Dart Fwd")
    assert after == before - 1
    snapshot = runtime.snapshot()
    slot_snapshot = next(slot for slot in snapshot["weapons"]["slots"] if slot["name"] == "Sea Dart Fwd")
    assert slot_snapshot["cooldown_remaining_s"] > 0

    cooldown = runtime.fire_weapon("Sea Dart Fwd")
    assert cooldown["ok"] is False
    assert cooldown["error"] == "COOLDOWN"
    runtime.tick(7.0)
    slot_after = next(slot for slot in runtime.snapshot()["weapons"]["slots"] if slot["name"] == "Sea Dart Fwd")
    assert slot_after["cooldown_remaining_s"] <= 0.1


def test_damage_asset_triggers_mission_failure(tmp_path: Path):
    settings = Settings(rng_seed=123)
    runtime = GameRuntime(repository=StateRepository(PersistenceConfig(tmp_path)), settings=settings)

    missions_dir = Path(__file__).resolve().parents[2] / "falklandv3" / "data" / "missions"
    loader = MissionLoader(missions_dir)
    runtime.mission = MissionManager(loader, active_id="protect_hermes", health_provider=runtime.health.lives)
    runtime.state.update_mission(runtime.mission.snapshot())
    runtime.mission.consume_announce()

    runtime.damage_asset("hermes", 5)

    snap = runtime.snapshot()
    assert snap["mission"]["status"] == "failure"
    assert any("Hermes" in msg["text"] for msg in snap["radio"]["messages"])


def test_hostile_strike_damages_asset(tmp_path: Path):
    settings = Settings(rng_seed=123)
    runtime = GameRuntime(repository=StateRepository(PersistenceConfig(tmp_path)), settings=settings)

    missions_dir = Path(__file__).resolve().parents[2] / "falklandv3" / "data" / "missions"
    runtime.mission = MissionManager(MissionLoader(missions_dir), active_id="protect_hermes", health_provider=runtime.health.lives)
    runtime.state.update_mission(runtime.mission.snapshot())
    runtime.mission.consume_announce()

    runtime.radar.force_spawn(
        name="Missile",
        allegiance="Hostile",
        x_nm=runtime.engine.ship.x_nm + 1.0,
        y_nm=runtime.engine.ship.y_nm + 1.0,
        heading_deg=90.0,
        speed_kts=500.0,
    )

    lives_before = runtime.health.lives("hermes")
    runtime.tick(1.0)
    lives_after = runtime.health.lives("hermes")

    assert lives_after == max(0, (lives_before or 0) - 1)
    snap = runtime.snapshot()
    decision = snap["mission"].get("decision")
    assert decision is None or decision.get("status") in {"pending", "resolved"}
    assert any("hit" in msg["text"].lower() for msg in snap["radio"]["messages"])
