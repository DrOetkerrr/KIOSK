from falklandv3.stations.radar import build_radar_station_view


def test_radar_station_view_sorts_and_flags_contacts():
    snapshot = {
        "radar": {
            "contacts": [
                {"id": 1, "label": "Bogey A", "allegiance": "Hostile", "range_nm": 4.5, "bearing_deg": 90, "heading_deg": 270, "speed_kts": 300},
                {"id": 2, "label": "Patrol", "allegiance": "Friendly", "range_nm": 6.0, "bearing_deg": 120, "heading_deg": 180, "speed_kts": 220},
                {"id": 3, "label": "Bogey B", "allegiance": "Hostile", "range_nm": 18.0, "bearing_deg": 45, "heading_deg": 200, "speed_kts": 280},
            ]
        },
        "wave": {
            "label": "Alpha",
            "elapsed_s": 30,
            "duration_s": 120,
            "remaining_s": 90,
            "spawn_rate_per_min": 3,
            "friendly_prob": 0.2,
            "direction_bearing": 45,
        },
    }

    view = build_radar_station_view(snapshot, max_contacts=12)
    # Hostile contacts should be sorted by priority (range) first.
    assert [entry.id for entry in view.contacts] == [1, 3, 2]
    assert view.hostile_count == 2
    assert view.friendly_count == 1
    assert view.max_contacts == 12
    assert view.wave is not None
    assert view.wave.label == "Alpha"


def test_radar_station_view_handles_missing_data():
    view = build_radar_station_view({})
    assert view.contacts == ()
    assert view.hostile_count == 0
    assert view.max_contacts == 0
    assert view.wave is None
