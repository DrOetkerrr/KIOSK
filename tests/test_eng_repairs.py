import math

from projects.falklandV2.subsystems import webcore as core


def test_repair_timer_counts_down_with_subsecond_tick():
    now = 1_000.0
    eng = {
        'teams_total': 1,
        'teams_free': 0,
        'systems': [
            {
                'id': 'COMMS',
                'name': 'COMMS station',
                'status': 'Damaged',
                'team_assigned': True,
                'timer_s': 2.0,
                'last_damaged_ts': now - 30.0,
                'response_deadline_ts': 0.0,
            }
        ],
    }

    # Four 0.25s ticks should reduce the timer by 1 second overall.
    for _ in range(4):
        assert core._advance_eng_repairs(eng, 0.25, now) is True
        now += 0.25

    sys = eng['systems'][0]
    assert math.isclose(sys['timer_s'], 1.0, rel_tol=1e-6, abs_tol=1e-6)
    assert sys['status'] == 'Damaged'
    assert sys['team_assigned'] is True
    assert eng['teams_free'] == 0

    # Another four ticks should finish the repair and free the team.
    for _ in range(4):
        assert core._advance_eng_repairs(eng, 0.25, now) is True
        now += 0.25

    sys = eng['systems'][0]
    assert sys['status'] == 'OK'
    assert sys['team_assigned'] is False
    assert sys['timer_s'] == 0.0
    assert eng['teams_free'] == 1
