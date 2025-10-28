from falklandv3.stations.weapons import build_weapons_station_view


def test_weapons_station_view_counts_states():
    snapshot = {
        "weapons": {
            "slots": [
                {"name": "Sea Dart Fwd", "state": "Safe", "ammo": 13, "max_ammo": 13, "supports": ["Aircraft"], "ammo_per_shot": 1},
                {"name": "Sea Dart Aft", "state": "Armed", "ammo": 12, "max_ammo": 13, "supports": ["Aircraft"], "ammo_per_shot": 1},
                {"name": "MM38 Exocet", "state": "Armed", "ammo": 1, "max_ammo": 4, "supports": ["Ship"], "ammo_per_shot": 1},
            ]
        }
    }

    view = build_weapons_station_view(snapshot)
    assert view.total_slots == 3
    assert view.armed_count == 2
    assert view.safe_count == 1
    assert view.low_ammo_count == 1
    slots_by_name = {slot.name: slot for slot in view.slots}
    assert slots_by_name["MM38 Exocet"].ammo == 1
    assert slots_by_name["Sea Dart Aft"].ammo_pct == round(12 / 13 * 100, 1)
    assert slots_by_name["Sea Dart Fwd"].cooldown_remaining_s == 0.0


def test_weapons_station_view_handles_missing_payload():
    view = build_weapons_station_view({})
    assert view.total_slots == 0
    assert view.slots == ()
    assert view.low_ammo_count == 0
