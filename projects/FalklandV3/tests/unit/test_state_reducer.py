from falklandv3.core.audio import AudioEvent
from falklandv3.core.events import ShipMoved
from falklandv3.core.engine import BOARD_MIN_X, BOARD_MIN_Y
from falklandv3.core.radar import RadarContactView
from falklandv3.core.state import StateReducer
from falklandv3.core.weather import WeatherState
from falklandv3.core.weapons import WeaponSlot
from falklandv3.utils.grid import world_to_label


def test_initial_snapshot_contains_ship_view():
    reducer = StateReducer()
    snap = reducer.snapshot_dict()
    assert "ship" in snap
    ship = snap["ship"]
    assert ship["cell"] == world_to_label(BOARD_MIN_X + 10.0, BOARD_MIN_Y + 12.0)
    assert ship["hud"].startswith("Ship")
    assert "radar" in snap
    assert snap["radar"]["contacts"] == []
    assert snap["radar"]["locked_contact_id"] is None
    assert "mission" in snap
    assert "weapons" in snap
    assert snap["weapons"]["slots"] == []
    assert "audio" in snap
    assert snap["audio"]["events"] == []
    assert snap["audio"]["shots_in_flight"] == []
    assert "weather" in snap
    assert "radio" in snap
    assert "cap_history" in snap


def test_ship_moved_event_updates_snapshot():
    reducer = StateReducer()
    reducer.handle_ship_moved(ShipMoved(heading_deg=90.0, speed_kts=20.0, x_nm=20.0, y_nm=15.0))
    ship = reducer.snapshot_dict()["ship"]
    assert ship["heading_deg"] == 90.0
    assert ship["speed_kts"] == 20.0
    assert ship["x_nm"] == 20.0
    assert "hdg 90°" in ship["hud"]


def test_update_radar_replaces_contacts():
    reducer = StateReducer()
    contact = RadarContactView(
        id=1,
        label="Bogey",
        allegiance="Hostile",
        range_nm=12.0,
        bearing_deg=45.0,
        heading_deg=270.0,
        speed_kts=350.0,
        category="Aircraft",
        primary_weapon=None,
        cell="AA00",
    )
    reducer.update_radar([contact])
    radar = reducer.snapshot_dict()["radar"]
    assert len(radar["contacts"]) == 1
    assert radar["contacts"][0]["label"] == "Bogey"
    assert radar["locked_contact_id"] is None


def test_set_radar_lock_tracks_contact():
    reducer = StateReducer()
    contact = RadarContactView(
        id=7,
        label="Raid",
        allegiance="Hostile",
        range_nm=8.0,
        bearing_deg=120.0,
        heading_deg=300.0,
        speed_kts=420.0,
        category="Aircraft",
        primary_weapon="Exocet",
        cell="AB05",
    )
    reducer.update_radar([contact])
    reducer.set_radar_lock(7)
    radar = reducer.snapshot_dict()["radar"]
    assert radar["locked_contact_id"] == 7
    reducer.set_radar_lock(99)
    radar = reducer.snapshot_dict()["radar"]
    assert radar["locked_contact_id"] is None


def test_world_to_label_extremes():
    assert world_to_label(0.0, 0.0) == "AA00"
    assert world_to_label(40.0, 40.0) == "BN39"


def test_update_mission_adjusts_snapshot():
    reducer = StateReducer()
    reducer.update_mission({
        "id": "patrol",
        "label": "Sector Patrol",
        "description": "Maintain presence",
        "status": "in_progress",
        "elapsed_s": 120.0,
        "time_left_s": 480.0,
    })
    mission = reducer.snapshot_dict()["mission"]
    assert mission["id"] == "patrol"
    assert mission["elapsed_s"] == 120.0


def test_update_weapons_sets_slots():
    reducer = StateReducer()
    reducer.update_weapons(
        {
            "Sea Dart Fwd": WeaponSlot(
                name="Sea Dart Fwd",
                state="Armed",
                ammo=12,
                max_ammo=13,
                min_range_nm=2.0,
                max_range_nm=35.0,
                supports=("Aircraft",),
                ammo_per_shot=1,
                category="SAM",
            ),
            "Goalkeeper": WeaponSlot(
                name="Goalkeeper",
                state="Safe",
                ammo=400,
                max_ammo=500,
                min_range_nm=0.3,
                max_range_nm=2.5,
                supports=("Aircraft",),
                ammo_per_shot=50,
                category="CIWS",
            ),
        },
        {"Sea Dart Fwd": 5.0},
    )
    weapons = reducer.snapshot_dict()["weapons"]["slots"]
    names = {slot["name"] for slot in weapons}
    assert {"Sea Dart Fwd", "Goalkeeper"} <= names
    armed_state = next(slot for slot in weapons if slot["name"] == "Sea Dart Fwd")
    assert armed_state["state"] == "Armed"
    assert armed_state["cooldown_remaining_s"] == 5.0


def test_update_audio_records_events():
    reducer = StateReducer()
    reducer.update_audio(tuple(), tuple())
    events = (AudioEvent(kind="radio", message="Test", ts=1.0),)
    shots = ({
        "weapon": "Sea Dart",
        "target": "Bogey",
        "cell": "AA00",
        "range_nm": 12.0,
        "pk_pct": 80,
        "eta_s": 15.0,
        "result": None,
        "mode": "real",
    },)
    reducer.update_audio(events, shots)
    audio_payload = reducer.snapshot_dict()["audio"]
    assert len(audio_payload["events"]) == 1
    assert audio_payload["events"][0]["message"] == "Test"
    assert len(audio_payload["shots_in_flight"]) == 1
    assert audio_payload["shots_in_flight"][0]["weapon"] == "Sea Dart"


def test_update_weather_sets_snapshot():
    reducer = StateReducer()
    reducer.update_weather(WeatherState(wind_dir_deg=90.0, wind_speed_kts=12.0, sea_state=3.5))
    weather = reducer.snapshot_dict()["weather"]
    assert weather["wind_dir_deg"] == 90.0
    assert weather["wind_speed_kts"] == 12.0


def test_update_radio_sets_messages():
    from falklandv3.core.radio import RadioMessage

    reducer = StateReducer()
    reducer.update_radio((RadioMessage(id=1, text="Hello", category="info", ts=1.0),))
    radio = reducer.snapshot_dict()["radio"]["messages"]
    assert len(radio) == 1
    assert radio[0]["text"] == "Hello"


def test_update_nav_history_sets_entries():
    reducer = StateReducer()
    reducer.update_nav_history(({"id": 1, "ts": 1.0, "action": "course", "value": 90.0},))
    history = reducer.snapshot_dict()["nav_history"]["entries"]
    assert history[0]["action"] == "course"


def test_update_cap_history_sets_entries():
    reducer = StateReducer()
    reducer.update_cap_history(({
        "id": 1,
        "ts": 1.0,
        "action": "launch",
        "sorties": 1,
        "mission_status": "in_progress",
    },))
    cap_history = reducer.snapshot_dict()["cap_history"]["entries"]
    assert cap_history[0]["action"] == "launch"
