from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projects.falklandV2.web import create_app


def test_create_app_registers_core_routes():
    app = create_app({"TESTING": True})

    # Flask stores the runtime instance on extensions; ensure it is attached.
    assert "falkland.runtime" in app.extensions

    routes = {rule.rule for rule in app.url_map.iter_rules()}
    # Spot-check a few critical routes provided by registered blueprints.
    assert "/api/command" in routes
    assert "/radar/force_spawn_hostile" in routes
    assert "/radar/reload_catalog" in routes
