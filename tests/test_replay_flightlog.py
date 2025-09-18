import json
import os
import pytest


def _find_log_path():
    # Prefer mounted path, fallback to local repo path
    p1 = "/mnt/data/flight.jsonl"
    p2 = "./flight.jsonl"
    return p1 if os.path.exists(p1) else (p2 if os.path.exists(p2) else None)


@pytest.mark.skipif(not _find_log_path(), reason="No flight.jsonl available for replay")
def test_replay_invariants_from_flightlog():
    from projects.falklandV2.subsystems import webcore as core
    path = _find_log_path()
    assert path is not None

    wmap = core.WEAP_MAP
    # Counters
    out_of_envelope_hits = 0
    tti_bad = 0
    linger_shots = 0
    ammo_regress = 0
    first = {"out_of_envelope_hits": None, "tti_bad": None, "linger_shots": None, "ammo_regress": None}
    seen_ammo = {}

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            ts = obj.get("ts") or obj.get("time")
            resp = obj.get("response") if isinstance(obj.get("response"), dict) else {}
            req = obj.get("request") if isinstance(obj.get("request"), dict) else {}
            text = resp.get("text") or ""

            # TTI should not appear as 'TTI None' strings
            if isinstance(text, str) and "TTI None" in text:
                tti_bad += 1
                if first["tti_bad"] is None:
                    first["tti_bad"] = ts

            # Out-of-envelope hits (based on recorded weapon.result.hit events if present)
            if resp.get("event") in ("weapon.result.hit", "hit"):
                name = (req.get("weapon") or resp.get("weapon") or "")
                rng = req.get("rng") if isinstance(req.get("rng"), (int, float)) else resp.get("range_nm")
                if name in wmap and isinstance(rng, (int, float)):
                    mn = float(wmap[name].get("min_nm", 0.0) or 0.0)
                    mx = float(wmap[name].get("max_nm", 0.0) or 0.0)
                    if rng < mn or rng > mx:
                        out_of_envelope_hits += 1
                        if first["out_of_envelope_hits"] is None:
                            first["out_of_envelope_hits"] = ts

            # Shots lingering after resolution: status payload includes shots_in_flight with a result label
            if isinstance(resp.get("audio"), dict):
                shots = resp["audio"].get("shots_in_flight")
                if isinstance(shots, list):
                    for s in shots:
                        if str(s.get("result") or "").upper() in ("HIT", "MISS"):
                            linger_shots += 1
                            if first["linger_shots"] is None:
                                first["linger_shots"] = ts

            # Ammo never increases without a reload; detect monotonic decreases between polls
            if isinstance(resp.get("weapons"), list):
                for w in resp["weapons"]:
                    nm = w.get("name")
                    if not nm:
                        continue
                    cur = int(w.get("ammo") or 0)
                    if nm in seen_ammo and cur > seen_ammo[nm]:
                        ammo_regress += 1
                        if first["ammo_regress"] is None:
                            first["ammo_regress"] = ts
                    seen_ammo[nm] = cur

    # Assert all invariants hold for the playback
    assert out_of_envelope_hits == 0, f"out_of_envelope_hits={out_of_envelope_hits} first_ts={first['out_of_envelope_hits']}"
    assert tti_bad == 0, f"tti_bad={tti_bad} first_ts={first['tti_bad']}"
    assert linger_shots == 0, f"linger_shots={linger_shots} first_ts={first['linger_shots']}"
    assert ammo_regress == 0, f"ammo_regress={ammo_regress} first_ts={first['ammo_regress']}"

