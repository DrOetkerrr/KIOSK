from __future__ import annotations

"""
Status builder extracted from webdash /api/status to keep webdash slim.
This module reads live state from the webdash module (single instance).

Usage from webdash:
    from projects.falklandV2.subsystems.status import build as build_status
    payload = build_status()
"""

from typing import Any, Dict
from datetime import datetime, timezone

from . import webcore as core


def build() -> Dict[str, Any]:
    # Import lazily to avoid circular import issues during app bootstrap.
    from .. import webdash as wd  # type: ignore
    import time

    now = time.time()

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
        if primary_ui is None:
            try:
                if locked_id is not None:
                    primary_ui = next((d for d in radar_list if int(d.get('id', -1)) == int(locked_id)), None)
            except Exception:
                primary_ui = None
        def _order_key(rec: Dict[str,Any]):
            nm = rec.get('name',''); cls = rec.get('class','Other')
            if nm == 'MM38 Exocet': return (0, nm)
            cls_rank = {'Missile':1, 'SAM':2, 'Gun':3, 'Decoy':4}.get(cls, 5)
            return (cls_rank, nm)
        arming_raw = wd._load_json(wd.ARMING_PATH, {})

        def _arming_record(nm: str) -> Dict[str, Any]:
            if isinstance(arming_raw, dict):
                weapons_section = arming_raw.get('weapons')
                if isinstance(weapons_section, dict):
                    rec = weapons_section.get(nm)
                    if isinstance(rec, dict):
                        return rec
                rec = arming_raw.get(nm)
                if isinstance(rec, dict):
                    return rec
            return {}

        def _cooldown_left_s(nm: str) -> int:
            try:
                rec = _arming_record(nm)
                if rec:
                    cu = float(rec.get('cooldown_until', 0.0) or 0.0)
                    left = int(max(0.0, cu - time.time()))
                    return left
            except Exception:
                pass
            return 0

        def _arming_left_s(nm: str) -> int:
            try:
                rec = _arming_record(nm)
                if rec and not bool(rec.get('armed')):
                    au = float(rec.get('arming_until', 0.0) or 0.0)
                    left = int(max(0.0, au - time.time()))
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
                'arming_s': _arming_left_s(nm),
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
            audio_state = dict(wd.AUDIO_STATE)
        shots_raw = audio_state.get('shots_in_flight')
        shots_out = []
        seen_ids: set[str] = set()
        RESULT_TTL = 15.0
        if isinstance(shots_raw, list):
            for item in shots_raw:
                try:
                    shot_id = str(item.get('id') or '')
                    weapon = str(item.get('weapon') or '')
                    target_label = str(item.get('target_name') or item.get('target') or '')
                    target_cell = str(item.get('target_cell') or item.get('cell') or '')
                    target_id = item.get('target_id')
                    target_class = str(item.get('target_class') or '')
                    try:
                        cleanup_ts = float(item.get('cleanup_ts', 0.0) or 0.0)
                    except Exception:
                        cleanup_ts = 0.0
                    if cleanup_ts and cleanup_ts <= now:
                        continue
                    result_raw = str(item.get('result') or '').strip().lower()
                    try:
                        range_nm = float(item.get('range_nm', 0.0) or 0.0)
                    except Exception:
                        range_nm = 0.0
                    try:
                        pk = float(item.get('pk', 0.0) or 0.0)
                    except Exception:
                        pk = 0.0
                    try:
                        due_ts = float(item.get('due_ts', 0.0) or 0.0)
                    except Exception:
                        due_ts = 0.0
                    try:
                        fired_ts = float(item.get('fired_ts', 0.0) or 0.0)
                    except Exception:
                        fired_ts = 0.0
                    try:
                        result_ts = float(item.get('result_ts', 0.0) or 0.0)
                    except Exception:
                        result_ts = 0.0
                    if result_raw and not cleanup_ts:
                        cleanup_ts = result_ts + RESULT_TTL if result_ts > 0 else 0.0
                        if cleanup_ts and cleanup_ts <= now:
                            continue
                    eta = 0 if result_raw else (max(0, int(round(due_ts - now))) if due_ts > 0 else 0)
                    elapsed = max(0, int(round(now - fired_ts))) if fired_ts > 0 else 0
                    if result_raw in ('hit', 'miss'):
                        result_label = 'HIT' if result_raw == 'hit' else 'MISS'
                    else:
                        result_label = ''
                    shots_out.append({
                        'id': shot_id,
                        'weapon': weapon,
                        'target': target_label,
                        'target_id': target_id,
                        'target_class': target_class,
                        'cell': target_cell,
                        'range_nm': round(range_nm, 1),
                        'eta_s': eta,
                        'elapsed_s': elapsed,
                        'pk_pct': max(0, min(100, int(round(pk * 100)))),
                        'result': result_label,
                        'result_ts': result_ts if result_ts > 0 else None
                    })
                    if shot_id:
                        seen_ids.add(shot_id)
                except Exception:
                    continue
        # Fallback: derive from pending resolve_fire events if audio snapshots haven't been stamped yet
        try:
            pending = getattr(wd, 'PENDING_EVENTS', [])
        except Exception:
            pending = []
        if isinstance(pending, list):
            for ev in pending:
                try:
                    if str(ev.get('kind') or '') != 'resolve_fire':
                        continue
                    shot_id = str(ev.get('shot_id') or '')
                    if shot_id and shot_id in seen_ids:
                        continue
                    weapon = str(ev.get('weapon') or '')
                    target_label = str(ev.get('target_name') or ev.get('target') or '')
                    target_cell = str(ev.get('target_cell') or ev.get('cell') or '')
                    target_id = ev.get('target_id')
                    target_class = str(ev.get('target_class') or '')
                    try:
                        range_nm = float(ev.get('range_nm', 0.0) or 0.0)
                    except Exception:
                        range_nm = 0.0
                    try:
                        pk = float(ev.get('pk', 0.0) or 0.0)
                    except Exception:
                        pk = 0.0
                    try:
                        due_ts = float(ev.get('due', 0.0) or 0.0)
                    except Exception:
                        due_ts = 0.0
                    try:
                        fired_ts = float(ev.get('fired_ts', 0.0) or 0.0)
                    except Exception:
                        fired_ts = 0.0
                    eta = max(0, int(round(due_ts - now))) if due_ts > 0 else 0
                    elapsed = max(0, int(round(now - fired_ts))) if fired_ts > 0 else 0
                    entry = {
                        'id': shot_id,
                        'weapon': weapon,
                        'target': target_label,
                        'target_id': target_id,
                        'target_class': target_class,
                        'cell': target_cell,
                        'range_nm': round(range_nm, 1),
                        'eta_s': eta,
                        'elapsed_s': elapsed,
                        'pk_pct': max(0, min(100, int(round(pk * 100)))),
                        'result': '',
                        'result_ts': None
                    }
                    shots_out.append(entry)
                    if shot_id:
                        seen_ids.add(shot_id)
                except Exception:
                    continue
        def _shot_sort_key(entry: Dict[str, Any]) -> tuple[Any, Any, Any]:
            has_result = 1 if entry.get('result') else 0
            eta = entry.get('eta_s', 0)
            result_ts = entry.get('result_ts') or 0
            return (has_result, eta, -float(result_ts) if result_ts else 0, str(entry.get('weapon', '')))

        shots_out.sort(key=_shot_sort_key)
        # Trim internal timestamp helpers before sending to client
        for entry in shots_out:
            entry.pop('result_ts', None)
        audio_state['shots_in_flight'] = shots_out
        payload['audio'] = audio_state
    except Exception:
        payload['audio'] = {
            'last_launch': None,
            'last_result': None,
            'radio': None,
            'shots_in_flight': []
        }

    # Grid and nav hints for desktop UI
    try:
        payload['grid'] = {'world_n': wd.WORLD_N, 'board_n': wd.BOARD_N}
    except Exception:
        pass
    try:
        payload['nav'] = {'turn_target': float(wd.NAV_STATE.get('turn_target')) if wd.NAV_STATE.get('turn_target') is not None else None}
    except Exception:
        pass

    try:
        cap_obj = getattr(wd, 'CAP', None)
        if cap_obj is not None:
            snap = cap_obj.snapshot()
            if isinstance(snap, dict):
                if 'tasks' not in snap and 'missions' in snap:
                    snap['tasks'] = list(snap.get('missions') or [])
            payload['cap'] = snap
        else:
            payload['cap'] = {'readiness': {}, 'missions': [], 'tasks': []}
    except Exception:
        payload['cap'] = {'readiness': {}, 'missions': [], 'tasks': []}

    try:
        rules_doc = core._load_json(core.BASE_DIR / 'templates' / 'Validation.json', {})
        loss_after_s = int((rules_doc.get('repair_rules') or {}).get('permanent_loss_if_unrepaired_after_s', 120))
    except Exception:
        loss_after_s = 120

    try:
        eng_raw = core.load_eng_sys()
    except Exception:
        eng_raw = {'teams_total': 4, 'teams_free': 4, 'systems': []}

    systems_defs = [
        ('Navigation', 'NAV station'),
        ('Radar', 'RDR station'),
        ('FireControl_Weapons', 'FCR / Weapons'),
        ('COMMS', 'COMMS station'),
        ('Engine_Propulsion', 'Engine / Propulsion'),
        ('Rudder_Steering', 'Rudder / Steering'),
        ('Hull', 'Hull')
    ]

    teams_total = int(eng_raw.get('teams_total', 0) or 0)
    teams_free = int(eng_raw.get('teams_free', teams_total))
    systems_map: Dict[str, Dict[str, Any]] = {}
    raw_list = eng_raw.get('systems') if isinstance(eng_raw.get('systems'), list) else []
    for item in raw_list:
        try:
            systems_map[str(item.get('id') or '')] = item
        except Exception:
            continue

    systems_snapshot = []
    for idx, (sys_id, label) in enumerate(systems_defs, start=1):
        raw = systems_map.get(sys_id, {})
        status = str(raw.get('status', 'OK'))
        timer_s = int(float(raw.get('timer_s', 0) or 0))
        team_assigned = bool(raw.get('team_assigned'))
        last_damaged_ts = float(raw.get('last_damaged_ts', 0.0) or 0.0)
        since_damage = None
        to_loss = None
        if last_damaged_ts > 0:
            since_damage = max(0.0, now - last_damaged_ts)
            to_loss = max(0.0, (last_damaged_ts + loss_after_s) - now)
        warn_half = bool(not team_assigned and since_damage is not None and since_damage >= (loss_after_s / 2))
        warn_critical = False
        if team_assigned:
            warn_critical = timer_s > 0 and timer_s <= 30
        else:
            warn_critical = to_loss is not None and to_loss <= 30
        systems_snapshot.append({
            'index': idx,
            'id': sys_id,
            'label': label,
            'status': status,
            'timer_s': timer_s,
            'team_assigned': team_assigned,
            'last_damaged_ts': last_damaged_ts,
            'time_since_damage_s': since_damage,
            'time_to_loss_s': to_loss if not team_assigned else None,
            'warn_halfway': warn_half,
            'warn_critical': warn_critical
        })

    try:
        health = core._load_health()
    except Exception:
        health = {}
    max_lives = int(health.get('max_lives', 0) or 0)
    lives = int(health.get('lives', max_lives) or 0)
    ship_pct = 100 if max_lives <= 0 else max(0, min(100, int(round(100 * lives / max_lives))))

    payload['eng'] = {
        'timestamp': now,
        'teams_total': teams_total,
        'teams_free': teams_free,
        'teams_used': max(0, teams_total - teams_free),
        'loss_threshold_s': loss_after_s,
        'ship_lives': lives,
        'ship_max_lives': max_lives,
        'ship_pct': ship_pct,
        'systems': systems_snapshot,
    }

    try:
        events_raw = []
        with wd.STATE_LOCK:
            events_raw = list(getattr(wd, 'EVENT_QUEUE', [])[-10:])
        formatted = []
        for ev in events_raw:
            if not isinstance(ev, dict):
                continue
            ts = ev.get('ts')
            try:
                iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            except Exception:
                iso = None
            formatted.append({
                'id': ev.get('id'),
                'text': ev.get('text'),
                'ts': ts,
                'iso': iso,
                'data': ev.get('data') or {}
            })
        payload['events'] = formatted
    except Exception:
        payload['events'] = []

    return payload
