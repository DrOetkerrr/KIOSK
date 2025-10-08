from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Tuple

from flask import Flask

from projects.falklandV2.engine_adapter import world_to_cell
from projects.falklandV2.subsystems import webcore as core
from projects.falklandV2.web import fallbacks, runtime

BASE_DIR = Path(__file__).resolve().parents[1]
TPL_DIR = BASE_DIR / "templates"


def _load_blueprint(import_path: str, attr: str) -> Tuple[str, object] | None:
    try:
        mod = __import__(import_path, fromlist=[attr])
        bp = getattr(mod, attr)
        return import_path, bp
    except Exception as exc:
        logging.exception("Failed to register blueprint %s.%s: %s", import_path, attr, exc)
        return None


_DEFAULT_BLUEPRINTS = OrderedDict([
    ('projects.falklandV2.routes.command', 'bp'),
    ('projects.falklandV2.routes.radar', 'bp'),
    ('projects.falklandV2.routes.radar_dev', 'bp'),
    ('projects.falklandV2.routes.weapons', 'bp'),
    ('projects.falklandV2.routes.cap', 'bp'),
    ('projects.falklandV2.routes.radio', 'bp'),
    ('projects.falklandV2.routes.interpreter', 'bp'),
    ('projects.falklandV2.routes.nav', 'bp'),
    ('projects.falklandV2.routes.skirmish', 'bp'),
    ('projects.falklandV2.routes.roadmap', 'bp'),
    ('projects.falklandV2.routes.contacts', 'bp'),
    ('projects.falklandV2.routes.pages', 'bp'),
    ('projects.falklandV2.routes.diag', 'bp'),
    ('projects.falklandV2.routes.flight', 'bp'),
    ('projects.falklandV2.routes.eng', 'bp'),
    ('projects.falklandV2.routes.resupply', 'bp'),
    ('projects.falklandV2.routes.mission', 'bp'),
    ('projects.falklandV2.routes.audio', 'bp'),
])


def register_blueprints(app: Flask, items: Iterable[Tuple[str, str]] | None = None) -> None:
    for import_path, attr in (items or _DEFAULT_BLUEPRINTS.items()):
        loaded = _load_blueprint(import_path, attr)
        if loaded:
            _, bp = loaded
            app.register_blueprint(bp)  # type: ignore[arg-type]


def create_app(config: dict | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(TPL_DIR),
        static_folder=str(BASE_DIR / "static"),
        static_url_path="/static",
    )
    if config:
        app.config.update(config)

    rt = runtime.init_runtime()
    runtime.attach_runtime(app, rt)

    register_blueprints(app)
    state = SimpleNamespace(
        CAP=getattr(rt, "cap", None),
        cap=getattr(rt, "cap", None),
        RADAR=getattr(rt, "radar", None),
        radar=getattr(rt, "radar", None),
        CONVOY=getattr(rt, "convoy", None),
        convoy=getattr(rt, "convoy", None),
        ENG=getattr(rt, "engine", None),
        engine=getattr(rt, "engine", None),
        record_flight=getattr(rt, "record_flight", None),
        TARGET_CLASS_BY_NAME=getattr(core, "TARGET_CLASS_BY_NAME", {}),
        world_to_cell=world_to_cell,
        cell_to_world=getattr(core, "cell_to_world", lambda *_: (20.0, 20.0)),
        ship_cell_from_state=getattr(core, "ship_cell_from_state", lambda *_: "K13"),
        radar_xy_from_state=getattr(core, "radar_xy_from_state", lambda *_: (20.0, 20.0)),
    )
    fallbacks.ensure_cap_fallbacks(app, state)

    return app
