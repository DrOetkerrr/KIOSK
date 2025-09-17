from __future__ import annotations

"""
Status builder extracted from webdash /api/status to keep webdash slim.
This module reads live state from the webdash module (single instance).

Usage from webdash:
    from projects.falklandV2.subsystems.status import build as build_status
    payload = build_status()
"""

from typing import Any, Dict


def build() -> Dict[str, Any]:
    # Import lazily to avoid circular import issues during app bootstrap.
    from .. import webdash as wd  # type: ignore
    import time

    # Capability helpers
    def _engine_kind() -> str:
        try:
            mod = getattr(wd.ENG, '__module__', '')
            return 'falklands' if 'falklands' in (str(mod) or '') else 'v2'
        except Exception:
            return 'unknown'

    def _capabilities() -> list[str]:
        caps: list[str] = []
        try:
            caps.append('nav:set')
            caps.append('weapons:cooldown')
            if wd.CAP is not None:
                caps.append('cap:ready')
            caps.append('radar:force_spawn')
        except Exception:
            pass
        return caps

    payload: Dict[str, Any] = {
        "ok": True,
        "api_version": 1,
        "server_version": wd.APP_VERSION,
        "engine_kind": _engine_kind(),
        "capabilities": _capabilities(),
    }

    # State snapshot (robust against minimal engines)
    if hasattr(wd.ENG, "public_state"):
        try:
            payload["state"] = wd.ENG.public_state()  # type: ignore
        except Exception:
            payload["hud"] = wd.ENG.hud_line() if hasattr(wd.ENG, "hud_line") else "OK"
    else:
        try:
            if hasattr(wd.ENG, "_ship_xy") and hasattr(wd.ENG, "_ship_course_speed"):
                x, y = wd.ENG._ship_xy()  # type: ignore[attr-defined]
                hdg, spd = wd.ENG._ship_course_speed()  # type: ignore[attr-defined]
                payload["state"] = {"ship": {"col": float(x), "row": float(y), "heading": float(hdg), "speed": float(spd)}, "CELL_NM": 1.0}
            else:
                payload["hud"] = wd.ENG.hud_line() if hasattr(wd.ENG, "hud_line") else "OK"
        except Exception:
            payload["hud"] = wd.ENG.hud_line() if hasattr(wd.ENG, "hud_line") else "OK"

    # Own fleet snapshot (leader + escorts with formation and delay)
    try:
        st = payload.get('state') if isinstance(payload.get('state'), dict) else {}
        ship = (st or {}).get('ship', {}) if isinstance(st, dict) else {}
        # Leader cell via adapter
        try:
            own_cell = wd.ship_cell_from_state(st)
        except Exception:
            own_cell = 'K13'
        # Build own row
        own_row = {
            'id': 'own',
            'name': wd._load_json(wd.DATA_DIR / 'ship.json', {}).get('name', 'HMS Sheffield'),
            'class': wd._load_json(wd.DATA_DIR / 'ship.json', {}).get('class', 'DD'),
            'cell': own_cell,
            'speed': ship.get('speed'),
            'heading': ship.get('heading'),
            'status': {}
        }
        # Escorts via Convoy.update (rotated offsets + lagged course/speed)
        try:
            try:
                from projects.falklandV2.subsystems.convoy import Convoy  # type: ignore
            except Exception:
                Convoy = None  # type: ignore
            convoy = getattr(wd, 'CONVOY', None)
            if convoy is None and Convoy is not None:
                convoy = Convoy.load(wd.DATA_DIR)  # type: ignore
            sx, sy = wd.radar_xy_from_state(st)
            crs = float(ship.get('heading', 0.0) or 0.0)
            spd = float(ship.get('speed', 0.0) or 0.0)
            escorts = []
            if convoy is not None:
                try:
                    snaps = convoy.update(float(sx), float(sy), crs, spd, None)  # type: ignore
                    for s in snaps:
                        escorts.append({
                            'id': s.id,
                            'name': s.name,
                            'class': s.klass,
                            'cell': s.cell,
                            'speed': s.speed_kts,
                            'heading': s.course_deg,
                            'status': {}
                        })
                except Exception:
                    escorts = []
            payload['ownfleet'] = [own_row] + escorts
        except Exception:
            payload['ownfleet'] = [own_row]
    except Exception:
        payload['ownfleet'] = []

    # Contacts (from RADAR)
    try:
        st = payload.get("state") or (wd.ENG.public_state() if hasattr(wd.ENG, "public_state") else {})
        own_xy = wd.radar_xy_from_state(st)
        try:
            global _RADAR_BOOTSTRAPPED  # type: ignore
        except Exception:
            _RADAR_BOOTSTRAPPED = False  # type: ignore
        if (not getattr(wd.RADAR, 'contacts', None)) and (not _RADAR_BOOTSTRAPPED):  # type: ignore
            try:
                wd._spawn_initial_friendlies()
                _RADAR_BOOTSTRAPPED = True  # type: ignore
            except Exception:
                pass
        radar_list = [wd.contact_to_ui(c, own_xy) for c in wd.RADAR.contacts]
        # normalize cells, classes, and caps
        for d, c in zip(radar_list, wd.RADAR.contacts):
            try:
                d['cell'] = wd.world_to_cell(c.x, c.y)
            except Exception:
                pass
            try:
                nm = str(d.get('name',''))
                cls = wd.TARGET_CLASS_BY_NAME.get(nm)
                if cls:
                    d['class'] = cls
            except Exception:
                pass
            try:
                if str(getattr(c, 'meta', {}).get('kind','')) == 'missile':
                    d['class'] = 'Missile'
            except Exception:
                pass
            try:
                cap = getattr(c, 'meta', {}).get('cap', {})
                pw = cap.get('primary_weapon')
                rmin = cap.get('min_range_nm')
                rmax = cap.get('max_range_nm')
                if pw: d['primary_weapon'] = pw
                if rmin is not None: d['min_nm'] = rmin
                if rmax is not None: d['max_nm'] = rmax
            except Exception:
                pass
    except Exception:
        radar_list = []
    radar_list.sort(key=lambda d: float(d.get('range_nm', 1e9)))
    threats = [d for d in radar_list if str(d.get('type','')).lower() == 'hostile']
    payload["contacts"] = radar_list
    payload["threats"] = threats
    payload["top_threat_id"] = (threats[0]["id"] if threats else None)

    # Radar scan timing (for UI countdown) and lock info
    try:
        interval = int(getattr(wd.RADAR, 'cfg', {}).get('scan_interval_s', 180))
    except Exception:
        interval = 180
    try:
        accum = float(getattr(wd.RADAR, '_accum', 0.0))
    except Exception:
        accum = 0.0
    left = max(0, int(round(interval - accum)))
    try:
        locked_id = int(getattr(wd.RADAR, 'priority_id', None)) if getattr(wd.RADAR, 'priority_id', None) is not None else None
    except Exception:
        locked_id = None
    try:
        # extend the existing radar dict if present
        rdict = payload.get('radar') if isinstance(payload.get('radar'), dict) else {}
        if not isinstance(rdict, dict):
            rdict = {}
        rdict['scan_interval_s'] = interval
        rdict['scan_left_s'] = left
        rdict['locked_id'] = locked_id
        payload['radar'] = rdict
    except Exception:
        payload['radar_scan'] = {'interval_s': interval, 'left_s': left, 'locked_id': locked_id}

    # Weapons
    try:
        ammo = wd.load_ammo(); arming = wd.load_arming()
        primary_ui = payload.get('primary') if isinstance(payload.get('primary'), dict) else None
        def _order_key(rec: Dict[str,Any]):
            nm = rec.get('name',''); cls = rec.get('class','Other')
            if nm == 'MM38 Exocet': return (0, nm)
            cls_rank = {'Missile':1, 'SAM':2, 'Gun':3, 'Decoy':4}.get(cls, 5)
            return (cls_rank, nm)
        def _cooldown_left_s(nm: str) -> int:
            try:
                raw = wd._load_json(wd.ARMING_PATH, {})
                rec = (raw or {}).get(nm) if isinstance(raw, dict) else None
                if isinstance(rec, dict):
                    cu = float(rec.get('cooldown_until', 0.0) or 0.0)
                    left = int(max(0.0, cu - time.time()))
                    return left
            except Exception:
                pass
            return 0
        weaps = []
        for w in wd.WEAP_CATALOG:
            nm = w.get('name'); cls = w.get('class')
            rec = {
                'name': nm,
                'class': cls,
                'min_nm': w.get('min_nm'),
                'max_nm': w.get('max_nm'),
                'armed': arming.get(nm, 'Safe'),
                'ammo': ammo.get(nm, 0),
                'in_range': wd.compute_in_range(nm, primary_ui),
                'cooldown_s': _cooldown_left_s(nm),
            }
            weaps.append(rec)
        weaps.sort(key=_order_key)
        payload['weapons'] = weaps
    except Exception:
        pass

    # Primary from module-level PRIMARY_ID (best-effort)
    try:
        if 'PRIMARY_ID' in wd.globals() and wd.PRIMARY_ID is not None:  # type: ignore
            cid = int(wd.PRIMARY_ID)  # type: ignore
            for d in payload.get("contacts", []):
                try:
                    if int(d.get("id", -1)) == cid:
                        payload["primary"] = d
                        break
                except Exception:
                    continue
    except Exception:
        pass

    # Audio snapshot
    try:
        with wd.STATE_LOCK:
            payload['audio'] = dict(wd.AUDIO_STATE)
    except Exception:
        payload['audio'] = {'last_launch': None, 'last_result': None, 'radio': None}

    # Grid and nav hints for desktop UI
    try:
        payload['grid'] = {'world_n': wd.WORLD_N, 'board_n': wd.BOARD_N}
    except Exception:
        pass
    try:
        payload['nav'] = {'turn_target': float(wd.NAV_STATE.get('turn_target')) if wd.NAV_STATE.get('turn_target') is not None else None}
    except Exception:
        pass

    return payload
