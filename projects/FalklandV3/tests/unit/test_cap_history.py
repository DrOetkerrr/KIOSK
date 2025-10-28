from falklandv3.core.cap_history import CapLog


def test_cap_log_records_entries():
    log = CapLog(max_entries=2)
    entry1 = log.record("launch", sorties=1, mission_status="in_progress")
    entry2 = log.record("reset", sorties=0, mission_status="standby")
    entry3 = log.record("launch", sorties=1, mission_status="resume")

    entries = log.entries()
    assert entries[0].action == entry2.action
    assert entries[1].action == entry3.action
