from falklandv3.stations.nav import build_nav_station_view


def test_nav_station_view_limits_and_sorts_history():
    snapshot = {
        "ship": {
            "hud": "Ship B12 | hdg 180° spd 12 kn",
            "heading_deg": 180.0,
            "speed_kts": 12.0,
            "cell": "B12",
            "x_nm": 11.0,
            "y_nm": 10.0,
        },
        "nav_history": {
            "entries": [
                {"id": 1, "ts": 10.0, "action": "course", "value": 90},
                {"id": 2, "ts": 20.0, "action": "speed", "value": 18},
                {"id": 3, "ts": 15.0, "action": "course", "value": 135},
            ]
        },
    }

    view = build_nav_station_view(snapshot, history_limit=2, tick_dt=0.5)
    assert view.tick_dt == 0.5
    assert view.history_total == 3
    # Ensure entries sorted by timestamp descending and limited to two latest.
    assert [entry.id for entry in view.history] == [2, 3]
    assert view.heading_deg == 180.0
    assert view.cell == "B12"


def test_nav_station_view_handles_missing_values():
    view = build_nav_station_view({}, history_limit=5, tick_dt=None)
    assert view.hud == ""
    assert view.heading_deg == 0.0
    assert view.history_total == 0
    assert view.history == ()
