from falklandv3.stations.engineering import build_engineering_station_view


def test_engineering_station_view_orders_assets_and_flags_status():
    snapshot = {
        "health": {
            "assets": [
                {"name": "Hermes", "max_lives": 8, "lives": 2},
                {"name": "Sea Dart", "max_lives": 5, "lives": 2},
            ]
        },
        "weather": {"wind_dir_deg": 90, "wind_speed_kts": 18, "sea_state": 3.5},
    }

    view = build_engineering_station_view(snapshot)
    asset_lookup = {asset.name: asset for asset in view.assets}
    assert asset_lookup["Hermes"].status == "critical"
    assert asset_lookup["Sea Dart"].status == "warning"
    assert view.weather is not None
    assert view.damage_alert is True


def test_engineering_station_view_handles_missing_payload():
    view = build_engineering_station_view({})
    assert view.assets == ()
    assert view.damage_alert is False
