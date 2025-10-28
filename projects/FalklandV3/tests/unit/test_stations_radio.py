from falklandv3.stations.radio import build_radio_station_view


def test_radio_station_view_limits_and_summarises_messages():
    snapshot = {
        "radio": {
            "messages": [
                {"id": 1, "text": "Message A", "category": "cap", "ts": 10.0},
                {"id": 2, "text": "Message B", "category": "mission", "ts": 12.0},
                {"id": 3, "text": "Message C", "category": "cap", "ts": 11.0},
            ]
        }
    }

    view = build_radio_station_view(snapshot, limit=2)
    assert view.total_messages == 3
    # Should return the two newest messages (id 2 then 3).
    assert [msg.id for msg in view.messages] == [2, 3]
    # Summaries reflect limited set.
    summary = {item.category: item.count for item in view.summaries}
    assert summary == {"cap": 1, "mission": 1}


def test_radio_station_view_handles_missing_payload():
    view = build_radio_station_view({}, limit=5)
    assert view.total_messages == 0
    assert view.messages == ()
