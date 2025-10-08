from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@dataclass
class _Target:
    id: int
    x: float
    y: float
    speed_kts: float
    course_deg: float


@dataclass
class _StubMission:
    mission_id: int
    target_cell: str
    status: str
    base_now: float
    kind: str = "intercept"
    loadout: str = "aim9"
    missiles_left: int = 4
    outbound_s: float = 60.0
    inbound_s: float = 60.0
    deck_cycle_s: float = 0.0
    intercept_speed_kts: float = 600.0
    cruise_speed_kts: float = 420.0
    origin_xy: Tuple[float, float] = (0.0, 0.0)
    permission_authorized: bool = False
    permission_required: bool = True
    ts: Dict[str, float] = field(init=False)

    def __post_init__(self) -> None:
        self.id = self.mission_id
        # Mirror HermesCAP fields expected by route logic
        self.ts = {
            "launch": self.base_now - 180.0,
            "eta_onstation": self.base_now - 30.0,
            "onstation": self.base_now - 30.0,
            "etd_rtb": self.base_now + 120.0,
        }


class _StubCAP:
    def __init__(self, mission: _StubMission, *, launch_response: Dict[str, Any] | None = None) -> None:
        self.missions: List[_StubMission] = [mission]
        self._mission = mission
        self.cfg = {
            "deck_cycle_per_pair_s": 180,
            "cruise_speed_kts": mission.cruise_speed_kts,
            "intercept_speed_kts": mission.intercept_speed_kts,
        }
        self._request_calls: List[Dict[str, Any]] = []
        self.last_permission: Tuple[int, bool] | None = None
        self._launch_response = launch_response or {"ok": False, "message": "should_not_be_called", "mission": None}

    def snapshot(self) -> Dict[str, Any]:
        m = self._mission
        return {
            "missions": [
                {
                    "id": m.id,
                    "status": m.status,
                    "loadout": m.loadout,
                    "kind": m.kind,
                    "target_cell": m.target_cell,
                    "deck_cycle_s": m.deck_cycle_s,
                    "outbound_s": m.outbound_s,
                    "cruise_speed_kts": m.cruise_speed_kts,
                    "intercept_speed_kts": m.intercept_speed_kts,
                    "timestamps": dict(m.ts),
                    "missiles_left": m.missiles_left,
                    "origin_xy": list(m.origin_xy),
                }
            ]
        }

    def request_cap_to_cell(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        self._request_calls.append({"args": args, "kwargs": kwargs})
        return dict(self._launch_response)

    def set_permission(self, mission_id: int, authorized: bool, now: float | None = None) -> None:
        if mission_id == self._mission.id:
            self._mission.permission_authorized = authorized
        self.last_permission = (mission_id, authorized)

    def mission_status(self) -> Dict[str, Any]:
        return {
            "missions": [
                {
                    "id": m.id,
                    "status": m.status,
                    "loadout": m.loadout,
                    "kind": m.kind,
                    "target_cell": m.target_cell,
                    "timestamps": dict(m.ts),
                }
                for m in self.missions
            ]
        }


def _world_to_cell_mapping(x: float, y: float) -> str:
    if (x, y) == (10.0, 0.0):
        return "Z4"
    return "H26"


def _cell_to_world_mapping(cell: str) -> Tuple[float, float]:
    mapping = {
        "H26": (5.0, 0.0),
        "Z4": (10.0, 0.0),
    }
    return mapping.get(cell, (0.0, 0.0))


def _make_lazy_payload(
    cap_stub: _StubCAP,
    mission: _StubMission,
    target: _Target,
    *,
    voice_emit=lambda *a, **k: None,
    record_event=lambda *a, **k: None,
    stamp_cap_launch=lambda *a, **k: None,
):
    meta = {mission.id: {"origin_xy": mission.origin_xy}}

    class _Radar:
        priority_id = target.id
        contacts = [target]

    class _Eng:
        @staticmethod
        def public_state() -> Dict[str, Any]:
            return {"ship": {"heading": 0.0, "speed": 0.0}}

    return {
        "CAP": cap_stub,
        "CAP_META": meta,
        "RADAR": _Radar,
        "ENG": _Eng,
        "CONVOY": None,
        "voice_emit": voice_emit,
        "officer_say": lambda *a, **k: None,
        "record_event": record_event,
        "record_flight": lambda *a, **k: None,
        "stamp_cap_launch": stamp_cap_launch,
        "radar_xy_from_state": lambda _st: (0.0, 0.0),
        "world_to_cell": _world_to_cell_mapping,
        "cell_to_world": _cell_to_world_mapping,
        "ship_cell_from_state": lambda _st: "N18",
    }


def test_cap_request_vectors_onstation_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    from projects.falklandV2.routes import cap

    app = Flask(__name__)
    app.config["TESTING"] = True

    base_now = time.time()
    mission = _StubMission(mission_id=1, target_cell="H26", status="onstation", base_now=base_now)
    cap_stub = _StubCAP(mission)
    target = _Target(id=4, x=10.0, y=0.0, speed_kts=250.0, course_deg=270.0)

    lazy_payload = _make_lazy_payload(cap_stub, mission, target)

    monkeypatch.setattr(cap, "_lazy", lambda: lazy_payload)

    with app.test_request_context("/cap/request", method="POST", json={"id": target.id}):
        resp = cap.cap_request()

    resp_obj, status = _unwrap_response(resp)
    assert status == 200
    data = resp_obj.get_json()
    assert data["ok"] is True
    assert data["message"] == "Vectoring airborne pair to Z4"
    assert data["mission"] == {"id": mission.id, "target_cell": "Z4"}
    assert data["loadout"] == "aim9"

    # Ensure the existing mission was retasked instead of a fresh launch
    assert cap_stub._request_calls == []
    assert mission.target_cell == "Z4"
    assert mission.status == "airborne"
    assert mission.ts.get("vector") is True
    assert "onstation" not in mission.ts
    assert mission.ts.get("etd_rtb") is None
    assert mission.ts.get("eta_onstation", 0) > base_now
    assert lazy_payload["CAP_META"][mission.id]["target_cell"] == "Z4"
    assert lazy_payload["CAP_META"][mission.id]["target_id"] == target.id
    assert cap_stub.last_permission == (mission.id, False)


class _StubCAPLaunch(_StubCAP):
    def __init__(self, mission_id: int = 2):
        self.launch_calls: List[Dict[str, Any]] = []
        self._mission_id = mission_id
        self.missions: List[Any] = []
        self.cfg = {"deck_cycle_per_pair_s": 180}
        self.last_permission: Tuple[int, bool] | None = None

    def snapshot(self) -> Dict[str, Any]:
        return {"missions": []}

    def request_cap_to_cell(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        self.launch_calls.append({"args": args, "kwargs": kwargs})
        return {"ok": True, "message": "Hermes: CAP pair launching", "mission": {"id": self._mission_id, "kind": "intercept"}}

    def set_permission(self, mission_id: int, authorized: bool, now: float | None = None) -> None:
        self.last_permission = (mission_id, authorized)


class _StubCAPLaunchWithBombs(_StubCAPLaunch):
    def __init__(self, *, mission_id: int = 4, base_now: float | None = None) -> None:
        super().__init__(mission_id=mission_id)
        base = base_now if base_now is not None else time.time()
        self.bombs_mission = _StubMission(
            mission_id=99,
            target_cell="H26",
            status="airborne",
            base_now=base,
            kind="cap",
            loadout="bombs",
        )
        self.bombs_mission.loadout = "bombs"
        self.missions = [self.bombs_mission]

    def snapshot(self) -> Dict[str, Any]:
        m = self.bombs_mission
        return {
            "missions": [
                {
                    "id": m.id,
                    "status": m.status,
                    "loadout": m.loadout,
                    "kind": m.kind,
                    "missiles_left": m.missiles_left,
                    "target_cell": m.target_cell,
                    "timestamps": dict(m.ts),
                    "outbound_s": m.outbound_s,
                    "deck_cycle_s": m.deck_cycle_s,
                    "intercept_speed_kts": m.intercept_speed_kts,
                    "cruise_speed_kts": m.cruise_speed_kts,
                }
            ]
        }


def test_cap_request_launches_new_pair_with_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    from projects.falklandV2.routes import cap

    app = Flask(__name__)
    app.config["TESTING"] = True

    base_now = time.time()
    # Mission placeholder so helper can create CAP_META entry
    mission = _StubMission(mission_id=1, target_cell="H26", status="onstation", base_now=base_now)
    cap_launch_stub = _StubCAPLaunch(mission_id=3)
    target = _Target(id=6, x=10.0, y=0.0, speed_kts=250.0, course_deg=270.0)

    voice_calls: List[Tuple[Any, ...]] = []

    def _voice_emit(event_id: str, payload: Dict[str, Any], **kwargs: Any) -> None:
        voice_calls.append((event_id, payload, kwargs))

    lazy_payload = _make_lazy_payload(
        cap_launch_stub,
        mission,
        target,
        voice_emit=_voice_emit,
        record_event=lambda *a, **k: None,
        stamp_cap_launch=lambda *a, **k: None,
    )
    # Replace CAP_META with empty so launch branch populates fresh meta under new mission id
    lazy_payload["CAP_META"] = {}

    monkeypatch.setattr(cap, "_lazy", lambda: lazy_payload)

    with app.test_request_context("/cap/request", method="POST", json={"id": target.id}):
        resp = cap.cap_request()

    resp_obj, status = _unwrap_response(resp)
    assert status == 200
    data = resp_obj.get_json()
    assert data["ok"] is True
    assert data["mission"]["id"] == 3

    # Should have triggered a new launch, not vectoring existing pair
    assert len(cap_launch_stub.launch_calls) == 1
    assert cap_launch_stub.launch_calls[0]["kwargs"]["loadout"] == "aim9"

    # Voice line for intercept launch should have been emitted
    events = [evt for (evt, _payload, _kw) in voice_calls]
    assert 'pilot.intercept.launch' in events

    # Mission status should reflect new CAP_META entry
    mission_status = cap_launch_stub.mission_status()
    assert len(mission_status["missions"]) == 0  # snapshot returns empty for launch stub


def test_cap_request_launches_surface_target_with_bombs(monkeypatch: pytest.MonkeyPatch) -> None:
    from projects.falklandV2.routes import cap

    app = Flask(__name__)
    app.config["TESTING"] = True

    base_now = time.time()
    mission = _StubMission(mission_id=5, target_cell="H26", status="ready", base_now=base_now, kind="cap")
    surface_stub = _StubCAPLaunch(mission_id=6)
    target = _Target(id=9, x=14.0, y=2.0, speed_kts=120.0, course_deg=90.0)
    target.name = "Enemy Corvette"
    target.meta = {"class": "Ship"}

    captured_events: List[Tuple[str, Dict[str, Any]]] = []

    lazy_payload = _make_lazy_payload(
        surface_stub,
        mission,
        target,
        record_event=lambda event, data: captured_events.append((event, dict(data))),
        stamp_cap_launch=lambda *a, **k: None,
    )
    lazy_payload["CAP_META"] = {}

    monkeypatch.setattr(cap, "_lazy", lambda: lazy_payload)

    with app.test_request_context("/cap/request", method="POST", json={"id": target.id}):
        resp = cap.cap_request()

    resp_obj, status = _unwrap_response(resp)
    assert status == 200
    data = resp_obj.get_json()
    assert data["ok"] is True
    assert data["loadout"] == "bombs"

    assert len(surface_stub.launch_calls) == 1
    assert surface_stub.launch_calls[0]["kwargs"]["loadout"] == "bombs"

    surface_meta = lazy_payload["CAP_META"].get(surface_stub._mission_id)
    assert surface_meta is not None
    assert "target_cell" in surface_meta


def test_cap_request_avoids_vectoring_bomb_loadout(monkeypatch: pytest.MonkeyPatch) -> None:
    from projects.falklandV2.routes import cap

    app = Flask(__name__)
    app.config["TESTING"] = True

    base_now = time.time()
    bombs_stub = _StubCAPLaunchWithBombs(base_now=base_now)
    target = _Target(id=7, x=12.0, y=0.0, speed_kts=250.0, course_deg=270.0)

    launch_events: List[Dict[str, Any]] = []

    def _record_event(*args: Any, **kwargs: Any) -> None:
        launch_events.append({"args": args, "kwargs": kwargs})

    lazy_payload = _make_lazy_payload(
        bombs_stub,
        bombs_stub.bombs_mission,
        target,
        record_event=_record_event,
        stamp_cap_launch=lambda *a, **k: None,
    )

    monkeypatch.setattr(cap, "_lazy", lambda: lazy_payload)

    with app.test_request_context("/cap/request", method="POST", json={"id": target.id}):
        resp = cap.cap_request()

    resp_obj, status = _unwrap_response(resp)
    assert status == 200
    data = resp_obj.get_json()
    assert data["ok"] is True
    assert data["loadout"] == "aim9"

    assert len(bombs_stub.launch_calls) == 1
    assert bombs_stub.launch_calls[0]["kwargs"]["loadout"] == "aim9"

    # Ensure no vector call altered the existing bombs mission
    assert bombs_stub.bombs_mission.target_cell == "H26"
def _unwrap_response(resp: Any):
    if isinstance(resp, tuple):
        response_obj = resp[0]
        status = resp[1] if len(resp) > 1 else getattr(response_obj, "status_code", None)
    else:
        response_obj = resp
        status = getattr(resp, "status_code", None)
    return response_obj, status
