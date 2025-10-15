from __future__ import annotations

from typing import Any, Callable

from flask import Flask  # type: ignore

from ..routes.cap import _resolve_hermes_origin

def _safe_call(fn: Callable[[dict], None] | None, payload: dict) -> None:
    if callable(fn):
        try:
            fn(payload)
        except Exception:
            pass


def ensure_cap_fallbacks(app: Flask, state: Any) -> None:
    """Install CAP fallbacks when corresponding blueprints are unavailable."""
    try:
        from flask import jsonify, request  # type: ignore
    except Exception:
        return

    try:
        existing = {rule.rule for rule in app.url_map.iter_rules()}
    except Exception:
        existing = set()

    record_flight = getattr(state, "record_flight", None)

    def _attr(obj: Any, *names: str) -> Any:
        for name in names:
            try:
                value = getattr(obj, name)
            except Exception:
                continue
            if value is not None:
                return value
        return None

    def _runtime_refs():
        return {
            "cap": _attr(state, "CAP", "cap"),
            "radar": _attr(state, "RADAR", "radar"),
            "convoy": _attr(state, "CONVOY", "convoy"),
            "eng": _attr(state, "ENG", "engine", "eng"),
            "runtime": _attr(state, "RUNTIME", "runtime"),
            "primary_id": getattr(state, "PRIMARY_ID", None),
            "target_class_map": getattr(state, "TARGET_CLASS_BY_NAME", {}) or {},
            "world_to_cell": getattr(state, "world_to_cell", lambda *args, **kwargs: "K13"),
            "cell_to_world": getattr(state, "cell_to_world", lambda *_args, **_kw: (20.0, 20.0)),
            "ship_cell_from_state": getattr(state, "ship_cell_from_state", lambda *_: "K13"),
            "radar_xy_from_state": getattr(state, "radar_xy_from_state", lambda *_: (20.0, 20.0)),
        }

    if "/cap/request" not in existing:

        @app.post("/cap/request")
        def _cap_request_fallback():
            refs = _runtime_refs()
            cap = refs["cap"]
            if cap is None:
                return jsonify({"ok": False, "error": "CAP unavailable"}), 503

            data = request.get_json(silent=True) or {}
            tid = data.get("id")
            try:
                tid = int(tid) if tid is not None else None
            except Exception:
                tid = None

            if tid is None:
                pid = refs["primary_id"]
                try:
                    tid = int(pid) if pid is not None else None
                except Exception:
                    tid = None

            if tid is None:
                radar = refs["radar"]
                try:
                    tid = getattr(radar, "priority_id", None)
                except Exception:
                    tid = None

            radar = refs["radar"]
            contacts = getattr(radar, "contacts", []) if radar is not None else []
            tgt = next((c for c in contacts if int(getattr(c, "id", -1)) == int(tid)), None) if tid is not None else None

            cs = data.get("cell")
            fallback_cell = str(cs).strip().upper() if cs else None
            if tgt is None and not fallback_cell:
                return jsonify({"ok": False, "error": "no locked/selected target"}), 400

            eng = refs["eng"]
            st = eng.public_state() if eng is not None and hasattr(eng, "public_state") else {}
            helper_ctx = {
                "radar_xy_from_state": refs["radar_xy_from_state"],
                "ship_cell_from_state": refs["ship_cell_from_state"],
                "cell_to_world": refs["cell_to_world"],
                "CONVOY": refs["convoy"],
                "CAP": cap,
                "RUNTIME": refs.get("runtime"),
            }
            hx, hy, hermes_cell = _resolve_hermes_origin(helper_ctx, st)

            target_class_map = refs["target_class_map"]

            def _contact_class(contact):
                if contact is None:
                    return None
                try:
                    meta = getattr(contact, "meta", {}) or {}
                    if isinstance(meta, dict):
                        cap_meta = meta.get("cap") or {}
                        cls = cap_meta.get("class") if isinstance(cap_meta, dict) else None
                        if not cls:
                            cls = meta.get("class") or meta.get("type")
                        if cls:
                            return str(cls).title()
                except Exception:
                    pass
                for attr in ("class", "type"):
                    try:
                        val = getattr(contact, attr, None)
                        if val:
                            return str(val).title()
                    except Exception:
                        continue
                try:
                    name = getattr(contact, "name", None)
                    if name:
                        cls = target_class_map.get(str(name))
                        if cls:
                            return str(cls).title()
                except Exception:
                    pass
                return None

            def _normalize_loadout(value):
                if not value:
                    return ""
                v = str(value).strip().lower()
                if v in ("aim9", "aim-9", "sidewinder", "missile"):
                    return "aim9"
                if v in ("bomb", "bombs", "mk82", "iron"):
                    return "bombs"
                if v == "auto":
                    return ""
                return ""

            if tgt is not None:
                dx = float(getattr(tgt, "x", 0.0)) - float(hx)
                dy = float(getattr(tgt, "y", 0.0)) - float(hy)
                rng_nm = (dx * dx + dy * dy) ** 0.5
                try:
                    cell = refs["world_to_cell"](float(getattr(tgt, "x", 0.0)), float(getattr(tgt, "y", 0.0)))
                except Exception:
                    cell = "K13"
            else:
                tx, ty = refs["cell_to_world"](fallback_cell)
                dx, dy = float(tx) - float(hx), float(ty) - float(hy)
                rng_nm = (dx * dx + dy * dy) ** 0.5
                cell = fallback_cell

            target_class = _contact_class(tgt)
            requested_loadout = _normalize_loadout(data.get("loadout"))
            surface_classes = {"Ship", "Surface", "Carrier", "Escort", "Landing Craft", "Merchant", "Convoy"}
            air_classes = {"Aircraft", "Helicopter", "Missile", "Bomber", "Fighter"}
            auto_default = "aim9"
            if target_class and target_class in surface_classes:
                auto_default = "bombs"
            loadout = requested_loadout or auto_default
            if loadout == "bombs" and target_class and target_class in air_classes:
                loadout = "aim9"
            elif loadout == "aim9" and target_class and target_class in surface_classes and not requested_loadout:
                loadout = "bombs"

            try:
                res = cap.request_cap_launch(cell, loadout=loadout, range_nm=float(rng_nm))
            except Exception as exc:
                _safe_call(record_flight, {
                    "route": "/cap/request.fallback",
                    "method": request.method,
                    "status": 500,
                    "duration_ms": 0,
                    "request": {"cell": cell, "range_nm": rng_nm, "loadout": loadout},
                    "response": {"ok": False, "error": str(exc)},
                })
                return jsonify({"ok": False, "error": str(exc)}), 500

            status = 200 if res.get("ok") else 400
            payload = {
                "ok": bool(res.get("ok")),
                "message": res.get("message"),
                "target": {"id": getattr(tgt, "id", None), "cell": cell, "class": target_class},
                "loadout": loadout,
                "hermes_cell": hermes_cell,
            }
            _safe_call(record_flight, {
                "route": "/cap/request.fallback",
                "method": request.method,
                "status": status,
                "duration_ms": 0,
                "request": {"cell": cell, "range_nm": round(rng_nm, 2), "loadout": loadout, "target_class": target_class},
                "response": payload,
            })
            return jsonify(payload), status

    if "/cap/launch_to" not in existing:

        @app.post("/cap/launch_to")
        def _cap_launch_to_fallback():
            refs = _runtime_refs()
            cap = refs["cap"]
            if cap is None:
                return jsonify({"ok": False, "error": "CAP unavailable"}), 503

            data = request.get_json(silent=True) or {}
            cell = str(data.get("cell") or "").strip().upper()
            if not cell:
                return jsonify({"ok": False, "error": "missing cell"}), 400

            eng = refs["eng"]
            st = eng.public_state() if eng is not None and hasattr(eng, "public_state") else {}
            helper_ctx = {
                "radar_xy_from_state": refs["radar_xy_from_state"],
                "ship_cell_from_state": refs["ship_cell_from_state"],
                "cell_to_world": refs["cell_to_world"],
                "CONVOY": refs["convoy"],
                "CAP": cap,
                "RUNTIME": refs.get("runtime"),
            }
            hx, hy, hermes_cell = _resolve_hermes_origin(helper_ctx, st)

            tx, ty = refs["cell_to_world"](cell)
            dx, dy = float(tx) - float(hx), float(ty) - float(hy)
            rng_nm = (dx * dx + dy * dy) ** 0.5
            sm = data.get("station_minutes", 20)
            rm = data.get("radius_nm", 5)

            follow_raw = data.get("follow")
            follow = str(follow_raw).strip().lower() if follow_raw else None

            try:
                loadout_raw = str((data.get("loadout") or "aim9")).lower()
            except Exception:
                loadout_raw = "aim9"
            loadout = "bombs" if loadout_raw in ("bomb", "bombs") else "aim9"
            loadout_forced = None
            if follow == "hermes" and loadout != "aim9":
                loadout = "aim9"
                loadout_forced = "hermes_follow"

            try:
                res = cap.request_cap_to_cell(
                    cell,
                    distance_nm=float(rng_nm),
                    station_minutes=float(sm),
                    radius_nm=float(rm),
                    origin_xy=(hx, hy),
                    origin_cell=hermes_cell,
                    loadout=loadout,
                    follow=follow,
                )
            except Exception as exc:
                _safe_call(record_flight, {
                    "route": "/cap/launch_to.fallback",
                    "method": request.method,
                    "status": 500,
                    "duration_ms": 0,
                    "request": {},
                    "response": {"ok": False, "error": str(exc)},
                })
                return jsonify({"ok": False, "error": str(exc)}), 500

            status = 200 if res.get("ok") else 400
            mission = res.get("mission") or {}
            actual_loadout = str(mission.get("loadout") or loadout)
            payload = {
                "ok": bool(res.get("ok")),
                "message": res.get("message"),
                "mission": mission,
                "loadout": actual_loadout,
            }
            if loadout_forced and actual_loadout == "aim9":
                payload["loadout_forced"] = loadout_forced

            _safe_call(record_flight, {
                "route": "/cap/launch_to.fallback",
                "method": request.method,
                "status": status,
                "duration_ms": 0,
                "request": {
                    "cell": cell,
                    "range_nm": round(rng_nm, 2),
                    "loadout": loadout,
                    "follow": follow,
                },
                "response": payload,
            })
            return jsonify(payload), status
