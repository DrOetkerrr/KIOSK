from __future__ import annotations

import json
from pathlib import Path

import pytest

from projects.falklandV2.subsystems import ui_snapshot as snap


@pytest.fixture()
def ship_config(tmp_path: Path) -> Path:
    ship = {
        "name": "HMS Sheffield",
        "class": "DD",
        "weapons": {
            "seacat": {"rounds": 26, "range_nm": [2, 35]},
            "exocet_mm38": {"rounds": 4, "range_nm": [3, 22]},
            "gun_4_5in": {"ammo_he": 550, "range_nm": [0, 8]},
            "oerlikon_20mm": {"rounds": 5000, "range_nm": [0.3, 0.5]},
            "gam_bo1_20mm": {"rounds": 1850, "range_nm": [0.3, 2.5]},
            "corvus_chaff": {"salvoes": 15},
        },
    }
    ship_path = tmp_path / "ship.json"
    ship_path.write_text(json.dumps(ship), encoding="utf-8")
    return tmp_path


def test_weapons_snapshot_uses_dynamic_ammo(monkeypatch: pytest.MonkeyPatch, ship_config: Path) -> None:
    ammo_state = {
        "Sea Dart SAM": 12,
        "MM38 Exocet": 2,
        "4.5 inch Mk.8 gun": 320,
        "20mm Oerlikon": 1200,
        "20mm GAM-BO1 (twin)": 900,
        "Corvus chaff": 11,
    }

    monkeypatch.setattr(snap.core, "load_ammo", lambda: ammo_state.copy())
    monkeypatch.setattr(snap.weap, "weapons_status", lambda ship: "STATUS")

    result = snap.weapons_snapshot(ship_config, locked_range_nm=None)
    table = {entry["name"]: entry for entry in result["table"]}

    assert table["Sea Dart"]["ammo"] == 12
    assert table["Exocet MM38"]["ammo"] == 2
    assert table["4.5in Mk.8"]["ammo"] == 320
    assert table["20mm Oerlikon"]["ammo"] == 1200
    assert table["GAM-BO1 20mm"]["ammo"] == 900
    assert table["Corvus chaff"]["ammo"] == 11
