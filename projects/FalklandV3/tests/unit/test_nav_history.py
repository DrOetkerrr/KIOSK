from falklandv3.core.nav_history import NavHistory


def test_nav_history_records_course_and_speed():
    history = NavHistory(max_entries=3)
    history.record_course(90)
    history.record_speed(12)
    history.record_course(180)
    history.record_speed(20)

    entries = history.entries()
    assert len(entries) == 3  # max size enforced
    assert entries[-1].action == "speed"
    assert entries[-1].value == 20
