from falklandv3.core.cap import CAPManager, CAPStatus
from falklandv3.core.shar import SeaHarrierStatus


def test_cap_launch_and_cycle():
    cap = CAPManager(launch_duration_s=10.0, cycle_duration_s=40.0)

    snap = cap.snapshot()
    assert snap.status == CAPStatus.READY
    assert len(snap.harriers) == 2
    assert all(h.status == SeaHarrierStatus.ON_DECK for h in snap.harriers)

    cap.launch()
    snap = cap.snapshot()
    assert snap.status == CAPStatus.LAUNCHED
    assert snap.sorties == 1
    assert any(h.status == SeaHarrierStatus.LAUNCHED for h in snap.harriers)

    cap.tick(10.0)
    snap = cap.snapshot()
    assert snap.status == CAPStatus.RETURNING
    assert any(h.status == SeaHarrierStatus.RETURNING for h in snap.harriers)

    cap.tick(90.0)
    cap.tick(120.0)
    snap = cap.snapshot()
    assert snap.status in {CAPStatus.READY, CAPStatus.LAUNCHED}
    assert any(h.status == SeaHarrierStatus.ON_DECK for h in snap.harriers)
